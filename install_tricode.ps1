# Tricode-cli Automatic Installer for Windows
# Author: Tricode-cli Team
# Language: English with Chinese comments
# This script will auto download the latest release binary and add it to your PATH

# ==== CONFIGURATION 配置 ====
$ErrorActionPreference = "Stop"
$REPO = "Trirrin/Tricode-cli"
$INSTALL_DIR = "$env:LOCALAPPDATA\Tricode"
$GITHUB_API = "https://api.github.com/repos/$REPO/releases/latest"

# ==== TEXTS 说明文本（EN/中）====
$TEXT_START = "Installing Tricode-cli..."
$TEXT_DOWNLOAD = "Downloading the latest release..."
$TEXT_EXTRACT = "Extracting binary..."
$TEXT_PATH = "Adding Tricode to PATH..."
$TEXT_DONE = @"

[Success] Tricode-cli installed successfully!
(成功) Tricode-cli 已安装成功！

Installation directory: $INSTALL_DIR
Run: tricode --help

Note: Please restart your terminal or PowerShell to use tricode command.
提示: 请重启终端或 PowerShell 以使用 tricode 命令。
"@

# ==== Functions 函数 ====
function Write-Info {
    param([string]$Message)
    Write-Host $Message -ForegroundColor Green
}

function Write-Error-Exit {
    param([string]$Message, [int]$Code = 1)
    Write-Host "`n[ERROR] $Message" -ForegroundColor Red
    Write-Host "Please report issues to: https://github.com/$REPO/issues" -ForegroundColor Yellow
    exit $Code
}

# ==== MAIN 主流程 ====
Write-Info $TEXT_START

# 1. Detect Architecture 检测架构
$ARCH = $env:PROCESSOR_ARCHITECTURE
Write-Host "Detected Architecture: $ARCH"

if ($ARCH -eq "AMD64" -or $ARCH -eq "x64") {
    $ASSET_ARCH = "windows-x86_64"
} elseif ($ARCH -eq "ARM64") {
    $ASSET_ARCH = "windows-arm64"
} else {
    Write-Error-Exit "Unsupported architecture: $ARCH"
}

Write-Host "Looking for release: tricode-$ASSET_ARCH.exe"

# 2. Get download URL from GitHub API 通过GitHub API获取下载链接
Write-Info $TEXT_DOWNLOAD

try {
    $API_RESPONSE = Invoke-RestMethod -Uri $GITHUB_API -ErrorAction Stop
} catch {
    Write-Error-Exit "Cannot connect to GitHub API. Please check your network.`nError: $_" 2
}

$ASSET_URL = $API_RESPONSE.assets | Where-Object { $_.name -like "*$ASSET_ARCH*" } | Select-Object -First 1 -ExpandProperty browser_download_url

if (-not $ASSET_URL) {
    Write-Host "Debug: Available assets:" -ForegroundColor Yellow
    $API_RESPONSE.assets | ForEach-Object { Write-Host "  - $($_.name)" }
    Write-Error-Exit "Cannot find matching release binary for tricode-$ASSET_ARCH.exe`nPlease check if the release exists at: https://github.com/$REPO/releases" 2
}

Write-Host "Downloading from: $ASSET_URL"

# 3. Download binary 下载二进制文件
$TMP_DIR = "$env:TEMP\tricode-install-$(Get-Random)"
New-Item -ItemType Directory -Path $TMP_DIR -Force | Out-Null

try {
    $DOWNLOAD_FILE = Join-Path $TMP_DIR (Split-Path $ASSET_URL -Leaf)
    Invoke-WebRequest -Uri $ASSET_URL -OutFile $DOWNLOAD_FILE -ErrorAction Stop
} catch {
    Write-Error-Exit "Failed to download binary.`nError: $_" 3
}

# 4. Extract if needed and deploy 解压（如需要）并部署
Write-Info $TEXT_EXTRACT

$BIN_NAME = Split-Path $DOWNLOAD_FILE -Leaf

if ($BIN_NAME -like "*.zip") {
    try {
        Expand-Archive -Path $DOWNLOAD_FILE -DestinationPath $TMP_DIR -Force
        $BIN_FILE = Get-ChildItem -Path $TMP_DIR -Filter "tricode*.exe" -Recurse | Select-Object -First 1 -ExpandProperty FullName
        if (-not $BIN_FILE) {
            Write-Error-Exit "Cannot find tricode.exe in the archive" 4
        }
    } catch {
        Write-Error-Exit "Failed to extract archive.`nError: $_" 4
    }
} else {
    # Direct binary file
    $BIN_FILE = $DOWNLOAD_FILE
}

# 5. Install binary 安装二进制文件
New-Item -ItemType Directory -Path $INSTALL_DIR -Force | Out-Null

try {
    Copy-Item -Path $BIN_FILE -Destination "$INSTALL_DIR\tricode.exe" -Force
} catch {
    Write-Error-Exit "Failed to copy binary to $INSTALL_DIR`nError: $_" 5
}

# 6. Add to PATH 添加到PATH
Write-Info $TEXT_PATH

$USER_PATH = [Environment]::GetEnvironmentVariable("Path", "User")

if ($USER_PATH -notlike "*$INSTALL_DIR*") {
    try {
        $NEW_PATH = "$USER_PATH;$INSTALL_DIR"
        [Environment]::SetEnvironmentVariable("Path", $NEW_PATH, "User")
        Write-Host "Added $INSTALL_DIR to User PATH"
        
        # Update current session PATH
        $env:Path = "$env:Path;$INSTALL_DIR"
    } catch {
        Write-Warning "Failed to add to PATH automatically. Please add manually:`n  $INSTALL_DIR"
    }
} else {
    Write-Host "$INSTALL_DIR is already in PATH"
}

# 7. Cleanup 清理
Remove-Item -Path $TMP_DIR -Recurse -Force -ErrorAction SilentlyContinue

# 8. Success message 成功消息
Write-Host $TEXT_DONE -ForegroundColor Green

# Verify installation 验证安装
Write-Host "`nVerifying installation..." -ForegroundColor Cyan
if (Test-Path "$INSTALL_DIR\tricode.exe") {
    Write-Host "✓ Binary installed at: $INSTALL_DIR\tricode.exe" -ForegroundColor Green
} else {
    Write-Warning "Installation may have issues. Binary not found."
}
