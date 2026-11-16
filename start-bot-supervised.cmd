@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Install the project virtual environment first. See README.md.
  exit /b 1
)
".venv\Scripts\python.exe" -m vesterian.supervisor %*
