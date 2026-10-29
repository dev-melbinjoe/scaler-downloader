@echo off
rem Double-click after your classes: checks Scaler and downloads + documents every new recording.
rem Extra options pass through, e.g.  update.bat --limit 3
cd /d "%~dp0"
".venv\Scripts\python.exe" main.py update %*
echo.
pause
