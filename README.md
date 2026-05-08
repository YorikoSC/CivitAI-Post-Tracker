# CivitAI Tracker

A local Windows-first desktop app for tracking CivitAI post performance, collection activity, CSV snapshots, and a generated HTML dashboard.

The app keeps its runtime data on your machine. It can run from source or as a portable PyInstaller EXE build.

## Unofficial Tool And Platform Terms

CivitAI Tracker is an unofficial community tool. It is not affiliated with, endorsed by, or supported by CivitAI.

The app is intended for local personal analytics for a user's own CivitAI account and content. Users are responsible for complying with CivitAI's terms, platform rules, and applicable law.

Do not use this project to:

- bypass authentication, age gates, access controls, or content restrictions;
- scrape unrelated users or platform data at scale;
- manipulate CivitAI statistics, engagement, Buzz, rankings, or visibility;
- automate likes, comments, collections, follows, or other engagement actions;
- collect personal data without consent;
- package the project as a hosted paid analytics service or commercial redistribution that implies project or platform endorsement.

The source code is licensed under the MIT License. That license grants broad rights to use, copy, modify, and distribute the code. The statements above are project policy and usage guidance; they do not grant permission to violate CivitAI terms or imply that commercial services built from this project are endorsed or supported by the maintainers.

## What It Does

- Tracks CivitAI posts from a configured start point.
- Stores current reaction/comment totals and historical snapshots in SQLite.
- Exports CSV snapshots.
- Generates a local HTML dashboard with performance tables, preview thumbnails, charts, and collection analytics.
- Tracks collection additions for your images when authenticated access is available.
- Runs manual checks or automatic polling from a tray-enabled desktop UI.
- Checks GitHub Releases for portable EXE updates.

## Requirements

- Windows is the primary target.
- Python 3.11 or newer for source mode and local builds.
- A CivitAI API key is recommended and required for collection tracking.
- Desktop fonts are bundled with the app; users do not need to install them separately.

## API Key And Limited Mode

The app can start without a CivitAI API key. In that state it runs in limited public mode.

Without an API key:

- the main app should still open;
- public post checks can still run when CivitAI exposes the data publicly;
- collection tracking is unavailable;
- user-scoped transaction data is unavailable;
- restricted or NSFW posts may be missing or incomplete;
- dashboard totals may be incomplete for accounts that publish restricted content.

For full tracking, especially collection activity and restricted-content visibility, configure an API key in **Settings**.

The key can be stored inline in `config.json` or in a separate file such as `api_key.txt`. Do not commit either file.

## Quick Start: Source Mode

Install dependencies:

```powershell
python -m pip install -r requirements.txt
```

Create `config.json` from `config.example.json`, then launch:

```powershell
.\launch_tracker.pyw
```

For troubleshooting with a visible console:

```powershell
python tracker_app.py
```

`launch_tracker.bat` is also available as a console-backed launcher fallback.

## Quick Start: EXE Mode

Build the portable app:

```powershell
build_exe.bat
```

The build script creates and uses `.venv`, installs `requirements.txt`, installs PyInstaller, verifies runtime imports, and builds from that same environment.

Run:

```text
dist\CivitAITracker\CivitAITracker.exe
```

For a release ZIP:

```powershell
package_release.bat
```

The package is written to `release\CivitAITracker-v<version>-win64.zip`.

## Configuration Basics

The most important settings live in `config.json`:

```json
{
  "profile": {
    "username": "",
    "timezone": "UTC"
  },
  "auth": {
    "api_key": "",
    "api_key_file": "api_key.txt"
  },
  "tracking": {
    "start_mode": "post_id",
    "start_post_id": null,
    "start_date": null,
    "poll_minutes": 15
  },
  "options": {
    "check_updates_on_launch": true,
    "enable_collection_tracking": true
  }
}
```

Collection tracking options are under `collection_tracking`. The default config separates the first bootstrap sync from later maintenance syncs:

Automatic polling is intentionally conservative. The default is 15 minutes, and values below 5 minutes are raised to 5 minutes to avoid overly frequent CivitAI requests. Dashboard auto-refresh reloads the local HTML page every 5 minutes; it does not start a new CivitAI fetch by itself.

```json
{
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

Older configs are normalized at load time. `options.enable_buzz_ingest` is still accepted as a compatibility alias for `options.enable_collection_tracking`, but new configs should use `enable_collection_tracking`.

## Dashboard

The dashboard is generated as `dashboard.html` and opened from the desktop app. It includes:

- current tracking status;
- reaction and comment totals;
- daily and weekly movement;
- visual overview charts;
- suggested posting windows;
- post performance table with quick period filters;
- collection activity tables;
- thumbnail previews and post detail drawer when image metadata is available.

See `DASHBOARD_GUIDE.md` for interpretation notes and known limits.

## Updates

Source-mode users update through Git.

EXE users can use **Updates** in the app to check the latest GitHub Release, download a compatible portable ZIP package, and apply it automatically. Automatic apply is available only in the packaged EXE build.

The updater preserves local runtime data and stores replaced app files under `updates\backup-<timestamp>\`. Keep your own backup of `config.json`, `api_key.txt`, and `civitai_tracker.db` before major updates.

If GitHub Release assets are unavailable on your network, a release can provide a direct mirror URL in its notes:

```text
Update package mirror: https://example.com/CivitAITracker-v<version>-win64.zip
```

When a mirror line is present, the EXE Update Center prefers that package URL over GitHub Release assets.

See `UPDATE_GUIDE.md` for update, rollback, and release-package details.

## Local Data And Privacy

Runtime files are local and should not be committed:

- `config.json`
- `api_key.txt`
- `civitai_tracker.db`
- `csv/`
- `logs/`
- `dashboard.html`
- `runtime_status.json`
- `updates/`

To track multiple CivitAI accounts, use separate app folders. Each folder keeps its own config, API key file, database, logs, and dashboard.

## Troubleshooting

Open **Diagnostics** in the app first. It checks configuration, paths, API-key availability, and write access.

Run smoke tests:

```powershell
python tests\smoke_tests.py
```

Check the latest run summary:

```powershell
Get-Content .\logs\core_last.log
```

If collection tracking is empty, check that:

- an API key is configured;
- collection tracking is enabled;
- the account has recent collection events;
- `logs\core_last.log` does not report `collection_ingest.reason: API key required`.

If the dashboard looks stale, run the tracker again and check the `generated ...` timestamp in the dashboard header.

## Related Docs

- `DASHBOARD_GUIDE.md` - dashboard interpretation and limits.
- `UPDATE_GUIDE.md` - update and rollback flow.
- `EXE_BUILD.md` - build and package flow.
- `SECURITY.md` - vulnerability reporting and sensitive local data.
- `CONTRIBUTING.md` - issue, development, and pull-request guidance.
- `CHANGELOG.md` - release history.
