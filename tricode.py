#!/usr/bin/env python

import argparse
from agent import run_agent

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
        help="Natural language instruction for the agent"
    )
    
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Show detailed execution logs"
    )
    
    args = parser.parse_args()
    
    result = run_agent(
        args.prompt,
        verbose=args.verbose
    )
    
    print(result)

if __name__ == "__main__":
    main()
