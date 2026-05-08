@echo off
setlocal EnableExtensions
cd /d "%~dp0"

set "VENV_DIR=.venv"
set "PYTHON_EXE=%VENV_DIR%\Scripts\python.exe"

if exist "%PYTHON_EXE%" (
    "%PYTHON_EXE%" -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>nul
    if errorlevel 1 (
        echo Existing .venv is not usable with this Python install.
        call :backup_broken_venv || goto :fail
    )
)

if not exist "%PYTHON_EXE%" (
    echo Creating local Python environment in %VENV_DIR%...
    call :find_system_python || goto :no_python
    "%PYTHON_CMD%" %PYTHON_PREFIX% -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>nul || goto :old_python
    "%PYTHON_CMD%" %PYTHON_PREFIX% -m venv "%VENV_DIR%" || goto :fail
)

if not exist "%PYTHON_EXE%" (
    echo.
    echo Failed to create .venv. Install Python from python.org or make sure the Python launcher is available.
    pause
    exit /b 1
)

"%PYTHON_EXE%" -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>nul
if errorlevel 1 (
    echo.
    echo The .venv Python is still not usable. Install Python 3.11+ from python.org and rerun this script.
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
"%PYTHON_EXE%" -c "import customtkinter" >nul 2>nul
if errorlevel 1 (
    echo.
    echo CustomTkinter was not installed correctly.
    goto :fail
)

echo.
echo Verifying source UI dependencies...
"%PYTHON_EXE%" -c "import sys, customtkinter; print(sys.executable); print('customtkinter', customtkinter.__version__)"

echo.
echo Dependencies installed.
pause
goto :eof

:fail
echo.
echo Dependency installation failed.
pause
exit /b 1

:find_system_python
set "PYTHON_CMD="
set "PYTHON_PREFIX="
where py.exe >nul 2>nul
if not errorlevel 1 (
    set "PYTHON_CMD=py.exe"
    set "PYTHON_PREFIX=-3"
    exit /b 0
)
where python.exe >nul 2>nul
if not errorlevel 1 (
    set "PYTHON_CMD=python.exe"
    exit /b 0
)
exit /b 1

:backup_broken_venv
set "BROKEN_VENV=%VENV_DIR%.broken-%RANDOM%"
if exist "%BROKEN_VENV%" set "BROKEN_VENV=%VENV_DIR%.broken-%RANDOM%-%RANDOM%"
echo Moving broken .venv to %BROKEN_VENV%...
move "%VENV_DIR%" "%BROKEN_VENV%" >nul
exit /b %errorlevel%

:no_python
echo.
echo Python 3.11+ was not found.
echo Install Python from python.org, then run this script again.
pause
exit /b 1

:old_python
echo.
echo Python was found, but it is older than 3.11 or cannot create the environment.
echo Install Python 3.11+ from python.org, then run this script again.
pause
exit /b 1
