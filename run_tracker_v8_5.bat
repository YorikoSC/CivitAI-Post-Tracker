@echo off
cd /d "%~dp0"
start "" wscript.exe "%~dp0launch_tracker.vbs"
exit /b
