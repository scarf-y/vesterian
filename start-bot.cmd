@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Install the project virtual environment first. See README.md.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" -m vesterian.bot %*
if errorlevel 1 pause
