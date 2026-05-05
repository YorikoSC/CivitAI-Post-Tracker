# CivitAI Tracker v10.2.0-dev

A local Windows-first desktop utility for tracking CivitAI post performance, exporting CSV snapshots, and generating a runtime-aware HTML dashboard.

v10 adds **collection tracking**: the dashboard can now show which of your images were added to collections and which posts were affected through those images.

v10.1 turns the dashboard into a monitoring workspace: post performance rows, lazy image previews, a post detail drawer, and filtered analytics tabs for performance, collections, timing, and history.

v10.1.1 is a patch release focused on reliable source/EXE startup, no-console launching from Explorer, and one-instance-per-folder safety.

v10.2 development starts with a built-in Update Center for checking GitHub releases and downloading portable update packages.

## Features

- Local post analytics for CivitAI posts
- Reaction tracking by post
- Post performance table with current totals, recent gains, early-window snapshots, and collection activity
- Lazy-loaded thumbnail previews and a post detail drawer in the dashboard
- Visual dashboard overview with daily activity, reaction mix, and top movement charts
- Tabbed dashboard workspace with table search, period filters, and quick filters
- Suggested posting windows based on historical performance
- HTML dashboard with runtime status
- Auto polling with tray support
- Update Center for GitHub release checks and portable package downloads
- Source mode launcher via windowed Python launcher
- Single running instance per app folder to protect the local config and database
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

The app can start without an API key, but it will run in **limited public mode**. This mode is useful for basic public checks, but it is not equivalent to full tracking.

Without an API key:

- the main app should still start;
- collection tracking is unavailable;
- user-scoped transaction data is unavailable;
- restricted / NSFW posts may be missing or incomplete, depending on what CivitAI exposes publicly;
- dashboard statistics may be incomplete for accounts that publish PG-13+ / restricted content.

For full tracking, especially collection tracking and restricted-content visibility, provide a CivitAI API key.

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
.\launch_tracker.pyw
```

For source debugging with the console visible:

```powershell
python tracker_app.py
```

If the windowed launcher needs troubleshooting, `launch_tracker.bat` remains available as a console-backed fallback.

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

## Updating

Open **Updates** in the app to check the latest GitHub release and download an attached portable ZIP package.

By default, the app also checks for updates in the background on launch. The app is still updated as a portable folder replacement. Before replacing app files, close the app and back up `config.json`, `api_key.txt`, and `civitai_tracker.db`.

See `UPDATE_GUIDE.md` for the full update and rollback checklist.

## Recommended autonomous mode

For a mostly hands-off workflow:

- **Launch with Windows** = enabled
- **Start minimized to tray** = enabled
- **Start auto polling on launch** = enabled

## Configuration

Important collection-related settings:

```json
{
  "app": {
    "config_version": 2
  },
  "options": {
    "check_updates_on_launch": true,
    "enable_collection_tracking": true
  },
  "collection_tracking": {
    "account_type": "blue",
    "bootstrap_max_pages": 100,
    "maintenance_max_pages": 10,
    "overlap_hours": 24,
    "max_history_days": 120,
    "http_timeout_seconds": 60
  }
}
```

The old internal key name `enable_buzz_ingest` is still accepted for compatibility. New configs should use `enable_collection_tracking`, and the dashboard refers to this feature as collection tracking.

Older configs that still use `collection_tracking.max_pages` or `collection_tracking.backfill_days` are normalized into the newer `bootstrap_max_pages`, `maintenance_max_pages`, and `max_history_days` fields.

## Main files

- `tracker_app.py` — desktop UI
- `tracker_runner.py` — polling loop and runtime orchestration
- `tracker_service.py` — one-shot collection service and dashboard generation
- `tracker_core.py` — thin CLI wrapper around the service layer
- `buzz_ingest.py` — internal incoming engagement ingestion
- `collection_runtime.py` — collection config normalization and sync-mode decisions
- `collection_sync_state.py` — collection sync state schema and persistence
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
python -m py_compile tracker_app.py tracker_runner.py tracker_service.py tracker_core.py buzz_ingest.py collection_runtime.py collection_sync_state.py engagement_correlation.py engagement_dashboard.py
```

Run smoke tests:

```powershell
python tests\smoke_tests.py
```

Check the latest run summary:

```powershell
Get-Content .\logs\core_last.log
```

For collection tracking, inspect:

- `collection_ingest.ok`
- `collection_ingest.collection_mode`
- `collection_ingest.pages_fetched`
- `collection_ingest.events_seen`
- `collection_ingest.events_core`
- `collection_ingest.type_counts`
- `collection_ingest.stop_reason`
- `engagement_correlation.ok`
- `engagement_correlation.distinct_posts_correlated`

Check the engagement table directly:

```powershell
python -c "import sqlite3; c=sqlite3.connect('civitai_tracker.db'); print(c.execute(\"SELECT COUNT(*) FROM content_engagement_events WHERE normalized_type='collection_like'\").fetchone()[0]); c.close()"
```

If collection tracking is empty, verify that your API key is configured, recent collection events exist for your account, and `collection_ingest.page_summaries` contains `collectedContent:image`.

## Build verification

Before publishing or merging a cleanup branch:

1. Run the syntax check above.
2. Launch `launch_tracker.pyw` from Explorer and confirm the app opens without a console window.
3. Run the app from source and confirm `Run now` updates `logs/core_last.log`.
4. Build with `build_exe.bat`.
5. Launch `dist\CivitAITracker\CivitAITracker.exe`.
6. Test with and without an API key.
7. Open the dashboard from the app and confirm the `generated ...` timestamp changes after a run.


## Collection sync modes

Collection tracking now uses two modes:

- **bootstrap**: first full collection history load within the safe backfill window;
- **maintenance**: lightweight incremental sync after bootstrap is complete.

If the available source history ends before the selected tracking start, the tracker treats that as a normal completed load. If the page limit is reached first, collection totals are marked as potentially incomplete.

The generated dashboard is rewritten atomically and opened with a cache-busting version parameter from the desktop app. If the dashboard looks stale, check the `generated ...` timestamp at the top of the page first.
