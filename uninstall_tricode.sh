#!/usr/bin/env bash
# Tricode-cli Uninstaller for Linux/macOS
# Safely removes the installed binary and PATH modifications made by install_tricode.sh

set -u

REPO="Trirrin/Tricode-cli"
INSTALL_DIR="$HOME/.local/bin"
BIN_PATH="$INSTALL_DIR/tricode"

info() { printf '%s\n' "$1"; }
warn() { printf '%s\n' "$1" >&2; }
error_exit() { printf '\n[ERROR] %s\n' "$1" >&2; exit "${2:-1}"; }

info "Uninstalling Tricode-cli..."

# 1) Remove binary
if [ -f "$BIN_PATH" ]; then
  rm -f "$BIN_PATH" || error_exit "Failed to remove $BIN_PATH" 1
  info "Removed binary: $BIN_PATH"
else
  info "Binary not found at: $BIN_PATH (already removed)"
fi

# 2) Remove PATH modification from common shell profiles (precise, non-destructive)
cleanup_profile() {
  local file="$1"
  [ -f "$file" ] || return 0
  local tmp="${file}.tricode.tmp"
  awk -v install_dir="$INSTALL_DIR" '
    BEGIN { skip_next = 0 }
    {
      if (skip_next == 1) {
        # Remove only if this exact next line exports PATH with our install dir
        if ($0 ~ "^export[[:space:]]+PATH=.*" install_dir ".*\\$PATH") { skip_next = 0; next }
        skip_next = 0
      }
      if ($0 == "# Added by Tricode-cli installer") { skip_next = 1; next }
      print $0
    }
  ' "$file" > "$tmp" && mv "$tmp" "$file"
}

cleanup_profile "$HOME/.bashrc"
cleanup_profile "$HOME/.zshrc"
cleanup_profile "$HOME/.profile"

# 3) Done
printf '\n[Success] Tricode-cli uninstalled.\n'
printf 'Note: Restart your terminal or run: source ~/.bashrc (or ~/.zshrc)\n'
printf '提示: 重启终端或执行: source ~/.bashrc（或 ~/.zshrc）\n'

exit 0

