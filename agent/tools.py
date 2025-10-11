import os
import re
import subprocess
import tempfile
from typing import Tuple
from datetime import datetime
import stat

CURRENT_PLAN = None

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
            "description": "Edit specific line ranges in an existing file. IMPORTANT: Edit ONLY the lines that need to change - minimize the edit range to avoid unnecessary rewrites.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "The file path to edit"
                    },
                    "replacements": {
                        "type": "array",
                        "description": "List of replacements. Each replacement specifies 'range' [start_line, end_line] to replace and 'content' as the new text. Line numbers start from 1. Use precise ranges - only edit lines that actually need to change.",
                        "items": {
                            "type": "object",
                            "properties": {
                                "range": {
                                    "type": "array",
                                    "items": {"type": "integer"},
                                    "minItems": 2,
                                    "maxItems": 2,
                                    "description": "Line range [start, end] to replace. Be precise - only include lines that must change."
                                },
                                "content": {
                                    "type": "string",
                                    "description": "New content to replace the range. Use empty string to delete lines."
                                }
                            },
                            "required": ["range", "content"]
                        }
                    }
                },
                "required": ["path", "replacements"]
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
            "name": "plan",
            "description": "Manage task execution plan. MUST be called with 'create' action at the start of any non-trivial task.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["create", "update", "check"],
                        "description": "Action to perform: 'create' to initialize plan with tasks, 'update' to change task status, 'check' to view current plan"
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
                    }
                },
                "required": ["action"]
            }
        }
    }
]

def search_context(pattern: str, path: str = ".") -> Tuple[bool, str]:
    try:
        result = subprocess.run(
            ["rg", "-n", "--", pattern, path],
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
        return _fallback_search(pattern, path)
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

def read_file(path: str, ranges: list = None) -> Tuple[bool, str]:
    try:
        with open(path, 'r', encoding='utf-8') as f:
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
        return False, f"File not found: {path}"
    except Exception as e:
        return False, f"Read failed: {str(e)}"

def create_file(path: str, content: str) -> Tuple[bool, str]:
    try:
        if os.path.exists(path):
            return False, f"File already exists: {path}. Use edit_file to modify existing files."
        
        dir_path = os.path.dirname(path)
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
        
        os.rename(tmp_path, path)
        return True, f"Successfully created {path}"
    except Exception as e:
        return False, f"Create failed: {str(e)}"

def edit_file(path: str, replacements: list) -> Tuple[bool, str]:
    try:
        with open(path, 'r', encoding='utf-8') as f:
            lines = f.readlines()
        
        sorted_replacements = sorted(replacements, key=lambda r: r["range"][0], reverse=True)
        
        for replacement in sorted_replacements:
            start, end = replacement["range"]
            new_content = replacement["content"]
            
            if start < 1 or end > len(lines) or start > end:
                return False, f"Invalid range: ({start}, {end}), file has {len(lines)} lines"
            
            if not new_content.endswith('\n') and end < len(lines):
                new_content += '\n'
            
            lines[start-1:end] = [new_content]
        
        dir_path = os.path.dirname(path)
        with tempfile.NamedTemporaryFile(
            mode='w',
            encoding='utf-8',
            dir=dir_path or '.',
            delete=False
        ) as tmp:
            tmp.writelines(lines)
            tmp_path = tmp.name
        
        os.rename(tmp_path, path)
        return True, f"Successfully edited {path}"
    except FileNotFoundError:
        return False, f"File not found: {path}"
    except Exception as e:
        return False, f"Edit failed: {str(e)}"

def list_directory(path: str = ".", show_hidden: bool = True) -> Tuple[bool, str]:
    try:
        cmd = ["ls", "-la"] if show_hidden else ["ls", "-l"]
        result = subprocess.run(
            cmd + [path],
            capture_output=True,
            text=True,
            timeout=5
        )
        if result.returncode == 0:
            return True, result.stdout.strip()
        else:
            return False, f"ls command error: {result.stderr}"
    except FileNotFoundError:
        return _fallback_list_directory(path, show_hidden)
    except Exception as e:
        return _fallback_list_directory(path, show_hidden)

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

def plan(action: str, tasks: list = None, task_id: int = None, status: str = None) -> Tuple[bool, str]:
    global CURRENT_PLAN
    
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
        result = []
        for i, task in enumerate(CURRENT_PLAN["tasks"]):
            result.append(format_task(task, is_first=(i==0)))
        return True, "\n".join(result)
    
    elif action == "update":
        if CURRENT_PLAN is None:
            return False, "No plan exists. Create a plan first."
        if task_id is None or status is None:
            return False, "Update action requires 'task_id' and 'status' parameters"
        
        task = next((t for t in CURRENT_PLAN["tasks"] if t["id"] == task_id), None)
        if not task:
            return False, f"Task ID {task_id} not found"
        
        task["status"] = status
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

def get_plan_reminder() -> str:
    if CURRENT_PLAN is None:
        return "WARNING: No execution plan created. Use plan(action='create', tasks=[...]) to create one."
    
    incomplete = [t for t in CURRENT_PLAN["tasks"] if t["status"] != "completed"]
    if not incomplete:
        return None
    
    result = [f"WARNING: {len(incomplete)} task(s) still incomplete:"]
    for task in incomplete:
        result.append(f"  [{task['id']}] {task['status']:12} - {task['desc']}")
    return "\n".join(result)

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
        replacements = arguments.get("replacements", [])
        if replacements:
            ranges_str = ", ".join([f"{r['range'][0]}-{r['range'][1]}" for r in replacements])
            return f'EDIT("{path}", lines=[{ranges_str}])'
        return f'EDIT("{path}")'
    elif name == "list_directory":
        path = arguments.get("path", ".")
        return f'LIST("{path}")'
    elif name == "plan":
        action = arguments.get("action", "").upper()
        return f'PLAN {action}'
    else:
        return f'{name.upper()}({arguments})'

def execute_tool(name: str, arguments: dict) -> Tuple[bool, str]:
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
            arguments.get("replacements")
        )
    elif name == "list_directory":
        return list_directory(
            arguments.get("path", "."),
            arguments.get("show_hidden", True)
        )
    elif name == "plan":
        return plan(
            arguments.get("action"),
            arguments.get("tasks"),
            arguments.get("task_id"),
            arguments.get("status")
        )
    else:
        return False, f"Unknown tool: {name}"
