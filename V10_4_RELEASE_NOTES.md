# CivitAI Tracker v10.4.0

## Highlights

- Adds a dashboard Performance board for faster scanning of active posts.
- Groups compact cards into Recent momentum, Collection movers, and Fresh posts.
- Keeps the full per-post performance table below the board for detailed review.
- Adds a Timing board for best posting hours, weekdays, and the recommendation basis.
- Adds a History board for all-time and early-window leaders.
- Lets performance cards open the existing post detail drawer, matching table-row behavior.
- Keeps collection event storage and dashboard details focused on image/post identifiers instead of collector/actor identifiers.
- Improves source-mode setup and fallback launcher diagnostics around the local `.venv`.

## Upgrade Notes

- EXE users can update through **Updates** once the portable package is available.
- Source-mode users should update through Git, then run `install_requirements.bat`.
- Existing local configuration, API key, database, CSV files, logs, dashboard output, and update backups are preserved by the automatic updater.
- The primary source launcher remains `launch_tracker.pyw`; use `launch_tracker.bat` only as a fallback when you need console diagnostics.
- If a fallback launch reports that the local Python environment is not ready, rerun `install_requirements.bat`.

## Package

Expected portable package:

```text
CivitAITracker-v10.4.0-win64.zip
```

Build with:

```powershell
package_release.bat
```

If GitHub Release assets are unavailable on your network, publish the same ZIP to a direct-download mirror and include:

```text
Update package mirror: https://sourceforge.net/projects/civitai-post-tracker/files/CivitAITracker-v10.4.0-win64.zip/download
```
