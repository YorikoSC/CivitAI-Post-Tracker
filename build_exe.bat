@echo off
setlocal
cd /d "%~dp0"

python -m pip install pyinstaller
python -m PyInstaller ^
  --noconfirm ^
  --clean ^
  --windowed ^
  --onedir ^
  --name CivitAITracker ^
  --hidden-import pystray._win32 ^
  --hidden-import PIL._tkinter_finder ^
  --add-data "config.example.json;." ^
  tracker_app.py

echo.
echo Build finished. Check the dist\CivitAITracker folder.
pause
