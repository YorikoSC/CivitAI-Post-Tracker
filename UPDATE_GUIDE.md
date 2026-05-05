# Update Guide

This project is distributed as a local portable app. Runtime data lives next to the app unless your `config.json` points somewhere else.

## Before Updating

1. Close `CivitAITracker.exe`.
2. Confirm auto polling is stopped.
3. Back up these local files and folders if they exist:

```text
config.json
api_key.txt
civitai_tracker.db
csv/
logs/
dashboard.html
runtime_status.json
```

The most important files are `config.json`, `api_key.txt`, and `civitai_tracker.db`.

## In-App Update Check

1. Open **Updates** in the app.
2. Wait for the GitHub release check.
3. If an update is available, open the release page or download the attached ZIP package.
4. The downloaded package is saved to `updates/`.
5. In EXE mode, choose **Apply downloaded update** to close the app, back up replaced app files, apply the package, and restart.

The app can also check for updates in the background on launch. This can be changed in **Settings**.

The update applier preserves local runtime data such as `config.json`, `api_key.txt`, `civitai_tracker.db`, `csv/`, `logs/`, `dashboard.html`, and `runtime_status.json`.

Automatic apply is intentionally limited to the packaged EXE build. Source-mode users should update through Git.

## Release Packages

Release ZIP packages should be built with:

```powershell
package_release.bat
```

The package is written to:

```text
release\CivitAITracker-v<version>-win64.zip
```

Attach that ZIP to the GitHub Release so the app can find and download it.

## Portable EXE Update

1. Build or download the new `dist\CivitAITracker` folder.
2. Keep your existing runtime files from the old folder.
3. Replace the application files with the new build.
4. Copy your runtime files back if you updated into a fresh folder.
5. Start `CivitAITracker.exe`.
6. Open Diagnostics.
7. Run now.
8. Open the dashboard and confirm the generated timestamp changed.

## Update Backups

Automatic updates store replaced app files under:

```text
updates\backup-<timestamp>\
```

The updater writes its log to:

```text
updates\update_apply.log
```

## Database Migrations

The tracker performs small SQLite migrations at startup/run time. For v10.1, the `post_images` table gains:

- `image_url`
- `thumbnail_url`

These columns are added automatically. Keep a database backup before the first run after any update.

## Config Compatibility

Older collection settings are still normalized:

- `options.enable_buzz_ingest` still maps to collection tracking.
- `collection_tracking.max_pages` maps into bootstrap and maintenance page limits.
- `collection_tracking.backfill_days` maps into `max_history_days`.

New configs should use `options.enable_collection_tracking`.

## After Updating

Check:

- Diagnostics opens without errors.
- `Run now` completes.
- `logs\core_last.log` has `ok: true` for core sections.
- `dashboard.html` shows the new app version in the header.
- The dashboard preview images render or fall back to `Open image`.

## Rollback

If an update fails:

1. Close the app.
2. Restore the previous app folder.
3. Restore the backed-up `config.json`, `api_key.txt`, and `civitai_tracker.db`.
4. Launch the previous version and run Diagnostics.

Avoid mixing an older executable with a database after a newer version has already migrated it unless you have a backup.
