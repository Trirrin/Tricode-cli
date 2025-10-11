import json
import os
import uuid
from pathlib import Path
from datetime import datetime
from openai import OpenAI
from .tools import TOOLS_SCHEMA, execute_tool, format_tool_call, get_plan_reminder, get_plan_final_reminder, set_session_id, restore_plan
from .config import load_config, CONFIG_FILE
from .output import HumanWriter, JsonWriter

def get_session_dir() -> Path:
    session_dir = Path.home() / ".tricode" / "session"
    session_dir.mkdir(parents=True, exist_ok=True)
    return session_dir

def save_session(session_id: str, messages: list) -> None:
    session_file = get_session_dir() / f"{session_id}.json"
    try:
        with open(session_file, 'w', encoding='utf-8') as f:
            json.dump(messages, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"Warning: Failed to save session {session_id}: {e}", flush=True)

def load_session(session_id: str) -> list:
    session_file = get_session_dir() / f"{session_id}.json"
    if not session_file.exists():
        raise FileNotFoundError(f"Session {session_id} not found")
    
    try:
        with open(session_file, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        raise ValueError(f"Failed to load session {session_id}: {e}")

def list_conversations() -> None:
    session_dir = get_session_dir()
    
    if not session_dir.exists():
        print("No conversations found.", flush=True)
        return
    
    session_files = sorted(session_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    
    if not session_files:
        print("No conversations found.", flush=True)
        return
    
    print(f"Available conversation sessions (stored in {session_dir}):\n", flush=True)
    
    for session_file in session_files:
        session_id = session_file.stem
        
        try:
            with open(session_file, 'r', encoding='utf-8') as f:
                messages = json.load(f)
            
            mtime = datetime.fromtimestamp(session_file.stat().st_mtime)
            time_str = mtime.strftime("%Y-%m-%d %H:%M:%S")
            
            user_messages = [m for m in messages if m.get("role") == "user"]
            msg_count = len(messages)
            
            first_prompt = "N/A"
            if user_messages:
                first_prompt = user_messages[0].get("content", "N/A")
                if len(first_prompt) > 60:
                    first_prompt = first_prompt[:57] + "..."
            
            print(f"  {session_id}", flush=True)
            print(f"    Last modified: {time_str}", flush=True)
            print(f"    Messages: {msg_count}", flush=True)
            print(f"    First prompt: {first_prompt}", flush=True)
            print(flush=True)
            
        except Exception as e:
            print(f"  {session_id} (error reading: {e})", flush=True)
            print(flush=True)

def load_agents_md() -> str:
    agents_content = []
    
    local_path = Path.cwd() / "AGENTS.md"
    if local_path.exists():
        try:
            with open(local_path, 'r', encoding='utf-8') as f:
                agents_content.append(f.read().strip())
        except Exception as e:
            print(f"Warning: Failed to read {local_path}: {e}", flush=True)
    
    global_path = Path.home() / ".tricode" / "AGENTS.md"
    if global_path.exists():
        try:
            with open(global_path, 'r', encoding='utf-8') as f:
                agents_content.append(f.read().strip())
        except Exception as e:
            print(f"Warning: Failed to read {global_path}: {e}", flush=True)
    
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

def run_agent(user_input: str, verbose: bool = False, stdio_mode: bool = False, override_system_prompt: bool = False, resume_session_id: str = None) -> str:
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
    
    if resume_session_id:
        try:
            messages = load_session(resume_session_id)
            messages.append({"role": "user", "content": user_input})
            session_id = resume_session_id
            set_session_id(session_id)
            restore_plan(session_id)
            writer.write_system(f"Resumed session {session_id}")
        except (FileNotFoundError, ValueError) as e:
            return f"Error: {e}"
    else:
        session_id = str(uuid.uuid4())[:8]
        set_session_id(session_id)
        messages = None
    
    if not messages:
        default_system_prompt = (
            "You are Tricode, a powerful autonomous agent running in terminal. "
            "You are a capable autonomous agent with access to file system tools. "
            "Your goal is to complete user requests efficiently and intelligently.\n\n"
            "Available tools:\n"
            "- plan: MANDATORY first tool call (create/update/check/skip)\n"
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
            "CRITICAL: PLAN TOOL MUST BE YOUR FIRST TOOL CALL:\n"
            "Before using ANY other tool, you MUST call plan with one of these actions:\n\n"
            "1. plan(action='create', tasks=[...]) - For multi-step tasks:\n"
            "   - User requests multiple operations (e.g., 'fix X and update Y')\n"
            "   - Task involves editing ≥2 files\n"
            "   - Task requires verification steps (edit → test → fix)\n"
            "   - User explicitly lists steps\n\n"
            "   CRITICAL: After creating a plan, you MUST update it as you work:\n"
            "   - BEFORE starting a task: plan(action='update', task_id=X, status='in_progress')\n"
            "   - AFTER finishing a task: plan(action='update', task_id=X, status='completed')\n"
            "   - DO NOT batch updates - update immediately after each task completion\n"
            "   - If you do 2+ operations without updating, you will be warned\n\n"
            "2. plan(action='skip', reason='...') - For simple tasks:\n"
            "   - Greetings or casual conversation\n"
            "   - Single file read/search\n"
            "   - Single command execution\n"
            "   - Simple questions about code\n\n"
            "If you try to use other tools before making this decision, they will be BLOCKED.\n\n"
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
    
    writer.write_system(f"Session ID: {session_id}")
    
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
            if round_num > 1:
                reminder = get_plan_final_reminder()
                if reminder:
                    writer.write_reminder(reminder)
                    assistant_content = (message.content or "") + f"\n\n[SYSTEM REMINDER]\n{reminder}"
                    messages.append({
                        "role": "assistant",
                        "content": assistant_content
                    })
                    save_session(session_id, messages)
                    continue
            final_content = message.content or "No response generated"
            writer.write_final(final_content)
            messages.append({
                "role": "assistant",
                "content": final_content
            })
            save_session(session_id, messages)
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
        
        tool_results = []
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
            
            tool_results.append({
                "tool_call_id": tool_call.id,
                "content": result
            })
        
        reminder = get_plan_reminder()
        if reminder and round_num >= 1 and tool_results:
            writer.write_reminder(reminder)
            tool_results[-1]["content"] += f"\n\n{reminder}"
        
        for tool_result in tool_results:
            messages.append({
                "role": "tool",
                "tool_call_id": tool_result["tool_call_id"],
                "content": tool_result["content"]
            })
        
        save_session(session_id, messages)
