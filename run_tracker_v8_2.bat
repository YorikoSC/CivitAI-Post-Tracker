@echo off
setlocal
cd /d "%~dp0"
echo Installing/updating Python dependencies...
python -m pip install -r requirements.txt
if errorlevel 1 (
  echo Failed to install dependencies.
  pause
  exit /b 1
)
if not exist config.json (
  echo config.json not found. Running setup wizard...
  python setup_config.py
  if errorlevel 1 (
    echo Setup wizard failed.
    pause
    exit /b 1
  )
)
python tracker_v8_2.py
pause
