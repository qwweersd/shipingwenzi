@echo off
REM ============================================================
REM  Install dependencies into a local virtual environment.
REM  ASCII only in this file to avoid Windows codepage issues.
REM ============================================================
cd /d "%~dp0"
echo.
echo  [1/3] Creating local Python environment (.venv) ...
echo.

REM Prefer the official Python launcher: it resolves to a full CPython
REM install that ships tkinter. Bundled / minimal Pythons often omit it.
set "PYCMD=py -3"
where py >nul 2>nul
if errorlevel 1 set "PYCMD=python"

where python >nul 2>nul
if errorlevel 1 (
    if "%PYCMD%"=="python" (
        echo  [ERROR] Python not found. Install Python 3.10+ from python.org
        echo          and check "Add Python to PATH" during setup.
        pause
        exit /b 1
    )
)

REM Rebuild an existing venv if it came from a Python without tkinter.
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" -c "import tkinter" >nul 2>nul
    if errorlevel 1 (
        echo  Existing .venv has no tkinter - rebuilding with full Python ...
        rmdir /s /q .venv
    )
)

if not exist ".venv\Scripts\python.exe" (
    %PYCMD% -m venv .venv
    if errorlevel 1 (
        echo  [ERROR] Failed to create virtual environment.
        pause
        exit /b 1
    )
)

".venv\Scripts\python.exe" -c "import tkinter" >nul 2>nul
if errorlevel 1 (
    echo  [ERROR] This Python has no tkinter module, so the GUI cannot start.
    echo          Install official CPython from https://www.python.org/downloads/
    echo          keep "tcl/tk and IDLE" checked, then run this again.
    pause
    exit /b 1
)

echo  [2/3] Installing dependencies (faster-whisper / tkinterdnd2 / zhconv) ...
echo         This may take 1-5 minutes, please wait.
echo.
".venv\Scripts\python.exe" -m pip install --timeout 10 --retries 0 -r requirements.txt
if errorlevel 1 (
    echo.
    echo  Official PyPI is slow or unreachable. Trying TUNA mirror...
    echo.
    ".venv\Scripts\python.exe" -m pip install -i https://pypi.tuna.tsinghua.edu.cn/simple --timeout 60 -r requirements.txt
    if errorlevel 1 (
        echo  [ERROR] Installation failed. Check your network and try again.
        pause
        exit /b 1
    )
)

echo  [3/3] Verifying installation ...
".venv\Scripts\python.exe" -c "import faster_whisper; print('faster-whisper OK')"
if errorlevel 1 (
    echo  [ERROR] Verification failed. Please check the messages above.
    pause
    exit /b 1
)

echo.
echo  ============================================
echo   Done! Now double-click the launcher
echo   with the Chinese filename to open the app.
echo  ============================================
echo  Note: the Whisper model itself is downloaded
echo        on first use, not during this install.
echo.
pause
