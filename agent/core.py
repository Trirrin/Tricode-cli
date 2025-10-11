import json
from openai import OpenAI
from .tools import TOOLS_SCHEMA, execute_tool, format_tool_call, get_plan_reminder
from .config import load_config, CONFIG_FILE

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
    else:
        preview = result[:100].replace('\n', ' ')
        return f"[OK] {preview}"

def run_agent(user_input: str, verbose: bool = False) -> str:
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
    
    messages = [
        {
            "role": "system",
            "content": (
                "You are a capable autonomous agent with access to file system tools. "
                "Your goal is to complete user requests efficiently and intelligently.\n\n"
                "Available tools:\n"
                "- plan: MANDATORY task management (create/update/check)\n"
                "- search_context: Search for patterns in files (use liberally to explore)\n"
                "- read_file: Read file contents\n"
                "- create_file: Create new files\n"
                "- edit_file: Modify existing files\n"
                "- list_directory: List directory contents\n\n"
                "PLAN TOOL USAGE:\n"
                "For complex or multi-step tasks, use plan(action='create', tasks=[...]) to track progress.\n"
                "Update task status as you progress: plan(action='update', task_id=X, status='in_progress'/'completed').\n"
                "If you create a plan, complete all tasks before finishing.\n\n"
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
        },
        {"role": "user", "content": user_input}
    ]
    
    round_num = 0
    while True:
        round_num += 1
        if verbose:
            print(f"\n[Round {round_num}]")
        
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
                    print(f"\n{reminder}\n")
                    messages.append({
                        "role": "assistant",
                        "content": message.content
                    })
                    messages.append({
                        "role": "user",
                        "content": reminder
                    })
                    continue
            return message.content or "No response generated"
        
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
            
            if func_name == "plan":
                print(format_tool_call(func_name, func_args))
            else:
                print(f"  {format_tool_call(func_name, func_args)}")
            
            success, result = execute_tool(func_name, func_args)
            
            if func_name == "plan":
                print(format_tool_result(func_name, success, result, func_args))
            else:
                print(f"  ↳ {format_tool_result(func_name, success, result, func_args)}")
            
            if verbose:
                print(f"  Full result:\n{result}")
            
            messages.append({
                "role": "tool",
                "tool_call_id": tool_call.id,
                "content": result
            })
