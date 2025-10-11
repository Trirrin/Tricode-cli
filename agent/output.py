import json
import sys
from abc import ABC, abstractmethod
from typing import Any, Dict

class OutputWriter(ABC):
    @abstractmethod
    def write_tool_call(self, name: str, arguments: dict, formatted: str):
        pass
    
    @abstractmethod
    def write_tool_result(self, name: str, success: bool, result: str, formatted: str):
        pass
    
    @abstractmethod
    def write_round(self, round_num: int):
        pass
    
    @abstractmethod
    def write_reminder(self, message: str):
        pass
    
    @abstractmethod
    def write_final(self, content: str):
        pass
    
    @abstractmethod
    def write_system(self, message: str):
        pass

class HumanWriter(OutputWriter):
    def __init__(self, verbose: bool = False):
        self.verbose = verbose
    
    def write_tool_call(self, name: str, arguments: dict, formatted: str):
        if name == "plan":
            print(formatted, flush=True)
        else:
            print(f"  {formatted}", flush=True)
    
    def write_tool_result(self, name: str, success: bool, result: str, formatted: str):
        if name == "plan":
            print(formatted, flush=True)
        else:
            print(f"  ↳ {formatted}", flush=True)
        
        if self.verbose:
            print(f"  Full result:\n{result}", flush=True)
    
    def write_round(self, round_num: int):
        if self.verbose:
            print(f"\n[Round {round_num}]", flush=True)
    
    def write_reminder(self, message: str):
        if self.verbose:
            print(f"\n[REMINDER] {message}\n", flush=True)
    
    def write_final(self, content: str):
        print(content, flush=True)
    
    def write_system(self, message: str):
        if self.verbose:
            print(f"[{message}]", flush=True)

class JsonWriter(OutputWriter):
    def _write_json(self, obj: Dict[str, Any]):
        print(json.dumps(obj, ensure_ascii=False), flush=True)
    
    def write_tool_call(self, name: str, arguments: dict, formatted: str):
        self._write_json({
            "type": "tool_call",
            "name": name,
            "arguments": arguments,
            "formatted": formatted
        })
    
    def write_tool_result(self, name: str, success: bool, result: str, formatted: str):
        self._write_json({
            "type": "tool_result",
            "name": name,
            "success": success,
            "result": result,
            "formatted": formatted
        })
    
    def write_round(self, round_num: int):
        self._write_json({
            "type": "round",
            "number": round_num
        })
    
    def write_reminder(self, message: str):
        self._write_json({
            "type": "reminder",
            "message": message
        })
    
    def write_final(self, content: str):
        self._write_json({
            "type": "final",
            "content": content
        })
    
    def write_system(self, message: str):
        self._write_json({
            "type": "system",
            "message": message
        })
