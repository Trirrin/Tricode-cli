import os
import re
import hashlib
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Tuple

from tree_sitter import Node, Parser

from agent.symbol_search import (
    SymbolBlock,
    _EXTENSION_LANGUAGE,
    _get_parser,
    collect_all_symbol_blocks,
    compute_symbol_id,
)


@dataclass
class DefinitionLocation:
    file: str
    start_line: int
    start_col: Optional[int] = None


@dataclass
class SymbolIdentity:
    language: Optional[str]
    name: str
    kind: Optional[str] = None
    symbol_id: Optional[str] = None


@dataclass
class SymbolReference:
    file_path: str
    start_line: int
    start_col: int
    end_line: int
    end_col: int
    language: Optional[str]
    usage_kind: str
    is_definition: bool
    confidence: str
    reason: Optional[str] = None


@dataclass
class ReferenceSearchOptions:
    include_tests: bool = True
    include_third_party: bool = True
    max_results: Optional[int] = None
    mode: str = "include_text"
    sort_by: str = "file"
    include_definition: bool = False
    group_by: str = "none"


SEMANTIC_REFERENCE_LANGUAGES = {"python", "c", "cpp", "java", "go", "rust"}

_TEST_DIR_NAMES = {
    "test",
    "tests",
    "testing",
    "__tests__",
    "spec",
}

_THIRD_PARTY_DIR_NAMES = {
    "third_party",
    "third-party",
    "vendor",
    "node_modules",
    "dist",
    "build",
}


def locate_definition_for_position(
    root: str, location: DefinitionLocation
) -> Optional[SymbolBlock]:
    target_path = os.path.realpath(location.file)
    try:
        blocks = collect_all_symbol_blocks(root, max_results=None)
    except Exception:
        return None

    candidates: List[SymbolBlock] = []
    for block in blocks:
        if os.path.realpath(block.filepath) != target_path:
            continue
        if block.start_line <= location.start_line <= block.end_line:
            candidates.append(block)
    if not candidates:
        return None
    candidates.sort(
        key=lambda b: (b.end_line - b.start_line, b.start_line)
    )
    return candidates[0]


def find_references(
    root: str,
    definition: Optional[DefinitionLocation],
    symbol: Optional[SymbolIdentity],
    options: ReferenceSearchOptions,
) -> Dict:
    if not definition and not symbol:
        return {
            "status": "error",
            "error": "invalid_input",
            "message": "Either definition or symbol must be provided.",
        }
    if definition and symbol:
        return {
            "status": "error",
            "error": "invalid_input",
            "message": "Provide either definition or symbol, not both.",
        }

    resolved_root = os.path.realpath(root)

    definition_block: Optional[SymbolBlock] = None
    identity: Optional[SymbolIdentity] = None

    if definition is not None:
        definition_block = locate_definition_for_position(resolved_root, definition)
        if definition_block is None:
            return {
                "status": "error",
                "error": "definition_not_found",
                "message": "No indexed symbol definition found at the given location.",
            }
        language = getattr(definition_block, "language", None)
        name = getattr(definition_block, "name", "")
        kind = getattr(definition_block, "kind", None)
        qualified_name = getattr(definition_block, "qualified_name", None)
        symbol_id = compute_symbol_id(language, kind, qualified_name, name)
        identity = SymbolIdentity(
            language=language,
            name=name,
            kind=kind,
            symbol_id=symbol_id,
        )
    else:
        if symbol is None:
            return {
                "status": "error",
                "error": "invalid_input",
                "message": "Symbol must have a non-empty name.",
            }
        if symbol.symbol_id and not symbol.name:
            block = _find_block_by_symbol_id(resolved_root, symbol.symbol_id)
            if block is None:
                return {
                    "status": "error",
                    "error": "symbol_id_not_found",
                    "message": "No indexed symbol matched the given symbol_id.",
                }
            language = getattr(block, "language", None)
            name = getattr(block, "name", "")
            kind = getattr(block, "kind", None)
            qualified_name = getattr(block, "qualified_name", None)
            symbol_id = compute_symbol_id(language, kind, qualified_name, name)
            definition_block = block
            identity = SymbolIdentity(
                language=language,
                name=name,
                kind=kind,
                symbol_id=symbol_id,
            )
        else:
            if not symbol.name:
                return {
                    "status": "error",
                    "error": "invalid_input",
                    "message": "Symbol must have a non-empty name.",
                }
            identity = symbol

    if identity is None:
        return {
            "status": "error",
            "error": "invalid_state",
            "message": "Failed to resolve symbol identity.",
        }

    symbol_name = identity.name
    symbol_language = identity.language
    max_results = options.max_results if isinstance(options.max_results, int) and options.max_results > 0 else None
    mode = options.mode if options.mode in ("precise", "include_text") else "include_text"

    sort_by_raw = options.sort_by or "file"
    if sort_by_raw not in ("file", "confidence"):
        sort_by = "file"
        warnings: List[str] = [
            (
                f"Unknown sort_by='{sort_by_raw}', falling back to sort_by='file'. "
                "Supported values are 'file' or 'confidence'."
            )
        ]
    else:
        sort_by = sort_by_raw
        warnings = []

    semantic_references: List[SymbolReference] = []
    text_references: List[SymbolReference] = []
    seen_semantic_locations: set[Tuple[str, int, int, int, int]] = set()

    if definition_block is not None and options.include_definition:
        def_start_col = definition.start_col if definition and isinstance(definition.start_col, int) else 0
        def_ref = SymbolReference(
            file_path=definition_block.filepath,
            start_line=definition_block.start_line,
            start_col=def_start_col,
            end_line=definition_block.start_line,
            end_col=def_start_col,
            language=getattr(definition_block, "language", None),
            usage_kind="definition",
            is_definition=True,
            confidence="exact",
            reason="definition_location",
        )
        semantic_references.append(def_ref)

    for dirpath, dirnames, filenames in os.walk(resolved_root):
        _prune_directories(dirnames, options)
        for filename in filenames:
            filepath = os.path.join(dirpath, filename)
            file_language = _infer_language_from_extension(filename)

            if symbol_language and file_language and symbol_language != file_language:
                has_semantic = file_language in SEMANTIC_REFERENCE_LANGUAGES
            else:
                has_semantic = file_language in SEMANTIC_REFERENCE_LANGUAGES

            semantic_for_file: List[SymbolReference] = []
            if has_semantic and file_language is not None and symbol_language in (None, file_language):
                if file_language == "python":
                    semantic_for_file = _python_semantic_references(
                        filepath, symbol_name
                    )
                else:
                    language_key = _EXTENSION_LANGUAGE.get(
                        os.path.splitext(filename)[1].lower()
                    )
                    if language_key:
                        semantic_for_file = _tree_sitter_semantic_references(
                            filepath, language_key, symbol_name
                        )
                for ref in semantic_for_file:
                    key = (
                        ref.file_path,
                        ref.start_line,
                        ref.start_col,
                        ref.end_line,
                        ref.end_col,
                    )
                    if key in seen_semantic_locations:
                        continue
                    seen_semantic_locations.add(key)
                    semantic_references.append(ref)
                    if max_results is not None and len(semantic_references) >= max_results:
                        break
            if max_results is not None and len(semantic_references) >= max_results:
                break

            if mode == "include_text":
                remaining = None
                if max_results is not None:
                    remaining = max_results - len(semantic_references) - len(text_references)
                    if remaining <= 0:
                        continue
                text_for_file = _text_references_for_file(
                    filepath,
                    symbol_name,
                    remaining,
                    seen_semantic_locations,
                    file_language or symbol_language,
                )
                text_references.extend(text_for_file)
                if max_results is not None and len(semantic_references) + len(text_references) >= max_results:
                    break
        if max_results is not None and len(semantic_references) + len(text_references) >= max_results:
            break

    all_references: List[SymbolReference] = []
    if mode == "precise":
        all_references.extend(semantic_references)
    else:
        all_references.extend(semantic_references)
        all_references.extend(text_references)

    references_json = _merge_references(
        all_references,
        sort_by,
        symbol_id=identity.symbol_id,
        group_by=options.group_by,
    )

    semantic_count = sum(1 for r in references_json if r.get("confidence") != "text_only")
    text_count = sum(1 for r in references_json if r.get("confidence") == "text_only") if mode == "include_text" else 0

    payload = {
        "status": "ok",
        "mode": mode,
        "symbol": {
            "source": "definition" if definition is not None else "identifier",
            "language": identity.language,
            "name": identity.name,
            "kind": identity.kind,
            "symbol_id": identity.symbol_id,
        },
        "capabilities": {
            "semantic_languages": sorted(SEMANTIC_REFERENCE_LANGUAGES),
            "text_fallback": True,
        },
        "summary": {
            "total": len(references_json),
            "semantic": semantic_count,
            "text_only": text_count,
        },
        "references": references_json,
    }
    if warnings:
        payload["warnings"] = warnings
    return payload


def _prune_directories(dirnames: List[str], options: ReferenceSearchOptions) -> None:
    initial = list(dirnames)
    dirnames[:] = []
    for name in initial:
        lowered = name.lower()
        if not options.include_tests and lowered in _TEST_DIR_NAMES:
            continue
        if not options.include_third_party and lowered in _THIRD_PARTY_DIR_NAMES:
            continue
        dirnames.append(name)


def _infer_language_from_extension(filename: str) -> Optional[str]:
    ext = os.path.splitext(filename)[1].lower()
    language_key = _EXTENSION_LANGUAGE.get(ext)
    if language_key == "c":
        return "c"
    if language_key == "cpp":
        return "cpp"
    if language_key == "java":
        return "java"
    if language_key == "go":
        return "go"
    if language_key == "rust":
        return "rust"
    if ext == ".py":
        return "python"
    return None


def _merge_references(
    references: List[SymbolReference],
    sort_by: str,
    symbol_id: Optional[str],
    group_by: str,
) -> List[Dict]:
    merged: Dict[Tuple[str, int, int, int, int], Dict] = {}

    for ref in references:
        key = (ref.file_path, ref.start_line, ref.start_col, ref.end_line, ref.end_col)
        entry = merged.get(key)
        if entry is None:
            primary_kind = ref.usage_kind
            entry = {
                "file_path": ref.file_path,
                "start_line": ref.start_line,
                "start_col": ref.start_col,
                "end_line": ref.end_line,
                "end_col": ref.end_col,
                "language": ref.language,
                "kind": primary_kind,
                "primary_kind": primary_kind,
                "secondary_kinds": [],
                "is_definition": ref.is_definition,
                "confidence": ref.confidence,
                "reason": ref.reason,
                "symbol_id": symbol_id,
            }
            merged[key] = entry
        else:
            current_kind = entry.get("primary_kind") or entry.get("kind") or "other"
            new_kind = _select_primary_kind(current_kind, ref.usage_kind)
            if new_kind != current_kind:
                if current_kind not in entry["secondary_kinds"]:
                    entry["secondary_kinds"].append(current_kind)
                entry["primary_kind"] = new_kind
                entry["kind"] = new_kind
            else:
                if (
                    ref.usage_kind != current_kind
                    and ref.usage_kind not in entry["secondary_kinds"]
                ):
                    entry["secondary_kinds"].append(ref.usage_kind)
            if ref.is_definition:
                entry["is_definition"] = True
            rank = {"exact": 0, "probable": 1, "text_only": 2}
            existing_rank = rank.get(entry.get("confidence", "probable"), 1)
            new_rank = rank.get(ref.confidence or "probable", 1)
            if new_rank < existing_rank:
                entry["confidence"] = ref.confidence
                entry["reason"] = ref.reason

    results = list(merged.values())
    if sort_by == "confidence":
        rank = {"exact": 0, "probable": 1, "text_only": 2}
        results.sort(
            key=lambda r: (
                rank.get(r.get("confidence", "probable"), 1),
                r.get("file_path", ""),
                r.get("start_line", 0),
                r.get("start_col", 0),
            )
        )
    else:
        results.sort(
            key=lambda r: (
                r.get("file_path", ""),
                r.get("start_line", 0),
                r.get("start_col", 0),
            )
        )

    if group_by == "file":
        by_file: Dict[str, List[Dict]] = {}
        for ref in results:
            path = ref.get("file_path") or ""
            by_file.setdefault(path, []).append(ref)
        for path, refs in by_file.items():
            for index, ref in enumerate(refs, 1):
                ref.setdefault("by_file_index", index)

    for index, ref in enumerate(results, 1):
        raw = f"{ref.get('file_path','')}:{ref.get('start_line',0)}:{ref.get('start_col',0)}:{index}"
        ref["reference_id"] = hashlib.sha256(raw.encode("utf-8", errors="ignore")).hexdigest()[:16]

    return results


def _select_primary_kind(current: str, new: str) -> str:
    if current == new:
        return current
    priority = {
        "definition": 0,
        "call": 1,
        "write": 2,
        "read": 3,
        "import": 4,
        "inheritance": 5,
        "annotation": 6,
        "type_usage": 7,
        "macro_use": 8,
        "other": 9,
    }
    current_rank = priority.get(current, 9)
    new_rank = priority.get(new, 9)
    if new_rank < current_rank:
        return new
    return current


def _find_block_by_symbol_id(root: str, symbol_id: str) -> Optional[SymbolBlock]:
    try:
        blocks = collect_all_symbol_blocks(root, max_results=None)
    except Exception:
        return None
    for block in blocks:
        if getattr(block, "symbol_id", None) == symbol_id:
            return block
    return None


def _python_semantic_references(filepath: str, symbol_name: str) -> List[SymbolReference]:
    try:
        import ast

        with open(filepath, "r", encoding="utf-8", errors="ignore") as handle:
            source = handle.read()
    except Exception:
        return []

    try:
        module = ast.parse(source, filename=filepath)
    except SyntaxError:
        return []

    references: List[SymbolReference] = []
    for node in ast.walk(module):
        if isinstance(node, ast.Call):
            target = node.func
            name = None
            if isinstance(target, ast.Name):
                name = target.id
            elif isinstance(target, ast.Attribute) and isinstance(target.attr, str):
                name = target.attr
            if name != symbol_name:
                continue
            line = getattr(node, "lineno", None)
            col = getattr(node, "col_offset", None)
            end_line = getattr(node, "end_lineno", line)
            end_col = getattr(node, "end_col_offset", col)
            if not isinstance(line, int) or not isinstance(col, int):
                continue
            if not isinstance(end_line, int) or not isinstance(end_col, int):
                end_line = line
                end_col = col
            references.append(
                SymbolReference(
                    file_path=filepath,
                    start_line=line,
                    start_col=col,
                    end_line=end_line,
                    end_col=end_col,
                    language="python",
                    usage_kind="call",
                    is_definition=False,
                    confidence="probable",
                    reason=None,
                )
            )
        elif isinstance(node, ast.Name) and isinstance(node.id, str) and node.id == symbol_name:
            line = getattr(node, "lineno", None)
            col = getattr(node, "col_offset", None)
            end_line = getattr(node, "end_lineno", line)
            end_col = getattr(node, "end_col_offset", col)
            if not isinstance(line, int) or not isinstance(col, int):
                continue
            if not isinstance(end_line, int) or not isinstance(end_col, int):
                end_line = line
                end_col = col
            references.append(
                SymbolReference(
                    file_path=filepath,
                    start_line=line,
                    start_col=col,
                    end_line=end_line,
                    end_col=end_col,
                    language="python",
                    usage_kind="other",
                    is_definition=False,
                    confidence="probable",
                    reason=None,
                )
            )
    return references


def _tree_sitter_semantic_references(
    filepath: str, language_key: str, symbol_name: str
) -> List[SymbolReference]:
    try:
        with open(filepath, "r", encoding="utf-8", errors="ignore") as handle:
            source = handle.read()
    except Exception:
        return []

    source_bytes = source.encode("utf-8", errors="ignore")
    try:
        parser: Parser = _get_parser(language_key)
        tree = parser.parse(source_bytes)
    except Exception:
        return []

    references: List[SymbolReference] = []
    language = _map_language_key(language_key)
    for node in _iter_identifier_like_nodes(tree.root_node, language_key):
        text = _slice_text(source_bytes, node)
        if text != symbol_name:
            continue
        usage_kind = _classify_usage_kind(node, language_key)
        start_row, start_col = node.start_point
        end_row, end_col = node.end_point
        references.append(
            SymbolReference(
                file_path=filepath,
                start_line=start_row + 1,
                start_col=start_col,
                end_line=end_row + 1,
                end_col=end_col,
                language=language,
                usage_kind=usage_kind,
                is_definition=False,
                confidence="probable",
                reason=None,
            )
        )
    return references


def _map_language_key(language_key: str) -> str:
    if language_key == "cpp":
        return "cpp"
    if language_key == "c":
        return "c"
    if language_key == "go":
        return "go"
    if language_key == "java":
        return "java"
    if language_key == "rust":
        return "rust"
    return language_key


def _iter_identifier_like_nodes(root: Node, language_key: str) -> Iterable[Node]:
    stack = [root]
    if language_key in ("c", "cpp", "go", "java", "rust"):
        candidate_types = {
            "identifier",
            "field_identifier",
            "type_identifier",
            "scoped_identifier",
        }
    else:
        candidate_types = {"identifier"}
    while stack:
        node = stack.pop()
        if node.type in candidate_types:
            yield node
        stack.extend(node.children)


def _slice_text(source: bytes, node: Node) -> str:
    return source[node.start_byte : node.end_byte].decode("utf-8", errors="ignore")


def _classify_usage_kind(node: Node, language_key: str) -> str:
    parent = node.parent
    depth = 0
    while parent is not None and depth < 4:
        t = parent.type
        if t in (
            "call_expression",
            "function_call_expression",
            "function_call",
            "method_invocation",
            "constructor_expression",
        ):
            return "call"
        if t in (
            "import_declaration",
            "import_spec",
            "using_declaration",
            "namespace_import",
            "package_clause",
        ):
            return "import"
        if t in (
            "class_declaration",
            "interface_declaration",
            "extends_clause",
            "implements_clause",
            "base_clause",
            "inheritance_specifier",
        ):
            return "inheritance"
        if t in (
            "annotation",
            "marker_annotation",
            "attribute",
        ):
            return "annotation"
        parent = parent.parent
        depth += 1
    return "other"


def _text_references_for_file(
    filepath: str,
    symbol_name: str,
    max_results: Optional[int],
    seen_semantic_locations: set[Tuple[str, int, int, int, int]],
    language: Optional[str],
) -> List[SymbolReference]:
    try:
        pattern = re.compile(r"\\b" + re.escape(symbol_name) + r"\\b")
    except re.error:
        pattern = re.compile(re.escape(symbol_name))

    references: List[SymbolReference] = []
    try:
        with open(filepath, "r", encoding="utf-8", errors="ignore") as handle:
            for line_no, line in enumerate(handle, 1):
                match = pattern.search(line)
                if not match:
                    continue
                start_col = match.start()
                end_col = match.end()
                key = (filepath, line_no, start_col, line_no, end_col)
                if key in seen_semantic_locations:
                    continue
                references.append(
                    SymbolReference(
                        file_path=filepath,
                        start_line=line_no,
                        start_col=start_col,
                        end_line=line_no,
                        end_col=end_col,
                        language=language,
                        usage_kind="other",
                        is_definition=False,
                        confidence="text_only",
                        reason="fallback_text_search",
                    )
                )
                if max_results is not None and len(references) >= max_results:
                    break
    except Exception:
        return []
    return references
