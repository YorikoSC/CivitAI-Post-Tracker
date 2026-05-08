# CivitAI Tracker v10.3.0

## Highlights

- Refreshes the desktop app with a CustomTkinter-based interface.
- Improves readability across the main window, Settings, Updates, and Diagnostics.
- Bundles Exo 2 and Russo One font files so the packaged app does not depend on system-installed fonts.
- Keeps the main Activity area and status footer visible at the default window size.
- Uses themed scrollbars and larger text areas in secondary windows.
- Keeps the existing Dashboard, update flow, local data model, and API-key behavior unchanged.

## Upgrade Notes

- EXE users can update through **Updates** once the portable package is available.
- Source-mode users should update through Git, then run `python -m pip install -r requirements.txt`.
- Existing local configuration, API key, database, CSV files, logs, dashboard output, and update backups are preserved by the automatic updater.
- A manual backup of `config.json`, `api_key.txt`, and `civitai_tracker.db` is still recommended before updating.

## Package

Expected portable package:

```text
CivitAITracker-v10.3.0-win64.zip
```

Build with:

```powershell
package_release.bat
```

If GitHub Release assets are unavailable on your network, publish the same ZIP to a direct-download mirror and include:

```text
Update package mirror: https://example.com/CivitAITracker-v10.3.0-win64.zip
```
