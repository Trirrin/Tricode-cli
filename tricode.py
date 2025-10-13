#!/usr/bin/env python

import argparse
import sys
from agent import run_agent, list_conversations
from agent.tui import run_tui

try:
    from version import get_runtime_version, get_full_version_string, __version__, __commit_id__
except ImportError:
    # Fallback for development mode without build
    __version__ = "dev"
    __commit_id__ = "unknown"
    def get_runtime_version():
        return f"Tricode-cli {__version__} (git-{__commit_id__})"
    def get_full_version_string():
        return f"Tricode-cli {__version__} (git-{__commit_id__})"


class VersionedHelpFormatter(argparse.RawDescriptionHelpFormatter):
    """Custom help formatter that includes version info in the header"""
    def format_help(self):
        # Get the original help text
        help_text = super().format_help()
        # Add version info at the beginning
        version_line = f"{get_runtime_version()}\n\n"
        return version_line + help_text

def main():
    parser = argparse.ArgumentParser(
        description="Autonomous AI agent with file operation capabilities",
        formatter_class=VersionedHelpFormatter,
        epilog="""
Examples:
  tricode "Find all TODO comments in the codebase"
  tricode "Read config.py and tell me the database settings"
  tricode "Replace old_name with new_name in all Python files"
        """
    )
    
    parser.add_argument(
        "--version",
        action="version",
        version=get_runtime_version(),
        help="Show version information and exit"
    )
    
    parser.add_argument(
        "prompt",
        type=str,
        nargs='?',
        help="Natural language instruction for the agent (omit for TUI mode with --tui)"
    )
    
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Show detailed execution logs"
    )
    
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Show API request and response details"
    )
    
    parser.add_argument(
        "--stdio",
        action="store_true",
        help="Output all messages in JSON format for programmatic integration"
    )
    
    parser.add_argument(
        "--tools",
        type=str,
        help="Comma-separated list of allowed tools (e.g., 'read_file,search_context,plan'). If not specified, all tools are available."
    )
    
    parser.add_argument(
        "--override-system-prompt",
        action="store_true",
        help="Replace default system prompt with AGENTS.md content instead of appending"
    )
    
    parser.add_argument(
        "-r", "--resume",
        type=str,
        metavar="SESSION_ID",
        help="Resume a previous conversation session by ID"
    )
    
    parser.add_argument(
        "-l", "--list-conversations",
        action="store_true",
        help="List all available conversation sessions"
    )
    
    parser.add_argument(
        "--work-dir",
        type=str,
        metavar="PATH",
        help="Set working directory (default: current directory). Agent can only access files under this path."
    )
    
    parser.add_argument(
        "--bypass-work-directory-limit",
        action="store_true",
        help="Allow access to files outside the working directory"
    )
    
    parser.add_argument(
        "--provider",
        type=str,
        metavar="NAME",
        help="Specify which provider to use (e.g., 'openai', 'anthropic'). If not specified, uses default_provider from config."
    )
    
    parser.add_argument(
        "--tui",
        action="store_true",
        help="Launch interactive TUI (Text User Interface) mode"
    )
    
    args = parser.parse_args()
    
    if args.list_conversations:
        list_conversations()
        return
    
    if args.tui:
        allowed_tools = None
        if args.tools:
            allowed_tools = [t.strip() for t in args.tools.split(',') if t.strip()]
            if 'plan' not in allowed_tools:
                allowed_tools.insert(0, 'plan')
        
        run_tui(
            work_dir=args.work_dir,
            bypass_work_dir_limit=args.bypass_work_directory_limit,
            allowed_tools=allowed_tools,
            override_system_prompt=args.override_system_prompt,
            resume_session_id=args.resume,
            debug=args.debug,
            provider_name=args.provider
        )
        return
    
    if not args.prompt:
        parser.error("the following arguments are required: prompt")
    
    allowed_tools = None
    if args.tools:
        allowed_tools = [t.strip() for t in args.tools.split(',') if t.strip()]
        if 'plan' not in allowed_tools:
            allowed_tools.insert(0, 'plan')
    
    result = run_agent(
        args.prompt,
        verbose=args.verbose,
        stdio_mode=args.stdio,
        override_system_prompt=args.override_system_prompt,
        resume_session_id=args.resume,
        allowed_tools=allowed_tools,
        work_dir=args.work_dir,
        bypass_work_dir_limit=args.bypass_work_directory_limit,
        debug=args.debug,
        provider_name=args.provider
    )
    
    if result:
        print(result, flush=True)

if __name__ == "__main__":
    main()
