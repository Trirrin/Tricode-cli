# Security Features

## Permission System

Tricode-cli implements a robust permission system to protect against unintended destructive operations.

### Default Behavior: Safe Mode

By default, Tricode-cli operates in **Safe Mode**, where all potentially destructive operations require explicit user approval before execution.

### Destructive Operations

The following operations are considered destructive and require user permission:

- `create_file` - Creating new files
- `edit_file` - Modifying existing files
- `delete_file` - Deleting files
- `delete_path` - Deleting files or directories
- `mkdir` - Creating directories
- `run_command` - Executing shell commands
- `start_session` - Starting interactive sessions
- `send_input` - Sending input to sessions
- `close_session` - Closing sessions

### User Options

When a destructive operation is requested, the user is presented with three options:

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
  1 - Allow this operation (once)
  2 - Allow all future operations of this type in this session
  3 - Deny and terminate agent
============================================================
Your choice [1/2/3]:
```

#### Option 1: Single Approval
- Allows only this specific operation
- Next operation of the same type will require approval again
- Recommended for careful review of each action

#### Option 2: Session-wide Approval
- Approves all future operations of this tool type
- Approval lasts for the current session only
- Useful when you trust the agent's planned actions
- New sessions reset all approvals

#### Option 3: Deny and Terminate
- Immediately blocks the operation
- Terminates the agent execution
- Use when you detect unwanted behavior

### Bypassing Permission Checks

**⚠️ WARNING: Use with extreme caution**

You can disable permission checks with the `--bypass-permission` flag:

```bash
tricode "Create a new config file" --bypass-permission
```

This flag should only be used when:
- You fully trust the AI model's behavior
- You are testing or debugging
- You are running in a sandboxed environment
- You have reviewed the planned operations

### Examples

#### Safe Mode (Default)
```bash
# User will be prompted for each destructive operation
tricode "Create test.txt and run tests"
```

#### Bypass Mode (Dangerous)
```bash
# All operations execute without prompts
tricode "Create test.txt and run tests" --bypass-permission
```

#### With Work Directory Restrictions
```bash
# Safe mode + directory restrictions
tricode "Clean up old files" --work-dir /path/to/project

# Bypass permission but keep directory restrictions
tricode "Clean up old files" --work-dir /path/to/project --bypass-permission

# Bypass everything (very dangerous!)
tricode "Clean up old files" --bypass-work-directory-limit --bypass-permission
```

### Best Practices

1. **Default to Safe Mode**: Always use the default safe mode unless you have a specific reason not to
2. **Review Carefully**: Read the operation details before approving
3. **Use Option 1 Initially**: Start with single approvals to verify behavior
4. **Session Approval Wisely**: Only use option 2 after you trust the agent's plan
5. **Don't Hesitate to Deny**: If something looks wrong, choose option 3
6. **Combine with Work Directory**: Use `--work-dir` to limit scope even in bypass mode
7. **Test in Safe Environment**: Test with `--bypass-permission` only in sandboxed environments

### Implementation Details

- Permission state is stored per session
- Session approvals are cleared when a new session starts
- Permissions cannot be escalated once denied
- Denial terminates the agent immediately with `sys.exit(1)`
- Permission checks occur before plan validation
- Bypass flag must be explicitly set on command line

### Security Considerations

The permission system provides defense-in-depth:

1. **First Layer**: Work directory restrictions (default enabled)
2. **Second Layer**: Permission checks (default enabled)
3. **Third Layer**: User review of each operation

Even with `--bypass-permission`, you still have:
- Work directory restrictions (unless also bypassed)
- Model's built-in safety features
- Operating system permissions
- File system security

However, no system is perfect. Always:
- Run Tricode-cli with appropriate OS user permissions
- Keep your AI provider API keys secure
- Review code before deploying to production
- Use version control to track changes
- Maintain backups of important data
