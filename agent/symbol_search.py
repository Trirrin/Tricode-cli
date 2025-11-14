import ast
import os
import re
from dataclasses import dataclass
from functools import lru_cache
from typing import Callable, Iterable, List, Optional

from tree_sitter import Node, Parser
from tree_sitter_language_pack import get_language


@dataclass
class SymbolBlock:
    filepath: str
    start_line: int
    end_line: int
    language: Optional[str] = None
    kind: Optional[str] = None
    name: Optional[str] = None


@dataclass
class TreeSitterConfig:
    language: str
    node_types: tuple[str, ...]
    comment_prefixes: tuple[str, ...]
    extractor: Callable[[Node, bytes], Iterable[str]]


def search_symbol_blocks(symbol: str, root: str, max_results: Optional[int]) -> List[SymbolBlock]:
    if not symbol:
        return []

    python_blocks = _collect_python_blocks(
        root=root,
        max_results=max_results,
        name_predicate=lambda name: name == symbol,
    )
    remaining = None
    if isinstance(max_results, int) and max_results > 0:
        remaining = max(max_results - len(python_blocks), 0)

    tree_sitter_limit = remaining if remaining not in (None, 0) else None
    tree_blocks = _collect_tree_sitter_blocks(
        root=root,
        max_results=tree_sitter_limit,
        name_predicate=lambda names: _symbol_matches(symbol, names),
    )
    return python_blocks + tree_blocks


def collect_all_symbol_blocks(root: str, max_results: Optional[int]) -> List[SymbolBlock]:
    python_blocks = _collect_python_blocks(root=root, max_results=max_results)
    remaining = None
    if isinstance(max_results, int) and max_results > 0:
        remaining = max(max_results - len(python_blocks), 0)
    tree_sitter_limit = remaining if remaining not in (None, 0) else None
    tree_blocks = _collect_tree_sitter_blocks(root=root, max_results=tree_sitter_limit)
    return python_blocks + tree_blocks


_PY_SHEBANG_RE = re.compile(r"^#!.*\bpython[0-9.]*\b")


def _is_python_source_file(filepath: str, name: str) -> bool:
    if name.endswith(".py"):
        return True
    try:
        with open(filepath, "r", encoding="utf-8", errors="ignore") as handle:
            first_line = handle.readline()
    except Exception:
        return False
    return bool(_PY_SHEBANG_RE.match(first_line))


def _collect_python_blocks(
    root: str,
    max_results: Optional[int],
    name_predicate: Optional[Callable[[str], bool]] = None,
) -> List[SymbolBlock]:
    matches: List[SymbolBlock] = []
    max_count = max_results if isinstance(max_results, int) and max_results > 0 else None

    for dirpath, _, filenames in os.walk(root):
        for name in filenames:
            filepath = os.path.join(dirpath, name)
            if not _is_python_source_file(filepath, name):
                continue
            try:
                with open(filepath, "r", encoding="utf-8", errors="ignore") as handle:
                    source = handle.read()
            except Exception:
                continue

            try:
                module = ast.parse(source, filename=filepath)
            except SyntaxError:
                continue

            lines = source.splitlines(keepends=True)
            for node in ast.walk(module):
                if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    continue
                node_name = getattr(node, "name", None)
                if not isinstance(node_name, str):
                    continue
                if name_predicate is not None and not name_predicate(node_name):
                    continue

                start_line = _python_node_start_line(node)
                end_line = getattr(node, "end_lineno", None)
                if not isinstance(end_line, int):
                    end_line = _infer_python_end_line(lines, start_line)

                if isinstance(node, ast.ClassDef):
                    kind = "class"
                else:
                    kind = "function"

                matches.append(
                    SymbolBlock(
                        filepath=filepath,
                        start_line=start_line,
                        end_line=end_line,
                        language="python",
                        kind=kind,
                        name=node_name,
                    )
                )
                if max_count is not None and len(matches) >= max_count:
                    return matches

    return matches


def _python_node_start_line(node: ast.AST) -> int:
    start_line = getattr(node, "lineno", 1)
    decorators = getattr(node, "decorator_list", [])
    if decorators:
        deco_lines = [getattr(deco, "lineno", start_line) for deco in decorators]
        start_line = min([start_line] + deco_lines)
    return max(1, start_line)


def _infer_python_end_line(lines: List[str], start_line: int) -> int:
    index = max(0, start_line - 1)
    if index >= len(lines):
        return start_line

    base_indent = len(lines[index]) - len(lines[index].lstrip(" \t"))
    end_index = index
    max_span = 400
    last_index = min(len(lines) - 1, index + max_span - 1)

    for current in range(index + 1, last_index + 1):
        raw = lines[current]
        stripped = raw.lstrip()
        if not stripped:
            end_index = current
            continue
        indent = len(raw) - len(stripped)
        if indent <= base_indent and not stripped.startswith(("#", "@")):
            break
        end_index = current

    return end_index + 1


def _collect_tree_sitter_blocks(
    root: str,
    max_results: Optional[int],
    name_predicate: Optional[Callable[[Iterable[str]], bool]] = None,
) -> List[SymbolBlock]:
    matches: List[SymbolBlock] = []
    max_count = max_results if isinstance(max_results, int) and max_results > 0 else None

    for dirpath, _, filenames in os.walk(root):
        for name in filenames:
            ext = os.path.splitext(name)[1].lower()
            language_key = _EXTENSION_LANGUAGE.get(ext)
            if not language_key:
                continue

            config = _TREE_SITTER_CONFIGS.get(language_key)
            if not config:
                continue

            filepath = os.path.join(dirpath, name)
            try:
                with open(filepath, "r", encoding="utf-8", errors="ignore") as handle:
                    source = handle.read()
            except Exception:
                continue

            source_bytes = source.encode("utf-8", errors="ignore")
            lines = source.splitlines(keepends=True)

            parser = _get_parser(language_key)
            tree = parser.parse(source_bytes)

            for node in _iter_nodes_of_types(tree.root_node, config.node_types):
                names = list(config.extractor(node, source_bytes))
                if not names:
                    continue
                if name_predicate is not None and not name_predicate(names):
                    continue

                start_line = node.start_point[0] + 1
                end_line = node.end_point[0] + 1
                if config.comment_prefixes:
                    start_line = _extend_comment_region(lines, start_line, config.comment_prefixes)

                primary_name: Optional[str] = None
                if name_predicate is None:
                    primary_name = names[0]
                else:
                    for candidate_name in names:
                        if name_predicate([candidate_name]):
                            primary_name = candidate_name
                            break
                    if primary_name is None:
                        primary_name = names[0]

                kind = _infer_symbol_kind(language_key, node.type)

                matches.append(
                    SymbolBlock(
                        filepath=filepath,
                        start_line=start_line,
                        end_line=end_line,
                        language=config.language,
                        kind=kind,
                        name=primary_name,
                    )
                )
                if max_count is not None and len(matches) >= max_count:
                    return matches

    return matches


def _symbol_matches(target: str, candidates: Iterable[str]) -> bool:
    for candidate in candidates:
        if not candidate:
            continue
        if candidate == target:
            return True
        if "::" in candidate and (candidate.endswith(f"::{target}") or candidate == target):
            return True
        if "::" in target and candidate == target.split("::")[-1]:
            if target.endswith(candidate):
                return True
    return False


def _extend_comment_region(lines: List[str], start_line: int, prefixes: tuple[str, ...]) -> int:
    index = max(0, start_line - 1)
    while index > 0:
        prev = lines[index - 1].strip()
        if not prev:
            index -= 1
            continue
        if any(prev.startswith(prefix) for prefix in prefixes):
            index -= 1
            continue
        break
    return index + 1


def _iter_nodes_of_types(root: Node, node_types: tuple[str, ...]) -> Iterable[Node]:
    stack = [root]
    wanted = set(node_types)
    while stack:
        node = stack.pop()
        if node.type in wanted:
            yield node
        stack.extend(node.children)


def _make_field_extractor(field_name: str) -> Callable[[Node, bytes], Iterable[str]]:
    def _extract(node: Node, source: bytes) -> Iterable[str]:
        child = node.child_by_field_name(field_name)
        if child is None:
            return []
        return [_slice_text(source, child)]

    return _extract


def _c_like_extractor(node: Node, source: bytes) -> Iterable[str]:
    declarator = node.child_by_field_name("declarator")
    if declarator is None:
        return []
    text = _slice_text(source, declarator)
    if not text:
        return []
    paren_index = text.find("(")
    if paren_index != -1:
        head = text[:paren_index]
    else:
        head = text
    head = head.replace("*", " ").replace("&", " ").strip()
    if not head:
        return []

    tokens = []
    for chunk in _NON_IDENTIFIER_SPLIT.split(head):
        chunk = chunk.strip()
        if not chunk:
            continue
        tokens.append(chunk)

    names = set()
    for chunk in tokens:
        names.add(chunk)
        parts = [part for part in chunk.split("::") if part]
        if len(parts) > 1:
            names.add(parts[-1])
    return names


def _cpp_extractor(node: Node, source: bytes) -> Iterable[str]:
    node_type = node.type
    if node_type in ("function_definition", "function_declaration", "method_definition"):
        return _c_like_extractor(node, source)
    if node_type in ("class_specifier", "struct_specifier", "enum_specifier"):
        child = node.child_by_field_name("name")
        if child is None:
            return []
        return [_slice_text(source, child)]
    return []


def _slice_text(source: bytes, node: Node) -> str:
    return source[node.start_byte : node.end_byte].decode("utf-8", errors="ignore")


def _infer_symbol_kind(language_key: str, node_type: str) -> Optional[str]:
    lang = language_key
    t = node_type
    if lang == "cpp":
        if t in ("class_specifier", "struct_specifier"):
            return "class"
        if t == "enum_specifier":
            return "enum"
        if "function" in t:
            return "function"
        if "method" in t:
            return "method"
        return None
    if lang in ("c", "go"):
        if "function" in t:
            return "function"
        if "method" in t:
            return "method"
        if t == "type_spec":
            return "type"
        return None
    if lang == "rust":
        if t in ("function_item", "method_item"):
            return "function"
        if t == "struct_item":
            return "struct"
        if t == "enum_item":
            return "enum"
        if t == "trait_item":
            return "trait"
        if t == "impl_item":
            return "impl"
        if t == "mod_item":
            return "module"
        return None
    if lang == "java":
        if t == "class_declaration":
            return "class"
        if t == "interface_declaration":
            return "interface"
        if t == "enum_declaration":
            return "enum"
        if t == "record_declaration":
            return "record"
        if t == "annotation_type_declaration":
            return "annotation"
        if t == "method_declaration":
            return "method"
        if t == "constructor_declaration":
            return "constructor"
        return None
    return None


@lru_cache(maxsize=None)
def _get_parser(language_key: str) -> Parser:
    language = get_language(language_key)  # type: ignore[arg-type]
    parser = Parser()
    parser.language = language
    return parser


_NON_IDENTIFIER_SPLIT = re.compile(r"[^0-9A-Za-z_:~]+")

_TREE_SITTER_CONFIGS = {
    "c": TreeSitterConfig(
        language="c",
        node_types=("function_definition",),
        comment_prefixes=("//", "/*", "*", "*/"),
        extractor=_c_like_extractor,
    ),
    "cpp": TreeSitterConfig(
        language="cpp",
        node_types=(
            "function_definition",
            "function_declaration",
            "method_definition",
            "class_specifier",
            "struct_specifier",
            "enum_specifier",
        ),
        comment_prefixes=("//", "/*", "*", "*/"),
        extractor=_cpp_extractor,
    ),
    "java": TreeSitterConfig(
        language="java",
        node_types=(
            "class_declaration",
            "interface_declaration",
            "enum_declaration",
            "record_declaration",
            "annotation_type_declaration",
            "method_declaration",
            "constructor_declaration",
        ),
        comment_prefixes=("//", "/*", "*", "*/"),
        extractor=_make_field_extractor("name"),
    ),
    "go": TreeSitterConfig(
        language="go",
        node_types=(
            "function_declaration",
            "method_declaration",
            "type_spec",
        ),
        comment_prefixes=("//", "/*", "*", "*/"),
        extractor=_make_field_extractor("name"),
    ),
    "rust": TreeSitterConfig(
        language="rust",
        node_types=(
            "function_item",
            "method_item",
            "struct_item",
            "enum_item",
            "trait_item",
            "impl_item",
            "mod_item",
        ),
        comment_prefixes=("//", "/*", "*", "*/"),
        extractor=_make_field_extractor("name"),
    ),
}

_EXTENSION_LANGUAGE = {
    ".c": "c",
    ".h": "c",
    ".cpp": "cpp",
    ".hpp": "cpp",
    ".cc": "cpp",
    ".cxx": "cpp",
    ".java": "java",
    ".go": "go",
    ".rs": "rust",
}
