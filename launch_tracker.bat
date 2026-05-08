@echo off
setlocal

set "SCRIPT_DIR=%~dp0"
cd /d "%SCRIPT_DIR%"

set "LOG_DIR=%SCRIPT_DIR%logs"
if not exist "%LOG_DIR%" mkdir "%LOG_DIR%" >nul 2>nul
set "LAUNCHER_LOG=%LOG_DIR%\launcher_last.log"

> "%LAUNCHER_LOG%" echo [%date% %time%] launch_tracker.bat starting in %CD%

set "TRACKER_SCRIPT=%SCRIPT_DIR%tracker_app.py"
if not exist "%TRACKER_SCRIPT%" (
    >> "%LAUNCHER_LOG%" echo [%date% %time%] tracker_app.py was not found: %TRACKER_SCRIPT%
    echo tracker_app.py was not found.
    echo Expected path: %TRACKER_SCRIPT%
    pause
    exit /b 1
)

set "PYTHON_CMD="
set "PYTHON_PREFIX="
set "PYTHON_LABEL="
set "VENV_PYTHON=%SCRIPT_DIR%.venv\Scripts\python.exe"
set "PYTHONNOUSERSITE=1"

if exist "%VENV_PYTHON%" (
    "%VENV_PYTHON%" -c "import sys, customtkinter; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>nul
    if errorlevel 1 (
        >> "%LAUNCHER_LOG%" echo [%date% %time%] local .venv exists but is not usable or is missing CustomTkinter.
        echo The local Python environment is not ready for the source UI.
        echo.
        echo Run install_requirements.bat from this folder, then start the tracker again.
        pause
        exit /b 1
    )
    set "PYTHON_CMD=%VENV_PYTHON%"
    set "PYTHON_LABEL=local .venv"
) else (
    where py.exe >nul 2>nul
    if not errorlevel 1 (
        set "PYTHON_CMD=py.exe"
        set "PYTHON_PREFIX=-3"
        set "PYTHON_LABEL=Python launcher"
    ) else (
        where python.exe >nul 2>nul
        if not errorlevel 1 (
            set "PYTHON_CMD=python.exe"
            set "PYTHON_LABEL=PATH python"
        )
    )
)

if not defined PYTHON_CMD (
    >> "%LAUNCHER_LOG%" echo [%date% %time%] Python 3.11+ was not found.
    echo Python 3.11+ was not found. Install Python from python.org, then run install_requirements.bat.
    pause
    exit /b 1
)

if not "%PYTHON_LABEL%"=="local .venv" (
    "%PYTHON_CMD%" %PYTHON_PREFIX% -c "import sys, customtkinter; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>nul
    if errorlevel 1 (
        >> "%LAUNCHER_LOG%" echo [%date% %time%] selected Python is missing CustomTkinter or is older than 3.11: %PYTHON_CMD% %PYTHON_PREFIX%
        echo Python was found, but the source UI dependencies are not installed.
        echo.
        echo Run install_requirements.bat from this folder, then start the tracker again.
        pause
        exit /b 1
    )
)

>> "%LAUNCHER_LOG%" echo [%date% %time%] selected launcher: %PYTHON_LABEL% - %PYTHON_CMD% %PYTHON_PREFIX%
>> "%LAUNCHER_LOG%" echo [%date% %time%] args: "%TRACKER_SCRIPT%" --hide-console %*

start "CivitAI Tracker" /D "%SCRIPT_DIR%" "%PYTHON_CMD%" %PYTHON_PREFIX% "%TRACKER_SCRIPT%" --hide-console %*
