# Tricode STDIO API 文档

## 概述

使用 `--stdio` 参数启动 Tricode 时，所有输出以 JSON Lines 格式（每行一个 JSON 对象）输出到 stdout，便于第三方程序解析和集成。

## 启动方式

```bash
tricode --stdio "你的指令"
```

## 消息类型

所有消息都是单行 JSON 对象，包含 `type` 字段标识消息类型。

### 1. round - 执行轮次

标识 Agent 进入新的思考/执行轮次。

```json
{"type": "round", "number": 1}
```

**字段说明：**
- `number` (int): 轮次编号，从 1 开始递增

---

### 2. tool_call - 工具调用

Agent 调用某个工具时触发。

```json
{
  "type": "tool_call",
  "name": "read_file",
  "arguments": {"path": "tricode.py", "ranges": [[1, 10]]},
  "formatted": "READ(\"tricode.py\", lines=[1-10])"
}
```

**字段说明：**
- `name` (string): 工具名称（search_context / read_file / create_file / edit_file / list_directory / plan）
- `arguments` (object): 工具的原始参数
- `formatted` (string): 人类可读的工具调用描述

---

### 3. tool_result - 工具执行结果

工具执行完成后触发。

```json
{
  "type": "tool_result",
  "name": "read_file",
  "success": true,
  "result": "#!/usr/bin/env python\n\nimport argparse...",
  "formatted": "[OK] read 47 lines"
}
```

**字段说明：**
- `name` (string): 工具名称
- `success` (bool): 执行是否成功
- `result` (string): 工具的完整输出内容（可能很长）
- `formatted` (string): 简化的结果摘要（用于快速显示）

---

### 4. reminder - 警告/提醒

Agent 内部提醒消息（如未完成的计划任务）。

```json
{
  "type": "reminder",
  "message": "WARNING: 2 task(s) still incomplete:\n  [1] pending      - Read configuration file\n  [2] in_progress  - Update settings"
}
```

**字段说明：**
- `message` (string): 提醒内容

---

### 5. final - 最终响应

Agent 完成任务后的最终回复。

```json
{
  "type": "final",
  "content": "已成功读取文件内容，共 47 行代码。主要包含 CLI 参数解析和 agent 调用逻辑。"
}
```

**字段说明：**
- `content` (string): Agent 的最终回复内容

---

## 完整交互示例

### 输入命令
```bash
tricode --stdio "list files in current directory"
```

### 输出流（JSON Lines）
```json
{"type": "round", "number": 1}
{"type": "tool_call", "name": "list_directory", "arguments": {"path": "."}, "formatted": "LIST(\".\")"}
{"type": "tool_result", "name": "list_directory", "success": true, "result": "总计 72\ndrwxrwxr-x  7 xrain xrain 4096 ...\n-rwxrwxr-x  1 xrain xrain 1140 tricode.py", "formatted": "[OK] 18 items"}
{"type": "round", "number": 2}
{"type": "final", "content": "当前目录包含以下文件和目录：\n\n- agent/ (目录)\n- build/ (目录)\n- tricode.py\n- README.md\n..."}
```

---

## 工具类型详解

### search_context
搜索文件内容中的模式。

**参数：**
```json
{"pattern": "def ", "path": "."}
```

**结果示例：**
```
6:def main():
15:def run_agent():
```

---

### read_file
读取文件内容。

**参数：**
```json
{"path": "tricode.py", "ranges": [[1, 10]]}
```
- `ranges` 可选，指定读取的行范围（从 1 开始）

---

### create_file
创建新文件。

**参数：**
```json
{"path": "new_file.py", "content": "print('hello')"}
```

---

### edit_file
编辑现有文件。

**参数：**
```json
{
  "path": "tricode.py",
  "replacements": [
    {
      "range": [10, 12],
      "content": "new content for lines 10-12"
    }
  ]
}
```

---

### list_directory
列出目录内容。

**参数：**
```json
{"path": "."}
```

---

### plan
任务计划管理。

**参数（创建计划）：**
```json
{
  "action": "create",
  "tasks": [
    {"id": 1, "desc": "Read config file"},
    {"id": 2, "desc": "Update settings"}
  ]
}
```

**参数（更新任务）：**
```json
{
  "action": "update",
  "task_id": 1,
  "status": "completed"
}
```

---

## 集成指南

### Python 示例

```python
import subprocess
import json

def call_tricode(prompt: str):
    proc = subprocess.Popen(
        ['python', 'tricode.py', '--stdio', prompt],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True
    )
    
    for line in proc.stdout:
        msg = json.loads(line)
        
        if msg['type'] == 'tool_call':
            print(f"[Tool] {msg['formatted']}")
        
        elif msg['type'] == 'tool_result':
            if not msg['success']:
                print(f"[Error] {msg['result']}")
        
        elif msg['type'] == 'final':
            print(f"[Result] {msg['content']}")
            return msg['content']
    
    proc.wait()

result = call_tricode("list files in current directory")
```

---

### Node.js 示例

```javascript
const { spawn } = require('child_process');
const readline = require('readline');

function callTricode(prompt) {
  return new Promise((resolve) => {
    const proc = spawn('python', ['tricode.py', '--stdio', prompt]);
    const rl = readline.createInterface({ input: proc.stdout });
    
    let finalResult = null;
    
    rl.on('line', (line) => {
      const msg = JSON.parse(line);
      
      if (msg.type === 'tool_call') {
        console.log(`[Tool] ${msg.formatted}`);
      } else if (msg.type === 'final') {
        finalResult = msg.content;
        console.log(`[Result] ${msg.content}`);
      }
    });
    
    proc.on('close', () => resolve(finalResult));
  });
}

callTricode('list files in current directory').then(result => {
  console.log('Done:', result);
});
```

---

## 错误处理

- **OpenAI API 错误**: 如果 API 密钥未配置或调用失败，会直接输出错误信息到 stdout（非 JSON 格式）
- **工具执行失败**: `tool_result` 消息的 `success` 字段为 `false`，`result` 包含错误详情

---

## 注意事项

1. **逐行解析**: 每行是一个独立的 JSON 对象，使用 JSON Lines 解析器
2. **顺序保证**: 消息按时间顺序输出，`tool_call` 必定先于对应的 `tool_result`
3. **无额外输出**: `--stdio` 模式下，所有输出都是结构化 JSON，无其他杂项信息
4. **编码**: 输出使用 UTF-8 编码，`ensure_ascii=False`
