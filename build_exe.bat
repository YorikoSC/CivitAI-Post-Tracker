@echo off
setlocal
cd /d "%~dp0"

python -m pip install pyinstaller || goto :fail
python -m PyInstaller --noconfirm --clean civitai_tracker.spec || goto :fail

echo.
echo Build finished. Check the dist\CivitAITracker folder.
goto :eof

:fail
echo.
echo Build failed.
exit /b 1
