# Tricode CLI Agent

> Tribbie,Trianne and Trinnon are so cute🥰

A command-line universal agent

## Installation

### Quick Install (Recommended)

One-line installation for pre-built binaries:

**Linux / macOS:**
```bash
curl -sSL https://raw.githubusercontent.com/Trirrin/Tricode-cli/main/install_tricode.sh | bash
```

**Windows (PowerShell):**
```powershell
irm https://raw.githubusercontent.com/Trirrin/Tricode-cli/main/install_tricode.ps1 | iex
```

After installation, restart your terminal and run:
```bash
tricode --help
```

### Update

Keep your `tricode` up to date with the latest release:

```bash
tricode --update
```

Behavior:
- Fetches the latest GitHub release tag.
- Compares with local version shown by `tricode --version`.
- If newer exists, downloads the matching asset for your OS/arch and replaces the current binary.
- Default install location is `~/.local/bin/tricode` when not found in `PATH`.

### Supported Platforms

- **Linux**: x86_64, ARM64
- **macOS**: x86_64 (Intel), ARM64 (Apple Silicon M1/M2)
- **Windows**: x86_64

### Manual Installation

1. Download the binary for your platform from [Releases](https://github.com/Trirrin/Tricode-cli/releases)
2. Extract and move to a directory in your PATH:
   - Linux/macOS: `~/.local/bin/tricode`
   - Windows: Add the directory containing `tricode.exe` to your PATH
3. Make it executable (Linux/macOS only): `chmod +x tricode`

### Install from Source

If you prefer to run from source:

```bash
git clone https://github.com/Trirrin/Tricode-cli.git
cd Tricode-cli
pip install -r requirements.txt
./tricode.py "test"
```

## Features

- **Search Context**: Search for patterns in your codebase
- **Read File**: Read file contents
- **Write File**: Write or modify files
- **Run Commands**: Execute shell commands
- **Interactive Sessions**: Manage persistent shell sessions (SSH, Python REPL, etc.)
- **Web Search**: Search the web using DuckDuckGo with rate limiting and retry logic
- **Fetch URL**: Retrieve and convert web content to Markdown format with security checks
- **Task Planning**: Break down complex tasks into manageable steps
- **Conversation History**: Resume previous sessions and maintain context
- **Tool Whitelisting**: Restrict agent to specific operations for safety
- **Work Directory Restriction**: Limit file access to specific directory for security
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

### TUI Mode (Interactive)

Launch the interactive Text User Interface for continuous conversations:

```bash
./tricode.py --tui
```

Features:
- **Real-time interaction**: Type messages and see agent responses instantly
- **Session management**: Create new sessions or continue existing ones
- **Keyboard shortcuts**:
  - `Enter`: Send message
  - `\` + `Enter`: Insert newline (shell-style line continuation)
  - `Ctrl+C`: Quit application
  - `Ctrl+N`: Create new session
  - `Ctrl+L`: Clear output
- **Visual feedback**: Color-coded tool calls and results

**Example usage**:
```bash
# Start TUI with default settings
./tricode.py --tui

# Start TUI with restricted tools
./tricode.py --tui --tools "read_file,search_context"

# Resume a session in TUI mode
./tricode.py --tui --resume abc123
```

### CLI Mode (Single Command)

```bash
./tricode.py "Find all TODO comments in the codebase"
./tricode.py "Read config.py and summarize the configuration"
./tricode.py "Replace old_name with new_name in all Python files"
```

### Command-Line Options

- `--tui`: Launch interactive TUI (Text User Interface) mode
- `-v, --verbose`: Show detailed execution logs
- `--stdio`: Output all messages in JSON format for programmatic integration
- `--tools <list>`: Comma-separated list of allowed tools (e.g., `read_file,search_context`)
  - Available tools: `search_context`, `read_file`, `create_file`, `edit_file`, `list_directory`, `delete_file`, `delete_path`, `mkdir`, `run_command`, `plan`, `start_session`, `send_input`, `read_output`, `close_session`, `list_sessions`, `web_search`, `fetch_url`
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

#### Work Directory Restriction (Security)

- `--work-dir <PATH>`: Set working directory (default: current directory)
  - Agent can only access files under this path
  - Uses `realpath()` to prevent symlink and `..` escapes
  - Applies to: `read_file`, `create_file`, `edit_file`, `search_context`, `list_directory`
  - **Security note**: `run_command` tool can still execute arbitrary commands
  
- `--bypass-work-directory-limit`: Allow access to files outside the working directory
  - Use with caution - removes all path restrictions
  - Useful for system-wide operations
#### Permission Control (Security Feature)

**Default Behavior: Safe Mode**

All destructive operations (create, edit, delete files, run commands, etc.) require explicit user approval by default.

- `--bypass-permission`: Skip user confirmation for destructive operations (use with caution)
  - ⚠️ **WARNING**: Use only when you fully trust the AI's behavior
  - Use cases: testing, debugging, sandboxed environments
  - Even with this flag, work directory restrictions still apply (unless also using `--bypass-work-directory-limit`)

**User Authorization Options**:

When a destructive operation is requested, you will see:
```
============================================================
⚠️  DESTRUCTIVE OPERATION REQUESTED
============================================================
Tool: create_file
Arguments:
  path: example.txt
  content: Hello World
============================================================
Options:
  1 - Allow this operation (once)           # Single approval
  2 - Allow all future operations of this   # Approve all in this session
      type in this session
  3 - Deny and terminate agent              # Reject and stop
============================================================
Your choice [1/2/3]:
```

**Best Practices**:
- Default to safe mode, review each operation
- Use option 2 only after you trust the agent's plan
- Don't hesitate to choose option 3 if something looks suspicious
- Combine with `--work-dir` to limit scope even in bypass mode

**For more details, see [SECURITY.md](docs/SECURITY.md)**

**Example usage**:
```bash
# Restrict to project directory
./tricode.py --work-dir /path/to/project "refactor the code"

# Restrict to current directory
./tricode.py --work-dir . "find all bugs"

# Allow system-wide access
./tricode.py --work-dir /path/to/project --bypass-work-directory-limit "compare with /etc/config"
```

Tips:
- `read_file` supports `with_metadata=true` to return JSON with `{path, total_lines, mtime, sha256, content}`.
- Prefer simple edits for whole-file changes: `edit_file(mode='overwrite'|'append'|'prepend', content=...)`.
- Use patch mode for precise anchor-based changes: `edit_file(mode='patch', hunks=[...])`. `precondition.file_sha256` is optional but recommended when race conditions matter.

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

Restricted access:
```bash
# Only allow agent to access files in the current project
./tricode.py --work-dir . "analyze the codebase structure"

# Prevent accidental modifications outside project
./tricode.py --work-dir ~/myproject "refactor all Python files"
```

## Architecture

```
tricode-cli/
├── tricode.py          # CLI entry point
├── agent/
│   ├── core.py         # Agent loop with OpenAI integration
│   ├── tools.py        # Tool implementations (search/read/write)
│   ├── config.py       # Configuration management
│   └── output.py       # Output formatting (human/JSON)
└── requirements.txt

User config: ~/.tricode/settings.json
Conversations: ~/.tricode/session/
Plans: ~/.tricode/plans/
```

## Implementation Details

- Agent runs until the task is completed, with no hard round limit (this line edited by edit tool).
- Search uses ripgrep (rg) if available, falls back to Python regex
- File writes are atomic (temp file + rename)
- Path validation uses `os.path.realpath()` to prevent escapes
- Sessions auto-expire after 30s idle or 5min total
- Conversation history stored as JSON for resumption
