# CivitAI Tracker v9

A local Windows-first desktop utility for tracking CivitAI post performance, exporting CSV snapshots, and generating a runtime-aware HTML dashboard.

## Current state

This repository includes:

- desktop app with tray support
- autonomous background polling
- runtime diagnostics
- source mode launcher via PowerShell
- EXE build flow via PyInstaller (`onedir`)
- dashboard with:
  - runtime status cards
  - daily reaction summaries
  - best-post blocks
  - suggested posting windows
  - collapsible analytics sections

## Requirements

- Python 3.11+
- Windows is the primary target
- A valid CivitAI API key

## Quick start (source mode)

1. Install dependencies:

```powershell
python -m pip install -r requirements.txt
```

2. Run the setup/config flow:
- either start the app and save settings there
- or use the existing setup helper if you prefer

3. Launch the app:

```powershell
.\launch_tracker.ps1
```

## Quick start (EXE mode)

1. Build the EXE:

```powershell
build_exe.bat
```

2. Open the generated folder:

```text
dist\CivitAITracker
```

3. Run:

```text
CivitAITracker.exe
```

On first launch, open **Settings**, save your configuration, then use **Diagnostics** to confirm the environment is healthy.

## Recommended autonomous mode

For a mostly hands-off workflow:

- **Launch with Windows** = enabled
- **Start minimized to tray** = enabled
- **Start auto polling on launch** = enabled

## Main files

- `tracker_app.py` — desktop UI
- `tracker_runner.py` — polling loop and runtime orchestration
- `tracker_service.py` — one-shot collection service
- `tracker_core.py` — thin CLI wrapper around the service layer
- `config_utils.py` — config, paths, startup helpers
- `launch_tracker.ps1` — source-mode launcher
- `build_exe.bat` — PyInstaller build helper

## Diagnostics

The app includes a **Diagnostics** view that checks:

- execution mode (`source` / `frozen`)
- Python version
- config presence
- username/API key availability
- writable paths for runtime data, logs, DB, and dashboard

## Notes

- `civitai.red` is the recommended source mode for full visibility above PG-13.
- Personal files such as `config.json`, `api_key.txt`, databases, logs, CSV exports, and generated dashboards are intentionally excluded from the repository package.
- VBS is no longer treated as the main launcher strategy.

## Next planned investigation

Adding statistics for collected images and post throu buzz reward system.
