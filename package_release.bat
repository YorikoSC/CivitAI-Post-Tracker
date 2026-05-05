@echo off
setlocal
cd /d "%~dp0"

set "PYTHON_EXE=python"
if exist ".venv\Scripts\python.exe" set "PYTHON_EXE=.venv\Scripts\python.exe"

if not exist ".tmp_build" mkdir ".tmp_build" >nul 2>nul
"%PYTHON_EXE%" -c "from app_info import APP_VERSION; print(APP_VERSION)" > ".tmp_build\app_version.txt" || goto :fail
set /p APP_VERSION=<".tmp_build\app_version.txt"
if not defined APP_VERSION goto :fail

call build_exe.bat || goto :fail

if not exist "release" mkdir "release" >nul 2>nul
set "PACKAGE=release\CivitAITracker-v%APP_VERSION%-win64.zip"
if exist "%PACKAGE%" del "%PACKAGE%" >nul 2>nul

powershell -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference='Stop'; Compress-Archive -Path 'dist\CivitAITracker' -DestinationPath '%PACKAGE%' -Force" || goto :fail

echo.
echo Release package created:
echo %PACKAGE%
goto :eof

:fail
echo.
echo Release package failed.
exit /b 1
