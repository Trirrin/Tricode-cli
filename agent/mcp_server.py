#!/usr/bin/env python

import asyncio
import sys
from typing import Any
from mcp.server.models import InitializationOptions
from mcp.server import NotificationOptions, Server
from mcp.server.stdio import stdio_server
from mcp.types import (
    Tool,
    TextContent,
    ImageContent,
    EmbeddedResource,
)

from . import tools

server = Server("tricode-mcp-server")


TOOL_DEFINITIONS = [
    Tool(
        name="search_context",
        description="Search for files matching a pattern (glob or regex). Use ripgrep for fast code search across the codebase.",
        inputSchema={
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": "Search pattern (supports glob patterns like '*.py' or regex patterns)"
                },
                "path": {
                    "type": "string",
                    "description": "Starting directory path (default: current directory)",
                    "default": "."
                }
            },
            "required": ["pattern"]
        }
    ),
    Tool(
        name="search_symbol",
        description=(
            "Search for function, type, and class definitions by symbol name across "
            "supported languages. Matching is case-sensitive and requires the exact "
            "symbol name. Only definitional symbols are returned, not usages. "
            "Macros and some preprocessor constructs are not indexed. "
            "Results are returned as JSON describing matches and metadata."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "symbol": {
                    "type": "string",
                    "description": "Function or symbol name to search for"
                },
                "path": {
                    "type": "string",
                    "description": "Starting directory path (default: current directory)",
                    "default": "."
                },
                "max_results": {
                    "type": "integer",
                    "description": "Maximum number of symbol definitions to return "
                    "(after applying filters)",
                    "minimum": 1
                },
                "language": {
                    "type": "string",
                    "description": (
                        "Optional language filter (for example: python, rust, cpp, java, go). "
                        "If omitted, all indexed languages are considered."
                    )
                },
                "kind": {
                    "type": "string",
                    "description": (
                        "Optional symbol kind filter (for example: function, class, struct, "
                        "enum, trait, method, module, type, interface, annotation, record, "
                        "constructor, impl). If omitted, all kinds are considered."
                    )
                },
                "offset": {
                    "type": "integer",
                    "description": (
                        "Optional number of matching definitions to skip before returning "
                        "results (for simple pagination)."
                    ),
                    "minimum": 0
                }
            },
            "required": ["symbol"]
        }
    ),
    Tool(
        name="read_file",
        description="Read contents of a file with optional line range filtering and metadata.",
        inputSchema={
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Path to the file to read"
                },
                "start_line": {
                    "type": "integer",
                    "description": "Starting line number (1-indexed, inclusive)",
                    "minimum": 1
                },
                "end_line": {
                    "type": "integer",
                    "description": "Ending line number (1-indexed, inclusive)",
                    "minimum": 1
                },
                "max_bytes": {
                    "type": "integer",
                    "description": "Maximum bytes to read (default: no limit)",
                    "minimum": 1
                },
                "with_metadata": {
                    "type": "boolean",
                    "description": "Include file metadata (size, modified time, permissions)",
                    "default": False
                }
            },
            "required": ["path"]
        }
    ),
    Tool(
        name="list_symbols",
        description=(
            "List indexed symbols (functions, types, classes, etc.) under a path. "
            "Results include file, line range, language, kind, and name. "
            "Results are returned as JSON with stable ordering. "
            "Macros and some preprocessor constructs are not indexed."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Starting directory path (default: current directory)",
                    "default": "."
                },
                "max_results": {
                    "type": "integer",
                    "description": "Maximum number of symbols to return (after filters)",
                    "minimum": 1
                },
                "language": {
                    "type": "string",
                    "description": (
                        "Optional language filter (for example: python, rust, cpp, java, go). "
                        "If omitted, all indexed languages are considered."
                    )
                },
                "kind": {
                    "type": "string",
                    "description": (
                        "Optional symbol kind filter (for example: function, class, struct, "
                        "enum, trait, method, module, type, interface, annotation, record, "
                        "constructor, impl). If omitted, all kinds are considered."
                    )
                },
                "offset": {
                    "type": "integer",
                    "description": (
                        "Optional number of matching symbols to skip before returning "
                        "results (applied after language/kind filters)."
                    ),
                    "minimum": 0
                }
            }
        }
    ),
    Tool(
        name="create_file",
        description="Create a new file with given content. Fails if file already exists.",
        inputSchema={
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Path where the file should be created"
                },
                "content": {
                    "type": "string",
                    "description": "Content to write to the file"
                }
            },
            "required": ["path", "content"]
        }
    ),
    Tool(
        name="edit_file",
        description="Edit file in two ways: simple modes (overwrite/append/prepend with content) or patch mode using hunks (regex/exact anchors). Precondition is optional.",
        inputSchema={
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Target file path"
                },
                "mode": {
                    "type": "string",
                    "enum": ["overwrite", "append", "prepend", "patch"],
                    "description": "Simple modes operate on whole-file content; patch mode uses hunks.",
                    "default": "patch"
                },
                "content": {
                    "type": "string",
                    "description": "File content for simple modes (overwrite/append/prepend)."
                },
                "hunks": {
                    "type": "array",
                    "description": "Patch operations for mode='patch'. Each hunk: op replace|insert_before|insert_after|delete with anchor (exact/regex).",
                    "items": {
                        "type": "object",
                        "properties": {
                            "op": {
                                "type": "string",
                                "enum": ["replace", "insert_before", "insert_after", "delete"],
                                "description": "Operation type"
                            },
                            "anchor": {
                                "type": "object",
                                "description": "Pattern to locate target position",
                                "properties": {
                                    "type": {
                                        "type": "string",
                                        "enum": ["exact", "regex"],
                                        "description": "Matching type"
                                    },
                                    "pattern": {
                                        "type": "string",
                                        "description": "Search pattern"
                                    },
                                    "occurrence": {
                                        "type": "string",
                                        "enum": ["first", "last"],
                                        "description": "Which occurrence to match",
                                        "default": "first"
                                    },
                                    "nth": {
                                        "type": "integer",
                                        "minimum": 1,
                                        "description": "Match the n-th occurrence (1-based)"
                                    },
                                    "dotall": {
                                        "type": "boolean",
                                        "description": "Regex DOTALL mode"
                                    },
                                    "ignorecase": {
                                        "type": "boolean",
                                        "description": "Regex IGNORECASE mode"
                                    },
                                    "range": {
                                        "type": "array",
                                        "description": "Optional [start_line, end_line] search window",
                                        "items": {"type": "integer"},
                                        "minItems": 2,
                                        "maxItems": 2
                                    }
                                },
                                "required": ["type", "pattern"]
                            },
                            "content": {
                                "type": "string",
                                "description": "New text for replace/insert ops"
                            },
                            "must_unique": {
                                "type": "boolean",
                                "description": "Fail if pattern matches multiple times",
                                "default": False
                            }
                        },
                        "required": ["op", "anchor"]
                    }
                },
                "precondition": {
                    "type": "object",
                    "description": "Optional precondition to guard against stale edits",
                    "properties": {
                        "file_sha256": {
                            "type": "string",
                            "description": "Expected whole-file sha256"
                        }
                    }
                },
                "dry_run": {
                    "type": "boolean",
                    "description": "Preview changes without applying them",
                    "default": False
                }
            },
            "required": ["path"]
        }
    ),
    Tool(
        name="list_directory",
        description="List contents of a directory with file metadata.",
        inputSchema={
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Directory path to list",
                    "default": "."
                },
                "show_hidden": {
                    "type": "boolean",
                    "description": "Include hidden files (starting with .)",
                    "default": False
                },
                "recursive": {
                    "type": "boolean",
                    "description": "Recurse into subdirectories (ls -R style)",
                    "default": False
                }
            }
        }
    ),
    Tool(
        name="delete_file",
        description="Delete a single file.",
        inputSchema={
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Path to the file to delete"
                }
            },
            "required": ["path"]
        }
    ),
    Tool(
        name="delete_path",
        description="Delete a file or directory (optionally recursive).",
        inputSchema={
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Path to delete"
                },
                "recursive": {
                    "type": "boolean",
                    "description": "Allow recursive deletion of directories",
                    "default": False
                }
            },
            "required": ["path"]
        }
    ),
    Tool(
        name="mkdir",
        description="Create a directory.",
        inputSchema={
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Directory path to create"
                },
                "parents": {
                    "type": "boolean",
                    "description": "Create parent directories as needed",
                    "default": True
                },
                "exist_ok": {
                    "type": "boolean",
                    "description": "Don't raise error if directory exists",
                    "default": False
                }
            },
            "required": ["path"]
        }
    ),
    Tool(
        name="run_command",
        description="Execute a shell command and return output. Use with caution.",
        inputSchema={
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "Shell command to execute"
                },
                "timeout": {
                    "type": "integer",
                    "description": "Timeout in seconds",
                    "default": 30,
                    "minimum": 1
                }
            },
            "required": ["command"]
        }
    ),
    Tool(
        name="start_session",
        description="Start an interactive shell session for running multiple commands.",
        inputSchema={
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "Initial command to run (optional)"
                },
                "shell": {
                    "type": "string",
                    "description": "Shell to use",
                    "default": "/bin/bash"
                }
            }
        }
    ),
    Tool(
        name="send_input",
        description="Send input to an active shell session.",
        inputSchema={
            "type": "object",
            "properties": {
                "session_id": {
                    "type": "string",
                    "description": "Session ID returned by start_session"
                },
                "input_text": {
                    "type": "string",
                    "description": "Text to send to the session"
                }
            },
            "required": ["session_id", "input_text"]
        }
    ),
    Tool(
        name="read_output",
        description="Read output from an active shell session.",
        inputSchema={
            "type": "object",
            "properties": {
                "session_id": {
                    "type": "string",
                    "description": "Session ID"
                },
                "timeout": {
                    "type": "integer",
                    "description": "Timeout in seconds",
                    "default": 2,
                    "minimum": 1
                }
            },
            "required": ["session_id"]
        }
    ),
    Tool(
        name="close_session",
        description="Close an active shell session.",
        inputSchema={
            "type": "object",
            "properties": {
                "session_id": {
                    "type": "string",
                    "description": "Session ID to close"
                }
            },
            "required": ["session_id"]
        }
    ),
    Tool(
        name="list_sessions",
        description="List all active shell sessions.",
        inputSchema={
            "type": "object",
            "properties": {}
        }
    ),
    Tool(
        name="fetch_url",
        description="Fetch content from a URL and convert to markdown.",
        inputSchema={
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "URL to fetch"
                },
                "timeout": {
                    "type": "integer",
                    "description": "Timeout in seconds",
                    "default": 10,
                    "minimum": 1
                }
            },
            "required": ["url"]
        }
    ),
    Tool(
        name="web_search",
        description="Search the web using DuckDuckGo and return results.",
        inputSchema={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search query"
                },
                "max_results": {
                    "type": "integer",
                    "description": "Maximum number of results to return",
                    "default": 5,
                    "minimum": 1,
                    "maximum": 20
                }
            },
            "required": ["query"]
        }
    ),
    Tool(
        name="plan",
        description="Create and manage task plans. Supports creating, updating, and tracking task status.",
        inputSchema={
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["create", "update", "skip"],
                    "description": "Action to perform: 'create' new plan, 'update' task status, 'skip' planning"
                },
                "tasks": {
                    "type": "array",
                    "description": "List of tasks (for 'create' action)",
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "integer"},
                            "description": {"type": "string"},
                            "status": {
                                "type": "string",
                                "enum": ["pending", "in_progress", "completed"]
                            }
                        }
                    }
                },
                "task_id": {
                    "type": "integer",
                    "description": "Task ID to update (for 'update' action)"
                },
                "status": {
                    "type": "string",
                    "enum": ["pending", "in_progress", "completed"],
                    "description": "New status (for 'update' action)"
                },
                "reason": {
                    "type": "string",
                    "description": "Reason for skipping (for 'skip' action)"
                }
            },
            "required": ["action"]
        }
    )
]


@server.list_tools()
async def handle_list_tools() -> list[Tool]:
    return TOOL_DEFINITIONS


@server.call_tool()
async def handle_call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent | ImageContent | EmbeddedResource]:
    success, result = tools.execute_tool(name, arguments)

    return [
        TextContent(
            type="text",
            text=result
        )
    ]


async def run_mcp_server(work_dir: str = None, bypass_work_dir_limit: bool = False, bypass_permission: bool = False):
    tools.set_work_dir(work_dir, bypass_work_dir_limit)
    tools.set_bypass_permission(bypass_permission)
    tools.set_bypass_plan_check(True)
    tools.set_exit_on_terminate(False)

    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            InitializationOptions(
                server_name="tricode-mcp-server",
                server_version="1.0.0",
                capabilities=server.get_capabilities(
                    notification_options=NotificationOptions(),
                    experimental_capabilities={},
                )
            )
        )


def main():
    asyncio.run(run_mcp_server())


if __name__ == "__main__":
    main()
