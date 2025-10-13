# Tricode CLI Agent

一个由 OpenAI 驱动的自主命令行文件操作助手。

## 功能特性

- **搜索上下文**：在代码库中搜索指定模式
- **读取文件**：读取文件内容
- **写入文件**：写入或修改文件
- **运行命令**：执行 Shell 命令
- **交互式会话**：管理持久化 Shell 会话（SSH、Python REPL 等）
- **网页搜索**：使用 DuckDuckGo 进行网页搜索，带有速率限制和重试机制
- **获取网页**：获取并转换网页内容为 Markdown 格式，包含安全检查
- **任务规划**：将复杂任务分解为可管理的步骤
- **对话历史**：恢复之前的会话并保持上下文
- **工具白名单**：限制 Agent 使用特定操作以提升安全性
- **工作目录限制**：限制文件访问到特定目录以增强安全性
- **主动智能**：Agent 主动探索并使用工具完成任务
  - 当路径不明确时搜索文件
  - 遇到错误时尝试替代方案
  - 在进行更改前先验证

## 快速开始

1. **安装依赖**:
```bash
pip install -r requirements.txt
```

2. **首次运行**（会自动创建配置文件）:
```bash
./tricode.py "test"
```

3. **编辑配置** 以添加你的 API Key:
```bash
nano ~/.tricode/settings.json
```

4. **开始使用**:
```bash
./tricode.py "Find all TODO comments"
```

## 配置说明

配置保存在 `~/.tricode/settings.json`。

首次运行时，默认配置文件会生成在 `~/.tricode/settings.json`。你可以通过以下命令编辑它：

```bash
nano ~/.tricode/settings.json
```

```json
{
  "openai_api_key": "sk-your-api-key-here",
  "openai_base_url": "https://api.openai.com/v1",
  "openai_model": "gpt-4o-mini"
}
```

### 配置选项

- `openai_api_key`：你的 OpenAI API 密钥（必填）
- `openai_base_url`：自定义 API 端点（可选，默认为 OpenAI 官方 API）
- `openai_model`：所使用的模型（可选，默认为 gpt-4o-mini）

### 环境变量覆盖

所有配置项都可以通过环境变量覆盖，环境变量优先级高于 `settings.json`。

环境变量命名规则：`TRICODE_` + 大写配置项名

```bash
export TRICODE_OPENAI_API_KEY="sk-your-api-key"
export TRICODE_OPENAI_BASE_URL="https://api.openai.com/v1"
export TRICODE_OPENAI_MODEL="gpt-4o"
```

优先级：**环境变量 > settings.json > 默认值**

## 使用方法

### TUI 模式（交互式）

启动交互式文本用户界面进行持续对话：

```bash
./tricode.py --tui
```

功能特性：
- **实时交互**：输入消息并即时查看 Agent 响应
- **会话管理**：创建新会话或继续现有会话
- **快捷键**：
  - `Enter`：发送消息
  - `\` + `Enter`：插入换行符（Shell 风格的行延续）
  - `Ctrl+C`：退出应用
  - `Ctrl+N`：创建新会话
  - `Ctrl+L`：清空输出
- **视觉反馈**：彩色显示工具调用和结果

**使用示例**：
```bash
# 使用默认设置启动 TUI
./tricode.py --tui

# 使用受限工具启动 TUI
./tricode.py --tui --tools "read_file,search_context"

# 在 TUI 模式下恢复会话
./tricode.py --tui --resume abc123
```

### CLI 模式（单次命令）

```bash
./tricode.py "Find all TODO comments in the codebase"
./tricode.py "Read config.py and summarize the configuration"
./tricode.py "Replace old_name with new_name in all Python files"
```

### 命令行参数

- `--tui`：启动交互式 TUI（文本用户界面）模式
- `-v, --verbose`：显示详细执行日志
- `--stdio`：以 JSON 格式输出所有消息，便于程序化集成
- `--tools <list>`：逗号分隔的允许工具列表（例如 `read_file,search_context`）
  - 可用工具：`search_context`、`read_file`、`create_file`、`edit_file`、`list_directory`、`delete_file`、`delete_path`、`mkdir`、`run_command`、`plan`、`start_session`、`send_input`、`read_output`、`close_session`、`list_sessions`、`web_search`、`fetch_url`
  - 如果未指定，则所有工具都可用
  - 注意：`plan` 工具会自动包含（Agent 运行必需）
  - Agent 只能看到和使用白名单内的工具；系统提示词会动态调整
  - 智能限制检测：当由于缺少工具而无法完成任务时，Agent 会告知你
  - 使用场景：
    - 仅读取操作：`--tools "read_file,search_context,list_directory"`
    - 代码生成：`--tools "read_file,create_file,edit_file"`
    - 命令执行：`--tools "run_command,read_file"`
- `--override-system-prompt`：用 AGENTS.md 内容替换默认系统提示词
- `-r, --resume <SESSION_ID>`：恢复之前的对话会话
- `-l, --list-conversations`：列出所有可用的对话会话

#### 工作目录限制（安全特性）

- `--work-dir <PATH>`：设置工作目录（默认：当前目录）
  - Agent 只能访问此路径下的文件
  - 使用 `realpath()` 防止符号链接和 `..` 逃逸
  - 适用于：`read_file`、`create_file`、`edit_file`、`search_context`、`list_directory`
  - **安全提示**：`run_command` 工具仍可执行任意命令
  
- `--bypass-work-directory-limit`：允许访问工作目录外的文件
  - 谨慎使用 - 移除所有路径限制
  - 适用于系统级操作

**使用示例**：
```bash
# 限制到项目目录
./tricode.py --work-dir /path/to/project "重构代码"

# 限制到当前目录
./tricode.py --work-dir . "查找所有 bug"

# 允许系统级访问
./tricode.py --work-dir /path/to/project --bypass-work-directory-limit "与 /etc/config 进行比较"
```

## 使用示例

搜索指定模式：
```bash
./tricode.py "Find all functions named 'execute' in the project"
```

读取和分析：
```bash
./tricode.py "Read agent/core.py and explain the main logic"
# Agent 会搜索 'core' 来找到 agent/core.py
```

修改文件：
```bash
./tricode.py "Add a docstring to the run_agent function in agent/core.py"
# Agent 会搜索 README，读取它，然后修改它
```

智能错误处理：
```bash
./tricode.py "Read the config file"
# 如果 'config' 有歧义，Agent 会搜索所有配置文件
# 然后询问或选择最相关的一个
```

限制访问：
```bash
# 只允许 Agent 访问当前项目中的文件
./tricode.py --work-dir . "分析代码库结构"

# 防止意外修改项目外的文件
./tricode.py --work-dir ~/myproject "重构所有 Python 文件"
```

## 项目结构

```
tricode-cli/
├── tricode.py          # CLI 入口
├── agent/
│   ├── core.py         # 与 OpenAI 集成的 Agent 主循环
│   ├── tools.py        # 工具实现（搜索/读取/写入）
│   ├── config.py       # 配置管理
│   └── output.py       # 输出格式化（人类可读/JSON）
└── requirements.txt

用户配置：~/.tricode/settings.json
对话历史：~/.tricode/session/
任务计划：~/.tricode/plans/
```

## 实现细节

- Agent 会自动运行直到任务完成（无轮数限制）
- 搜索优先使用 ripgrep (rg)，无法使用时退回 Python 正则
- 文件写入为原子操作（先写临时文件再重命名）
- 路径验证使用 `os.path.realpath()` 防止逃逸
- 会话在 30 秒空闲或 5 分钟后自动过期
- 对话历史以 JSON 格式存储，可恢复
