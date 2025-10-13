import json
import os
import uuid
import time
from pathlib import Path
from datetime import datetime
from typing import Union, Any
from openai import OpenAI, APITimeoutError, RateLimitError, APIConnectionError, InternalServerError
from anthropic import Anthropic, APIError as AnthropicAPIError, RateLimitError as AnthropicRateLimitError
from .tools import TOOLS_SCHEMA, execute_tool, format_tool_call, get_plan_reminder, get_plan_final_reminder, set_session_id, restore_plan, set_work_dir, WORK_DIR
from .config import load_config, get_provider_config, CONFIG_FILE
from .output import HumanWriter, JsonWriter
import difflib

def _render_beautiful_diff(diff_text: str, max_lines: int = 50) -> str:
    if not diff_text:
        return ""
    
    lines = diff_text.split('\n')
    result = []
    
    file_header = []
    for i, line in enumerate(lines):
        if line.startswith('---') or line.startswith('+++'):
            file_header.append(line)
        elif line.startswith('@@'):
            diff_content = lines[i:]
            break
    else:
        return diff_text
    
    if len(diff_content) > max_lines + 10:
        diff_content = diff_content[:max_lines]
        truncated = True
        remaining_lines = len(lines) - len(file_header) - max_lines
    else:
        truncated = False
        remaining_lines = 0
    
    max_line_num = 0
    line_num_old = None
    line_num_new = None
    max_content_width = 0
    
    for line in diff_content:
        if line.startswith('@@'):
            import re
            match = re.search(r'@@ -(\d+),?\d* \+(\d+),?\d* @@', line)
            if match:
                line_num_old = int(match.group(1))
                line_num_new = int(match.group(2))
                max_line_num = max(max_line_num, line_num_old, line_num_new)
        elif line.startswith('+'):
            if line_num_new is not None:
                max_line_num = max(max_line_num, line_num_new)
                line_num_new += 1
            max_content_width = max(max_content_width, len(line) - 1)
        elif line.startswith('-'):
            if line_num_old is not None:
                max_line_num = max(max_line_num, line_num_old)
                line_num_old += 1
            max_content_width = max(max_content_width, len(line) - 1)
        elif line.startswith(' '):
            if line_num_old is not None:
                max_line_num = max(max_line_num, line_num_old)
                line_num_old += 1
            if line_num_new is not None:
                line_num_new += 1
            max_content_width = max(max_content_width, len(line) - 1)
    
    num_width = len(str(max_line_num))
    max_content_width = min(max_content_width, 80)
    
    line_contents = []
    line_num_old = None
    line_num_new = None
    
    for line in diff_content:
        if line.startswith('@@'):
            import re
            match = re.search(r'@@ -(\d+),?\d* \+(\d+),?\d* @@', line)
            if match:
                line_num_old = int(match.group(1))
                line_num_new = int(match.group(2))
            line_contents.append(('header', line, None, None, None))
        elif line.startswith('+'):
            old_num = " " * num_width
            new_num = f"{line_num_new:>{num_width}}" if line_num_new is not None else " " * num_width
            content = line[1:] if len(line) > 1 else ""
            line_contents.append(('add', old_num, new_num, '+', content))
            if line_num_new is not None:
                line_num_new += 1
        elif line.startswith('-'):
            old_num = f"{line_num_old:>{num_width}}" if line_num_old is not None else " " * num_width
            new_num = " " * num_width
            content = line[1:] if len(line) > 1 else ""
            line_contents.append(('del', old_num, new_num, '-', content))
            if line_num_old is not None:
                line_num_old += 1
        elif line.startswith(' '):
            old_num = f"{line_num_old:>{num_width}}" if line_num_old is not None else " " * num_width
            new_num = f"{line_num_new:>{num_width}}" if line_num_new is not None else " " * num_width
            content = line[1:] if len(line) > 1 else ""
            line_contents.append(('ctx', old_num, new_num, ' ', content))
            if line_num_old is not None:
                line_num_old += 1
            if line_num_new is not None:
                line_num_new += 1
    
    # Utilities: terminal/width helpers and safe wrapping by display width
    import unicodedata

    def _ambiguous_wide() -> bool:
        return os.environ.get("TRICODE_WIDE_AMBIGUOUS", "0") in {"1", "true", "TRUE", "yes"}

    def _disp_width(s: str) -> int:
        if not s:
            return 0
        w = 0
        for ch in s:
            oc = ord(ch)
            # control characters (excluding tab handled by expandtabs)
            if oc < 32 or oc == 127:
                continue
            if unicodedata.combining(ch):
                continue
            eaw = unicodedata.east_asian_width(ch)
            if eaw in ("F", "W"):
                w += 2
            elif eaw == "A" and _ambiguous_wide():
                w += 2
            else:
                w += 1
        return w

    def _slice_by_width(s: str, max_w: int) -> tuple[str, str]:
        if max_w <= 0 or not s:
            return "", s
        acc = []
        w = 0
        for i, ch in enumerate(s):
            ch_w = 0
            oc = ord(ch)
            if oc < 32 or oc == 127:
                ch_w = 0
            elif unicodedata.combining(ch):
                ch_w = 0
            else:
                eaw = unicodedata.east_asian_width(ch)
                if eaw in ("F", "W"):
                    ch_w = 2
                elif eaw == "A" and _ambiguous_wide():
                    ch_w = 2
                else:
                    ch_w = 1
            if w + ch_w > max_w:
                return "".join(acc), s[i:]
            acc.append(ch)
            w += ch_w
        return "".join(acc), ""

    # Compute terminal width once and use full width for the box
    try:
        import shutil
        terminal_width = shutil.get_terminal_size().columns
    except Exception:
        terminal_width = 80

    # Anchor mode: draw right border at terminal's last column using CSI n G
    anchor_right = os.environ.get("TRICODE_DIFF_ANCHOR_RIGHT", "0") in {"1", "true", "TRUE", "yes"}

    # Keep borders consistent: "│ <content padded to inner_width> │"
    box_width = max(20, terminal_width)
    inner_width = box_width - 4
    
    top_border = "\033[90m╭" + "─" * (box_width - 2) + "╮\033[0m"
    result.append(top_border)
    
    for item in line_contents:
        if item[0] == 'header':
            raw = item[1].expandtabs(4)
            rest = raw
            first = True
            while True:
                chunk, rest = _slice_by_width(rest, inner_width if not anchor_right else inner_width * 4)
                if first and not chunk and rest:
                    # ensure forward progress even if first char is width>inner
                    chunk = rest[:1]
                    rest = rest[1:]
                first = False
                if anchor_right:
                    # Disable wrap, print left border + content, jump to last column, print right border
                    line = (
                        f"\033[?7l\033[90m│\033[0m \033[36m{chunk}\033[0m"
                        f"\033[{box_width}G\033[90m│\033[0m\033[?7h"
                    )
                else:
                    pad = " " * max(0, inner_width - _disp_width(chunk))
                    line = f"\033[90m│\033[0m \033[36m{chunk}\033[0m{pad} \033[90m│\033[0m"
                result.append(line)
                if not rest:
                    break
        else:
            line_type, old_num, new_num, op, text_content = item
            prefix = f" {old_num} │ {new_num} {op} "
            # Normalize tabs to keep alignment predictable
            norm_text = (text_content or "").expandtabs(4)
            remaining = norm_text
            first_seg = True
            while True:
                line_prefix = prefix if first_seg else (" " * len(prefix))
                if anchor_right:
                    # allow a generous slice; no wrap due to anchor mode
                    chunk, remaining = _slice_by_width(remaining, inner_width * 4)
                else:
                    avail = inner_width - _disp_width(line_prefix)
                    chunk, remaining = _slice_by_width(remaining, avail)
                # Safety: ensure progress even on pathological characters
                if first_seg and not chunk and remaining:
                    chunk = remaining[:1]
                    remaining = remaining[1:]
                inner = f"{line_prefix}{chunk}"
                if anchor_right:
                    if line_type == 'add':
                        colored = f"\033[48;2;30;80;30m\033[97m {inner}\033[0m"
                    elif line_type == 'del':
                        colored = f"\033[48;2;80;30;30m\033[97m {inner}\033[0m"
                    else:
                        colored = f" {inner}"
                    line = (
                        f"\033[?7l\033[90m│\033[0m{colored}"
                        f"\033[{box_width}G\033[90m│\033[0m\033[?7h"
                    )
                else:
                    vis_len = _disp_width(line_prefix) + _disp_width(chunk)
                    pad = " " * max(0, inner_width - vis_len)
                    if line_type == 'add':
                        colored = f"\033[48;2;30;80;30m\033[97m {inner}{pad} \033[0m"
                    elif line_type == 'del':
                        colored = f"\033[48;2;80;30;30m\033[97m {inner}{pad} \033[0m"
                    else:
                        colored = f" {inner}{pad} "
                    line = f"\033[90m│\033[0m{colored}\033[90m│\033[0m"
                result.append(line)
                first_seg = False
                if not remaining:
                    break
    
    if truncated:
        msg = f"... {remaining_lines} more lines omitted ..."
        padding = " " * max(0, inner_width - _disp_width(msg))
        line = f"\033[90m│\033[0m {msg}{padding} \033[90m│\033[0m"
        result.append(line)
    
    bottom_border = "\033[90m╰" + "─" * (box_width - 2) + "╯\033[0m"
    result.append(bottom_border)
    
    return '\n'.join(result)

def convert_tools_to_anthropic(openai_tools: list) -> list:
    anthropic_tools = []
    for tool in openai_tools:
        if tool.get("type") == "function":
            func = tool["function"]
            anthropic_tools.append({
                "name": func["name"],
                "description": func.get("description", ""),
                "input_schema": func.get("parameters", {"type": "object", "properties": {}})
            })
    return anthropic_tools

def convert_messages_for_anthropic(openai_messages: list) -> tuple[str, list]:
    system_content = ""
    anthropic_messages = []
    
    for msg in openai_messages:
        role = msg.get("role")
        content = msg.get("content", "")
        
        if role == "system":
            system_content = content
        elif role == "assistant":
            tool_calls = msg.get("tool_calls")
            if tool_calls:
                converted_content = []
                if content:
                    converted_content.append({"type": "text", "text": content})
                for tc in tool_calls:
                    converted_content.append({
                        "type": "tool_use",
                        "id": tc["id"],
                        "name": tc["function"]["name"],
                        "input": json.loads(tc["function"]["arguments"])
                    })
                anthropic_messages.append({"role": "assistant", "content": converted_content})
            else:
                anthropic_messages.append({"role": "assistant", "content": content})
        elif role == "tool":
            tool_call_id = msg.get("tool_call_id")
            anthropic_messages.append({
                "role": "user",
                "content": [{
                    "type": "tool_result",
                    "tool_use_id": tool_call_id,
                    "content": content
                }]
            })
        elif role == "user":
            anthropic_messages.append({"role": "user", "content": content})
    
    return system_content, anthropic_messages

def convert_anthropic_to_openai_response(anthropic_response: Any, model: str) -> Any:
    class OpenAIMessage:
        def __init__(self, content: str = None, tool_calls: list = None):
            self.role = "assistant"
            self.content = content
            self.tool_calls = tool_calls
    
    class OpenAIChoice:
        def __init__(self, message):
            self.message = message
            self.finish_reason = "stop"
    
    class OpenAIResponse:
        def __init__(self, id: str, model: str, choices: list, usage: dict = None):
            self.id = id
            self.model = model
            self.choices = choices
            self.usage = usage
    
    class ToolCall:
        def __init__(self, id: str, name: str, arguments: str):
            self.id = id
            self.type = "function"
            self.function = type('Function', (), {'name': name, 'arguments': arguments})()
    
    content_text = ""
    tool_calls = []
    
    for block in anthropic_response.content:
        if block.type == "text":
            content_text += block.text
        elif block.type == "tool_use":
            tool_calls.append(ToolCall(
                id=block.id,
                name=block.name,
                arguments=json.dumps(block.input)
            ))
    
    message = OpenAIMessage(
        content=content_text if content_text else None,
        tool_calls=tool_calls if tool_calls else None
    )
    
    usage = None
    if hasattr(anthropic_response, 'usage'):
        usage = type('Usage', (), {
            'prompt_tokens': anthropic_response.usage.input_tokens,
            'completion_tokens': anthropic_response.usage.output_tokens,
            'total_tokens': anthropic_response.usage.input_tokens + anthropic_response.usage.output_tokens
        })()
    
    return OpenAIResponse(
        id=anthropic_response.id,
        model=model,
        choices=[OpenAIChoice(message)],
        usage=usage
    )

def call_openai_with_retry(client, model: str, messages: list, tools: list, max_retries: int = 3, stream: bool = False, debug: bool = False):
    retryable_errors = (APITimeoutError, RateLimitError, APIConnectionError, InternalServerError)
    
    for attempt in range(max_retries + 1):
        try:
            kwargs = {
                "model": model,
                "messages": messages,
                "tools": tools,
                "tool_choice": "auto"
            }
            if stream:
                kwargs["stream"] = True
                kwargs["stream_options"] = {"include_usage": True}
            
            if debug:
                print("\n" + "="*80, flush=True)
                print("[DEBUG] API Request:", flush=True)
                print("="*80, flush=True)
                print(f"Model: {model}", flush=True)
                print(f"Messages ({len(messages)}):", flush=True)
                for i, msg in enumerate(messages):
                    print(f"\n  [{i}] Role: {msg.get('role', 'unknown')}", flush=True)
                    content = msg.get('content', '')
                    if content:
                        if len(content) > 500:
                            print(f"  Content: {content[:500]}... (truncated)", flush=True)
                        else:
                            print(f"  Content: {content}", flush=True)
                    if 'tool_calls' in msg:
                        print(f"  Tool calls: {len(msg['tool_calls'])}", flush=True)
                print(f"\nTools: {len(tools)} available", flush=True)
                print("="*80 + "\n", flush=True)
            
            response = client.chat.completions.create(**kwargs)
            
            if debug and not stream:
                print("\n" + "="*80, flush=True)
                print("[DEBUG] API Response:", flush=True)
                print("="*80, flush=True)
                print(f"Response ID: {getattr(response, 'id', 'N/A')}", flush=True)
                print(f"Model: {getattr(response, 'model', 'N/A')}", flush=True)
                if hasattr(response, 'usage'):
                    print(f"Usage: {response.usage}", flush=True)
                if hasattr(response, 'choices') and response.choices:
                    msg = response.choices[0].message
                    print(f"Message role: {msg.role}", flush=True)
                    if msg.content:
                        if len(msg.content) > 500:
                            print(f"Content: {msg.content[:500]}... (truncated)", flush=True)
                        else:
                            print(f"Content: {msg.content}", flush=True)
                    if msg.tool_calls:
                        print(f"Tool calls: {len(msg.tool_calls)}", flush=True)
                        for tc in msg.tool_calls:
                            print(f"  - {tc.function.name}", flush=True)
                print("="*80 + "\n", flush=True)
            
            return response
        except retryable_errors as e:
            if attempt == max_retries:
                raise
            delay = 2 ** attempt
            time.sleep(delay)
        except Exception:
            raise

def convert_anthropic_stream_to_openai(anthropic_stream, model: str):
    class StreamDelta:
        def __init__(self, content: str = None, tool_calls: list = None):
            self.content = content
            self.tool_calls = tool_calls
    
    class StreamChoice:
        def __init__(self, delta, index: int = 0):
            self.delta = delta
            self.index = index
            self.finish_reason = None
    
    class StreamChunk:
        def __init__(self, id: str, model: str, choices: list, usage=None):
            self.id = id
            self.model = model
            self.choices = choices
            self.usage = usage
    
    class ToolCallDelta:
        def __init__(self, index: int, id: str = None, function=None):
            self.index = index
            self.id = id
            self.function = function
    
    class FunctionDelta:
        def __init__(self, name: str = None, arguments: str = None):
            self.name = name
            self.arguments = arguments
    
    tool_use_blocks = {}
    
    for event in anthropic_stream:
        if event.type == "message_start":
            yield StreamChunk(
                id=event.message.id,
                model=model,
                choices=[StreamChoice(StreamDelta())],
                usage=None
            )
        elif event.type == "content_block_start":
            if event.content_block.type == "tool_use":
                tool_use_blocks[event.index] = {
                    "id": event.content_block.id,
                    "name": event.content_block.name,
                    "input": ""
                }
                func_delta = FunctionDelta(name=event.content_block.name)
                tc_delta = ToolCallDelta(index=event.index, id=event.content_block.id, function=func_delta)
                yield StreamChunk(
                    id="",
                    model=model,
                    choices=[StreamChoice(StreamDelta(tool_calls=[tc_delta]))],
                    usage=None
                )
        elif event.type == "content_block_delta":
            if event.delta.type == "text_delta":
                yield StreamChunk(
                    id="",
                    model=model,
                    choices=[StreamChoice(StreamDelta(content=event.delta.text))],
                    usage=None
                )
            elif event.delta.type == "input_json_delta":
                tool_block = tool_use_blocks[event.index]
                tool_block["input"] += event.delta.partial_json
                func_delta = FunctionDelta(arguments=event.delta.partial_json)
                tc_delta = ToolCallDelta(index=event.index, function=func_delta)
                yield StreamChunk(
                    id="",
                    model=model,
                    choices=[StreamChoice(StreamDelta(tool_calls=[tc_delta]))],
                    usage=None
                )
        elif event.type == "message_delta":
            if event.usage:
                usage = type('Usage', (), {
                    'prompt_tokens': 0,
                    'completion_tokens': event.usage.output_tokens,
                    'total_tokens': event.usage.output_tokens
                })()
                yield StreamChunk(
                    id="",
                    model=model,
                    choices=[],
                    usage=usage
                )
        elif event.type == "message_stop":
            if hasattr(event, 'message') and hasattr(event.message, 'usage'):
                usage = type('Usage', (), {
                    'prompt_tokens': event.message.usage.input_tokens,
                    'completion_tokens': event.message.usage.output_tokens,
                    'total_tokens': event.message.usage.input_tokens + event.message.usage.output_tokens
                })()
                yield StreamChunk(
                    id="",
                    model=model,
                    choices=[],
                    usage=usage
                )

def call_anthropic_with_retry(client: Anthropic, model: str, messages: list, tools: list, max_retries: int = 3, stream: bool = False, debug: bool = False):
    retryable_errors = (AnthropicAPIError, AnthropicRateLimitError)
    
    for attempt in range(max_retries + 1):
        try:
            system_content, anthropic_messages = convert_messages_for_anthropic(messages)
            anthropic_tools = convert_tools_to_anthropic(tools)
            
            kwargs = {
                "model": model,
                "messages": anthropic_messages,
                "max_tokens": 4096
            }
            
            if system_content:
                kwargs["system"] = system_content
            
            if anthropic_tools:
                kwargs["tools"] = anthropic_tools
            
            if stream:
                kwargs["stream"] = True
            
            if debug:
                print("\n" + "="*80, flush=True)
                print("[DEBUG] Anthropic API Request:", flush=True)
                print("="*80, flush=True)
                print(f"Model: {model}", flush=True)
                print(f"System: {system_content[:500] if len(system_content) > 500 else system_content}", flush=True)
                print(f"Messages ({len(anthropic_messages)}):", flush=True)
                for i, msg in enumerate(anthropic_messages[:3]):
                    print(f"\n  [{i}] Role: {msg.get('role', 'unknown')}", flush=True)
                    content = msg.get('content', '')
                    if isinstance(content, str):
                        if len(content) > 300:
                            print(f"  Content: {content[:300]}... (truncated)", flush=True)
                        else:
                            print(f"  Content: {content}", flush=True)
                    else:
                        print(f"  Content: {len(content)} blocks", flush=True)
                if len(anthropic_messages) > 3:
                    print(f"\n  ... and {len(anthropic_messages) - 3} more messages", flush=True)
                print(f"\nTools: {len(anthropic_tools)} available", flush=True)
                print("="*80 + "\n", flush=True)
            
            response = client.messages.create(**kwargs)
            
            if stream:
                return convert_anthropic_stream_to_openai(response, model)
            
            if debug:
                print("\n" + "="*80, flush=True)
                print("[DEBUG] Anthropic API Response:", flush=True)
                print("="*80, flush=True)
                print(f"Response ID: {response.id}", flush=True)
                print(f"Model: {response.model}", flush=True)
                print(f"Usage: input={response.usage.input_tokens}, output={response.usage.output_tokens}", flush=True)
                print(f"Stop reason: {response.stop_reason}", flush=True)
                for i, block in enumerate(response.content):
                    if block.type == "text":
                        text = block.text
                        if len(text) > 300:
                            print(f"Content[{i}] (text): {text[:300]}... (truncated)", flush=True)
                        else:
                            print(f"Content[{i}] (text): {text}", flush=True)
                    elif block.type == "tool_use":
                        print(f"Content[{i}] (tool_use): {block.name}", flush=True)
                print("="*80 + "\n", flush=True)
            
            converted_response = convert_anthropic_to_openai_response(response, model)
            return converted_response
        except retryable_errors as e:
            if attempt == max_retries:
                raise
            delay = 2 ** attempt
            time.sleep(delay)
        except Exception:
            raise

def call_llm_api(client: Union[OpenAI, Anthropic], model: str, messages: list, tools: list, 
                 provider: str = "openai", max_retries: int = 3, stream: bool = False, debug: bool = False):
    if provider == "anthropic":
        return call_anthropic_with_retry(client, model, messages, tools, max_retries, stream, debug)
    else:
        return call_openai_with_retry(client, model, messages, tools, max_retries, stream, debug)

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
        # CLI 简要统计，TUI 渲染原始 diff
        try:
            result_data = json.loads(result)
            diff_text = result_data.get("diff", "")
            if not diff_text:
                return "[OK] no changes made"

            lines = diff_text.split('\n')
            added = deleted = 0
            started = False
            for ln in lines:
                if ln.startswith('@@'):
                    started = True
                    continue
                if not started:
                    continue
                if ln.startswith('+') and not ln.startswith('+++'):
                    added += 1
                elif ln.startswith('-') and not ln.startswith('---'):
                    deleted += 1
            if added or deleted:
                return f"[OK] edited (+{added} / -{deleted}) lines"

            # 参数回退：使用 hunk 数量
            if arguments and 'hunks' in arguments:
                return f"[OK] applied {len(arguments['hunks'])} hunks"
            return "[OK] edited successfully"
        except (json.JSONDecodeError, KeyError, TypeError):
            if arguments and 'hunks' in arguments:
                return f"[OK] applied {len(arguments['hunks'])} hunks"
            return "[OK] edited successfully"
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
    elif tool_name == "delete_file":
        return result
    elif tool_name == "delete_path":
        return result
    elif tool_name == "mkdir":
        return result
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
    elif tool_name == "fetch_url":
        if not success:
            return f"[FAIL] {result}"
        chars = len(result)
        lines = result.count('\n') + 1 if result else 0
        preview = result[:200].replace('\n', ' ') if len(result) > 200 else result.replace('\n', ' ')
        return f"[OK] fetched {chars} chars ({lines} lines): {preview}..."
    elif tool_name == "web_search":
        if not success:
            return f"[FAIL] {result}"
        if "No results found" in result:
            return "[OK] 0 results"
        count = result.count('[') if result else 0
        return f"[OK] found {count} results"
    else:
        preview = result[:100].replace('\n', ' ')
        return f"[OK] {preview}"

TOOL_DESCRIPTIONS = {
    "plan": "MANDATORY first tool call (create/update/check/skip)",
    "search_context": "Search for patterns in files (use liberally to explore)",
    "read_file": "Read file contents",
    "create_file": "Create new files",
    "edit_file": "Modify existing files",
    "list_directory": "List directory contents",
    "delete_file": "Delete a single file or symlink",
    "delete_path": "Delete a file or directory (recursive optional)",
    "mkdir": "Create a directory (parents/exist_ok)",
    "run_command": "Execute shell commands",
    "start_session": "Start interactive shell session (for SSH, Python REPL, etc.)",
    "send_input": "Send commands to an active session",
    "read_output": "Read output from an active session",
    "close_session": "Close an active session",
    "list_sessions": "List all active sessions",
    "fetch_url": "Fetch web content and convert to Markdown (HTTP/HTTPS only, no JS rendering)",
    "web_search": "Search the web using DuckDuckGo and get results with titles, URLs, and snippets"
}

def filter_tools_schema(allowed_tools: list = None) -> list:
    if allowed_tools is None:
        return TOOLS_SCHEMA
    
    filtered = []
    for tool in TOOLS_SCHEMA:
        tool_name = tool["function"]["name"]
        if tool_name in allowed_tools:
            filtered.append(tool)
    
    return filtered

def build_tools_description(allowed_tools: list = None) -> str:
    if allowed_tools is None:
        tool_names = list(TOOL_DESCRIPTIONS.keys())
        prefix = "Available tools:"
    else:
        tool_names = [t for t in TOOL_DESCRIPTIONS.keys() if t in allowed_tools]
        prefix = f"Available tools (ONLY these {len(tool_names)} tools, no others):"
    
    lines = [prefix]
    for tool_name in tool_names:
        lines.append(f"- {tool_name}: {TOOL_DESCRIPTIONS[tool_name]}")
    
    return "\n".join(lines)

def run_agent(user_input: str, verbose: bool = False, stdio_mode: bool = False, override_system_prompt: bool = False, resume_session_id: str = None, allowed_tools: list = None, work_dir: str = None, bypass_work_dir_limit: bool = False, debug: bool = False, provider_name: str = None) -> str:
    set_work_dir(work_dir, bypass_work_dir_limit)
    
    try:
        provider_config = get_provider_config(provider_name)
    except ValueError as e:
        return f"Error: {e}"
    
    api_key = provider_config["api_key"]
    base_url = provider_config["base_url"]
    provider = provider_config["provider"]
    model = provider_config["model"]
    
    if provider == "anthropic":
        client = Anthropic(api_key=api_key, base_url=base_url)
    else:
        client = OpenAI(api_key=api_key, base_url=base_url)
    
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
        writer.write_system(f"Session ID: {session_id}")
        messages = None
    
    if not messages:
        tools_desc = build_tools_description(allowed_tools)
        
        has_session_tools = (
            allowed_tools is None or 
            any(t in allowed_tools for t in ["start_session", "send_input", "read_output", "close_session", "list_sessions"])
        )
        
        base_identity = (
            "You are Tricode, a powerful autonomous agent running in terminal. "
            "You are a capable autonomous agent with access to file system tools. "
            "Your goal is to complete user requests efficiently and intelligently.\n\n"
        )
        
        current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S %A")
        time_section = (
            f"CURRENT LOCAL TIME: {current_time}\n"
            f"Use this timestamp when processing time-related requests.\n\n"
        )
        
        work_dir_section = (
            f"WORKING DIRECTORY: {WORK_DIR}\n"
            f"All relative paths (like '.', 'file.txt', 'subdir/') are relative to this directory.\n\n"
        )
        
        tools_section = (
            f"{tools_desc}\n\n"
            "CRITICAL: PLAN TOOL MUST BE YOUR FIRST TOOL CALL:\n"
            "Before using ANY other tool, you MUST call plan with one of these actions:\n\n"
            "IMPORTANT: First check if you have ALL necessary tools to complete the request. "
            "If you lack critical tools (e.g., need read_file but don't have it, or need edit_file but don't have it), "
            "use plan(action='skip', reason='Missing required tools: [list]') and explain to the user that the task "
            "is impossible without those tools. DO NOT create a plan or attempt the task if tools are insufficient.\n\n"
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
        )
        
        if has_session_tools:
            tools_section += (
                "INTERACTIVE SESSION USAGE:\n"
                "For persistent interactive processes (SSH, Python REPL, Docker exec):\n"
                "1. Use start_session to launch the process - returns a session_id\n"
                "2. Use send_input to send commands to the session\n"
                "3. Use read_output to retrieve the output (wait for command completion)\n"
                "4. Always close_session when done to clean up resources\n"
                "Note: Sessions auto-expire after 30s of inactivity or 5 minutes total. Max 3 concurrent sessions.\n\n"
            )
        
        has_search = allowed_tools is None or "search_context" in allowed_tools
        has_read = allowed_tools is None or "read_file" in allowed_tools
        has_edit = allowed_tools is None or "edit_file" in allowed_tools or "create_file" in allowed_tools
        
        principles = [
            "1. Plan first: Break down user requests into clear tasks",
            "2. Be proactive: Always use tools to verify and explore before concluding",
            "3. Handle errors gracefully: If a tool fails, try alternative approaches"
        ]
        
        if has_search:
            principles.append("4. Search first: When uncertain about file names or locations, use search_context")
        if has_read and has_edit:
            principles.append(f"{len(principles) + 1}. Verify before acting: Read files before modifying them")
        principles.append(f"{len(principles) + 1}. Track progress: Update plan status after each task completion")
        
        tools_section += "Core principles:\n" + "\n".join(principles) + "\n\n"
        
        if has_search or has_read or has_edit:
            examples = ["Examples of proactive behavior:"]
            if has_search:
                examples.append("- File not found? Search for similar names or patterns")
                examples.append("- Unclear request? Search to understand the codebase structure")
            if has_read and has_edit:
                examples.append("- Before editing? Read the file first to understand context")
            tools_section += "\n".join(examples) + "\n\n"
        
        tools_section += (
            "Never give up immediately when encountering errors. Try different approaches.\n\n"
            "IMPORTANT: If the user's request cannot be completed with your available tools, "
            "clearly tell them that the task is not possible with the current tool limitations. "
            "Explain what tools would be needed and do not attempt to complete the task with inappropriate tools."
        )
        
        agents_md_content = load_agents_md()
        
        if agents_md_content and override_system_prompt:
            system_prompt = time_section + work_dir_section + tools_section + "\n\n" + agents_md_content
        elif agents_md_content:
            system_prompt = base_identity + time_section + work_dir_section + tools_section + "\n\n" + agents_md_content
        else:
            system_prompt = base_identity + time_section + work_dir_section + tools_section
        
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_input}
        ]
    
    filtered_tools = filter_tools_schema(allowed_tools)
    
    round_num = 0
    while True:
        round_num += 1
        writer.write_round(round_num)
        
        try:
            response = call_llm_api(
                client=client,
                model=model,
                messages=messages,
                tools=filtered_tools,
                provider=provider,
                debug=debug
            )
        except Exception as e:
            return f"OpenAI API error: {str(e)}"
        
        if response is None or not hasattr(response, 'choices') or not response.choices:
            return "OpenAI API error: Invalid response from API"
        
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
