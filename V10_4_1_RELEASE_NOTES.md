# CivitAI Tracker v10.4.1

## What This Release Is

This is a small feature patch, not a hotfix. It was mostly added for my own analytics workflow: export the tracker data as clean CSV, then give it to an AI assistant or another analysis tool and ask questions like which posts grew fastest, which publication times worked better, and how reactions changed over time.

The same export can be useful for any user who wants to analyze their CivitAI posting history outside the built-in dashboard.

## Added

- **Export data** action in the desktop app.
- CLI export mode:

```powershell
.venv\Scripts\python.exe tracker_service.py --export-analytics
```

- New `analytics_export/` folder with stable analysis-ready files:

```text
posts_summary.csv
post_snapshots.csv
post_deltas.csv
post_images.csv
export_metadata.json
```

- UTF-8 CSV output with comma separators.
- UTC timestamps plus local timestamps based on the configured profile timezone.
- Snapshot-to-snapshot deltas for reactions, comments, and collection counts.
- First-window reaction samples include elapsed/distance columns and are left blank when the nearest snapshot is too far from the target time.
- Optional image metadata storage for width, height, prompt, negative prompt, model, sampler, steps, CFG, and seed when CivitAI provides those fields.

## Data Scope

The export is meant for external analysis, including AI-assisted analysis. It focuses on tracked posts, snapshots, deltas, images, and aggregate counts rather than the full internal database.

View-count columns are included for a stable export shape, but the current CivitAI source used by the tracker does not provide view counts, so those fields are blank.

## Upgrade Notes

- EXE users can update through **Updates** once the portable package is available.
- Source-mode users should update through Git, then run `install_requirements.bat`.
- Existing local configuration, API key, database, CSV files, logs, dashboard output, and update backups are preserved by the automatic updater.
- `analytics_export/` is local runtime output and should not be committed.

## Package

Expected portable package:

```text
CivitAITracker-v10.4.1-win64.zip
```

Build with:

```powershell
package_release.bat
```

If GitHub Release assets are unavailable on your network, publish the same ZIP to a direct-download mirror and include:

```text
Update package mirror: https://sourceforge.net/projects/civitai-post-tracker/files/CivitAITracker-v10.4.1-win64.zip/download
```
