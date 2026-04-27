# EXE build

## Requirements

- Python 3.11+
- PyInstaller

## Build

From the project root:

```powershell
python -m pip install pyinstaller
build_exe.bat
```

The build uses **PyInstaller onedir** mode and writes output to:

```text
dist/CivitAITracker/
```

## What to test after build

1. `CivitAITracker.exe` starts without a console window.
2. The app opens settings when `config.json` does not exist.
3. `Run now` works.
4. `Start auto polling` works.
5. `dashboard.html` updates.
6. `runtime_status.json` updates.
7. Tray mode still works.

## Notes

- The packaged app keeps runtime files alongside the EXE directory in this stage.
- `config.example.json` is bundled as a template, but your personal `config.json` is never bundled.
- Source-mode users should prefer `launch_tracker.ps1` instead of the legacy VBS launcher.


## Post-build checks

After launching the app, open **Diagnostics** and confirm that:

- execution mode is `frozen`
- runtime directory is writable
- dashboard/database parents are writable
- config and API key are available
