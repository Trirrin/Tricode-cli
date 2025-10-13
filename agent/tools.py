import os
import shutil
import re
import subprocess
import tempfile
from typing import Tuple, Dict, Optional
from datetime import datetime
import stat
import threading
import queue
import time
import uuid
import json
from pathlib import Path
import socket
from urllib.parse import urlparse
import requests
import html2text
from bs4 import BeautifulSoup
from ddgs import DDGS
import difflib
import hashlib

CURRENT_PLAN = None
CURRENT_SESSION_ID = None
PLAN_DECISION_MADE = False
SIGNIFICANT_ACTIONS_COUNT = 0
LAST_PLAN_UPDATE_AT = 0

WORK_DIR = None
BYPASS_WORK_DIR_LIMIT = False
LAST_WEB_SEARCH_TIME = 0
WEB_SEARCH_RATE_LIMIT = 1.5

def get_plan_dir() -> Path:
    plan_dir = Path.home() / ".tricode" / "plans"
    plan_dir.mkdir(parents=True, exist_ok=True)
    return plan_dir

def set_work_dir(work_dir: str = None, bypass: bool = False) -> None:
    global WORK_DIR, BYPASS_WORK_DIR_LIMIT
    BYPASS_WORK_DIR_LIMIT = bypass
    if work_dir:
        WORK_DIR = os.path.realpath(work_dir)
    else:
        WORK_DIR = os.path.realpath(os.getcwd())

def resolve_path(path: str) -> str:
    expanded = os.path.expanduser(path)
    if os.path.isabs(expanded):
        return os.path.realpath(expanded)
    else:
        return os.path.realpath(os.path.join(WORK_DIR, expanded))

def validate_path(path: str) -> Tuple[bool, str]:
    if BYPASS_WORK_DIR_LIMIT:
        return True, ""
    
    try:
        real_path = os.path.realpath(path)
        if not real_path.startswith(WORK_DIR + os.sep) and real_path != WORK_DIR:
            return False, f"Access denied: {path} is outside work directory {WORK_DIR}"
        return True, ""
    except Exception as e:
        return False, f"Path validation error: {str(e)}"

def save_plan_state(session_id: str, plan_data: dict) -> None:
    if not session_id:
        return
    plan_file = get_plan_dir() / f"{session_id}.json"
    try:
        with open(plan_file, 'w', encoding='utf-8') as f:
            json.dump(plan_data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"Warning: Failed to save plan for session {session_id}: {e}", flush=True)

def load_plan_state(session_id: str) -> Optional[dict]:
    if not session_id:
        return None
    plan_file = get_plan_dir() / f"{session_id}.json"
    if not plan_file.exists():
        return None
    try:
        with open(plan_file, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        print(f"Warning: Failed to load plan for session {session_id}: {e}", flush=True)
        return None

def set_session_id(session_id: str) -> None:
    global CURRENT_SESSION_ID, PLAN_DECISION_MADE, SIGNIFICANT_ACTIONS_COUNT, LAST_PLAN_UPDATE_AT
    CURRENT_SESSION_ID = session_id
    PLAN_DECISION_MADE = False
    SIGNIFICANT_ACTIONS_COUNT = 0
    LAST_PLAN_UPDATE_AT = 0

def restore_plan(session_id: str) -> None:
    global CURRENT_PLAN, PLAN_DECISION_MADE, SIGNIFICANT_ACTIONS_COUNT
    plan_data = load_plan_state(session_id)
    if plan_data:
        CURRENT_PLAN = plan_data
        PLAN_DECISION_MADE = True
        SIGNIFICANT_ACTIONS_COUNT = 0

def get_plan_state() -> Optional[dict]:
    return CURRENT_PLAN

def get_plan_final_reminder() -> str:
    if CURRENT_PLAN is None:
        return None
    
    incomplete = [t for t in CURRENT_PLAN["tasks"] if t["status"] != "completed"]
    if not incomplete:
        return None
    
    result = [f"⚠️ WARNING: {len(incomplete)} task(s) still incomplete:"]
    for task in incomplete:
        result.append(f"  [{task['id']}] {task['status']:12} - {task['desc']}")
    result.append("\nYou must complete all tasks before finishing.")
    return "\n".join(result)

ACTIVE_SESSIONS: Dict[str, dict] = {}
SESSION_LOCK = threading.Lock()
MAX_SESSIONS = 3
SESSION_TIMEOUT = 300
SESSION_IDLE_TIMEOUT = 30
OUTPUT_BUFFER_SIZE = 4096

TOOLS_SCHEMA = [
    {
        "type": "function",
        "function": {
            "name": "search_context",
            "description": "Search for a pattern in files within the specified path",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {
                        "type": "string",
                        "description": "The search pattern (regex supported)"
                    },
                    "path": {
                        "type": "string",
                        "description": "The directory to search in",
                        "default": "."
                    }
                },
                "required": ["pattern"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "mkdir",
            "description": "Create a directory. Supports creating parents and handling existing paths.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "The directory path to create"
                    },
                    "parents": {
                        "type": "boolean",
                        "description": "Create parent directories as needed (like -p)",
                        "default": True
                    },
                    "exist_ok": {
                        "type": "boolean",
                        "description": "Do not error if directory already exists",
                        "default": False
                    }
                },
                "required": ["path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read file content, optionally reading only specified line ranges",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "The file path to read"
                    },
                    "ranges": {
                        "type": "array",
                        "description": "Optional list of line ranges to read, e.g., [[1, 10], [20, 30]]. Line numbers start from 1. If not provided, reads entire file.",
                        "items": {
                            "type": "array",
                            "items": {"type": "integer"},
                            "minItems": 2,
                            "maxItems": 2
                        }
                    }
                },
                "required": ["path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "create_file",
            "description": "Create a new file. Fails if file already exists - use edit_file to modify existing files.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "The file path to create"
                    },
                    "content": {
                        "type": "string",
                        "description": "The content to write to the file"
                    }
                },
                "required": ["path", "content"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "edit_file",
            "description": "Edit a file using robust anchor-based hunks to replace/insert/delete content with minimal token footprint.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Target file path"
                    },
                    "hunks": {
                        "type": "array",
                        "description": "List of operations. Each hunk locates an anchor (exact or regex) and applies an op: replace | insert_before | insert_after | delete.",
                        "items": {
                            "type": "object",
                            "properties": {
                                "op": {"type": "string", "enum": ["replace", "insert_before", "insert_after", "delete"]},
                                "anchor": {
                                    "type": "object",
                                    "properties": {
                                        "type": {"type": "string", "enum": ["exact", "regex"]},
                                        "pattern": {"type": "string"},
                                        "occurrence": {"type": "string", "enum": ["first", "last"], "default": "first"},
                                        "nth": {"type": "integer", "minimum": 1, "description": "If set, match the n-th occurrence (1-based)"}
                                    },
                                    "required": ["type", "pattern"]
                                },
                                "content": {"type": "string", "description": "New text for replace/insert ops"},
                                "must_unique": {"type": "boolean", "default": True}
                            },
                            "required": ["op", "anchor"]
                        }
                    },
                    "precondition": {
                        "type": "object",
                        "properties": {
                            "file_sha256": {"type": "string", "description": "Optional whole-file sha256 to ensure we edit the expected version"}
                        }
                    },
                    "dry_run": {"type": "boolean", "default": False}
                },
                "required": ["path", "hunks"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "list_directory",
            "description": "List all files and directories in the specified path with detailed information (similar to ls -la)",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "The directory path to list",
                        "default": "."
                    },
                    "show_hidden": {
                        "type": "boolean",
                        "description": "Whether to show hidden files (starting with .)",
                        "default": True
                    }
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "delete_file",
            "description": "Delete a file or symlink. Fails if the path is a directory.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "The file path to delete"
                    }
                },
                "required": ["path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "delete_path",
            "description": "Delete a file or directory. For directories, set recursive=true to remove non-empty trees.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "The path to delete (file or directory)"
                    },
                    "recursive": {
                        "type": "boolean",
                        "description": "Recursively delete non-empty directories",
                        "default": False
                    }
                },
                "required": ["path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "plan",
            "description": "Manage task execution plan. MUST be called in the FIRST round: either 'create' for multi-step tasks or 'skip' for simple tasks.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["create", "update", "check", "skip"],
                        "description": "Action to perform: 'create' to initialize plan with tasks, 'update' to change task status, 'check' to view current plan, 'skip' to explicitly mark this as a simple task that doesn't need planning"
                    },
                    "tasks": {
                        "type": "array",
                        "description": "List of task descriptions (only for 'create' action)",
                        "items": {"type": "string"}
                    },
                    "task_id": {
                        "type": "integer",
                        "description": "Task ID to update (only for 'update' action)"
                    },
                    "status": {
                        "type": "string",
                        "enum": ["pending", "in_progress", "completed"],
                        "description": "New status for the task (only for 'update' action)"
                    },
                    "reason": {
                        "type": "string",
                        "description": "Brief reason for skipping plan (only for 'skip' action)"
                    }
                },
                "required": ["action"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "run_command",
            "description": "Execute a shell command and return its output",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "The shell command to execute"
                    },
                    "timeout": {
                        "type": "integer",
                        "description": "Maximum execution time in seconds",
                        "default": 30
                    }
                },
                "required": ["command"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "start_session",
            "description": "Start an interactive shell session for persistent command execution (e.g., SSH, Python REPL). Returns a session ID for subsequent operations.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "Initial command to execute after starting the session (e.g., 'ssh user@host', 'python3', 'docker exec -it container bash')"
                    },
                    "shell": {
                        "type": "string",
                        "description": "Shell to use for the session",
                        "default": "/bin/bash"
                    }
                },
                "required": ["command"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "send_input",
            "description": "Send input/command to an active session",
            "parameters": {
                "type": "object",
                "properties": {
                    "session_id": {
                        "type": "string",
                        "description": "The session ID from start_session"
                    },
                    "input_text": {
                        "type": "string",
                        "description": "Text to send to the session stdin"
                    }
                },
                "required": ["session_id", "input_text"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "read_output",
            "description": "Read output from an active session",
            "parameters": {
                "type": "object",
                "properties": {
                    "session_id": {
                        "type": "string",
                        "description": "The session ID from start_session"
                    },
                    "timeout": {
                        "type": "integer",
                        "description": "Maximum time to wait for output in seconds",
                        "default": 2
                    }
                },
                "required": ["session_id"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "close_session",
            "description": "Close an active session and clean up resources",
            "parameters": {
                "type": "object",
                "properties": {
                    "session_id": {
                        "type": "string",
                        "description": "The session ID to close"
                    }
                },
                "required": ["session_id"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "list_sessions",
            "description": "List all active sessions with their status",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "fetch_url",
            "description": "Fetch content from HTTP/HTTPS URL and convert HTML to Markdown format. Automatically filters out scripts and styles. Security: blocks private IP addresses.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "The HTTP or HTTPS URL to fetch"
                    },
                    "timeout": {
                        "type": "integer",
                        "description": "Request timeout in seconds",
                        "default": 10
                    }
                },
                "required": ["url"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "Search the web using DuckDuckGo and return a list of results with titles, URLs, and snippets",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "The search query string"
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "Maximum number of results to return",
                        "default": 5
                    }
                },
                "required": ["query"]
            }
        }
    }
]

def search_context(pattern: str, path: str = ".") -> Tuple[bool, str]:
    resolved_path = resolve_path(path)
    valid, err_msg = validate_path(resolved_path)
    if not valid:
        return False, err_msg
    
    try:
        result = subprocess.run(
            ["rg", "-n", "--", pattern, resolved_path],
            capture_output=True,
            text=True,
            timeout=10
        )
        if result.returncode == 0:
            return True, result.stdout
        elif result.returncode == 1:
            return True, "No matches found"
        else:
            return False, f"Search error: {result.stderr}"
    except FileNotFoundError:
        return _fallback_search(pattern, resolved_path)
    except Exception as e:
        return False, f"Search failed: {str(e)}"

def _fallback_search(pattern: str, path: str) -> Tuple[bool, str]:
    try:
        regex = re.compile(pattern)
        results = []
        for root, _, files in os.walk(path):
            for file in files:
                filepath = os.path.join(root, file)
                try:
                    with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
                        for i, line in enumerate(f, 1):
                            if regex.search(line):
                                results.append(f"{filepath}:{i}:{line.rstrip()}")
                except Exception:
                    continue
        return True, "\n".join(results) if results else "No matches found"
    except Exception as e:
        return False, f"Fallback search failed: {str(e)}"

def delete_path(path: str, recursive: bool = False) -> Tuple[bool, str]:
    """Delete a file or a directory. For directories, allow recursive removal when requested."""
    resolved_path = resolve_path(path)
    valid, err_msg = validate_path(resolved_path)
    if not valid:
        return False, err_msg
    try:
        if not os.path.exists(resolved_path) and not os.path.islink(resolved_path):
            return False, f"Path not found: {resolved_path}"
        if os.path.realpath(resolved_path) == os.path.realpath(WORK_DIR):
            return False, f"Refusing to delete the work directory root: {resolved_path}"
        if os.path.isfile(resolved_path) or os.path.islink(resolved_path):
            os.unlink(resolved_path)
            return True, f"Deleted file: {resolved_path}"
        if os.path.isdir(resolved_path):
            if recursive:
                shutil.rmtree(resolved_path)
                return True, f"Deleted directory recursively: {resolved_path}"
            else:
                os.rmdir(resolved_path)
                return True, f"Deleted empty directory: {resolved_path}"
        return False, f"Unsupported path type: {resolved_path}"
    except PermissionError as e:
        return False, f"Permission denied: {resolved_path}: {str(e)}"
    except OSError as e:
        return False, f"Delete failed: {resolved_path}: {str(e)}"
    except Exception as e:
        return False, f"Delete failed: {str(e)}"

def read_file(path: str, ranges: list = None) -> Tuple[bool, str]:
    resolved_path = resolve_path(path)
    valid, err_msg = validate_path(resolved_path)
    if not valid:
        return False, err_msg
    
    try:
        with open(resolved_path, 'r', encoding='utf-8') as f:
            if ranges is None:
                content = f.read()
            else:
                lines = f.readlines()
                selected_lines = []
                for start, end in ranges:
                    if start < 1 or end > len(lines) or start > end:
                        return False, f"Invalid range: ({start}, {end}), file has {len(lines)} lines"
                    selected_lines.extend(lines[start-1:end])
                content = ''.join(selected_lines)
        return True, content
    except FileNotFoundError:
        return False, f"File not found: {resolved_path}"
    except Exception as e:
        return False, f"Read failed: {str(e)}"

def delete_file(path: str) -> Tuple[bool, str]:
    """Delete a single file or symlink. Directories are not allowed here."""
    resolved_path = resolve_path(path)
    valid, err_msg = validate_path(resolved_path)
    if not valid:
        return False, err_msg
    try:
        if not os.path.exists(resolved_path) and not os.path.islink(resolved_path):
            return False, f"File not found: {resolved_path}"
        if os.path.isdir(resolved_path) and not os.path.islink(resolved_path):
            return False, f"Is a directory: {resolved_path}. Use delete_path for directories."
        os.unlink(resolved_path)
        return True, f"Deleted file: {resolved_path}"
    except PermissionError as e:
        return False, f"Permission denied: {resolved_path}: {str(e)}"
    except Exception as e:
        return False, f"Delete failed: {str(e)}"

def mkdir(path: str, parents: bool = True, exist_ok: bool = False) -> Tuple[bool, str]:
    """Create a directory with optional parents and exist_ok semantics."""
    resolved_path = resolve_path(path)
    valid, err_msg = validate_path(resolved_path)
    if not valid:
        return False, err_msg
    try:
        # If path exists already
        if os.path.exists(resolved_path):
            if os.path.isdir(resolved_path):
                if exist_ok:
                    return True, f"Directory exists: {resolved_path}"
                return False, f"Directory already exists: {resolved_path}"
            return False, f"Path exists and is not a directory: {resolved_path}"

        # Create directory tree or single dir based on parents flag
        if parents:
            os.makedirs(resolved_path, exist_ok=exist_ok)
        else:
            # If parent doesn't exist and parents=False, this will raise
            if exist_ok and os.path.isdir(resolved_path):
                return True, f"Directory exists: {resolved_path}"
            os.mkdir(resolved_path)

        return True, f"Created directory: {resolved_path}"
    except PermissionError as e:
        return False, f"Permission denied: {resolved_path}: {str(e)}"
    except FileExistsError:
        if exist_ok:
            return True, f"Directory exists: {resolved_path}"
        return False, f"Directory already exists: {resolved_path}"
    except Exception as e:
        return False, f"Create directory failed: {str(e)}"

def create_file(path: str, content: str) -> Tuple[bool, str]:
    resolved_path = resolve_path(path)
    valid, err_msg = validate_path(resolved_path)
    if not valid:
        return False, err_msg
    
    try:
        if os.path.exists(resolved_path):
            return False, f"File already exists: {resolved_path}. Use edit_file to modify existing files."
        
        dir_path = os.path.dirname(resolved_path)
        if dir_path and not os.path.exists(dir_path):
            os.makedirs(dir_path)
        
        with tempfile.NamedTemporaryFile(
            mode='w',
            encoding='utf-8',
            dir=dir_path or '.',
            delete=False
        ) as tmp:
            tmp.write(content)
            tmp_path = tmp.name
        
        os.rename(tmp_path, resolved_path)
        return True, f"Successfully created {resolved_path}"
    except Exception as e:
        return False, f"Create failed: {str(e)}"

def _compute_sha256(text: str) -> str:
    # minimal helper to hash whole file
    h = hashlib.sha256()
    h.update(text.encode('utf-8'))
    return h.hexdigest()


def _index_to_line(idx: int, line_starts: list) -> int:
    # binary search to map byte offset to 1-based line number
    lo, hi = 0, len(line_starts) - 1
    while lo <= hi:
        mid = (lo + hi) // 2
        if line_starts[mid] <= idx:
            lo = mid + 1
        else:
            hi = mid - 1
    return hi + 1


def _build_line_starts(text: str) -> list:
    # record start offset for each line (1-based lines)
    starts = [0]
    for i, ch in enumerate(text):
        if ch == '\n':
            starts.append(i + 1)
    return starts


def _find_matches(text: str, anchor: dict) -> list:
    # return list of (start, end) spans for anchor
    a_type = anchor.get("type")
    pattern = anchor.get("pattern", "")
    if a_type == "regex":
        try:
            rgx = re.compile(pattern, re.MULTILINE)
        except re.error as e:
            raise ValueError(f"Invalid regex: {e}")
        return [(m.start(), m.end()) for m in rgx.finditer(text)]
    elif a_type == "exact":
        spans = []
        start = 0
        while True:
            i = text.find(pattern, start)
            if i == -1:
                break
            spans.append((i, i + len(pattern)))
            start = i + (1 if len(pattern) == 0 else max(1, len(pattern)))
        return spans
    else:
        raise ValueError("Unsupported anchor.type; use 'exact' or 'regex'")


def edit_file(path: str, hunks: list, precondition: dict = None, dry_run: bool = False) -> Tuple[bool, str]:
    resolved_path = resolve_path(path)
    valid, err_msg = validate_path(resolved_path)
    if not valid:
        return False, err_msg

    try:
        with open(resolved_path, 'r', encoding='utf-8') as f:
            original_text = f.read()

        if precondition and precondition.get("file_sha256"):
            cur = _compute_sha256(original_text)
            if cur != precondition.get("file_sha256"):
                return False, f"Precondition failed: file sha256 mismatch"

        text = original_text
        applied = 0
        matches_meta = []

        for h in hunks:
            op = h.get("op")
            anchor = h.get("anchor") or {}
            must_unique = h.get("must_unique", True)
            content = h.get("content", "")

            spans = _find_matches(text, anchor)
            if not spans:
                return False, f"Anchor not found for op={op}"

            # choose occurrence
            nth = h.get("nth")
            occ = anchor.get("occurrence", "first")
            if nth is not None:
                idx = nth - 1
            elif occ == "last":
                idx = len(spans) - 1
            else:
                idx = 0

            if must_unique and len(spans) != 1:
                return False, f"Anchor ambiguous: {len(spans)} matches"

            if idx < 0 or idx >= len(spans):
                return False, f"Requested occurrence not found"

            s, e = spans[idx]
            # record line mapping for this hunk
            line_starts = _build_line_starts(text)
            start_line = _index_to_line(s, line_starts)
            end_line = _index_to_line(e - 1 if e > s else s, line_starts)

            if op == "replace":
                text = text[:s] + content + text[e:]
            elif op == "insert_before":
                text = text[:s] + content + text[s:]
            elif op == "insert_after":
                text = text[:e] + content + text[e:]
            elif op == "delete":
                text = text[:s] + text[e:]
            else:
                return False, f"Unsupported op: {op}"

            applied += 1
            snippet = original_text[s:e] if (s < len(original_text) and e <= len(original_text)) else ""
            matches_meta.append({
                "op": op,
                "start_line": start_line,
                "end_line": end_line,
                "anchor_snippet": snippet[:120]
            })

        if dry_run:
            # compute diff without writing
            diff = difflib.unified_diff(
                original_text.splitlines(keepends=True),
                text.splitlines(keepends=True),
                fromfile=f"a/{os.path.basename(resolved_path)}",
                tofile=f"b/{os.path.basename(resolved_path)}",
                lineterm='\n'
            )
            diff_text = ''.join(diff)
            result = {
                "success": True,
                "path": resolved_path,
                "applied": False,
                "hunks_applied": applied,
                "matches": matches_meta,
                "diff": diff_text
            }
            return True, json.dumps(result)

        dir_path = os.path.dirname(resolved_path)
        with tempfile.NamedTemporaryFile(
            mode='w',
            encoding='utf-8',
            dir=dir_path or '.',
            delete=False
        ) as tmp:
            tmp.write(text)
            tmp_path = tmp.name

        os.rename(tmp_path, resolved_path)

        diff = difflib.unified_diff(
            original_text.splitlines(keepends=True),
            text.splitlines(keepends=True),
            fromfile=f"a/{os.path.basename(resolved_path)}",
            tofile=f"b/{os.path.basename(resolved_path)}",
            lineterm='\n'
        )
        diff_text = ''.join(diff)

        result = {
            "success": True,
            "path": resolved_path,
            "applied": True,
            "hunks_applied": applied,
            "matches": matches_meta,
            "diff": diff_text
        }

        return True, json.dumps(result)
    except FileNotFoundError:
        return False, f"File not found: {resolved_path}"
    except Exception as e:
        return False, f"Edit failed: {str(e)}"

def list_directory(path: str = ".", show_hidden: bool = True) -> Tuple[bool, str]:
    resolved_path = resolve_path(path)
    valid, err_msg = validate_path(resolved_path)
    if not valid:
        return False, err_msg
    
    try:
        cmd = ["ls", "-la"] if show_hidden else ["ls", "-l"]
        result = subprocess.run(
            cmd + [resolved_path],
            capture_output=True,
            text=True,
            timeout=5
        )
        if result.returncode == 0:
            return True, result.stdout.strip()
        else:
            return False, f"ls command error: {result.stderr}"
    except FileNotFoundError:
        return _fallback_list_directory(resolved_path, show_hidden)
    except Exception as e:
        return _fallback_list_directory(resolved_path, show_hidden)

def _fallback_list_directory(path: str, show_hidden: bool = True) -> Tuple[bool, str]:
    try:
        if not os.path.exists(path):
            return False, f"Path not found: {path}"
        
        if not os.path.isdir(path):
            return False, f"Not a directory: {path}"
        
        entries = []
        items = os.listdir(path)
        
        if not show_hidden:
            items = [item for item in items if not item.startswith('.')]
        
        items.sort()
        
        for item in items:
            full_path = os.path.join(path, item)
            try:
                stat_info = os.lstat(full_path)
                
                mode = stat_info.st_mode
                perms = stat.filemode(mode)
                nlink = stat_info.st_nlink
                size = stat_info.st_size
                mtime = datetime.fromtimestamp(stat_info.st_mtime).strftime('%b %d %H:%M')
                
                entry = f"{perms} {nlink:3} {size:8} {mtime} {item}"
                entries.append(entry)
            except Exception as e:
                entries.append(f"????????? ??? ???????? ??? ??? {item} [Error: {str(e)}]")
        
        if not entries:
            return True, "Empty directory"
        
        return True, "\n".join(entries)
    except PermissionError:
        return False, f"Permission denied: {path}"
    except Exception as e:
        return False, f"List failed: {str(e)}"

def plan(action: str, tasks: list = None, task_id: int = None, status: str = None, reason: str = None) -> Tuple[bool, str]:
    global CURRENT_PLAN, PLAN_DECISION_MADE, SIGNIFICANT_ACTIONS_COUNT, LAST_PLAN_UPDATE_AT
    
    def format_task(task, is_first=False):
        color_map = {
            "pending": "\033[31m",
            "in_progress": "\033[33m",
            "completed": "\033[32m"
        }
        color = color_map.get(task["status"], "")
        reset = "\033[0m" if color else ""
        prefix = "↳ " if is_first else "  "
        return f"{prefix}- {color}{task['desc']}{reset}"
    
    if action == "create":
        if not tasks or not isinstance(tasks, list):
            return False, "Create action requires 'tasks' parameter as a list"
        CURRENT_PLAN = {
            "tasks": [{"id": i+1, "desc": t, "status": "pending"} for i, t in enumerate(tasks)],
            "created_at": datetime.now().isoformat()
        }
        PLAN_DECISION_MADE = True
        SIGNIFICANT_ACTIONS_COUNT = 0
        LAST_PLAN_UPDATE_AT = 0
        if CURRENT_SESSION_ID:
            save_plan_state(CURRENT_SESSION_ID, CURRENT_PLAN)
        result = []
        for i, task in enumerate(CURRENT_PLAN["tasks"]):
            result.append(format_task(task, is_first=(i==0)))
        return True, "\n".join(result)
    
    elif action == "skip":
        PLAN_DECISION_MADE = True
        skip_msg = f"Plan skipped: {reason}" if reason else "Plan skipped (simple task)"
        return True, skip_msg
    
    elif action == "update":
        if CURRENT_PLAN is None:
            return False, "No plan exists. Create a plan first."
        if task_id is None or status is None:
            return False, "Update action requires 'task_id' and 'status' parameters"
        
        task = next((t for t in CURRENT_PLAN["tasks"] if t["id"] == task_id), None)
        if not task:
            return False, f"Task ID {task_id} not found"
        
        task["status"] = status
        SIGNIFICANT_ACTIONS_COUNT = 0
        LAST_PLAN_UPDATE_AT = SIGNIFICANT_ACTIONS_COUNT
        if CURRENT_SESSION_ID:
            save_plan_state(CURRENT_SESSION_ID, CURRENT_PLAN)
        result = []
        for i, t in enumerate(CURRENT_PLAN["tasks"]):
            result.append(format_task(t, is_first=(i==0)))
        return True, "\n".join(result)
    
    elif action == "check":
        if CURRENT_PLAN is None:
            return False, "No plan exists. Create a plan first."
        
        result = []
        for i, task in enumerate(CURRENT_PLAN["tasks"]):
            result.append(format_task(task, is_first=(i==0)))
        return True, "\n".join(result)
    
    else:
        return False, f"Unknown action: {action}"

def run_command(command: str, timeout: int = 30) -> Tuple[bool, str]:
    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout
        )
        
        output_parts = []
        if result.stdout:
            output_parts.append(result.stdout)
        if result.stderr:
            output_parts.append(f"[stderr]\n{result.stderr}")
        
        output = "\n".join(output_parts) if output_parts else "[no output]"
        
        if result.returncode != 0:
            return False, f"Exit code {result.returncode}\n{output}"
        
        return True, output
    except subprocess.TimeoutExpired:
        return False, f"Command timed out after {timeout} seconds"
    except Exception as e:
        return False, f"Execution failed: {str(e)}"

def _read_stream(stream, output_queue, stream_name):
    try:
        for line in iter(stream.readline, ''):
            if line:
                output_queue.put((stream_name, line))
    except Exception:
        pass
    finally:
        stream.close()

def _cleanup_expired_sessions():
    while True:
        time.sleep(10)
        with SESSION_LOCK:
            now = time.time()
            expired = []
            for sid, session in ACTIVE_SESSIONS.items():
                idle_time = now - session["last_accessed"]
                total_time = now - session["created_at"]
                if idle_time > SESSION_IDLE_TIMEOUT or total_time > SESSION_TIMEOUT:
                    expired.append(sid)
            
            for sid in expired:
                session = ACTIVE_SESSIONS[sid]
                try:
                    session["process"].terminate()
                    session["process"].wait(timeout=2)
                except Exception:
                    try:
                        session["process"].kill()
                    except Exception:
                        pass
                del ACTIVE_SESSIONS[sid]

_cleanup_thread = threading.Thread(target=_cleanup_expired_sessions, daemon=True)
_cleanup_thread.start()

def start_session(command: str, shell: str = "/bin/bash") -> Tuple[bool, str]:
    with SESSION_LOCK:
        if len(ACTIVE_SESSIONS) >= MAX_SESSIONS:
            return False, f"Maximum {MAX_SESSIONS} sessions reached. Close existing sessions first."
        
        try:
            process = subprocess.Popen(
                [shell],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1
            )
            
            session_id = str(uuid.uuid4())[:8]
            output_queue = queue.Queue()
            
            stdout_thread = threading.Thread(
                target=_read_stream,
                args=(process.stdout, output_queue, "stdout"),
                daemon=True
            )
            stderr_thread = threading.Thread(
                target=_read_stream,
                args=(process.stderr, output_queue, "stderr"),
                daemon=True
            )
            
            stdout_thread.start()
            stderr_thread.start()
            
            ACTIVE_SESSIONS[session_id] = {
                "process": process,
                "output_queue": output_queue,
                "created_at": time.time(),
                "last_accessed": time.time(),
                "command": command
            }
            
            if command:
                process.stdin.write(command + "\n")
                process.stdin.flush()
            
            return True, f"Session {session_id} started"
        
        except Exception as e:
            return False, f"Failed to start session: {str(e)}"

def send_input(session_id: str, input_text: str) -> Tuple[bool, str]:
    with SESSION_LOCK:
        session = ACTIVE_SESSIONS.get(session_id)
        if not session:
            return False, f"Session {session_id} not found"
        
        try:
            if session["process"].poll() is not None:
                return False, f"Session {session_id} has terminated"
            
            session["process"].stdin.write(input_text + "\n")
            session["process"].stdin.flush()
            session["last_accessed"] = time.time()
            
            return True, f"Input sent to session {session_id}"
        
        except Exception as e:
            return False, f"Failed to send input: {str(e)}"

def read_output(session_id: str, timeout: int = 2) -> Tuple[bool, str]:
    with SESSION_LOCK:
        session = ACTIVE_SESSIONS.get(session_id)
        if not session:
            return False, f"Session {session_id} not found"
        
        session["last_accessed"] = time.time()
    
    try:
        output_lines = []
        total_size = 0
        deadline = time.time() + timeout
        
        while time.time() < deadline:
            try:
                remaining = deadline - time.time()
                if remaining <= 0:
                    break
                
                stream_name, line = session["output_queue"].get(timeout=min(remaining, 0.1))
                output_lines.append(line.rstrip())
                total_size += len(line)
                
                if total_size > OUTPUT_BUFFER_SIZE:
                    output_lines.append(f"[output truncated at {OUTPUT_BUFFER_SIZE} bytes]")
                    break
            
            except queue.Empty:
                if output_lines:
                    break
                continue
        
        if not output_lines:
            return True, "[no output within timeout]"
        
        return True, "\n".join(output_lines)
    
    except Exception as e:
        return False, f"Failed to read output: {str(e)}"

def close_session(session_id: str) -> Tuple[bool, str]:
    with SESSION_LOCK:
        session = ACTIVE_SESSIONS.get(session_id)
        if not session:
            return False, f"Session {session_id} not found"
        
        try:
            process = session["process"]
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
            
            del ACTIVE_SESSIONS[session_id]
            return True, f"Session {session_id} closed"
        
        except Exception as e:
            return False, f"Failed to close session: {str(e)}"

def list_sessions() -> Tuple[bool, str]:
    with SESSION_LOCK:
        if not ACTIVE_SESSIONS:
            return True, "No active sessions"
        
        now = time.time()
        lines = []
        for sid, session in ACTIVE_SESSIONS.items():
            age = int(now - session["created_at"])
            idle = int(now - session["last_accessed"])
            alive = "alive" if session["process"].poll() is None else "dead"
            lines.append(f"{sid}: {alive}, age={age}s, idle={idle}s")
        
        return True, "\n".join(lines)

def _is_private_ip(hostname: str) -> bool:
    try:
        ip = socket.gethostbyname(hostname)
        parts = ip.split('.')
        if len(parts) != 4:
            return True
        
        first = int(parts[0])
        second = int(parts[1])
        
        if first == 127:
            return True
        if first == 10:
            return True
        if first == 172 and 16 <= second <= 31:
            return True
        if first == 192 and second == 168:
            return True
        if first == 169 and second == 254:
            return True
        
        return False
    except Exception:
        return True

def fetch_url(url: str, timeout: int = 10) -> Tuple[bool, str]:
    MAX_SIZE = 5 * 1024 * 1024
    
    try:
        parsed = urlparse(url)
        
        if parsed.scheme not in ['http', 'https']:
            return False, f"Only HTTP/HTTPS protocols are supported, got: {parsed.scheme}"
        
        if not parsed.netloc:
            return False, "Invalid URL: missing hostname"
        
        hostname = parsed.netloc.split(':')[0]
        if _is_private_ip(hostname):
            return False, f"Access to private IP addresses is forbidden: {hostname}"
        
        headers = {
            'User-Agent': 'Mozilla/5.0 (compatible; TriCode/1.0)'
        }
        
        response = requests.get(url, headers=headers, timeout=timeout, stream=True)
        response.raise_for_status()
        
        content_length = response.headers.get('Content-Length')
        if content_length and int(content_length) > MAX_SIZE:
            return False, f"Content too large: {content_length} bytes (max: {MAX_SIZE})"
        
        chunks = []
        total_size = 0
        for chunk in response.iter_content(chunk_size=8192):
            total_size += len(chunk)
            if total_size > MAX_SIZE:
                return False, f"Content exceeded size limit during download (max: {MAX_SIZE} bytes)"
            chunks.append(chunk)
        
        html_content = b''.join(chunks).decode(response.encoding or 'utf-8', errors='ignore')
        
        soup = BeautifulSoup(html_content, 'html.parser')
        for script in soup(['script', 'style', 'noscript']):
            script.decompose()
        
        h2t = html2text.HTML2Text()
        h2t.ignore_links = False
        h2t.ignore_images = False
        h2t.ignore_emphasis = False
        h2t.body_width = 0
        
        markdown = h2t.handle(str(soup))
        markdown = markdown.strip()
        
        if not markdown:
            return False, "Website returned no readable content (possibly JavaScript-rendered SPA or empty page)"
        
        return True, markdown
        
    except requests.exceptions.Timeout:
        return False, f"Request timed out after {timeout} seconds"
    except requests.exceptions.ConnectionError as e:
        return False, f"Connection error: {str(e)}"
    except requests.exceptions.HTTPError as e:
        return False, f"HTTP error: {e.response.status_code} {e.response.reason}"
    except Exception as e:
        return False, f"Failed to fetch URL: {str(e)}"

def web_search(query: str, max_results: int = 5) -> Tuple[bool, str]:
    global LAST_WEB_SEARCH_TIME
    
    if max_results < 1:
        return False, "max_results must be at least 1"
    if max_results > 20:
        max_results = 20
    
    elapsed = time.time() - LAST_WEB_SEARCH_TIME
    if elapsed < WEB_SEARCH_RATE_LIMIT:
        wait_time = WEB_SEARCH_RATE_LIMIT - elapsed
        time.sleep(wait_time)
    
    max_retries = 3
    base_delay = 2
    
    for attempt in range(max_retries):
        try:
            with DDGS() as ddgs:
                results = list(ddgs.text(query, max_results=max_results))
            
            LAST_WEB_SEARCH_TIME = time.time()
            
            if not results:
                return True, "No results found"
            
            formatted = []
            for i, result in enumerate(results, 1):
                title = result.get('title', 'No title')
                url = result.get('href', 'No URL')
                snippet = result.get('body', 'No description')
                formatted.append(f"[{i}] {title}\n    URL: {url}\n    {snippet}\n")
            
            return True, "\n".join(formatted)
            
        except Exception as e:
            error_str = str(e).lower()
            
            if 'ratelimit' in error_str or '429' in error_str or '202' in error_str:
                if attempt < max_retries - 1:
                    delay = base_delay * (2 ** attempt)
                    time.sleep(delay)
                    continue
                else:
                    return False, (
                        f"⚠️ Rate limit exceeded after {max_retries} retries. "
                        "DuckDuckGo is temporarily blocking requests. "
                        "Please wait a few minutes before searching again."
                    )
            else:
                return False, f"Search failed: {str(e)}"
    
    return False, "Search failed: Maximum retries exceeded"

def get_plan_reminder() -> str:
    if CURRENT_PLAN is None:
        if not PLAN_DECISION_MADE:
            return "WARNING: No plan decision made. Use plan(action='create', tasks=[...]) or plan(action='skip', reason='...')."
        return None
    
    if SIGNIFICANT_ACTIONS_COUNT >= 2:
        return (
            f"⚠️ WARNING: You performed {SIGNIFICANT_ACTIONS_COUNT} significant operations (edit/create/run) "
            "but haven't updated the plan.\n"
            "Call plan(action='update', task_id=X, status='completed') to mark finished tasks, "
            "or plan(action='update', task_id=Y, status='in_progress') to start the next one."
        )
    
    incomplete = [t for t in CURRENT_PLAN["tasks"] if t["status"] != "completed"]
    if not incomplete:
        return None
    
    in_progress = [t for t in incomplete if t["status"] == "in_progress"]
    if in_progress and SIGNIFICANT_ACTIONS_COUNT >= 1:
        task = in_progress[0]
        return (
            f"⚠️ Task [{task['id']}] '{task['desc']}' is in_progress. "
            f"If you finished it, call plan(action='update', task_id={task['id']}, status='completed')."
        )
    
    return None

def format_tool_call(name: str, arguments: dict) -> str:
    if name == "search_context":
        pattern = arguments.get("pattern", "")
        path = arguments.get("path", ".")
        return f'SEARCH(pattern="{pattern}", path="{path}")'
    elif name == "read_file":
        path = arguments.get("path", "")
        ranges = arguments.get("ranges")
        if ranges:
            ranges_str = ", ".join([f"{start}-{end}" for start, end in ranges])
            return f'READ("{path}", lines=[{ranges_str}])'
        return f'READ("{path}")'
    elif name == "create_file":
        path = arguments.get("path", "")
        return f'CREATE("{path}")'
    elif name == "edit_file":
        path = arguments.get("path", "")
        hunks = arguments.get("hunks", [])
        if hunks:
            return f'EDIT("{path}", hunks={len(hunks)})'
        return f'EDIT("{path}")'
    elif name == "list_directory":
        path = arguments.get("path", ".")
        return f'LIST("{path}")'
    elif name == "delete_file":
        path = arguments.get("path", "")
        return f'DELETE FILE("{path}")'
    elif name == "delete_path":
        path = arguments.get("path", "")
        recursive = arguments.get("recursive", False)
        suffix = ", recursive=True" if recursive else ""
        return f'DELETE PATH("{path}"{suffix})'
    elif name == "mkdir":
        path = arguments.get("path", "")
        parents = arguments.get("parents", True)
        exist_ok = arguments.get("exist_ok", False)
        flags = []
        if parents:
            flags.append("parents=True")
        if exist_ok:
            flags.append("exist_ok=True")
        flag_str = ", " + ", ".join(flags) if flags else ""
        return f'MKDIR("{path}"{flag_str})'
    elif name == "plan":
        action = arguments.get("action", "").upper()
        return f'PLAN {action}'
    elif name == "run_command":
        command = arguments.get("command", "")
        return f'RUN({command})'
    elif name == "start_session":
        command = arguments.get("command", "")
        return f'START SESSION({command})'
    elif name == "send_input":
        sid = arguments.get("session_id", "")
        text = arguments.get("input_text", "")
        return f'SEND TO SESSION({sid}, "{text}")'
    elif name == "read_output":
        sid = arguments.get("session_id", "")
        return f'READ SESSTION OUTPUT({sid})'
    elif name == "close_session":
        sid = arguments.get("session_id", "")
        return f'CLOSE SESSION({sid})'
    elif name == "list_sessions":
        return 'LIST SESSIONS()'
    elif name == "fetch_url":
        url = arguments.get("url", "")
        return f'FETCH URL("{url}")'
    elif name == "web_search":
        query = arguments.get("query", "")
        max_results = arguments.get("max_results", 5)
        return f'WEB SEARCH("{query}", max={max_results})'
    else:
        return f'{name.upper()}({arguments})'

def execute_tool(name: str, arguments: dict) -> Tuple[bool, str]:
    global PLAN_DECISION_MADE, SIGNIFICANT_ACTIONS_COUNT
    
    if name != "plan" and not PLAN_DECISION_MADE:
        return False, (
            "⚠️ BLOCKED: You must make a plan decision first.\n"
            "Call plan(action='create', tasks=[...]) for multi-step tasks, "
            "or plan(action='skip', reason='...') for simple tasks."
        )
    
    significant_action_tools = ["read_file", "edit_file", "create_file", "delete_file", "delete_path", "mkdir", "run_command"]
    if name in significant_action_tools and CURRENT_PLAN is not None:
        SIGNIFICANT_ACTIONS_COUNT += 1
    
    if name == "search_context":
        return search_context(
            arguments.get("pattern"),
            arguments.get("path", ".")
        )
    elif name == "read_file":
        return read_file(
            arguments.get("path"),
            arguments.get("ranges")
        )
    elif name == "create_file":
        return create_file(
            arguments.get("path"),
            arguments.get("content")
        )
    elif name == "edit_file":
        return edit_file(
            arguments.get("path"),
            arguments.get("hunks"),
            arguments.get("precondition"),
            arguments.get("dry_run", False)
        )
    elif name == "list_directory":
        return list_directory(
            arguments.get("path", "."),
            arguments.get("show_hidden", True)
        )
    elif name == "delete_file":
        return delete_file(
            arguments.get("path")
        )
    elif name == "delete_path":
        return delete_path(
            arguments.get("path"),
            arguments.get("recursive", False)
        )
    elif name == "mkdir":
        return mkdir(
            arguments.get("path"),
            arguments.get("parents", True),
            arguments.get("exist_ok", False)
        )
    elif name == "plan":
        return plan(
            arguments.get("action"),
            arguments.get("tasks"),
            arguments.get("task_id"),
            arguments.get("status"),
            arguments.get("reason")
        )
    elif name == "run_command":
        return run_command(
            arguments.get("command"),
            arguments.get("timeout", 30)
        )
    elif name == "start_session":
        return start_session(
            arguments.get("command"),
            arguments.get("shell", "/bin/bash")
        )
    elif name == "send_input":
        return send_input(
            arguments.get("session_id"),
            arguments.get("input_text")
        )
    elif name == "read_output":
        return read_output(
            arguments.get("session_id"),
            arguments.get("timeout", 2)
        )
    elif name == "close_session":
        return close_session(
            arguments.get("session_id")
        )
    elif name == "list_sessions":
        return list_sessions()
    elif name == "fetch_url":
        return fetch_url(
            arguments.get("url"),
            arguments.get("timeout", 10)
        )
    elif name == "web_search":
        return web_search(
            arguments.get("query"),
            arguments.get("max_results", 5)
        )
    else:
        return False, f"Unknown tool: {name}"
