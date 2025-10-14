# Tricode STDIO API 文档

## 概述

使用 `--stdio` 参数启动 Tricode 时，所有输出以 JSON Lines 格式（每行一个 JSON 对象）输出到 stdout，便于第三方程序解析和集成。

## 启动方式

### 基本用法

```bash
tricode --stdio "你的指令"
```

### 限制可用工具

通过 `--tools` 参数可以指定 Agent 可以使用的工具白名单：

```bash
tricode --stdio --tools "read_file,search_context,plan" "你的指令"
```

**工具名称列表：**
- `search_context` - 搜索文件内容
- `read_file` - 读取文件
- `create_file` - 创建文件
- `edit_file` - 编辑文件
- `list_directory` - 列出目录
- `run_command` - 执行 shell 命令
- `plan` - 任务计划管理
- `start_session` - 启动交互式会话
- `send_input` - 向会话发送输入
- `read_output` - 读取会话输出
- `close_session` - 关闭会话
- `list_sessions` - 列出活动会话

**注意事项：**
- 工具名称使用逗号分隔，不要有空格
- 如果不指定 `--tools` 参数，则所有工具都可用
- `plan` 工具会自动包含在白名单中（即使未显式指定），因为它是 Agent 工作的必要前提
- Agent 只能看到和使用白名单中的工具，不会知道其他工具的存在
- 系统提示词会根据可用工具动态调整，只提及白名单中的工具
- **智能工具检测**：当用户的请求需要白名单中不存在的工具时，Agent 会主动告知用户任务无法完成，并说明缺少哪些必要工具

**使用场景：**
- 只读操作：`--tools "read_file,search_context,list_directory,plan"`
- 代码生成：`--tools "read_file,create_file,edit_file,plan"`
- 命令执行：`--tools "run_command,read_file,plan"`

## 消息类型

所有消息都是单行 JSON 对象，包含 `type` 字段标识消息类型。

### 1. system - 系统消息

系统级别的通知消息，包括会话 ID、会话恢复提示等。

```json
{"type": "system", "message": "Session ID: 2bc53e39"}
```

**字段说明：**
- `message` (string): 系统消息内容

**重要说明：**
- Session ID 会在每次对话开始时的第一条消息输出
- Session ID 可用于通过 `-r/--resume` 参数恢复之前的对话
- 格式为 8 位随机字符串，会话数据保存在 `~/.tricode/session/` 目录

---

### 2. round - 执行轮次

标识 Agent 进入新的思考/执行轮次。

```json
{"type": "round", "number": 1}
```

**字段说明：**
- `number` (int): 轮次编号，从 1 开始递增

---

### 3. tool_call - 工具调用

Agent 调用某个工具时触发。

```json
{
  "type": "tool_call",
  "name": "read_file",
  "arguments": {"path": "tricode.py", "start_line": 1, "end_line": 10},
  "formatted": "READ(\"tricode.py\", start=1, end=10)"
}
```

**字段说明：**
- `name` (string): 工具名称（search_context / read_file / create_file / edit_file / list_directory / plan）
- `arguments` (object): 工具的原始参数
- `formatted` (string): 人类可读的工具调用描述

---

### 4. tool_result - 工具执行结果

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

### 5. reminder - 警告/提醒

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

### 6. final - 最终响应

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
{"type": "system", "message": "Session ID: 2bc53e39"}
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


### delete_file
删除单个文件或符号链接。

**参数：**
```json
{"path": "path/to/file.txt"}
```

**说明：**
- 只能删除文件和符号链接，不能用于目录。
- 删除成功或返回错误原因。


### delete_path
递归删除文件或目录。

**参数：**
```json
{"path": "path/to/file_or_dir", "recursive": true}
```

**说明：**
- 如果目标是目录，必须指定 `"recursive": true` 否则报错。
- 能安全删除非空目录。


### mkdir
创建目录，可递归创建父目录。

**参数：**
```json
{"path": "path/to/newdir", "parents": true, "exist_ok": false}
```

**说明：**
- `parents` 默认true，允许递归创建父目录。
- `exist_ok` 控制已存在时是否报错，默认false（即存在时报错）。

### read_file
读取文件内容（默认整文件）。支持行窗口与字节上限。

**参数：**
```json
{"path": "tricode.py", "start_line": 1, "end_line": 10, "max_bytes": 4096, "with_metadata": false}
```
- `start_line`/`end_line` 可选，1 基，包含端点；未提供时读全文件。
- `max_bytes` 可选，按 UTF-8 字节截断（安全丢弃半个字符）。
- `with_metadata` 可选，默认为 `false`。为 `true` 时返回 JSON 对象：
  ```json
  {
    "path": "/abs/path/tricode.py",
    "total_lines": 47,
    "mtime": "2025-01-01T12:00:00",
    "sha256": "...",
    "content": "...file content..."
  }
  ```
  可与 `edit_file.precondition.file_sha256` 搭配以提升安全性（可选）。

---

### create_file
创建新文件。

**参数：**
```json
{"path": "new_file.py", "content": "print('hello')"}
```

---

### edit_file
两种模式：
- 简单模式（推荐）：整文件覆盖/追加/前置，最稳健，最省 token。
- Patch 模式：基于锚点的精确编辑（exact/regex），适合局部修改。

简单模式参数示例：
```json
{"path": "README.md", "mode": "overwrite", "content": "# Title\nNew content..."}
```

Patch 模式参数示例：
```json
{
  "path": "tricode.py",
  "mode": "patch",
  "hunks": [
    {"op": "replace", "anchor": {"type": "exact", "pattern": "def main():"}, "content": "def main():\n    print('hi')\n"},
    {"op": "insert_after", "anchor": {"type": "regex", "pattern": "^class Runner\\(.*\\):$", "occurrence": "first"}, "content": "\n    def ping(self):\n        return 'pong'\n"}
  ],
  "precondition": {"file_sha256": "<optional sha256>"},
  "dry_run": false
}
```

说明：
- `mode` 缺省为 `patch`。简单模式必须提供 `content`；`overwrite` 支持新建文件。
- `precondition.file_sha256` 为可选；若提供且不匹配则拒绝写入。
- `anchor.nth`（1-based）用于选择第 n 个匹配；提供 `nth` 或 `occurrence: "last"` 时不强制唯一匹配。

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
