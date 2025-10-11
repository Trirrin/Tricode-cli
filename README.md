# Tricode CLI Agent

## Features

- **Search Context**: Search for patterns in your codebase
- **Read File**: Read file contents
- **Write File**: Write or modify files
- **Proactive Intelligence**: Agent actively explores and uses tools to complete tasks
  - Searches for files when paths are unclear
  - Tries alternative approaches when errors occur
  - Verifies before making changes
- **Proactive Intelligence**: Agent actively explores and uses tools to complete tasks
  - Searches for files when paths are unclear
  - Tries alternative approaches when errors occur
  - Verifies before making changes
- **Read File**: Read file contents
- **Write File**: Write or modify files
- **Proactive Intelligence**: Agent actively explores and uses tools to complete tasks
  - Searches for files when paths are unclear
  - Tries alternative approaches when errors occur
  - Verifies before making changes
- **Proactive Intelligence**: Agent actively explores and uses tools to complete tasks
  - Searches for files when paths are unclear
  - Tries alternative approaches when errors occur
  - Verifies before making changes

## Quick Start

1. **Install dependencies**:
```bash
pip install -r requirements.txt
```

2. **Run for the first time** (creates config):
```bash
./tricode.py "test"
```

3. **Edit config** to add your API key:
```bash
nano ~/.tricode/settings.json
```

4. **Start using**:
```bash
./tricode.py "Find all TODO comments"
```

## Configuration

Configuration is stored in `~/.tricode/settings.json`.

On first run, a default config file will be created at `~/.tricode/settings.json`. Edit it to add your settings:

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

Or see `.tricode-settings.example.json` for reference.

### Configuration Options

- `openai_api_key`: Your OpenAI API key (required)
- `openai_base_url`: Custom API endpoint (optional, defaults to OpenAI's official API)
- `openai_model`: Model to use (optional, defaults to gpt-4o-mini)

### Environment Variable Override

All configuration options can be overridden via environment variables, which take precedence over `settings.json`.

Environment variable naming: `TRICODE_` + UPPERCASE config key

```bash
export TRICODE_OPENAI_API_KEY="sk-your-api-key"
export TRICODE_OPENAI_BASE_URL="https://api.openai.com/v1"
export TRICODE_OPENAI_MODEL="gpt-4o"
```

Priority: **Environment Variables > settings.json > Defaults**

## Usage

```bash
./tricode.py "Find all TODO comments in the codebase"
./tricode.py "Read config.py and summarize the configuration"
./tricode.py "Replace old_name with new_name in all Python files"
```

### Options

- `-v, --verbose`: Show detailed execution logs
- `--stdio`: Output all messages in JSON format for programmatic integration
- `--tools <list>`: Comma-separated list of allowed tools (e.g., `read_file,search_context`)
  - Available tools: `search_context`, `read_file`, `create_file`, `edit_file`, `list_directory`, `run_command`, `plan`, `start_session`, `send_input`, `read_output`, `close_session`, `list_sessions`
  - If not specified, all tools are available
  - Note: `plan` tool is automatically included (required for agent operation)
  - Agent only sees and uses whitelisted tools; system prompt adapts dynamically
  - Smart limitation detection: Agent will tell you when a task cannot be completed due to missing tools
  - Use cases:
    - Only read operations: `--tools "read_file,search_context,list_directory"`
    - Code generation: `--tools "read_file,create_file,edit_file"`
    - Command execution: `--tools "run_command,read_file"`
- `--override-system-prompt`: Replace default system prompt with AGENTS.md content
- `-r, --resume <SESSION_ID>`: Resume a previous conversation session
- `-l, --list-conversations`: List all available conversation sessions

## Examples

Search for patterns:
```bash
./tricode.py "Find all functions named 'execute' in the project"
```

Read and analyze:
```bash
./tricode.py "Read the core agent file and explain the main logic"
# Agent will search for 'core' to find agent/core.py
```

Modify files:
```bash
./tricode.py "Update the README with new installation instructions"
# Agent will search for README, read it, then modify it
```

Smart error handling:
```bash
./tricode.py "Read the config file"
# If 'config' is ambiguous, agent searches for all config files
# Then asks or chooses the most relevant one
```

## Architecture

```
tricode-cli/
├── tricode.py          # CLI entry point
├── agent/
│   ├── core.py         # Agent loop with OpenAI integration
│   ├── tools.py        # Tool implementations (search/read/write)
│   └── config.py       # Configuration management
└── requirements.txt

User config: ~/.tricode/settings.json
```

## Implementation Details

- Agent runs until task completion (no round limit)
- Search uses ripgrep (rg) if available, falls back to Python regex
- File writes are atomic (temp file + rename)
- **Write File**: Write or modify files
- **Proactive Intelligence**: Agent actively explores and uses tools to complete tasks
  - Searches for files when paths are unclear
  - Tries alternative approaches when errors occur
  - Verifies before making changes

## Quick Start

1. **Install dependencies**:
```bash
pip install -r requirements.txt
```

2. **Run for the first time** (creates config):
```bash
./tricode.py "test"
```

3. **Edit config** to add your API key:
```bash
nano ~/.tricode/settings.json
```

4. **Start using**:
```bash
./tricode.py "Find all TODO comments"
```

## Configuration

Configuration is stored in `~/.tricode/settings.json`.

On first run, a default config file will be created at `~/.tricode/settings.json`. Edit it to add your settings:

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

Or see `.tricode-settings.example.json` for reference.

### Configuration Options

- `openai_api_key`: Your OpenAI API key (required)
- `openai_base_url`: Custom API endpoint (optional, defaults to OpenAI's official API)
- `openai_model`: Model to use (optional, defaults to gpt-4o-mini)

### Environment Variable Override

All configuration options can be overridden via environment variables, which take precedence over `settings.json`.

Environment variable naming: `TRICODE_` + UPPERCASE config key

```bash
export TRICODE_OPENAI_API_KEY="sk-your-api-key"
export TRICODE_OPENAI_BASE_URL="https://api.openai.com/v1"
export TRICODE_OPENAI_MODEL="gpt-4o"
```

Priority: **Environment Variables > settings.json > Defaults**

## Usage

```bash
./tricode.py "Find all TODO comments in the codebase"
./tricode.py "Read config.py and summarize the configuration"
./tricode.py "Replace old_name with new_name in all Python files"
```

### Options

- `-v, --verbose`: Show detailed execution logs
- `--stdio`: Output all messages in JSON format for programmatic integration
- `--tools <list>`: Comma-separated list of allowed tools (e.g., `read_file,search_context`)
  - Available tools: `search_context`, `read_file`, `create_file`, `edit_file`, `list_directory`, `run_command`, `plan`, `start_session`, `send_input`, `read_output`, `close_session`, `list_sessions`
  - If not specified, all tools are available
  - Note: `plan` tool is automatically included (required for agent operation)
  - Agent only sees and uses whitelisted tools; system prompt adapts dynamically
  - Smart limitation detection: Agent will tell you when a task cannot be completed due to missing tools
  - Use cases:
    - Only read operations: `--tools "read_file,search_context,list_directory"`
    - Code generation: `--tools "read_file,create_file,edit_file"`
    - Command execution: `--tools "run_command,read_file"`
- `--override-system-prompt`: Replace default system prompt with AGENTS.md content
- `-r, --resume <SESSION_ID>`: Resume a previous conversation session
- `-l, --list-conversations`: List all available conversation sessions

## Examples

Search for patterns:
```bash
./tricode.py "Find all functions named 'execute' in the project"
```

Read and analyze:
```bash
./tricode.py "Read the core agent file and explain the main logic"
# Agent will search for 'core' to find agent/core.py
```

Modify files:
```bash
./tricode.py "Update the README with new installation instructions"
# Agent will search for README, read it, then modify it
```

Smart error handling:
```bash
./tricode.py "Read the config file"
# If 'config' is ambiguous, agent searches for all config files
# Then asks or chooses the most relevant one
```

## Architecture

```
tricode-cli/
├── tricode.py          # CLI entry point
├── agent/
│   ├── core.py         # Agent loop with OpenAI integration
│   ├── tools.py        # Tool implementations (search/read/write)
│   └── config.py       # Configuration management
└── requirements.txt

User config: ~/.tricode/settings.json
```

## Implementation Details

- Agent runs until task completion (no round limit)
- Search uses ripgrep (rg) if available, falls back to Python regex
- File writes are atomic (temp file + rename)
