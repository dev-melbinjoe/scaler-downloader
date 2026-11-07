@echo off
rem Double-click after your classes: checks Scaler and downloads + documents every new recording.
rem Extra options pass through, e.g.  update.bat --limit 3
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo [ERROR] Virtual environment not found in .venv\
    echo Please set up the environment first:
    echo   py -m venv .venv
    echo   .venv\Scripts\pip install -r requirements.txt
    echo.
    pause
    exit /b 1
)

".venv\Scripts\python.exe" main.py update %*
echo.
pause
