# Update Guide

CivitAI Tracker is a portable local app. Runtime data lives next to the app folder unless `config.json` points elsewhere.

## What To Back Up

Before major updates, back up:

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

## Source Mode

Source-mode users update through Git:

```powershell
git pull
.\install_requirements.bat
```

Then launch the app and run **Diagnostics**.

## EXE Mode

Open **Updates** in the app.

1. Run **Check now**.
2. Review the latest release notes.
3. Download the selected package.
4. Choose **Apply update**.

Automatic apply is available only in the packaged EXE build. The package must contain `CivitAITracker.exe` and the `_internal/` app folder. Source ZIP files are rejected before the app closes.

During apply, the updater:

- closes the running app;
- extracts the portable package;
- preserves local runtime data;
- backs up replaced app files under `updates\backup-<timestamp>\`;
- writes `updates\update_apply.log`;
- restarts the app when possible.

## Manual Package Fallback

If the in-app download fails, use **Open release**, download the ZIP in a browser, then choose **Select ZIP** in the Updates dialog.

If GitHub Release assets are unavailable on a network, publish the same portable ZIP to a direct-download mirror and add this line to the GitHub Release notes:

```text
Update package mirror: https://example.com/CivitAITracker-v<version>-win64.zip
```

When a mirror line is present, the EXE Update Center prefers it over GitHub Release assets.

## Release Packages

Build the release ZIP with:

```powershell
package_release.bat
```

Output:

```text
release\CivitAITracker-v<version>-win64.zip
```

Attach this ZIP to the GitHub Release when release assets are usable, and publish the same ZIP to the mirror used in the release notes.

## After Updating

Check:

- the app starts and shows the expected version;
- **Diagnostics** opens without errors;
- **Run now** completes;
- `logs\core_last.log` does not show a fatal error;
- `dashboard.html` shows a fresh `generated ...` timestamp;
- local `config.json`, `api_key.txt`, and `civitai_tracker.db` are still present.

## Rollback

If an update fails:

1. Close the app.
2. Restore the previous app folder or the relevant files from `updates\backup-<timestamp>\`.
3. Restore `config.json`, `api_key.txt`, and `civitai_tracker.db` from your manual backup if needed.
4. Launch the previous version and run **Diagnostics**.

Avoid using an older executable with a database that has already been migrated by a newer version unless you have a backup.

## Compatibility Notes

The app performs small SQLite migrations at startup or run time. These are automatic, but a database backup is still recommended before major updates.

Legacy config keys are normalized:

- `options.enable_buzz_ingest` maps to collection tracking.
- `collection_tracking.max_pages` maps into bootstrap and maintenance page limits.
- `collection_tracking.backfill_days` maps to `max_history_days`.

New configs should use `options.enable_collection_tracking`.
