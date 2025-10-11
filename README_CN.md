# Tricode CLI Agent

一个极简的、由 OpenAI 驱动的 AI 命令行文件操作助手。

## 功能特性

- **搜索上下文**：在代码库中搜索指定模式
- **读取文件**：读取文件内容
- **写入文件**：写入或修改文件

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

或者参考 `.tricode-settings.example.json` 文件。

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

```bash
./tricode.py "Find all TODO comments in the codebase"
./tricode.py "Read config.py and summarize the configuration"
./tricode.py "Replace old_name with new_name in all Python files"
```

### 参数选项

- `-v, --verbose`：显示详细执行日志

## 使用示例

搜索指定模式：
```bash
./tricode.py "Find all functions named 'execute' in the project"
```

读取和分析：
```bash
./tricode.py "Read agent/core.py and explain the main logic"
```

修改文件：
```bash
./tricode.py "Add a docstring to the run_agent function in agent/core.py"
```

## 项目结构

```
tricode-cli/
├── tricode.py          # CLI 入口
├── agent/
│   ├── core.py         # 与 OpenAI 集成的 Agent 主循环
│   ├── tools.py        # 工具实现（搜索/读取/写入）
│   └── config.py       # 配置管理
└── requirements.txt

用户配置：~/.tricode/settings.json
```

## 实现细节

- Agent 会自动运行直到任务完成（无轮数限制）
- 搜索优先使用 ripgrep (rg)，无法使用时退回 Python 正则
- 文件写入为原子操作（先写临时文件再重命名）
