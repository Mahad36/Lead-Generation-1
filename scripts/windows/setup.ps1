# Lead Generator - one-time setup on Windows (no Docker, no database install).
# Run it by double-clicking setup-windows.bat in the project folder (e.g. E:\Lead-Generation).
# Safe to run again: it skips anything already installed.

$ErrorActionPreference = "Continue"
$Root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)   # scripts\windows -> project folder
Set-Location $Root
$Python = Join-Path $Root "apps\api\.venv\Scripts\python.exe"

function Step($message) { Write-Host "`n==> $message" -ForegroundColor Cyan }
function Fail($message) {
    Write-Host "`n!! $message" -ForegroundColor Red
    Write-Host "!! Fix the problem above, then run setup-windows.bat again." -ForegroundColor Red
    exit 1
}
function Refresh-Path {
    $machine = [Environment]::GetEnvironmentVariable("Path", "Machine")
    $user = [Environment]::GetEnvironmentVariable("Path", "User")
    $env:Path = "$machine;$user;$env:USERPROFILE\.local\bin"
}
function Has-Command($name) { return [bool](Get-Command $name -ErrorAction SilentlyContinue) }
function Has-Python312 {
    if (-not (Has-Command "py")) { return $false }
    & py -3.12 -c "import sys" *> $null
    return $LASTEXITCODE -eq 0
}
function Winget-Install($id, $label) {
    Write-Host "Installing $label (Windows may ask for permission)..."
    winget install -e --id $id --accept-source-agreements --accept-package-agreements --silent
    Refresh-Path
}

Write-Host "Lead Generator setup in $Root" -ForegroundColor Green
if (-not (Has-Command "winget")) {
    Fail "winget was not found. Install 'App Installer' from the Microsoft Store, then run this again."
}
Refresh-Path

Step "Python 3.12"
if (-not (Has-Python312)) { Winget-Install "Python.Python.3.12" "Python 3.12" }
if (-not (Has-Python312)) { Fail "Python 3.12 is not available. Close this window, open a new one and run setup again." }

Step "Node.js"
if (-not (Has-Command "npm.cmd")) { Winget-Install "OpenJS.NodeJS.LTS" "Node.js LTS" }
if (-not (Has-Command "npm.cmd")) { Fail "Node.js is not available. Close this window, open a new one and run setup again." }

Step "Git (Claude Code needs it on Windows)"
if (-not (Has-Command "git")) { Winget-Install "Git.Git" "Git" }

Step "Python packages for the app"
if (-not (Test-Path $Python)) { & py -3.12 -m venv "apps\api\.venv" }
& $Python -m pip install --upgrade pip --quiet
& $Python -m pip install -r "apps\api\requirements.txt" "pgserver==0.1.4"
if ($LASTEXITCODE -ne 0) { Fail "Installing Python packages failed." }

Step "Browser for website screenshots"
& $Python -m playwright install chromium
if ($LASTEXITCODE -ne 0) { Write-Host "(Screenshot browser not installed; audits will run without screenshots)" -ForegroundColor Yellow }

Step "Web app packages"
Push-Location "apps\web"
& npm.cmd install --no-audit --no-fund
$npmExit = $LASTEXITCODE
Pop-Location
if ($npmExit -ne 0) { Fail "Installing web packages failed." }

Step "Claude Code (website analysis on your Claude subscription)"
if (-not (Has-Command "claude")) {
    Invoke-RestMethod https://claude.ai/install.ps1 | Invoke-Expression
    Refresh-Path
}
if (-not (Has-Command "claude")) { Fail "Claude Code did not install. See https://code.claude.com/docs/en/setup" }

$EnvFile = Join-Path $Root ".env"
if (-not (Test-Path $EnvFile)) { New-Item -ItemType File -Path $EnvFile | Out-Null }
$HasToken = Select-String -Path $EnvFile -Pattern "^CLAUDE_CODE_OAUTH_TOKEN=." -Quiet
if (-not $HasToken) {
    Step "Connect Claude Code to your Claude subscription"
    Write-Host "A browser window will open. Approve access, then come back here."
    & claude setup-token
    $token = Read-Host "`nPaste the token shown above (starts with sk-ant-), or press Enter to skip"
    if ($token.Trim()) {
        $lines = @(Get-Content $EnvFile | Where-Object { $_ -notmatch "^CLAUDE_CODE_OAUTH_TOKEN=" })
        $lines += "CLAUDE_CODE_OAUTH_TOKEN=$($token.Trim())"
        # UTF-8 without a byte-order mark (Set-Content -Encoding UTF8 adds one in Windows PowerShell 5)
        [System.IO.File]::WriteAllLines($EnvFile, [string[]]$lines, (New-Object System.Text.UTF8Encoding($false)))
        Write-Host "Saved to .env" -ForegroundColor Green
    } else {
        Write-Host "Skipped. Later: run 'claude setup-token' and add CLAUDE_CODE_OAUTH_TOKEN=... to .env" -ForegroundColor Yellow
    }
}

Write-Host "`n============================================================" -ForegroundColor Green
Write-Host " Setup complete. Double-click start-windows.bat to run the app." -ForegroundColor Green
Write-Host "============================================================" -ForegroundColor Green
