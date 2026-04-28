# CivitAI Tracker v10.0-rc1

A local Windows-first desktop utility for tracking CivitAI post performance, exporting CSV snapshots, and generating a runtime-aware HTML dashboard.

v10 adds **collection tracking**: the dashboard can now show which of your images were added to collections and which posts were affected through those images.

## Features

- Local post analytics for CivitAI posts
- Reaction tracking by post
- Suggested posting windows based on historical performance
- HTML dashboard with runtime status
- Auto polling with tray support
- Source mode launcher via PowerShell
- EXE build flow via PyInstaller (`onedir`)
- Collection tracking for your images
- Image-to-post correlation for collection events

## Collection tracking

Collection tracking focuses on incoming engagement with your own content:

- **Added to collections**
- **Affected images**
- **Affected posts**
- Recent collection events
- Top posts/images by collection additions

The dashboard intentionally does not duplicate the existing reaction analytics. Likes/reactions remain in the existing reaction sections; the new Collections section focuses on collection additions.

## API key notes

The base public-post tracker can run in a limited mode without an API key, depending on what CivitAI exposes publicly at the time.

Collection tracking requires API key access because the tracker needs authenticated user-scoped transaction data to detect when your images are added to collections. If no API key is configured:

- the main tracker should still start;
- collection tracking is skipped/unavailable;
- the Collections section may be empty or unavailable;
- no collection events will be collected.

## Requirements

- Python 3.11+
- Windows is the primary target
- CivitAI API key recommended; required for collection tracking

## Quick start: source mode

1. Install dependencies:

```powershell
python -m pip install -r requirements.txt
```

2. Create or edit `config.json` based on `config.example.json`.

3. Launch the app:

```powershell
.\launch_tracker.ps1
```

Or directly:

```powershell
python tracker_app.py
```

## Quick start: EXE mode

1. Build the EXE:

```powershell
build_exe.bat
```

2. Open:

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

## Configuration

Important collection-related settings:

```json
{
  "options": {
    "enable_buzz_ingest": true
  },
  "collection_tracking": {
    "account_type": "blue",
    "backfill_days": 60,
    "overlap_hours": 24,
    "max_pages": 10
  }
}
```

The internal key name `enable_buzz_ingest` is kept for compatibility. User-facing documentation and dashboard terminology refer to this feature as collection tracking.

## Main files

- `tracker_app.py` — desktop UI
- `tracker_runner.py` — polling loop and runtime orchestration
- `tracker_service.py` — one-shot collection service and dashboard generation
- `tracker_core.py` — thin CLI wrapper around the service layer
- `buzz_ingest.py` — internal incoming engagement ingestion
- `engagement_correlation.py` — maps image-level events to tracked posts
- `engagement_dashboard.py` — renders the Collections dashboard section
- `config_utils.py` — config, path and diagnostics helpers

## Local data and privacy

The tracker stores runtime data locally:

- `config.json`
- `api_key.txt` if you choose file-based API key storage
- `civitai_tracker.db`
- `csv/`
- `logs/`
- `dashboard.html`
- `runtime_status.json`

Do not commit these files to GitHub.

## Troubleshooting

Run a syntax check:

```powershell
python -m py_compile tracker_app.py tracker_runner.py tracker_service.py tracker_core.py buzz_ingest.py engagement_correlation.py engagement_dashboard.py
```

Check the engagement table:

```powershell
python -c "import sqlite3; c=sqlite3.connect('civitai_tracker.db'); print(c.execute(\"SELECT COUNT(*) FROM content_engagement_events WHERE normalized_type='collection_like'\").fetchone()[0]); c.close()"
```

If collection tracking is empty, verify that your API key is configured and that recent collection events exist for your account.
