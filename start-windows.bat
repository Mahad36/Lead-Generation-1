@echo off
rem Starts Lead Generator on this PC: database, API, automatic audit poller and web app.
rem Keep this window open while you use the app. Press Ctrl+C or close the window to stop.
cd /d "%~dp0"
if not exist "apps\api\.venv\Scripts\python.exe" (
  echo Run setup-windows.bat first.
  pause
  exit /b 1
)
set "PATH=%USERPROFILE%\.local\bin;%PATH%"
"apps\api\.venv\Scripts\python.exe" scripts\run_local.py %*
pause
