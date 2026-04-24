# CivitAI Tracker v8.8

A local desktop utility for tracking CivitAI post performance, generating CSV exports, and updating a local dashboard.

## What is new in v8.8

- Dashboard analytics tables now use a cleaner **single-column flow**
- Detailed analytics blocks are rendered as **collapsible sections** to reduce visual noise
- Open `dashboard.html` now **auto-refreshes every 60 seconds** while viewed in a browser
- Runtime status cards remain visible alongside the post analytics overview
- `launch_tracker.vbs` remains the main user launcher for the desktop app
- Windows autostart still uses the VBS launcher flow


## Core behavior

The tracker uses a one-shot collection core and a desktop runner:

- `tracker_core.py` performs one data collection run
- `tracker_runner.py` handles repeated polling
- `tracker_app.py` provides the desktop UI

This keeps the data collection logic simple while giving the user a friendlier always-on app.

## Requirements

- Python 3.11+
- A valid CivitAI API key
- Windows is the primary target for the desktop launcher flow

## Quick start

1. Install dependencies:

```powershell
python -m pip install -r requirements.txt
```

2. Start the app by double-clicking:

```text
launch_tracker.vbs
```

If `config.json` does not exist yet, the app opens the settings flow.

## Main launch options

### Recommended desktop mode

Double-click:

```text
launch_tracker.vbs
```

This launches the app without a visible console window.

### Development / debug mode

Run directly:

```powershell
python tracker_app.py
```

This is useful for debugging, but if you close the console window you also close the app.

### Compatibility launcher

`run_tracker_v8_5.bat` still exists, but it now just forwards to the VBS launcher.

## Timezone format

The app expects an **IANA timezone name**, for example:

- `Europe/Moscow`
- `Europe/Berlin`
- `America/New_York`
- `Asia/Tokyo`
- `UTC`

The desktop UI validates this before saving.

## Tray behavior

- Closing the window sends the app to the tray
- Auto polling continues while the app is hidden
- Use the tray menu to:
  - open the app
  - run a collection now
  - start or stop auto polling
  - open the dashboard
  - exit fully

## Output

A successful run updates:

- SQLite database
- CSV exports
- `dashboard.html`
- log files in `logs/`

## Notes

- Recommended API mode for full visibility: `red`
- `poll_minutes` controls the built-in app polling interval
- The app creates the external API key file automatically when file-based storage is selected
- `Launch with Windows` starts the app through the VBS-based launcher flow
- For a true set-and-forget mode, enable all three: `Launch with Windows`, `Start minimized to tray`, and `Start auto polling on launch`

## EXE packaging

A basic PyInstaller build flow is included.

See:

```text
EXE_BUILD.md
```

and run:

```text
build_exe.bat
```


## Dashboard refresh

The generated `dashboard.html` now refreshes itself every 60 seconds while open in a browser. You can also use the **Refresh now** button in the page header.
