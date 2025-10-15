<#
Tricode-cli Uninstaller for Windows (PowerShell)
Removes installed binary and PATH modifications made by install_tricode.ps1
#>

$ErrorActionPreference = "Stop"
$REPO = "Trirrin/Tricode-cli"
$INSTALL_DIR = Join-Path $env:LOCALAPPDATA "Tricode"
$TARGET = Join-Path $INSTALL_DIR "tricode.exe"

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

Write-Info "Uninstalling Tricode-cli..."

# 1) Remove binary
try {
    if (Test-Path $TARGET) {
        Remove-Item -Path $TARGET -Force
        Write-Host "Removed binary: $TARGET"
    } else {
        Write-Host "Binary not found at: $TARGET (already removed)"
    }
} catch {
    Write-Error-Exit "Failed to remove binary at $TARGET`nError: $_" 1
}

# 2) Remove INSTALL_DIR from User PATH if present (precise match only)
try {
    $USER_PATH = [Environment]::GetEnvironmentVariable("Path", "User")
    $normalizedInstall = $INSTALL_DIR.TrimEnd('\\')
    if ($null -ne $USER_PATH -and $USER_PATH -ne '') {
        $segments = $USER_PATH -split ";"
        $filtered = foreach ($seg in $segments) {
            if ($null -eq $seg -or $seg -eq '') { continue }
            $segTrim = $seg.Trim()
            $segNorm = $segTrim.TrimEnd('\\')
            if ($segNorm -ieq $normalizedInstall) { continue }
            $segTrim
        }
        $NEW_PATH = ($filtered | Where-Object { $_ -and $_ -ne '' }) -join ";"
        [Environment]::SetEnvironmentVariable("Path", $NEW_PATH, "User")
        Write-Host "Removed $INSTALL_DIR from User PATH"

        # Update current session PATH similarly
        $sessionPath = $env:Path
        if ($null -ne $sessionPath -and $sessionPath -ne '') {
            $sSegments = $sessionPath -split ";"
            $sFiltered = foreach ($seg in $sSegments) {
                if ($null -eq $seg -or $seg -eq '') { continue }
                $segTrim = $seg.Trim()
                $segNorm = $segTrim.TrimEnd('\\')
                if ($segNorm -ieq $normalizedInstall) { continue }
                $segTrim
            }
            $env:Path = ($sFiltered | Where-Object { $_ -and $_ -ne '' }) -join ";"
        }
    } else {
        Write-Host "$INSTALL_DIR was not in User PATH"
    }
} catch {
    Write-Host "Warning: Failed to update PATH automatically. You may remove it manually from Environment Variables." -ForegroundColor Yellow
}

# 3) Remove install directory if empty
try {
    if (Test-Path $INSTALL_DIR) {
        $items = Get-ChildItem -Path $INSTALL_DIR -Force -ErrorAction SilentlyContinue
        if (-not $items) {
            Remove-Item -Path $INSTALL_DIR -Force -Recurse
            Write-Host "Removed empty directory: $INSTALL_DIR"
        }
    }
} catch {
    Write-Host "Warning: Failed to remove directory $INSTALL_DIR. You can delete it manually." -ForegroundColor Yellow
}

Write-Host "`n[Success] Tricode-cli uninstalled."
Write-Host "(成功) Tricode-cli 已卸载。"
Write-Host "Note: Please restart your terminal or PowerShell."
Write-Host "提示: 请重启终端或 PowerShell。"

