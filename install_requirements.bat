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
    echo Failed to create .venv. Install Python from python.org or make sure the Python launcher is available.
    pause
    exit /b 1
)

set "PYTHONNOUSERSITE=1"
set "PIP_DISABLE_PIP_VERSION_CHECK=1"
set "PIP_RETRIES=1"
set "PIP_TIMEOUT=15"

echo Using Python:
"%PYTHON_EXE%" -c "import sys; print(sys.executable)"

echo.
echo Installing project dependencies into .venv...
"%PYTHON_EXE%" -m pip install --upgrade pip setuptools wheel || goto :fail
"%PYTHON_EXE%" -m pip install -r requirements.txt || goto :fail

echo.
echo Dependencies installed.
pause
goto :eof

:fail
echo.
echo Dependency installation failed.
pause
exit /b 1
