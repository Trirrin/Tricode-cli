#!/usr/bin/env python

import argparse
import sys
import os
import platform
import tempfile
import json
import urllib.request
import shutil
import tarfile
import zipfile
from agent import run_agent, list_conversations
from agent.tui import run_tui

try:
    from version import get_runtime_version, get_full_version_string, __version__, __commit_id__
except ImportError:
    # Fallback for development mode without build
    __version__ = "dev"
    __commit_id__ = "unknown"
    def get_runtime_version():
        return f"Tricode-cli {__version__} (git-{__commit_id__})"
    def get_full_version_string():
        return f"Tricode-cli {__version__} (git-{__commit_id__})"


class VersionedHelpFormatter(argparse.RawDescriptionHelpFormatter):
    """Custom help formatter that includes version info in the header unless just showing version"""
    def format_help(self):
        # Suppress version at top if only --version is being executed
        if '--version' in sys.argv or '-V' in sys.argv:
            return super().format_help()
        help_text = super().format_help()
        version_line = f"{get_runtime_version()}\n\n"
        return version_line + help_text


REPO = "Trirrin/Tricode-cli"


def _semver_tuple(v: str):
    """Convert version string like 'v1.2.3' to tuple(1,2,3)."""
    s = v.strip()
    if s.startswith(('v', 'V')):
        s = s[1:]
    parts = s.split('.')
    out = []
    for p in parts:
        try:
            out.append(int(p))
        except ValueError:
            # Fallback for non-numeric parts
            num = ''.join(ch for ch in p if ch.isdigit())
            out.append(int(num) if num else 0)
    while len(out) < 3:
        out.append(0)
    return tuple(out[:3])


def _detect_asset_suffix():
    """Map current OS/arch to release asset suffix."""
    system = platform.system().lower()
    machine = platform.machine().lower()
    if system == 'linux':
        if machine in ('x86_64', 'amd64'):
            return 'linux-x86_64'
        if machine in ('aarch64', 'arm64'):
            return 'linux-arm64'
        raise RuntimeError(f"Unsupported Linux architecture: {machine}")
    if system == 'darwin':
        if machine == 'x86_64':
            return 'macos-x86_64'
        if machine == 'arm64':
            return 'macos-arm64'
        raise RuntimeError(f"Unsupported macOS architecture: {machine}")
    raise RuntimeError(f"Unsupported OS: {system}")


def _fetch_latest_release():
    """Fetch latest release JSON from GitHub API."""
    url = f"https://api.github.com/repos/{REPO}/releases/latest"
    with urllib.request.urlopen(url, timeout=15) as resp:
        data = resp.read().decode('utf-8')
    return json.loads(data)


def _find_asset_url(assets, suffix: str):
    """Find asset url that contains required suffix."""
    for a in assets:
        url = a.get('browser_download_url') or ''
        name = a.get('name') or ''
        if f"tricode-{suffix}" in url or f"tricode-{suffix}" in name:
            return url
    return None


def _extract_binary(archive_path: str, workdir: str) -> str:
    """Extract downloaded archive and return path to binary."""
    base = os.path.basename(archive_path)
    if base.endswith('.tar.gz'):
        with tarfile.open(archive_path, 'r:gz') as tf:
            tf.extractall(workdir)
        # Try to locate binary
        for root, _dirs, files in os.walk(workdir):
            for f in files:
                if f == 'tricode' or f == 'tricode.exe':
                    return os.path.join(root, f)
        raise RuntimeError('Binary not found in tarball')
    if base.endswith('.zip'):
        with zipfile.ZipFile(archive_path) as zf:
            zf.extractall(workdir)
        for root, _dirs, files in os.walk(workdir):
            for f in files:
                if f == 'tricode' or f == 'tricode.exe':
                    return os.path.join(root, f)
        raise RuntimeError('Binary not found in zip')
    # Direct binary
    return archive_path


def _resolve_target_path() -> str:
    """Resolve current installation target path for replacement."""
    # Prefer the running executable if it looks like the installed binary
    exec_path = shutil.which('tricode')
    if exec_path and os.path.isfile(exec_path):
        return exec_path
    # Fallback to default install location
    return os.path.join(os.path.expanduser('~/.local/bin'), 'tricode')


def update_self(current_version: str):
    """Self-update by downloading latest release and replacing binary."""
    try:
        latest = _fetch_latest_release()
    except Exception as e:
        print(f"[ERROR] Failed to query GitHub releases: {e}")
        sys.exit(2)

    tag = latest.get('tag_name') or ''
    if not tag:
        print('[ERROR] Latest release tag not found')
        sys.exit(2)

    try:
        cur = _semver_tuple(current_version)
        lat = _semver_tuple(tag)
    except Exception:
        cur = (0, 0, 0)
        lat = (0, 0, 0)

    if lat <= cur:
        print(f"Already up-to-date (current {current_version}, latest {tag}).")
        return

    try:
        suffix = _detect_asset_suffix()
    except Exception as e:
        print(f"[ERROR] {e}")
        sys.exit(2)

    assets = latest.get('assets') or []
    url = _find_asset_url(assets, suffix)
    if not url:
        print(f"[ERROR] No matching asset for '{suffix}'.")
        sys.exit(2)

    target_path = _resolve_target_path()
    install_dir = os.path.dirname(target_path)
    os.makedirs(install_dir, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix='tricode-update-') as td:
        archive_path = os.path.join(td, os.path.basename(url))
        try:
            with urllib.request.urlopen(url, timeout=60) as resp, open(archive_path, 'wb') as f:
                shutil.copyfileobj(resp, f)
        except Exception as e:
            print(f"[ERROR] Download failed: {e}")
            sys.exit(3)

        try:
            bin_path = _extract_binary(archive_path, td)
        except Exception as e:
            print(f"[ERROR] Extract failed: {e}")
            sys.exit(4)

        final_tmp = os.path.join(td, 'tricode.new')
        shutil.copy2(bin_path, final_tmp)
        try:
            os.chmod(final_tmp, 0o755)
        except Exception:
            pass

        try:
            # Atomic replace
            os.replace(final_tmp, target_path)
        except PermissionError:
            print(f"[ERROR] Permission denied writing to {target_path}. Try with proper permissions.")
            sys.exit(5)
        except Exception as e:
            print(f"[ERROR] Failed to install binary: {e}")
            sys.exit(5)

    print(f"Updated to {tag}. Installed at: {target_path}")

def main():
    parser = argparse.ArgumentParser(
        description="Autonomous AI agent with file operation capabilities",
        formatter_class=VersionedHelpFormatter,
        epilog="""
Examples:
  tricode "Find all TODO comments in the codebase"
  tricode "Read config.py and tell me the database settings"
  tricode "Replace old_name with new_name in all Python files"
        """
    )
    
    parser.add_argument(
        "--version",
        action="version",
        version=get_runtime_version(),
        help="Show version information and exit"
    )
    parser.add_argument(
        "--update",
        action="store_true",
        help="Update tricode to the latest release"
    )
    
    parser.add_argument(
        "prompt",
        type=str,
        nargs='?',
        help="Natural language instruction for the agent (omit for TUI mode with --tui)"
    )
    
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Show detailed execution logs"
    )
    
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Show API request and response details"
    )
    
    parser.add_argument(
        "--stdio",
        action="store_true",
        help="Output all messages in JSON format for programmatic integration"
    )
    
    parser.add_argument(
        "--tools",
        type=str,
        help="Comma-separated list of allowed tools (e.g., 'read_file,search_context,plan'). If not specified, all tools are available."
    )
    
    parser.add_argument(
        "--override-system-prompt",
        action="store_true",
        help="Replace default system prompt with AGENTS.md content instead of appending"
    )
    
    parser.add_argument(
        "-r", "--resume",
        type=str,
        metavar="SESSION_ID",
        help="Resume a previous conversation session by ID"
    )
    
    parser.add_argument(
        "-l", "--list-conversations",
        action="store_true",
        help="List all available conversation sessions"
    )
    
    parser.add_argument(
        "--work-dir",
        type=str,
        metavar="PATH",
        help="Set working directory (default: current directory). Agent can only access files under this path."
    )
    
    parser.add_argument(
        "--bypass-work-directory-limit",
        action="store_true",
        help="Allow access to files outside the working directory"
    )    
    parser.add_argument(
        "--bypass-permission",
        action="store_true",
        help="Skip user confirmation for destructive operations (use with caution)"
    )
    
    parser.add_argument(
        "--provider",
        type=str,
        metavar="NAME",
        help="Specify which provider to use (e.g., 'openai', 'anthropic'). If not specified, uses default_provider from config."
    )
    
    parser.add_argument(
        "--tui",
        action="store_true",
        help="Launch interactive TUI (Text User Interface) mode"
    )
    
    args = parser.parse_args()
    
    if args.update:
        # Use imported build-time version if available
        try:
            current = __version__
        except NameError:
            current = "dev"
        update_self(current)
        return

    if args.list_conversations:
        list_conversations()
        return
    
    if args.tui:
        allowed_tools = None
        if args.tools:
            allowed_tools = [t.strip() for t in args.tools.split(',') if t.strip()]
            if 'plan' not in allowed_tools:
                allowed_tools.insert(0, 'plan')
        
        run_tui(
            work_dir=args.work_dir,
            bypass_work_dir_limit=args.bypass_work_directory_limit,
            bypass_permission=args.bypass_permission,
            allowed_tools=allowed_tools,
            override_system_prompt=args.override_system_prompt,
            resume_session_id=args.resume,
            debug=args.debug,
            provider_name=args.provider
        )
        return
    
    if not args.prompt:
        parser.error("the following arguments are required: prompt")
    
    allowed_tools = None
    if args.tools:
        allowed_tools = [t.strip() for t in args.tools.split(',') if t.strip()]
        if 'plan' not in allowed_tools:
            allowed_tools.insert(0, 'plan')
    
    result = run_agent(
        args.prompt,
        verbose=args.verbose,
        stdio_mode=args.stdio,
        override_system_prompt=args.override_system_prompt,
        resume_session_id=args.resume,
        allowed_tools=allowed_tools,
        work_dir=args.work_dir,
        bypass_work_dir_limit=args.bypass_work_directory_limit,
        bypass_permission=args.bypass_permission,
        debug=args.debug,
        provider_name=args.provider
    )
    
    if result:
        print(result, flush=True)

if __name__ == "__main__":
    main()
