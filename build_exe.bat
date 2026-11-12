@echo off
REM Local one-click build. ASCII-only content (Chinese filename safe).
cd /d "%~dp0"

REM Use a SEPARATE clean venv: PyInstaller must see the full standard
REM library (tkinter in particular), which patched venvs may hide.
set "PYCMD=py -3"
where py >nul 2>nul
if errorlevel 1 set "PYCMD=python"

if not exist ".build-venv\Scripts\python.exe" (
    echo  Creating a clean build environment (.build-venv) ...
    %PYCMD% -m venv .build-venv
    if errorlevel 1 (
        echo  [ERROR] Failed to create build environment.
        pause
        exit /b 1
    )
)

echo  Installing dependencies + PyInstaller ...
".build-venv\Scripts\python.exe" -m pip install -q --timeout 10 --retries 0 -r requirements.txt pyinstaller
if errorlevel 1 (
    echo  Official PyPI is slow. Trying TUNA mirror...
    ".build-venv\Scripts\python.exe" -m pip install -q -i https://pypi.tuna.tsinghua.edu.cn/simple -r requirements.txt pyinstaller
    if errorlevel 1 (
        echo  [ERROR] Failed to install build dependencies.
        pause
        exit /b 1
    )
)

".build-venv\Scripts\python.exe" build.py
echo.
pause
