@echo off
setlocal
cd /d "%~dp0"

set "PYTHON_EXE=python"
if exist ".venv\Scripts\python.exe" set "PYTHON_EXE=.venv\Scripts\python.exe"

if not exist ".tmp_build" mkdir ".tmp_build" >nul 2>nul
set "PYTHONNOUSERSITE=1"
set "PYTHONUSERBASE=%CD%\.tmp_build"
set "PYTHONUTF8=1"

"%PYTHON_EXE%" -m PyInstaller --version >nul 2>nul
if errorlevel 1 (
    "%PYTHON_EXE%" -m pip install pyinstaller || goto :fail
)

"%PYTHON_EXE%" -m PyInstaller --noconfirm --clean civitai_tracker.spec || goto :fail

echo.
echo Build finished. Check the dist\CivitAITracker folder.
goto :eof

:fail
echo.
echo Build failed.
exit /b 1
