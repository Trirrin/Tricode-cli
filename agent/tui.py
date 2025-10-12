import uuid
import json
import asyncio
from pathlib import Path
from datetime import datetime
from typing import Iterator
from openai import OpenAI
from textual.app import App, ComposeResult
from textual.widgets import Header, Footer, TextArea, RichLog
from textual.containers import Container, Vertical
from textual.binding import Binding
from textual import events
from textual.message import Message
from rich.markdown import Markdown
from .tools import TOOLS_SCHEMA, execute_tool, format_tool_call, set_session_id, set_work_dir, restore_plan
from .config import load_config
from .core import load_session, save_session, get_session_dir, filter_tools_schema, build_tools_description, load_agents_md, format_tool_result, call_openai_with_retry


class CustomTextArea(TextArea):
    class Submitted(Message):
        def __init__(self, text: str) -> None:
            self.text = text
            super().__init__()
    
    def _on_key(self, event: events.Key) -> None:
        if event.key == "enter":
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
    def __init__(self, session_id: str, messages: list, client: OpenAI, model: str, allowed_tools: list = None):
        self.session_id = session_id
        self.messages = messages
        self.client = client
        self.model = model
        self.filtered_tools = filter_tools_schema(allowed_tools)
        
    def send_message(self, content: str) -> Iterator[dict]:
        self.messages.append({"role": "user", "content": content})
        yield {"type": "user_message", "content": content}
        
        round_num = 0
        while True:
            round_num += 1
            yield {"type": "round", "number": round_num}
            
            try:
                response = call_openai_with_retry(
                    client=self.client,
                    model=self.model,
                    messages=self.messages,
                    tools=self.filtered_tools
                )
            except Exception as e:
                yield {"type": "error", "content": f"OpenAI API error: {str(e)}"}
                return
            
            message = response.choices[0].message
            
            if not message.tool_calls:
                final_content = message.content or "No response generated"
                yield {"type": "assistant_message", "content": final_content}
                self.messages.append({"role": "assistant", "content": final_content})
                save_session(self.session_id, self.messages)
                return
            
            self.messages.append({
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
                yield {"type": "tool_call", "name": func_name, "args": func_args, "formatted": formatted_call}
                
                success, result = execute_tool(func_name, func_args)
                
                formatted_result = format_tool_result(func_name, success, result, func_args)
                yield {"type": "tool_result", "name": func_name, "success": success, "result": result, "formatted": formatted_result}
                
                tool_results.append({"tool_call_id": tool_call.id, "content": result})
            
            for tool_result in tool_results:
                self.messages.append({
                    "role": "tool",
                    "tool_call_id": tool_result["tool_call_id"],
                    "content": tool_result["content"]
                })
            
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
        Binding("ctrl+l", "clear_output", "Clear"),
    ]
    
    def __init__(self, config: dict, work_dir: str = None, bypass_work_dir_limit: bool = False, 
                 allowed_tools: list = None, override_system_prompt: bool = False, resume_session_id: str = None):
        super().__init__()
        self.config = config
        self.work_dir = work_dir
        self.bypass_work_dir_limit = bypass_work_dir_limit
        self.allowed_tools = allowed_tools
        self.override_system_prompt = override_system_prompt
        
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
                self.session = AgentSession(self.session_id, messages, self.client, self.model, allowed_tools)
            except Exception as e:
                self.session_id = str(uuid.uuid4())[:8]
                set_session_id(self.session_id)
                self.session = AgentSession(
                    self.session_id, 
                    self._create_initial_messages(), 
                    self.client, 
                    self.model, 
                    allowed_tools
                )
        else:
            self.session_id = str(uuid.uuid4())[:8]
            set_session_id(self.session_id)
            self.session = AgentSession(
                self.session_id, 
                self._create_initial_messages(), 
                self.client, 
                self.model, 
                allowed_tools
            )
    
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
    
    async def on_custom_text_area_submitted(self, event: CustomTextArea.Submitted) -> None:
        await self._send_message()
    
    async def _send_message(self) -> None:
        input_widget = self.query_one("#input", CustomTextArea)
        message = input_widget.text.strip()
        
        if not message:
            return
        
        input_widget.clear()
        output = self.query_one("#output", RichLog)
        
        output.write(f"[bold #ffa500]You:[/bold #ffa500] {message}")
        output.write("")
        
        self.run_worker(self._process_response(message), exclusive=False)
    
    async def _process_response(self, message: str) -> None:
        output = self.query_one("#output", RichLog)
        
        def display_event(event):
            if event["type"] == "round":
                pass
            
            elif event["type"] == "tool_call":
                self.call_from_thread(lambda e=event: output.write(f"[#2b2420 on #ffb347] {e['formatted']} [/]"))
            
            elif event["type"] == "tool_result":
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
        
        def process_in_thread():
            try:
                for event in self.session.send_message(message):
                    display_event(event)
            except Exception as e:
                display_event({"type": "error", "content": str(e)})
        
        await asyncio.to_thread(process_in_thread)
    
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
            self.allowed_tools
        )
        
        output = self.query_one("#output", RichLog)
        output.clear()
        output.write(f"[bold #ff8c00]New Session Created[/bold #ff8c00]")
        output.write(f"Session ID: {self.session_id}")
        output.write(f"[dim]Press Enter to send, backslash+Enter for newline[/dim]")
        output.write("")
        
        self.query_one("#input", CustomTextArea).focus()
    
    async def action_clear_output(self) -> None:
        output = self.query_one("#output", RichLog)
        output.clear()
        output.write(f"[bold #ff8c00]Output Cleared[/bold #ff8c00]")
        output.write(f"Session ID: {self.session_id}")
        output.write(f"[dim]Press Enter to send, backslash+Enter for newline[/dim]")
        output.write("")


def run_tui(work_dir: str = None, bypass_work_dir_limit: bool = False, allowed_tools: list = None, 
            override_system_prompt: bool = False, resume_session_id: str = None):
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
        resume_session_id=resume_session_id
    )
    
    app.run()
