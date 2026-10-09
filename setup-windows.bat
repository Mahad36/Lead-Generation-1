@echo off
rem One-time setup on Windows (no Docker). Installs Python, Node.js, Git, Claude Code and the app's packages.
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\windows\setup.ps1"
pause
