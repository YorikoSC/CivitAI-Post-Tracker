@echo off
cd /d "%~dp0"
set "PYTHON_EXE=.venv\Scripts\python.exe"

if not exist "%PYTHON_EXE%" (
    echo Local .venv was not found.
    echo Run install_requirements.bat first.
    pause
    exit /b 1
)

"%PYTHON_EXE%" -c "import sys, customtkinter; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>nul
if errorlevel 1 (
    echo Local .venv is not ready for the source UI.
    echo Run install_requirements.bat first.
    pause
    exit /b 1
)

"%PYTHON_EXE%" tracker_app.py
pause
