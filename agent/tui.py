import uuid
import json
import asyncio
import os
import time
from pathlib import Path
from datetime import datetime
from typing import Iterator, Tuple, Union
from openai import OpenAI
from anthropic import Anthropic
import tiktoken
from textual.app import App, ComposeResult
from textual.widgets import Header, Footer, TextArea, Static, ListView, ListItem, Label
from textual.containers import Container, Vertical, Horizontal
from textual.binding import Binding
from textual import events
from textual.message import Message
from textual.screen import ModalScreen
from rich.markdown import Markdown
from rich.text import Text
from rich.panel import Panel
from rich.markup import escape as rich_escape
from rich.console import Group
from typing import Optional
import re
from .tools import TOOLS_SCHEMA, execute_tool, format_tool_call, set_session_id, set_work_dir, restore_plan
from .config import load_config, get_provider_config
from .core import load_session, save_session, get_session_dir, filter_tools_schema, build_tools_description, load_agents_md, format_tool_result, call_llm_api, build_system_prompt


# TUI-only renderer: unified diff -> Rich Panel
def render_diff_rich(diff_text: str, max_lines: int = 200) -> Panel:
    lines = diff_text.split('\n') if diff_text else []
    # Find first hunk header
    start_idx = 0
    for i, ln in enumerate(lines):
        if ln.startswith('@@'):
            start_idx = i
            break
    content = lines[start_idx:] if lines else []
    if not content:
        return Panel(Text("(no changes)", style="dim"), border_style="#888888")

    def classify(ln: str):
        if ln.startswith('@@'):
            return 'hunk', ln
        if not ln:
            return 'ctx', ''
        c = ln[0]
        if c in ('+', '-', ' '):
            return {'+': 'add', '-': 'del', ' ': 'ctx'}[c], ln[1:]
        return 'ctx', ln

    # First pass: number width
    num_width = 1
    ln_old = None
    ln_new = None
    for raw in content:
        kind, text = classify(raw)
        if kind == 'hunk':
            m = re.search(r'@@ -(\d+),?\d* \+(\d+),?\d* @@', raw)
            if m:
                ln_old = int(m.group(1))
                ln_new = int(m.group(2))
                num_width = max(num_width, len(str(ln_old)), len(str(ln_new)))
        elif kind == 'add':
            if ln_new is not None:
                num_width = max(num_width, len(str(ln_new)))
                ln_new += 1
        elif kind == 'del':
            if ln_old is not None:
                num_width = max(num_width, len(str(ln_old)))
                ln_old += 1
        else:
            if ln_old is not None:
                num_width = max(num_width, len(str(ln_old)))
                ln_old += 1
            if ln_new is not None:
                ln_new += 1

    # Second pass: single Text builder to avoid gaps
    body = Text()
    ln_old = None
    ln_new = None
    shown = 0
    total = len(content)
    for raw in content:
        if shown >= max_lines:
            body.append(f"... {total - shown} more lines omitted ...", style="dim")
            body.append("\n")
            break
        kind, text = classify(raw)
        if kind == 'hunk':
            m = re.search(r'@@ -(\d+),?\d* \+(\d+),?\d* @@', raw)
            if m:
                ln_old = int(m.group(1))
                ln_new = int(m.group(2))
            body.append(raw, style="cyan")
            body.append("\n")
            shown += 1
            continue
        if kind == 'add':
            old_s = ' ' * num_width
            new_s = f"{ln_new:>{num_width}}" if ln_new is not None else ' ' * num_width
            prefix = f" {old_s} │ {new_s} + "
            body.append(prefix)
            body.append(text if text else ' ', style="black on green")
            body.append("\n")
            if ln_new is not None:
                ln_new += 1
        elif kind == 'del':
            old_s = f"{ln_old:>{num_width}}" if ln_old is not None else ' ' * num_width
            new_s = ' ' * num_width
            prefix = f" {old_s} │ {new_s} - "
            body.append(prefix)
            body.append(text if text else ' ', style="white on dark_red")
            body.append("\n")
            if ln_old is not None:
                ln_old += 1
        else:
            old_s = f"{ln_old:>{num_width}}" if ln_old is not None else ' ' * num_width
            new_s = f"{ln_new:>{num_width}}" if ln_new is not None else ' ' * num_width
            prefix = f" {old_s} │ {new_s}   "
            body.append(prefix)
            body.append(text)
            body.append("\n")
            if ln_old is not None:
                ln_old += 1
            if ln_new is not None:
                ln_new += 1
        shown += 1

    return Panel(body, border_style="#888888")


class CollapsibleStatic(Static):
    """Static with collapsed preview for long content.

    Note: Don't override Textual's internal _render. Use _refresh() instead.
    """
    def __init__(self, content: str, *, markdown: bool = False, preview_chars: int = 800, collapsed: bool = True):
        super().__init__()
        self._full = content or ""
        self._markdown = markdown
        self._preview_chars = max(200, preview_chars)
        self._collapsed = collapsed and len(self._full) > self._preview_chars
        self._refresh()

    def toggle(self) -> None:
        self._collapsed = not self._collapsed
        self._refresh()

    def set_collapsed(self, collapsed: bool) -> None:
        """Set collapsed state and refresh if changed."""
        if bool(collapsed) != bool(self._collapsed):
            self._collapsed = bool(collapsed)
            self._refresh()

    def _refresh(self) -> None:
        if self._collapsed:
            preview = self._full[: self._preview_chars]
            suffix = "..." if len(self._full) > self._preview_chars else ""
            txt = Text(preview + suffix)
            self.update(txt)
        else:
            if self._markdown:
                self.update(Markdown(self._full))
            else:
                self.update(Text(self._full))


class LoadMoreItem(ListItem):
    """Item to request loading more history."""
    def __init__(self):
        super().__init__(Label("Load more history..."))
        self.can_focus = True


class MessageItem(ListItem):
    """Generic chat message item with header and body."""
    def __init__(self, header: str, header_style: str, body: Static):
        header_text = Text.from_markup(header_style + header + "[/]")
        container = Vertical(
            Static(header_text),
            body,
        )
        super().__init__(container)
        try:
            self.add_class("msg-item")
        except Exception:
            pass
        self.body = body


def get_available_sessions() -> list:
    session_dir = get_session_dir()
    if not session_dir.exists():
        return []
    
    session_files = sorted(session_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    sessions = []
    
    try:
        encoding = tiktoken.get_encoding("cl100k_base")
    except Exception:
        encoding = None
    
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
            
            total_tokens = 0
            if encoding:
                for message in messages:
                    role = message.get("role")
                    content = message.get("content", "")
                    
                    if content:
                        total_tokens += len(encoding.encode(str(content)))
                    total_tokens += 4
                    
                    if role == "assistant":
                        tool_calls = message.get("tool_calls", [])
                        for tc in tool_calls:
                            func_name = tc.get("function", {}).get("name", "")
                            func_args = tc.get("function", {}).get("arguments", "")
                            if func_name:
                                total_tokens += len(encoding.encode(func_name))
                            if func_args:
                                total_tokens += len(encoding.encode(func_args))
            
            sessions.append({
                "id": session_id,
                "time": time_str,
                "msg_count": msg_count,
                "first_prompt": first_prompt,
                "total_tokens": total_tokens
            })
        except Exception:
            pass
    
    return sessions


class PermissionDialog(ModalScreen):
    CSS = """
    PermissionDialog {
        align: center middle;
    }
    
    #permission_dialog {
        width: 80;
        height: auto;
        border: solid #ff0000;
        background: #3a3228;
    }
    
    #permission_title {
        width: 100%;
        height: 1;
        background: #ff0000;
        color: #ffffff;
        content-align: center middle;
    }
    
    #permission_content {
        width: 100%;
        height: auto;
        padding: 1;
        background: #3a3228;
        color: #fdf5e6;
    }
    
    #permission_options {
        width: 100%;
        height: auto;
        background: #3a3228;
    }
    
    ListView {
        height: auto;
        background: #3a3228;
    }
    
    ListItem {
        background: #3a3228;
        color: #fdf5e6;
    }
    
    ListItem.--highlight {
        background: #ff8c00;
        color: #2b2420;
    }
    """
    
    BINDINGS = [
        ("escape", "deny", "Deny"),
        ("enter", "confirm", "Confirm"),
    ]
    
    def __init__(self, tool_name: str, arguments: dict):
        super().__init__()
        self.tool_name = tool_name
        self.arguments = arguments
    
    def compose(self) -> ComposeResult:
        with Container(id="permission_dialog"):
            yield Label("⚠️  DESTRUCTIVE OPERATION REQUESTED", id="permission_title")
            
            content_lines = [
                f"Tool: {self.tool_name}",
                "Arguments:"
            ]
            for key, value in self.arguments.items():
                value_str = str(value)
                if len(value_str) > 100:
                    value_str = value_str[:100] + "..."
                content_lines.append(f"  {key}: {value_str}")
            
            yield Static("\n".join(content_lines), id="permission_content")
            
            with Container(id="permission_options"):
                with ListView():
                    yield ListItem(Label("Allow this operation (once)"))
                    yield ListItem(Label("Allow all future operations of this type in this session"))
                    yield ListItem(Label("Deny and terminate agent"))
    
    def action_confirm(self) -> None:
        list_view = self.query_one(ListView)
        selected_index = list_view.index
        
        if selected_index == 0:
            self.dismiss((True, False, ""))
        elif selected_index == 1:
            self.dismiss((True, True, ""))
        elif selected_index == 2:
            self.dismiss((False, True, f"User denied {self.tool_name} operation and requested termination"))
    
    def action_deny(self) -> None:
        self.dismiss((False, True, f"User denied {self.tool_name} operation and requested termination"))
    
    def on_list_view_selected(self, event: ListView.Selected) -> None:
        self.action_confirm()

class SessionListScreen(ModalScreen):
    CSS = """
    SessionListScreen {
        align: center middle;
    }
    
    #session_dialog {
        width: 100;
        height: 20;
        border: solid #ff8c00;
        background: #3a3228;
    }
    
    #session_title {
        width: 100%;
        height: 1;
        background: #ff8c00;
        color: #2b2420;
        content-align: center middle;
    }
    
    ListView {
        height: 1fr;
        background: #3a3228;
    }
    
    ListItem {
        background: #3a3228;
        color: #fdf5e6;
    }
    
    ListItem.--highlight {
        background: #ff8c00;
        color: #2b2420;
    }
    """
    
    BINDINGS = [
        ("escape", "dismiss", "Cancel"),
    ]
    
    def __init__(self, sessions: list):
        super().__init__()
        self.sessions = sessions
    
    def compose(self) -> ComposeResult:
        with Container(id="session_dialog"):
            yield Label("Resume Session (Enter to select, Esc to cancel)", id="session_title")
            
            with ListView():
                for session in self.sessions:
                    label_text = f"{session['id']} | {session['time']} | {session['msg_count']} msgs | {session['total_tokens']} tokens | {session['first_prompt']}"
                    yield ListItem(Label(label_text))
    
    def on_list_view_selected(self, event: ListView.Selected) -> None:
        selected_index = event.list_view.index
        if 0 <= selected_index < len(self.sessions):
            selected_session = self.sessions[selected_index]
            self.dismiss(selected_session["id"])
    
    def action_dismiss(self) -> None:
        self.dismiss(None)


class CheckpointListScreen(ModalScreen):
    CSS = """
    CheckpointListScreen {
        align: center middle;
    }
    
    #checkpoint_dialog {
        width: 100;
        height: 20;
        border: solid #ff8c00;
        background: #3a3228;
    }
    
    #checkpoint_title {
        width: 100%;
        height: 1;
        background: #ff8c00;
        color: #2b2420;
        content-align: center middle;
    }
    
    ListView {
        height: 1fr;
        background: #3a3228;
    }
    
    ListItem {
        background: #3a3228;
        color: #fdf5e6;
    }
    
    ListItem.--highlight {
        background: #ff8c00;
        color: #2b2420;
    }
    """
    
    BINDINGS = [
        ("escape", "dismiss", "Cancel"),
    ]
    
    def __init__(self, checkpoints: list):
        super().__init__()
        self.checkpoints = checkpoints
    
    def compose(self) -> ComposeResult:
        with Container(id="checkpoint_dialog"):
            yield Label("Select Checkpoint (Enter to rollback, Esc to cancel)", id="checkpoint_title")
            
            with ListView():
                for idx, checkpoint in enumerate(self.checkpoints):
                    preview = checkpoint if len(checkpoint) <= 80 else checkpoint[:77] + "..."
                    label_text = f"#{idx + 1}: {preview}"
                    yield ListItem(Label(label_text))
    
    def on_list_view_selected(self, event: ListView.Selected) -> None:
        selected_index = event.list_view.index
        if 0 <= selected_index < len(self.checkpoints):
            self.dismiss(selected_index)
    
    def action_dismiss(self) -> None:
        self.dismiss(None)


class CustomTextArea(TextArea):
    class Submitted(Message):
        def __init__(self, text: str) -> None:
            self.text = text
            super().__init__()
    
    class CheckpointRequested(Message):
        pass
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.last_esc_time = 0.0
        self.double_click_threshold = 0.5
    
    def _on_key(self, event: events.Key) -> None:
        if event.key == "escape":
            app = self.app
            if hasattr(app, 'agent_running') and app.agent_running:
                event.prevent_default()
                event.stop()
                app.action_cancel_request()
                return
            
            import time
            current_time = time.time()
            time_since_last_esc = current_time - self.last_esc_time
            
            if time_since_last_esc < self.double_click_threshold:
                event.prevent_default()
                event.stop()
                self.post_message(self.CheckpointRequested())
                self.last_esc_time = 0.0
                return
            else:
                self.last_esc_time = current_time
                event.prevent_default()
                event.stop()
                self.clear()
                return
        elif event.key == "enter":
            cursor_row, cursor_col = self.cursor_location
            
            if cursor_col > 0:
                lines = self.text.split('\n')
                if cursor_row < len(lines):
                    current_line = lines[cursor_row]
                    if cursor_col <= len(current_line) and current_line[cursor_col - 1:cursor_col] == '\\':
                        event.prevent_default()
                        event.stop()
                        
                        lines[cursor_row] = current_line[:cursor_col - 1] + current_line[cursor_col:]
                        lines.insert(cursor_row + 1, '')
                        self.text = '\n'.join(lines)
                        self.move_cursor((cursor_row + 1, 0))
                        return
            
            event.prevent_default()
            event.stop()
            self.post_message(self.Submitted(self.text))
        else:
            super()._on_key(event)


class AgentSession:
    def __init__(self, session_id: str, messages: list, client: Union[OpenAI, Anthropic], model: str, provider: str = "openai", allowed_tools: list = None, debug: bool = False):
        self.session_id = session_id
        self.messages = messages
        self.client = client
        self.model = model
        self.provider = provider
        self.filtered_tools = filter_tools_schema(allowed_tools)
        self.debug_mode = debug
        self.input_tokens = 0
        self.output_tokens = 0
        self.total_tokens = 0
        
        try:
            self.encoding = tiktoken.encoding_for_model(model)
        except KeyError:
            self.encoding = tiktoken.get_encoding("cl100k_base")
    
    def _count_messages_tokens(self) -> int:
        num_tokens = 0
        for message in self.messages:
            num_tokens += 4
            for key, value in message.items():
                if key == "content" and value:
                    num_tokens += len(self.encoding.encode(str(value)))
                elif key == "name":
                    num_tokens += len(self.encoding.encode(value))
                    num_tokens -= 1
        num_tokens += 2
        return num_tokens
    
    def calculate_history_tokens(self) -> tuple[int, int, int]:
        input_tokens = 0
        output_tokens = 0
        
        for message in self.messages:
            role = message.get("role")
            content = message.get("content", "")
            
            if role in ["system", "user", "tool"]:
                if content:
                    input_tokens += len(self.encoding.encode(str(content)))
                input_tokens += 4
            elif role == "assistant":
                if content:
                    output_tokens += len(self.encoding.encode(str(content)))
                tool_calls = message.get("tool_calls", [])
                for tc in tool_calls:
                    func_name = tc.get("function", {}).get("name", "")
                    func_args = tc.get("function", {}).get("arguments", "")
                    if func_name:
                        output_tokens += len(self.encoding.encode(func_name))
                    if func_args:
                        output_tokens += len(self.encoding.encode(func_args))
                output_tokens += 4
        
        total_tokens = input_tokens + output_tokens
        return input_tokens, output_tokens, total_tokens
        
    def send_message(self, content: str, cancel_check=None) -> Iterator[dict]:
        self.messages.append({"role": "user", "content": content})
        yield {"type": "user_message", "content": content}
        
        round_num = 0
        while True:
            if cancel_check and cancel_check():
                return
            
            round_num += 1
            yield {"type": "round", "number": round_num}
            
            try:
                stream = call_llm_api(
                    client=self.client,
                    model=self.model,
                    messages=self.messages,
                    tools=self.filtered_tools,
                    provider=self.provider,
                    stream=True,
                    debug=self.debug_mode
                )
            except Exception as e:
                yield {"type": "error", "content": f"OpenAI API error: {str(e)}"}
                return
            
            collected_content = ""
            collected_tool_calls = []
            tool_calls_map = {}
            
            round_start_input = self.input_tokens
            round_start_output = self.output_tokens
            
            prompt_tokens = self._count_messages_tokens()
            current_round_input = prompt_tokens
            
            for chunk in stream:
                if cancel_check and cancel_check():
                    return
                
                delta = chunk.choices[0].delta if chunk.choices else None
                if not delta:
                    if chunk.usage:
                        current_round_input = chunk.usage.prompt_tokens
                        current_round_output = chunk.usage.completion_tokens
                        self.input_tokens = round_start_input + current_round_input
                        self.output_tokens = round_start_output + current_round_output
                        self.total_tokens = self.input_tokens + self.output_tokens
                        yield {
                            "type": "token_update",
                            "input_tokens": self.input_tokens,
                            "output_tokens": self.output_tokens,
                            "total_tokens": self.total_tokens
                        }
                    continue
                
                if delta.content:
                    collected_content += delta.content
                    current_round_output = len(self.encoding.encode(collected_content))
                    self.input_tokens = round_start_input + current_round_input
                    self.output_tokens = round_start_output + current_round_output
                    self.total_tokens = self.input_tokens + self.output_tokens
                    yield {
                        "type": "token_update",
                        "input_tokens": self.input_tokens,
                        "output_tokens": self.output_tokens,
                        "total_tokens": self.total_tokens
                    }
                
                if delta.tool_calls:
                    for tc_delta in delta.tool_calls:
                        idx = tc_delta.index
                        if idx not in tool_calls_map:
                            tool_calls_map[idx] = {
                                "id": tc_delta.id or "",
                                "type": "function",
                                "function": {
                                    "name": "",
                                    "arguments": ""
                                }
                            }
                        if tc_delta.function:
                            if tc_delta.function.name:
                                tool_calls_map[idx]["function"]["name"] = tc_delta.function.name
                            if tc_delta.function.arguments:
                                tool_calls_map[idx]["function"]["arguments"] += tc_delta.function.arguments
            
            collected_tool_calls = [tool_calls_map[i] for i in sorted(tool_calls_map.keys())]
            
            if not collected_tool_calls:
                final_content = collected_content or "No response generated"
                yield {"type": "assistant_message", "content": final_content}
                self.messages.append({"role": "assistant", "content": final_content})
                save_session(self.session_id, self.messages)
                return
            
            self.messages.append({
                "role": "assistant",
                "content": collected_content,
                "tool_calls": collected_tool_calls
            })
            
            tool_calls_tokens = 0
            for tc in collected_tool_calls:
                func_name = tc.get("function", {}).get("name", "")
                func_args = tc.get("function", {}).get("arguments", "")
                if func_name:
                    tool_calls_tokens += len(self.encoding.encode(func_name))
                if func_args:
                    tool_calls_tokens += len(self.encoding.encode(func_args))
            
            self.output_tokens += tool_calls_tokens
            self.total_tokens = self.input_tokens + self.output_tokens
            yield {
                "type": "token_update",
                "input_tokens": self.input_tokens,
                "output_tokens": self.output_tokens,
                "total_tokens": self.total_tokens
            }
            
            tool_results = []
            for tool_call in collected_tool_calls:
                if cancel_check and cancel_check():
                    return
                
                func_name = tool_call["function"]["name"]
                try:
                    func_args = json.loads(tool_call["function"]["arguments"])
                except json.JSONDecodeError:
                    func_args = {}
                
                formatted_call = format_tool_call(func_name, func_args)
                yield {"type": "tool_call", "name": func_name, "args": func_args, "formatted": formatted_call}
                
                success, result = execute_tool(func_name, func_args)
                
                formatted_result = format_tool_result(func_name, success, result, func_args)
                yield {"type": "tool_result", "name": func_name, "success": success, "result": result, "formatted": formatted_result}
                
                tool_results.append({"tool_call_id": tool_call["id"], "content": result})
            
            tool_messages_tokens = 0
            for tool_result in tool_results:
                self.messages.append({
                    "role": "tool",
                    "tool_call_id": tool_result["tool_call_id"],
                    "content": tool_result["content"]
                })
                tool_messages_tokens += len(self.encoding.encode(str(tool_result["content"])))
                tool_messages_tokens += 4
            
            self.input_tokens += tool_messages_tokens
            self.total_tokens = self.input_tokens + self.output_tokens
            yield {
                "type": "token_update",
                "input_tokens": self.input_tokens,
                "output_tokens": self.output_tokens,
                "total_tokens": self.total_tokens
            }
            
            save_session(self.session_id, self.messages)


class TricodeCLI(App):
    CSS = """
    Screen {
        layout: vertical;
        background: #2b2420;
    }
    
    #output_container {
        height: 1fr;
        border: solid #ff8c00;
        background: #3a3228;
    }
    
    #output {
        height: 100%;
        background: #3a3228;
        color: #fdf5e6;
        padding: 0;
    }

    #output > ListItem {
        height: auto;
        min-height: 1;
        padding: 0 1;
        margin: 0;
    }

    /* Hide highlight visuals for message items to appear unselectable */
    #output > ListItem.msg-item.-highlight {
        background: transparent;
        color: #fdf5e6;
    }
    #output:focus > ListItem.msg-item.-highlight {
        background: transparent;
        color: #fdf5e6;
    }

    #output > ListItem > Vertical {
        height: auto;
        padding: 0;
        margin: 0;
        content-align: left top;
    }

    #output > ListItem Static {
        height: auto;
        padding: 0;
        margin: 0;
    }
    
    #stats_bar {
        height: 1;
        background: #2b2420;
    }
    
    #loading_status {
        width: 1fr;
        height: 1;
        background: #2b2420;
        color: #ffa500;
        text-align: left;
        padding-left: 1;
    }
    
    #token_stats {
        width: auto;
        height: 1;
        background: #2b2420;
        color: #ffa500;
        text-align: right;
        padding-right: 1;
    }
    
    #input_container {
        height: 5;
        border: solid #ffa500;
        background: #3a3228;
    }
    
    #input {
        height: 100%;
        background: #3a3228;
        color: #fdf5e6;
    }
    """
    
    BINDINGS = [
        Binding("ctrl+c", "quit", "Quit"),
        Binding("ctrl+n", "new_session", "New Session"),
        Binding("ctrl+r", "resume_session", "Resume"),
        Binding("ctrl+l", "clear_output", "Clear"),
    ]
    
    def __init__(self, config: dict, work_dir: str = None, bypass_work_dir_limit: bool = False, bypass_permission: bool = False,
                 allowed_tools: list = None, override_system_prompt: bool = False, resume_session_id: str = None, debug: bool = False, provider_name: str = None):
        super().__init__()
        self.config = config
        self.work_dir = work_dir
        self.bypass_work_dir_limit = bypass_work_dir_limit
        self.bypass_permission = bypass_permission
        self.allowed_tools = allowed_tools
        self.override_system_prompt = override_system_prompt
        self.debug_mode = debug
        self.agent_running = False
        self.cancel_requested = False
        self.spinner_chars = ['⠋', '⠙', '⠹', '⠸', '⠼', '⠴', '⠦', '⠧', '⠇', '⠏']
        self.spinner_index = 0
        self.dots_count = 1
        self.animation_timer = None
        self._token_last_update_ts: float = 0.0
        self._token_pending: Optional[Tuple[int, int, int]] = None
        self.history_window_size: int = int(os.getenv("TRICODE_TUI_WINDOW", "60"))
        self.history_render_from: int = 0
        # visible window tuning for auto-render
        self.render_above = int(os.getenv("TRICODE_TUI_VIS_ABOVE", "10"))
        self.render_below = int(os.getenv("TRICODE_TUI_VIS_BELOW", "30"))
        
        set_work_dir(work_dir, bypass_work_dir_limit)
        
        from . import tools as tools_module
        tools_module.set_bypass_permission(bypass_permission)
        tools_module.set_permission_callback(self._ask_permission_async)
        
        try:
            provider_config = get_provider_config(provider_name)
        except ValueError as e:
            print(f"Error: {e}")
            exit(1)
        
        api_key = provider_config["api_key"]
        base_url = provider_config["base_url"]
        self.provider = provider_config["provider"]
        self.model = provider_config["model"]
        
        if self.provider == "anthropic":
            self.client = Anthropic(api_key=api_key, base_url=base_url)
        else:
            self.client = OpenAI(api_key=api_key, base_url=base_url)
        
        if resume_session_id:
            try:
                messages = load_session(resume_session_id)
                self.session_id = resume_session_id
                set_session_id(self.session_id)
                restore_plan(self.session_id)
                self.session = AgentSession(self.session_id, messages, self.client, self.model, self.provider, allowed_tools, self.debug_mode)
                self.message_history = [msg.get("content") for msg in messages if msg.get("role") == "user"]
                self.history_index = -1
            except Exception as e:
                self.session_id = str(uuid.uuid4())[:8]
                set_session_id(self.session_id)
                self.session = AgentSession(
                    self.session_id, 
                    self._create_initial_messages(), 
                    self.client, 
                    self.model, 
                    self.provider,
                    allowed_tools,
                    self.debug_mode
                )
                self.message_history = []
                self.history_index = -1
        else:
            self.session_id = str(uuid.uuid4())[:8]
            set_session_id(self.session_id)
            self.session = AgentSession(
                self.session_id, 
                self._create_initial_messages(), 
                self.client, 
                self.model, 
                self.provider,
                allowed_tools,
                self.debug_mode
            )
            self.message_history = []
            self.history_index = -1
    
    def _create_initial_messages(self) -> list:
        system_prompt = build_system_prompt(self.allowed_tools, self.override_system_prompt)
        return [{"role": "system", "content": system_prompt}]
    
    def compose(self) -> ComposeResult:
        yield Header()
        
        with Vertical(id="output_container"):
            yield ListView(id="output")
        
        with Horizontal(id="stats_bar"):
            yield Static("", id="loading_status")
            yield Static("↑ 0 tokens  ↓ 0 tokens  total: 0 tokens", id="token_stats")
        
        with Container(id="input_container"):
            yield CustomTextArea(id="input", show_line_numbers=False)
        
        yield Footer()
    
    def on_mount(self) -> None:
        lst = self.query_one("#output", ListView)
        self._append_info_line("[bold #ff8c00]Tricode TUI Mode[/bold #ff8c00]")
        self._append_info_line(f"Session ID: {self.session_id}")
        self._append_info_line("[dim]Press Enter to send, backslash+Enter for newline[/dim]")
        self.query_one("#input", CustomTextArea).focus()
    def _ask_permission_async(self, tool_name: str, arguments: dict) -> Tuple[bool, bool, str]:
        import threading
        result_holder = {'result': None}
        event = threading.Event()
        
        def push_screen_callback(dialog_result):
            result_holder['result'] = dialog_result
            event.set()
        
        self.call_from_thread(self.push_screen, PermissionDialog(tool_name, arguments), push_screen_callback)
        
        event.wait(timeout=300)
        
        if result_holder['result'] is None:
            return False, True, "Permission request timed out"
        
        return result_holder['result']
    
    def _update_loading_animation(self) -> None:
        if not self.agent_running:
            return
        
        spinner = self.spinner_chars[self.spinner_index]
        dots = '.' * self.dots_count
        loading_text = f"{spinner} Running{dots}"
        
        loading_widget = self.query_one("#loading_status", Static)
        loading_widget.update(loading_text)
        
        self.spinner_index = (self.spinner_index + 1) % len(self.spinner_chars)
        self.dots_count = (self.dots_count % 3) + 1
    
    def _start_loading(self) -> None:
        self.agent_running = True
        self.cancel_requested = False
        self.animation_timer = self.set_interval(0.1, self._update_loading_animation)
    
    def _stop_loading(self) -> None:
        self.agent_running = False
        if self.animation_timer:
            self.animation_timer.stop()
            self.animation_timer = None
        loading_widget = self.query_one("#loading_status", Static)
        loading_widget.update("")
        self.spinner_index = 0
        self.dots_count = 1

    async def on_custom_text_area_submitted(self, event: CustomTextArea.Submitted) -> None:
        await self._send_message()

    # Output helpers
    def _output_view(self) -> ListView:
        return self.query_one("#output", ListView)

    def _scroll_to_end(self) -> None:
        lst = self._output_view()
        try:
            if hasattr(lst, "action_scroll_end"):
                lst.action_scroll_end()
            else:
                lst.index = max(0, len(lst.children) - 1)
        except Exception:
            pass

    def _append_item(self, item: ListItem) -> None:
        lst = self._output_view()
        try:
            lst.append(item)
        except Exception:
            lst.mount(item)
        self._scroll_to_end()
        self._update_render_window()

    def _append_info_line(self, markup_text: str) -> None:
        self._append_item(ListItem(Static(Text.from_markup(markup_text))))

    def _append_user_message(self, content: str) -> None:
        body = CollapsibleStatic(content, markdown=True, collapsed=True)
        self._append_item(MessageItem("You:", "[bold #ffa500]", body))

    def _append_agent_message(self, content: str) -> None:
        body = CollapsibleStatic(content, markdown=True, collapsed=True)
        self._append_item(MessageItem("Agent:", "[bold #ff8c00]", body))

    def _append_tool_call(self, formatted: str) -> None:
        text = Text.from_markup(f"[#2b2420 on #ffb347] {rich_escape(str(formatted))} [/]")
        self._append_item(ListItem(Static(text)))

    def _append_tool_result_generic(self, formatted: str, success: bool) -> None:
        safe = rich_escape(str(formatted))
        if success:
            self._append_item(ListItem(Static(Text.from_markup(f"[#fdf5e6]↳ {safe}[/#fdf5e6]"))))
        else:
            self._append_item(ListItem(Static(Text.from_markup(f"[red]↳ {safe}[/red]"))))

    def _append_tool_result(self, name: str, success: bool, result: str, formatted: str, func_args: dict) -> None:
        if name == "plan":
            if success:
                self._append_item(ListItem(Static(Text.from_ansi(formatted))))
            else:
                self._append_item(ListItem(Static(Text.from_markup(f"[red]{rich_escape(str(formatted))}[/red]"))))
            return
        if name == "edit_file":
            diff_text = ""
            try:
                data = json.loads(result)
                diff_text = data.get("diff", "")
            except Exception:
                diff_text = ""
            if diff_text:
                self._append_item(ListItem(Static(render_diff_rich(diff_text))))
                return
        self._append_tool_result_generic(formatted, success)

    def _clear_output(self) -> None:
        lst = self._output_view()
        for child in list(lst.children):
            try:
                child.remove()
            except Exception:
                pass

    def _token_update_throttled(self, input_tokens: int, output_tokens: int, total_tokens: int) -> None:
        now = time.time()
        self._token_pending = (input_tokens, output_tokens, total_tokens)
        if now - self._token_last_update_ts >= 0.2:
            self._token_last_update_ts = now
            it, ot, tt = self._token_pending
            token_stats = self.query_one("#token_stats", Static)
            token_stats.update(f"↑ {it} tokens  ↓ {ot} tokens  total: {tt} tokens")

    def _rebuild_from_messages(self, start_index: int) -> None:
        self.history_render_from = max(0, start_index)
        self._clear_output()
        if self.history_render_from > 0:
            self._append_item(LoadMoreItem())
        pending_tool_calls = {}
        pending_assistant_content = None
        for msg in self.session.messages[self.history_render_from:]:
            role = msg.get("role")
            content = msg.get("content")
            if role == "system":
                continue
            if role == "user":
                self._append_user_message(content or "")
                continue
            if role == "assistant":
                tool_calls = msg.get("tool_calls")
                if tool_calls:
                    pending_tool_calls = {}
                    for tc in tool_calls:
                        func_name = tc.get("function", {}).get("name", "unknown")
                        try:
                            func_args = json.loads(tc.get("function", {}).get("arguments", "{}"))
                        except json.JSONDecodeError:
                            func_args = {}
                        formatted_call = format_tool_call(func_name, func_args)
                        self._append_tool_call(formatted_call)
                        if tc.get("id"):
                            pending_tool_calls[tc.get("id")] = (func_name, func_args)
                    pending_assistant_content = content or None
                else:
                    if content:
                        self._append_agent_message(content)
                continue
            if role == "tool":
                call_id = msg.get("tool_call_id")
                func_info = pending_tool_calls.get(call_id)
                func_name, func_args = (func_info if isinstance(func_info, tuple) else (func_info, {})) if func_info else (None, {})
                if func_name == "edit_file":
                    try:
                        data = json.loads(content)
                        diff_text = data.get("diff", "")
                    except Exception:
                        diff_text = ""
                    if diff_text:
                        self._append_item(ListItem(Static(render_diff_rich(diff_text))))
                    else:
                        formatted = format_tool_result("edit_file", True, content, func_args)
                        self._append_item(ListItem(Static(Text.from_markup(f"[#fdf5e6]↳ {rich_escape(str(formatted))}[/#fdf5e6]"))))
                else:
                    if func_name == "plan":
                        formatted = format_tool_result("plan", True, content, func_args)
                        self._append_item(ListItem(Static(Text.from_ansi(formatted))))
                    else:
                        formatted = format_tool_result(func_name or "unknown", True, content, func_args)
                        self._append_item(ListItem(Static(Text.from_markup(f"[#fdf5e6]↳ {rich_escape(str(formatted))}[/#fdf5e6]"))))
                if call_id in pending_tool_calls:
                    pending_tool_calls.pop(call_id, None)
                if not pending_tool_calls and pending_assistant_content:
                    self._append_agent_message(pending_assistant_content)
                    pending_assistant_content = None
        self._append_info_line("[dim]--- History loaded ---[/dim]")
        self.query_one("#input", CustomTextArea).focus()
        self._update_render_window()

    def action_load_more_history(self) -> None:
        new_start = max(0, self.history_render_from - self.history_window_size)
        self._rebuild_from_messages(new_start)

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        try:
            if event.list_view.id != "output":
                return
        except Exception:
            return
        idx = event.list_view.index
        try:
            item = event.list_view.children[idx]
        except Exception:
            return
        if isinstance(item, LoadMoreItem):
            self.action_load_more_history()
            return

    def _update_render_window(self, center_index: Optional[int] = None) -> None:
        lst = self._output_view()
        try:
            count = len(lst.children)
        except Exception:
            return
        if count == 0:
            return
        if center_index is None:
            try:
                center_index = lst.index
            except Exception:
                center_index = None
        # Fallback if no highlighted index available
        if center_index is None or not isinstance(center_index, int):
            center_index = count - 1
        # Clamp within valid range
        if center_index < 0:
            center_index = 0
        if center_index > count - 1:
            center_index = count - 1
        above = getattr(self, "render_above", 10)
        below = getattr(self, "render_below", 30)
        start = max(0, center_index - above)
        end = min(count - 1, center_index + below)
        for i, child in enumerate(lst.children):
            if isinstance(child, MessageItem) and isinstance(child.body, CollapsibleStatic):
                child.body.set_collapsed(not (start <= i <= end))

    def on_list_view_highlighted(self, event: "ListView.Highlighted") -> None:
        try:
            if event.list_view.id != "output":
                return
        except Exception:
            return
        self._update_render_window(event.list_view.index)
    
    def on_custom_text_area_checkpoint_requested(self, event: CustomTextArea.CheckpointRequested) -> None:
        checkpoints = []
        for msg in self.session.messages:
            if msg.get("role") == "user":
                checkpoints.append(msg.get("content", ""))
        
        if not checkpoints:
            return
        
        def handle_checkpoint_selection(selected_index: int | None) -> None:
            if selected_index is not None:
                checkpoint_msg_idx = 0
                cut_index = -1
                selected_user_content = ""
                
                for idx, msg in enumerate(self.session.messages):
                    if msg.get("role") == "user":
                        if checkpoint_msg_idx == selected_index:
                            cut_index = idx
                            selected_user_content = msg.get("content", "")
                            break
                        checkpoint_msg_idx += 1
                
                if cut_index >= 1:
                    self.session.messages = self.session.messages[:cut_index]
                    save_session(self.session_id, self.session.messages)
                    
                    input_widget = self.query_one("#input", CustomTextArea)
                    input_widget.text = selected_user_content
                    input_widget.move_cursor((0, len(selected_user_content)))
                    
                    self.message_history = [msg.get("content") for msg in self.session.messages if msg.get("role") == "user"]
                    self.history_index = -1
                    
                    hist_input, hist_output, hist_total = self.session.calculate_history_tokens()
                    self.session.input_tokens = hist_input
                    self.session.output_tokens = hist_output
                    self.session.total_tokens = hist_total
                    
                    start_idx = max(0, len(self.session.messages) - self.history_window_size)
                    self._rebuild_from_messages(start_idx)
                    self._append_info_line(f"[bold #ff8c00]Rolled back before checkpoint #{selected_index + 1}[/bold #ff8c00]")
                    self._append_info_line("[dim]Message restored to input box for editing[/dim]")
                    self._append_info_line(f"Session ID: {self.session_id}")
                    
                    token_stats = self.query_one("#token_stats", Static)
                    token_text = f"↑ {hist_input} tokens  ↓ {hist_output} tokens  total: {hist_total} tokens"
                    token_stats.update(token_text)
                    
                    input_widget.focus()
        
        self.push_screen(CheckpointListScreen(checkpoints), handle_checkpoint_selection)
    
    async def _send_message(self) -> None:
        input_widget = self.query_one("#input", CustomTextArea)
        message = input_widget.text.strip()
        
        if not message:
            return
        
        if self.agent_running:
            return
        
        self.message_history.append(message)
        self.history_index = -1
        
        input_widget.clear()
        self._append_user_message(message)
        
        self._start_loading()
        self.run_worker(self._process_response(message), exclusive=False)
    
    async def _process_response(self, message: str) -> None:
        token_stats = self.query_one("#token_stats", Static)
        
        def display_event(event):
            if event["type"] == "round":
                pass
            
            elif event["type"] == "token_update":
                self.call_from_thread(lambda e=event: self._token_update_throttled(e['input_tokens'], e['output_tokens'], e['total_tokens']))
            
            elif event["type"] == "tool_call":
                self.call_from_thread(lambda e=event: self._append_tool_call(e['formatted']))
            
            elif event["type"] == "tool_result":
                self.call_from_thread(lambda e=event: self._append_tool_result(e.get('name'), e['success'], e['result'], e['formatted'], e.get('args', {})))
            
            elif event["type"] == "assistant_message":
                self.call_from_thread(lambda e=event: self._append_agent_message(e['content']))
            
            elif event["type"] == "error":
                self.call_from_thread(lambda e=event: self._append_info_line("[bold red]Error:[/bold red] " + rich_escape(str(e['content']))))
            
            elif event["type"] == "cancelled":
                self.call_from_thread(lambda: self._append_info_line("[bold yellow]Request cancelled by user[/bold yellow]"))
        
        def process_in_thread():
            try:
                for event in self.session.send_message(message, lambda: self.cancel_requested):
                    if self.cancel_requested:
                        display_event({"type": "cancelled"})
                        break
                    display_event(event)
            except Exception as e:
                display_event({"type": "error", "content": str(e)})
        
        try:
            await asyncio.to_thread(process_in_thread)
        finally:
            self._stop_loading()
    
    def action_cancel_request(self) -> None:
        if self.agent_running:
            self.cancel_requested = True
            self._append_info_line("[bold yellow]Cancelling request...[/bold yellow]")
    
    def action_quit(self) -> None:
        self.exit()
    
    async def action_new_session(self) -> None:
        self.session_id = str(uuid.uuid4())[:8]
        set_session_id(self.session_id)
        self.session = AgentSession(
            self.session_id, 
            self._create_initial_messages(), 
            self.client, 
            self.model, 
            self.provider,
            self.allowed_tools,
            self.debug_mode
        )
        
        self.message_history = []
        self.history_index = -1
        
        self._clear_output()
        self._append_info_line("[bold #ff8c00]New Session Created[/bold #ff8c00]")
        self._append_info_line(f"Session ID: {self.session_id}")
        self._append_info_line("[dim]Press Enter to send, backslash+Enter for newline[/dim]")
        
        token_stats = self.query_one("#token_stats", Static)
        token_stats.update("↑ 0 tokens  ↓ 0 tokens  total: 0 tokens")
        
        self.query_one("#input", CustomTextArea).focus()
    
    def action_resume_session(self) -> None:
        sessions = get_available_sessions()
        if not sessions:
            self._append_info_line("[bold red]No sessions available to resume[/bold red]")
            return
        
        def handle_session_selection(selected_session_id: str | None) -> None:
            if selected_session_id:
                try:
                    messages = load_session(selected_session_id)
                    self.session_id = selected_session_id
                    set_session_id(self.session_id)
                    restore_plan(self.session_id)
                    
                    self.session = AgentSession(
                        self.session_id,
                        messages,
                        self.client,
                        self.model,
                        self.provider,
                        self.allowed_tools,
                        self.debug_mode
                    )
                    
                    hist_input, hist_output, hist_total = self.session.calculate_history_tokens()
                    self.session.input_tokens = hist_input
                    self.session.output_tokens = hist_output
                    self.session.total_tokens = hist_total
                    
                    self.message_history = [msg.get("content") for msg in messages if msg.get("role") == "user"]
                    self.history_index = -1
                    
                    start_idx = max(0, len(messages) - self.history_window_size)
                    self._rebuild_from_messages(start_idx)
                    self._append_info_line("[bold #ff8c00]Session Resumed[/bold #ff8c00]")
                    self._append_info_line(f"Session ID: {self.session_id}")
                    self._append_info_line("[dim]Press Enter to send, backslash+Enter for newline[/dim]")
                    
                    token_stats = self.query_one("#token_stats", Static)
                    token_text = f"↑ {hist_input} tokens  ↓ {hist_output} tokens  total: {hist_total} tokens"
                    token_stats.update(token_text)
                    
                    self.query_one("#input", CustomTextArea).focus()
                except Exception as e:
                    self._append_info_line("[bold red]Failed to resume session: " + rich_escape(str(e)) + "[/bold red]")
        
        self.push_screen(SessionListScreen(sessions), handle_session_selection)
    
    async def action_clear_output(self) -> None:
        self._clear_output()
        self._append_info_line("[bold #ff8c00]Output Cleared[/bold #ff8c00]")
        self._append_info_line(f"Session ID: {self.session_id}")
        self._append_info_line("[dim]Press Enter to send, backslash+Enter for newline[/dim]")


def run_tui(work_dir: str = None, bypass_work_dir_limit: bool = False, bypass_permission: bool = False, allowed_tools: list = None, 
            override_system_prompt: bool = False, resume_session_id: str = None, debug: bool = False, provider_name: str = None):
    config = load_config()
    
    app = TricodeCLI(
        config=config,
        work_dir=work_dir,
        bypass_work_dir_limit=bypass_work_dir_limit,
        bypass_permission=bypass_permission,
        allowed_tools=allowed_tools,
        override_system_prompt=override_system_prompt,
        resume_session_id=resume_session_id,
        debug=debug,
        provider_name=provider_name
    )
    
    app.run()
