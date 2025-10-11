import json
import os
from pathlib import Path
from openai import OpenAI
from .tools import TOOLS_SCHEMA, execute_tool, format_tool_call, get_plan_reminder
from .config import load_config, CONFIG_FILE
from .output import HumanWriter, JsonWriter

def load_agents_md() -> str:
    agents_content = []
    
    local_path = Path.cwd() / "AGENTS.md"
    if local_path.exists():
        try:
            with open(local_path, 'r', encoding='utf-8') as f:
                agents_content.append(f.read().strip())
        except Exception as e:
            print(f"Warning: Failed to read {local_path}: {e}")
    
    global_path = Path.home() / ".tricode" / "AGENTS.md"
    if global_path.exists():
        try:
            with open(global_path, 'r', encoding='utf-8') as f:
                agents_content.append(f.read().strip())
        except Exception as e:
            print(f"Warning: Failed to read {global_path}: {e}")
    
    return "\n\n".join(agents_content) if agents_content else ""

def format_tool_result(tool_name: str, success: bool, result: str, arguments: dict = None) -> str:
    if not success:
        return f"[FAIL] {result}"
    
    if tool_name == "read_file":
        lines = result.count('\n')
        return f"[OK] read {lines} lines"
    elif tool_name == "create_file":
        if arguments and 'content' in arguments:
            lines = arguments['content'].count('\n') + 1
            return f"[OK] created {lines} lines"
        return f"[OK] created with content"
    elif tool_name == "edit_file":
        if arguments and 'replacements' in arguments:
            total_lines = sum(r['range'][1] - r['range'][0] + 1 for r in arguments['replacements'])
            return f"[OK] edited {total_lines} lines"
        return f"[OK] edited successfully"
    elif tool_name == "search_context":
        if "No matches found" in result:
            return "[OK] 0 results"
        matches = result.count('\n') if result else 0
        return f"[OK] {matches} results"
    elif tool_name == "list_directory":
        if "Empty directory" in result:
            return "[OK] 0 items"
        items = result.count('\n') + 1 if result else 0
        return f"[OK] {items} items"
    elif tool_name == "plan":
        return result
    elif tool_name == "run_command":
        if not success:
            return f"[FAIL] {result}"
        lines = result.count('\n') if result != "[no output]" else 0
        if lines > 0:
            return f"[OK] {lines} lines output"
        return f"[OK] no output"
    elif tool_name == "start_session":
        return result
    elif tool_name == "send_input":
        return result
    elif tool_name == "read_output":
        if not success:
            return f"[FAIL] {result}"
        lines = result.count('\n') if result != "[no output within timeout]" else 0
        if lines > 0:
            return f"[OK] {lines} lines output"
        return f"[OK] no output"
    elif tool_name == "close_session":
        return result
    elif tool_name == "list_sessions":
        if "No active sessions" in result:
            return "[OK] 0 sessions"
        sessions = result.count('\n') + 1 if result else 0
        return f"[OK] {sessions} sessions"
    else:
        preview = result[:100].replace('\n', ' ')
        return f"[OK] {preview}"

def run_agent(user_input: str, verbose: bool = False, stdio_mode: bool = False, override_system_prompt: bool = False) -> str:
    config = load_config()
    
    api_key = config.get("openai_api_key")
    if not api_key:
        return f"Error: openai_api_key not configured in {CONFIG_FILE}"
    
    model = config.get("openai_model", "gpt-4o-mini")
    base_url = config.get("openai_base_url")
    
    client_kwargs = {"api_key": api_key}
    if base_url:
        client_kwargs["base_url"] = base_url
    
    client = OpenAI(**client_kwargs)
    
    writer = JsonWriter() if stdio_mode else HumanWriter(verbose)
    
    default_system_prompt = (
        "You are Tricode, a powerful autonomous agent running in terminal. "
        "You are a capable autonomous agent with access to file system tools. "
        "Your goal is to complete user requests efficiently and intelligently.\n\n"
        "Available tools:\n"
        "- plan: MANDATORY task management (create/update/check)\n"
        "- search_context: Search for patterns in files (use liberally to explore)\n"
        "- read_file: Read file contents\n"
        "- create_file: Create new files\n"
        "- edit_file: Modify existing files\n"
        "- list_directory: List directory contents\n"
        "- run_command: Execute shell commands\n"
        "- start_session: Start interactive shell session (for SSH, Python REPL, etc.)\n"
        "- send_input: Send commands to an active session\n"
        "- read_output: Read output from an active session\n"
        "- close_session: Close an active session\n"
        "- list_sessions: List all active sessions\n\n"
        "PLAN TOOL USAGE:\n"
        "For complex or multi-step tasks, use plan(action='create', tasks=[...]) to track progress.\n"
        "Update task status as you progress: plan(action='update', task_id=X, status='in_progress'/'completed').\n"
        "If you create a plan, complete all tasks before finishing.\n\n"
        "INTERACTIVE SESSION USAGE:\n"
        "For persistent interactive processes (SSH, Python REPL, Docker exec):\n"
        "1. Use start_session to launch the process - returns a session_id\n"
        "2. Use send_input to send commands to the session\n"
        "3. Use read_output to retrieve the output (wait for command completion)\n"
        "4. Always close_session when done to clean up resources\n"
        "Note: Sessions auto-expire after 30s of inactivity or 5 minutes total. Max 3 concurrent sessions.\n\n"
        "Core principles:\n"
        "1. Plan first: Break down user requests into clear tasks\n"
        "2. Be proactive: Always use tools to verify and explore before concluding\n"
        "3. Handle errors gracefully: If a tool fails, try alternative approaches\n"
        "4. Search first: When uncertain about file names or locations, use search_context\n"
        "5. Verify before acting: Read files before modifying them\n"
        "6. Track progress: Update plan status after each task completion\n\n"
        "Examples of proactive behavior:\n"
        "- File not found? Search for similar names or patterns\n"
        "- Unclear request? Search to understand the codebase structure\n"
        "- Before editing? Read the file first to understand context\n\n"
        "Never give up immediately when encountering errors. Try different approaches."
    )
    
    agents_md_content = load_agents_md()
    
    if agents_md_content:
        if override_system_prompt:
            system_prompt = agents_md_content
        else:
            system_prompt = default_system_prompt + "\n\n" + agents_md_content
    else:
        system_prompt = default_system_prompt
    
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_input}
    ]
    
    round_num = 0
    while True:
        round_num += 1
        writer.write_round(round_num)
        
        try:
            response = client.chat.completions.create(
                model=model,
                messages=messages,
                tools=TOOLS_SCHEMA,
                tool_choice="auto"
            )
        except Exception as e:
            return f"OpenAI API error: {str(e)}"
        
        message = response.choices[0].message
        
        if not message.tool_calls:
            if round_num > 3:
                reminder = get_plan_reminder()
                if reminder:
                    writer.write_reminder(reminder)
                    messages.append({
                        "role": "assistant",
                        "content": message.content
                    })
                    messages.append({
                        "role": "user",
                        "content": reminder
                    })
                    continue
            final_content = message.content or "No response generated"
            writer.write_final(final_content)
            return ""
        
        messages.append({
            "role": "assistant",
            "content": message.content,
            "tool_calls": [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.function.name,
                        "arguments": tc.function.arguments
                    }
                } for tc in message.tool_calls
            ]
        })
        
        for tool_call in message.tool_calls:
            func_name = tool_call.function.name
            try:
                func_args = json.loads(tool_call.function.arguments)
            except json.JSONDecodeError:
                func_args = {}
            
            formatted_call = format_tool_call(func_name, func_args)
            writer.write_tool_call(func_name, func_args, formatted_call)
            
            success, result = execute_tool(func_name, func_args)
            
            formatted_result = format_tool_result(func_name, success, result, func_args)
            writer.write_tool_result(func_name, success, result, formatted_result)
            
            messages.append({
                "role": "tool",
                "tool_call_id": tool_call.id,
                "content": result
            })
