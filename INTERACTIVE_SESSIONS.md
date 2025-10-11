# Interactive Session Management

Tricode CLI now supports interactive shell sessions for persistent command execution. This is useful for scenarios like SSH connections, Python REPLs, Docker containers, and other long-running interactive processes.

## Features

- **Persistent Sessions**: Start shell sessions that remain active across multiple commands
- **Non-blocking IO**: Read output without blocking the agent
- **Auto-cleanup**: Sessions automatically expire after 30s of inactivity or 5 minutes total
- **Session Limits**: Maximum 3 concurrent sessions to prevent resource exhaustion
- **Output Buffering**: Output limited to 4KB per read to prevent memory issues

## Available Tools

### 1. start_session

Start an interactive shell session.

**Parameters:**
- `command` (required): Initial command to execute (e.g., "ssh user@host", "python3 -u", "docker exec -it container bash")
- `shell` (optional): Shell to use for the session (default: "/bin/bash")

**Returns:** Session ID for subsequent operations

**Example:**
```json
{
  "name": "start_session",
  "arguments": {
    "command": "python3 -u"
  }
}
```

### 2. send_input

Send input/command to an active session.

**Parameters:**
- `session_id` (required): The session ID from start_session
- `input_text` (required): Text to send to the session stdin

**Example:**
```json
{
  "name": "send_input",
  "arguments": {
    "session_id": "a1b2c3d4",
    "input_text": "ls -la"
  }
}
```

### 3. read_output

Read output from an active session.

**Parameters:**
- `session_id` (required): The session ID from start_session
- `timeout` (optional): Maximum time to wait for output in seconds (default: 2)

**Returns:** Output collected from the session (stdout + stderr)

**Example:**
```json
{
  "name": "read_output",
  "arguments": {
    "session_id": "a1b2c3d4",
    "timeout": 3
  }
}
```

### 4. close_session

Close an active session and clean up resources.

**Parameters:**
- `session_id` (required): The session ID to close

**Example:**
```json
{
  "name": "close_session",
  "arguments": {
    "session_id": "a1b2c3d4"
  }
}
```

### 5. list_sessions

List all active sessions with their status.

**Returns:** List of active sessions with age, idle time, and status

**Example:**
```json
{
  "name": "list_sessions",
  "arguments": {}
}
```

## Usage Patterns

### Pattern 1: Execute Multiple Commands in a Shell

```
1. start_session("") - Start a bash session
2. send_input(session_id, "cd /path/to/dir")
3. read_output(session_id)
4. send_input(session_id, "ls -la")
5. read_output(session_id)
6. close_session(session_id)
```

### Pattern 2: Python Code Execution

```
1. start_session("python3 -c 'code here'") - Execute Python directly
   OR
   start_session("") then send_input(session_id, "python3 -c 'code'")
2. read_output(session_id)
3. close_session(session_id)
```

### Pattern 3: Docker Container Operations

```
1. start_session("docker exec -i container_name bash")
2. send_input(session_id, "apt update")
3. read_output(session_id)
4. send_input(session_id, "apt install -y package")
5. read_output(session_id)
6. close_session(session_id)
```

## Important Notes

1. **Always close sessions**: Use `close_session` when done to prevent resource leaks
2. **Wait for output**: Add delays between send_input and read_output to allow commands to complete
3. **Timeout management**: Sessions auto-expire after 30s idle or 5 minutes total
4. **Output buffering**: Output is limited to 4KB per read
5. **No PTY**: Sessions use PIPE, not PTY - some interactive programs may not work (e.g., vim, nano)
6. **Python REPL limitation**: Python interactive mode requires PTY for prompts - use `-c` option instead

## Limitations

- **No interactive TUI**: Programs requiring terminal control (vim, top, htop) won't work
- **No password prompts**: SSH with password won't work - use key-based auth
- **Buffering issues**: Some programs may buffer output in non-TTY mode
- **Max 3 sessions**: Only 3 concurrent sessions allowed

## Best Practices

1. Use `run_command` for simple one-off commands
2. Use sessions only when you need persistent state across multiple commands
3. Always handle timeouts gracefully
4. Check session status with `list_sessions` before operations
5. For Python, prefer `-c` option over interactive REPL
