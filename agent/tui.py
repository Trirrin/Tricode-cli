import uuid
import json
import asyncio
from pathlib import Path
from datetime import datetime
from typing import Iterator
from openai import OpenAI
import tiktoken
from textual.app import App, ComposeResult
from textual.widgets import Header, Footer, TextArea, RichLog, Static, ListView, ListItem, Label
from textual.containers import Container, Vertical, Horizontal
from textual.binding import Binding
from textual import events
from textual.message import Message
from textual.screen import ModalScreen
from rich.markdown import Markdown
from rich.text import Text
from .tools import TOOLS_SCHEMA, execute_tool, format_tool_call, set_session_id, set_work_dir, restore_plan
from .config import load_config
from .core import load_session, save_session, get_session_dir, filter_tools_schema, build_tools_description, load_agents_md, format_tool_result, call_openai_with_retry


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
    def __init__(self, session_id: str, messages: list, client: OpenAI, model: str, allowed_tools: list = None, debug: bool = False):
        self.session_id = session_id
        self.messages = messages
        self.client = client
        self.model = model
        self.filtered_tools = filter_tools_schema(allowed_tools)
        self.debug = debug
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
                stream = call_openai_with_retry(
                    client=self.client,
                    model=self.model,
                    messages=self.messages,
                    tools=self.filtered_tools,
                    stream=True,
                    debug=self.debug
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


class TricodeApp(App):
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
    
    def __init__(self, config: dict, work_dir: str = None, bypass_work_dir_limit: bool = False, 
                 allowed_tools: list = None, override_system_prompt: bool = False, resume_session_id: str = None, debug: bool = False):
        super().__init__()
        self.config = config
        self.work_dir = work_dir
        self.bypass_work_dir_limit = bypass_work_dir_limit
        self.allowed_tools = allowed_tools
        self.override_system_prompt = override_system_prompt
        self.debug = debug
        self.agent_running = False
        self.cancel_requested = False
        self.spinner_chars = ['⠋', '⠙', '⠹', '⠸', '⠼', '⠴', '⠦', '⠧', '⠇', '⠏']
        self.spinner_index = 0
        self.dots_count = 1
        self.animation_timer = None
        
        set_work_dir(work_dir, bypass_work_dir_limit)
        
        api_key = config.get("openai_api_key")
        model = config.get("openai_model", "gpt-4o-mini")
        base_url = config.get("openai_base_url")
        
        client_kwargs = {"api_key": api_key}
        if base_url:
            client_kwargs["base_url"] = base_url
        
        self.client = OpenAI(**client_kwargs)
        self.model = model
        
        if resume_session_id:
            try:
                messages = load_session(resume_session_id)
                self.session_id = resume_session_id
                set_session_id(self.session_id)
                restore_plan(self.session_id)
                self.session = AgentSession(self.session_id, messages, self.client, self.model, allowed_tools, self.debug)
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
                    allowed_tools,
                    self.debug
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
                allowed_tools,
                self.debug
            )
            self.message_history = []
            self.history_index = -1
    
    def _create_initial_messages(self) -> list:
        tools_desc = build_tools_description(self.allowed_tools)
        
        base_identity = (
            "You are Tricode, a powerful autonomous agent running in terminal TUI mode. "
            "You are a capable autonomous agent with access to file system tools. "
            "Your goal is to complete user requests efficiently and intelligently.\n\n"
        )
        
        current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S %A")
        time_section = f"CURRENT LOCAL TIME: {current_time}\n\n"
        
        from .tools import WORK_DIR
        work_dir_section = f"WORKING DIRECTORY: {WORK_DIR}\n\n"
        
        tools_section = (
            f"{tools_desc}\n\n"
            "CRITICAL: PLAN TOOL MUST BE YOUR FIRST TOOL CALL\n\n"
            "Core principles:\n"
            "1. Plan first: Break down user requests into clear tasks\n"
            "2. Be proactive: Always use tools to verify and explore\n"
            "3. Handle errors gracefully: Try alternative approaches\n"
        )
        
        agents_md_content = load_agents_md()
        
        if agents_md_content and self.override_system_prompt:
            system_prompt = time_section + work_dir_section + tools_section + "\n\n" + agents_md_content
        elif agents_md_content:
            system_prompt = base_identity + time_section + work_dir_section + tools_section + "\n\n" + agents_md_content
        else:
            system_prompt = base_identity + time_section + work_dir_section + tools_section
        
        return [{"role": "system", "content": system_prompt}]
    
    def compose(self) -> ComposeResult:
        yield Header()
        
        with Vertical(id="output_container"):
            yield RichLog(id="output", wrap=True, highlight=True, markup=True)
        
        with Horizontal(id="stats_bar"):
            yield Static("", id="loading_status")
            yield Static("↑ 0 tokens  ↓ 0 tokens  total: 0 tokens", id="token_stats")
        
        with Container(id="input_container"):
            yield CustomTextArea(id="input", show_line_numbers=False)
        
        yield Footer()
    
    def on_mount(self) -> None:
        output = self.query_one("#output", RichLog)
        output.write(f"[bold #ff8c00]Tricode TUI Mode[/bold #ff8c00]")
        output.write(f"Session ID: {self.session_id}")
        output.write(f"[dim]Press Enter to send, backslash+Enter for newline[/dim]")
        output.write("")
        self.query_one("#input", CustomTextArea).focus()
    
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
                    
                    output = self.query_one("#output", RichLog)
                    output.clear()
                    output.write(f"[bold #ff8c00]Rolled back before checkpoint #{selected_index + 1}[/bold #ff8c00]")
                    output.write(f"[dim]Message restored to input box for editing[/dim]")
                    output.write(f"Session ID: {self.session_id}")
                    output.write("")
                    
                    for msg in self.session.messages:
                        role = msg.get("role")
                        content = msg.get("content")
                        
                        if role == "system":
                            continue
                        elif role == "user":
                            output.write(f"[bold #ffa500]You:[/bold #ffa500] {content}")
                            output.write("")
                        elif role == "assistant":
                            tool_calls = msg.get("tool_calls")
                            if tool_calls:
                                for tc in tool_calls:
                                    func_name = tc.get("function", {}).get("name", "unknown")
                                    try:
                                        func_args = json.loads(tc.get("function", {}).get("arguments", "{}"))
                                    except json.JSONDecodeError:
                                        func_args = {}
                                    formatted_call = format_tool_call(func_name, func_args)
                                    output.write(f"[#2b2420 on #ffb347] {formatted_call} [/]")
                            if content:
                                output.write("[bold #ff8c00]Agent:[/bold #ff8c00]")
                                md = Markdown(content)
                                output.write(md)
                                output.write("")
                        elif role == "tool":
                            tool_content = content[:200] + "..." if len(content) > 200 else content
                            output.write(f"[#fdf5e6]↳ {tool_content}[/#fdf5e6]")
                            output.write("")
                    
                    output.write(f"[dim]--- Rolled back, {len(self.session.messages)} messages remaining ---[/dim]")
                    output.write("")
                    
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
        output = self.query_one("#output", RichLog)
        
        output.write(f"[bold #ffa500]You:[/bold #ffa500] {message}")
        output.write("")
        
        self._start_loading()
        self.run_worker(self._process_response(message), exclusive=False)
    
    async def _process_response(self, message: str) -> None:
        output = self.query_one("#output", RichLog)
        token_stats = self.query_one("#token_stats", Static)
        
        def display_event(event):
            if event["type"] == "round":
                pass
            
            elif event["type"] == "token_update":
                token_text = f"↑ {event['input_tokens']} tokens  ↓ {event['output_tokens']} tokens  total: {event['total_tokens']} tokens"
                self.call_from_thread(lambda: token_stats.update(token_text))
            
            elif event["type"] == "tool_call":
                self.call_from_thread(lambda e=event: output.write(f"[#2b2420 on #ffb347] {e['formatted']} [/]"))
            
            elif event["type"] == "tool_result":
                if event.get("name") == "plan":
                    def write_plan_result(formatted):
                        plan_text = Text.from_ansi(formatted)
                        output.write(plan_text)
                    if event["success"]:
                        self.call_from_thread(lambda e=event: write_plan_result(e['formatted']))
                    else:
                        self.call_from_thread(lambda e=event: output.write(f"[red]{e['formatted']}[/red]"))
                else:
                    if event["success"]:
                        self.call_from_thread(lambda e=event: output.write(f"[#fdf5e6]↳ {e['formatted']}[/#fdf5e6]"))
                    else:
                        self.call_from_thread(lambda e=event: output.write(f"[red]↳ {e['formatted']}[/red]"))
                self.call_from_thread(lambda: output.write(""))
            
            elif event["type"] == "assistant_message":
                def write_markdown(content):
                    output.write("[bold #ff8c00]Agent:[/bold #ff8c00]")
                    md = Markdown(content)
                    output.write(md)
                    output.write("")
                self.call_from_thread(lambda e=event: write_markdown(e['content']))
            
            elif event["type"] == "error":
                self.call_from_thread(lambda e=event: output.write(f"[bold red]Error:[/bold red] {e['content']}"))
                self.call_from_thread(lambda: output.write(""))
            
            elif event["type"] == "cancelled":
                self.call_from_thread(lambda: output.write("[bold yellow]Request cancelled by user[/bold yellow]"))
                self.call_from_thread(lambda: output.write(""))
        
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
            output = self.query_one("#output", RichLog)
            output.write("[bold yellow]Cancelling request...[/bold yellow]")
    
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
            self.allowed_tools,
            self.debug
        )
        
        self.message_history = []
        self.history_index = -1
        
        output = self.query_one("#output", RichLog)
        output.clear()
        output.write(f"[bold #ff8c00]New Session Created[/bold #ff8c00]")
        output.write(f"Session ID: {self.session_id}")
        output.write(f"[dim]Press Enter to send, backslash+Enter for newline[/dim]")
        output.write("")
        
        token_stats = self.query_one("#token_stats", Static)
        token_stats.update("↑ 0 tokens  ↓ 0 tokens  total: 0 tokens")
        
        self.query_one("#input", CustomTextArea).focus()
    
    def action_resume_session(self) -> None:
        sessions = get_available_sessions()
        if not sessions:
            output = self.query_one("#output", RichLog)
            output.write("[bold red]No sessions available to resume[/bold red]")
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
                        self.allowed_tools,
                        self.debug
                    )
                    
                    hist_input, hist_output, hist_total = self.session.calculate_history_tokens()
                    self.session.input_tokens = hist_input
                    self.session.output_tokens = hist_output
                    self.session.total_tokens = hist_total
                    
                    self.message_history = [msg.get("content") for msg in messages if msg.get("role") == "user"]
                    self.history_index = -1
                    
                    output = self.query_one("#output", RichLog)
                    output.clear()
                    output.write(f"[bold #ff8c00]Session Resumed[/bold #ff8c00]")
                    output.write(f"Session ID: {self.session_id}")
                    output.write(f"[dim]Press Enter to send, backslash+Enter for newline[/dim]")
                    output.write("")
                    
                    for msg in messages:
                        role = msg.get("role")
                        content = msg.get("content")
                        
                        if role == "system":
                            continue
                        elif role == "user":
                            output.write(f"[bold #ffa500]You:[/bold #ffa500] {content}")
                            output.write("")
                        elif role == "assistant":
                            tool_calls = msg.get("tool_calls")
                            if tool_calls:
                                for tc in tool_calls:
                                    func_name = tc.get("function", {}).get("name", "unknown")
                                    try:
                                        func_args = json.loads(tc.get("function", {}).get("arguments", "{}"))
                                    except json.JSONDecodeError:
                                        func_args = {}
                                    formatted_call = format_tool_call(func_name, func_args)
                                    output.write(f"[#2b2420 on #ffb347] {formatted_call} [/]")
                            if content:
                                output.write("[bold #ff8c00]Agent:[/bold #ff8c00]")
                                md = Markdown(content)
                                output.write(md)
                                output.write("")
                        elif role == "tool":
                            tool_content = content[:200] + "..." if len(content) > 200 else content
                            output.write(f"[#fdf5e6]↳ {tool_content}[/#fdf5e6]")
                            output.write("")
                    
                    output.write(f"[dim]--- History loaded ---[/dim]")
                    output.write("")
                    
                    token_stats = self.query_one("#token_stats", Static)
                    token_text = f"↑ {hist_input} tokens  ↓ {hist_output} tokens  total: {hist_total} tokens"
                    token_stats.update(token_text)
                    
                    self.query_one("#input", CustomTextArea).focus()
                except Exception as e:
                    output = self.query_one("#output", RichLog)
                    output.write(f"[bold red]Failed to resume session: {str(e)}[/bold red]")
        
        self.push_screen(SessionListScreen(sessions), handle_session_selection)
    
    async def action_clear_output(self) -> None:
        output = self.query_one("#output", RichLog)
        output.clear()
        output.write(f"[bold #ff8c00]Output Cleared[/bold #ff8c00]")
        output.write(f"Session ID: {self.session_id}")
        output.write(f"[dim]Press Enter to send, backslash+Enter for newline[/dim]")
        output.write("")


def run_tui(work_dir: str = None, bypass_work_dir_limit: bool = False, allowed_tools: list = None, 
            override_system_prompt: bool = False, resume_session_id: str = None, debug: bool = False):
    from .config import CONFIG_FILE
    
    config = load_config()
    
    api_key = config.get("openai_api_key")
    if not api_key:
        print(f"Error: openai_api_key not configured in {CONFIG_FILE}")
        return
    
    app = TricodeApp(
        config=config,
        work_dir=work_dir,
        bypass_work_dir_limit=bypass_work_dir_limit,
        allowed_tools=allowed_tools,
        override_system_prompt=override_system_prompt,
        resume_session_id=resume_session_id,
        debug=debug
    )
    
    app.run()
