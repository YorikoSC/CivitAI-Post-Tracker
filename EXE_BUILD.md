# EXE build notes

Recommended build mode: **PyInstaller onedir**.

## Build

```powershell
build_exe.bat
```

Output:

```text
dist\CivitAITracker\CivitAITracker.exe
```

## What to test after build

1. Launch `CivitAITracker.exe`.
2. Open Diagnostics.
3. Save Settings if this is a fresh folder.
4. Run now.
5. Open dashboard.
6. Confirm the Collections section appears when collection data exists.

## v10 modules

The v10 collection tracking layer uses:

- `buzz_ingest.py`
- `engagement_correlation.py`
- `engagement_dashboard.py`

They are imported by `tracker_service.py` and should be picked up by PyInstaller. The spec also lists them explicitly as hidden imports for safety.

## Do not ship personal runtime files

Do not include:

- `config.json`
- `api_key.txt`
- `*.db`
- `csv/`
- `logs/`
- `dashboard.html`
- `runtime_status.json`
