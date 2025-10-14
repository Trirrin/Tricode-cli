#!/usr/bin/env bash
# Tricode-cli Automatic Installer
# Author: Tricode-cli Team
# Language: English with Chinese comments
# This script will auto download the latest release binary and add it to your PATH (~/.local/bin)

# ==== CONFIGURATION 配置 ====
REPO="Trirrin/Tricode-cli"
INSTALL_DIR="$HOME/.local/bin"
TMP_DIR="/tmp/tricode-cli-install-$$"
GITHUB_API="https://api.github.com/repos/$REPO/releases/latest"

# ==== TEXTS 说明文本（EN/中）====
TEXT_START="Installing Tricode-cli..."
TEXT_DOWNLOAD="Downloading the latest release..."
TEXT_EXTRACT="Extracting binary..."
TEXT_PATH="Adding Tricode to PATH (if needed)..."
TEXT_DONE="\n[Success] Tricode-cli installed! Run: tricode --help\n(成功) Tricode-cli 已安装！运行: tricode --help\n"
TEXT_ERROR="An error occurred. Please report to https://github.com/Trirrin/Tricode-cli/issues\n出错了，如需帮助请在 issues 留言。"

# ==== Error handler 错误处理 ====
error_exit() {
  echo -e "\n[ERROR] $1"
  cd ~
  rm -rf "$TMP_DIR" 2>/dev/null
  exit "${2:-1}"
}

# ==== MAIN 主流程 ====
echo "$TEXT_START"
mkdir -p "$INSTALL_DIR" "$TMP_DIR" || error_exit "Failed to create directories" 1
cd "$TMP_DIR" || error_exit "Failed to access temp directory" 1

# 1. Detect OS and Architecture (支持 Linux/macOS, x86_64/arm64)
ARCH=$(uname -m)
OS=$(uname -s | tr '[:upper:]' '[:lower:]')

echo "Detected OS: $OS, Architecture: $ARCH"

# Determine asset name based on OS and architecture
if [[ "$OS" == "linux" ]]; then
  if [[ "$ARCH" == "x86_64" ]]; then
    ASSET_ARCH="linux-x86_64"
  elif [[ "$ARCH" == "aarch64" || "$ARCH" == "arm64" ]]; then
    ASSET_ARCH="linux-arm64"
  else
    error_exit "Unsupported Linux architecture: $ARCH" 1
  fi
elif [[ "$OS" == "darwin" ]]; then
  if [[ "$ARCH" == "x86_64" ]]; then
    ASSET_ARCH="macos-x86_64"
  elif [[ "$ARCH" == "arm64" ]]; then
    ASSET_ARCH="macos-arm64"
  else
    error_exit "Unsupported macOS architecture: $ARCH" 1
  fi
else
  error_exit "Unsupported operating system: $OS (only Linux and macOS are supported)" 1
fi

echo "Looking for release: tricode-$ASSET_ARCH"

# 2. Get download URL from GitHub API 通过GitHub API拿二进制包下载链接
echo "$TEXT_DOWNLOAD"
API_RESPONSE=$(curl -sSL "$GITHUB_API" 2>/dev/null)
if [ -z "$API_RESPONSE" ]; then
  error_exit "Cannot connect to GitHub API. Please check your network." 2
fi

ASSET_URL=$(echo "$API_RESPONSE" | grep -o "https://[^\"]*tricode-$ASSET_ARCH[^\"]*" | head -n1)
if [ -z "$ASSET_URL" ]; then
  echo "Debug: Available assets:"
  echo "$API_RESPONSE" | grep browser_download_url || echo "No assets found"
  error_exit "Cannot find matching release binary for tricode-$ASSET_ARCH.\nPlease check if the release exists at: https://github.com/$REPO/releases" 2
fi

echo "Downloading from: $ASSET_URL"
curl -LO "$ASSET_URL" || error_exit "Failed to download binary" 3

# 3. Deploy binary 部署可执行文件
BASENAME=$(basename "$ASSET_URL")
echo "$TEXT_EXTRACT"

if [[ "$BASENAME" == *.tar.gz ]]; then
  tar -xzf "$BASENAME" || error_exit "Failed to extract tarball" 4
  # Find the binary in extracted files
  BIN=$(tar -tzf "$BASENAME" | grep -E "tricode$|tricode\.exe$" | head -n1)
  if [ -z "$BIN" ]; then
    error_exit "Cannot find tricode binary in archive" 4
  fi
elif [[ "$BASENAME" == *.zip ]]; then
  unzip -q "$BASENAME" || error_exit "Failed to extract zip" 4
  BIN="tricode"
else
  # Direct binary file
  BIN="$BASENAME"
fi

# Handle if BIN is in subdirectory
if [[ "$BIN" == */* ]]; then
  BIN=$(basename "$BIN")
fi

if [ ! -f "$BIN" ]; then
  error_exit "Binary file not found after extraction: $BIN" 4
fi

chmod +x "$BIN" || error_exit "Failed to set executable permission" 5
mv "$BIN" "$INSTALL_DIR/tricode" || error_exit "Failed to move binary to $INSTALL_DIR" 5

# 4. Add to PATH if needed 加入PATH（如必要）
if ! echo "$PATH" | grep -q "$INSTALL_DIR"; then
  echo "$TEXT_PATH"
  
  # Detect shell profile file
  if [ -n "$BASH_VERSION" ]; then
    PROFILE_FILE="$HOME/.bashrc"
  elif [ -n "$ZSH_VERSION" ]; then
    PROFILE_FILE="$HOME/.zshrc"
  else
    # Fallback to .profile
    PROFILE_FILE="$HOME/.profile"
  fi
  
  # Create profile file if it doesn't exist
  touch "$PROFILE_FILE"
  
  LINE_EXPORT="export PATH=\"$INSTALL_DIR:\$PATH\""
  
  # Add to profile if not already present
  if ! grep -qF "$INSTALL_DIR" "$PROFILE_FILE" 2>/dev/null; then
    echo "" >> "$PROFILE_FILE"
    echo "# Added by Tricode-cli installer" >> "$PROFILE_FILE"
    echo "$LINE_EXPORT" >> "$PROFILE_FILE"
    echo "Added to $PROFILE_FILE"
  fi
  
  # Export for current session
  export PATH="$INSTALL_DIR:$PATH"
fi

# Cleanup
cd ~
rm -rf "$TMP_DIR"

echo -e "$TEXT_DONE"
echo "Note: You may need to restart your terminal or run: source $PROFILE_FILE"
echo "提示: 可能需要重启终端或运行: source $PROFILE_FILE"
