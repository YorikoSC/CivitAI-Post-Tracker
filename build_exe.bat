@echo off
setlocal EnableExtensions
cd /d "%~dp0"

set "VENV_DIR=.venv"
set "PYTHON_EXE=%VENV_DIR%\Scripts\python.exe"

if not exist "%PYTHON_EXE%" (
    echo Creating local Python environment in %VENV_DIR%...
    where py >nul 2>nul
    if not errorlevel 1 (
        py -3 -m venv "%VENV_DIR%"
    ) else (
        python -m venv "%VENV_DIR%"
    )
)

if not exist "%PYTHON_EXE%" (
    echo.
    echo Build failed: local virtual environment was not created.
    echo Install Python from python.org or make sure the Python launcher is available, then run:
    echo   py -3 -m venv .venv
    echo   .venv\Scripts\python.exe -m pip install -r requirements.txt
    exit /b 1
)

if not exist ".tmp_build" mkdir ".tmp_build" >nul 2>nul
set "PYTHONNOUSERSITE=1"
set "PYTHONUSERBASE=%CD%\.tmp_build"
set "PYTHONUTF8=1"
set "PIP_DISABLE_PIP_VERSION_CHECK=1"
set "PIP_RETRIES=1"
set "PIP_TIMEOUT=15"

echo Using build Python:
"%PYTHON_EXE%" -c "import sys; print(sys.executable)" || goto :fail

echo.
echo Building app version:
"%PYTHON_EXE%" -c "from app_info import APP_TITLE; print(APP_TITLE)" || goto :fail

echo.
echo Installing project dependencies into .venv...
"%PYTHON_EXE%" -m pip install --upgrade pip setuptools wheel || goto :deps_fail
"%PYTHON_EXE%" -m pip install -r requirements.txt || goto :deps_fail

echo.
echo Installing PyInstaller into .venv...
"%PYTHON_EXE%" -m pip install --upgrade pyinstaller || goto :deps_fail

echo.
echo Verifying runtime dependencies...
"%PYTHON_EXE%" -c "import requests, pystray, PIL, customtkinter; print('requests', requests.__version__); print('customtkinter', customtkinter.__version__)" || goto :deps_fail

echo.
echo Building CivitAITracker.exe...
"%PYTHON_EXE%" -m PyInstaller --noconfirm --clean civitai_tracker.spec || goto :fail

echo.
echo Build finished. Check the dist\CivitAITracker folder.
goto :eof

:deps_fail
echo.
echo Build failed while preparing .venv dependencies.
echo Check your internet connection and run:
echo   .venv\Scripts\python.exe -m pip install -r requirements.txt
echo   .venv\Scripts\python.exe -m pip install --upgrade pyinstaller
exit /b 1

:fail
echo.
echo Build failed.
exit /b 1
