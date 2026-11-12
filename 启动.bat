@echo off
REM ============================================================
REM  Launcher. ASCII-only content: this filename is Chinese,
REM  so no non-ASCII bytes are allowed inside the file.
REM ============================================================
cd /d "%~dp0"
title Subtitle Generator

set "HF_HOME=%~dp0models"
set "HF_ENDPOINT=https://hf-mirror.com"
set "HF_HUB_DISABLE_SYMLINKS_WARNING=1"

if not exist ".venv\Scripts\pythonw.exe" (
    echo  First launch detected - installing dependencies...
    echo.
    call install.bat
)

if not exist ".venv\Scripts\pythonw.exe" (
    echo  [ERROR] Local Python environment is missing.
    echo          Run install.bat manually and check the output.
    pause
    exit /b 1
)

if not exist "subtitle_app.py" (
    echo  [ERROR] subtitle_app.py not found next to this launcher.
    pause
    exit /b 1
)

start "" ".venv\Scripts\pythonw.exe" "subtitle_app.py"
exit /b 0
