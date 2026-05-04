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

where python.exe >nul 2>nul
if not errorlevel 1 (
    set "PYTHON_CMD=python.exe"
) else (
    where py.exe >nul 2>nul
    if not errorlevel 1 (
        set "PYTHON_CMD=py.exe"
        set "PYTHON_PREFIX=-3"
    )
)

if not defined PYTHON_CMD (
    >> "%LAUNCHER_LOG%" echo [%date% %time%] Python 3.11+ was not found.
    echo Python 3.11+ was not found. Please install Python and run setup again.
    pause
    exit /b 1
)

>> "%LAUNCHER_LOG%" echo [%date% %time%] selected launcher: %PYTHON_CMD% %PYTHON_PREFIX%
>> "%LAUNCHER_LOG%" echo [%date% %time%] args: "%TRACKER_SCRIPT%" --hide-console %*

start "CivitAI Tracker" /D "%SCRIPT_DIR%" "%PYTHON_CMD%" %PYTHON_PREFIX% "%TRACKER_SCRIPT%" --hide-console %*
