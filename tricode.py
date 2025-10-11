#!/usr/bin/env python

import argparse
from agent import run_agent, list_conversations

def main():
    parser = argparse.ArgumentParser(
        description="Autonomous AI agent with file operation capabilities",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  tricode "Find all TODO comments in the codebase"
  tricode "Read config.py and tell me the database settings"
  tricode "Replace old_name with new_name in all Python files"
        """
    )
    
    parser.add_argument(
        "prompt",
        type=str,
        nargs='?',
        help="Natural language instruction for the agent"
    )
    
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Show detailed execution logs"
    )
    
    parser.add_argument(
        "--stdio",
        action="store_true",
        help="Output all messages in JSON format for programmatic integration"
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
    
    args = parser.parse_args()
    
    if args.list_conversations:
        list_conversations()
        return
    
    if not args.prompt:
        parser.error("the following arguments are required: prompt")
    
    result = run_agent(
        args.prompt,
        verbose=args.verbose,
        stdio_mode=args.stdio,
        override_system_prompt=args.override_system_prompt,
        resume_session_id=args.resume
    )
    
    if result:
        print(result)

if __name__ == "__main__":
    main()
