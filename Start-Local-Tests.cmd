@echo off
cd /d "%~dp0"
python -c "import tkinter, playwright, httpx" >nul 2>&1
if errorlevel 1 (
  echo Install Python with its desktop window support, then install the packages listed in execution_agent\requirements.txt.
  echo See execution_agent\STANDALONE.md for setup instructions.
  pause
  exit /b 1
)
python -m execution_agent.standalone
if errorlevel 1 pause
