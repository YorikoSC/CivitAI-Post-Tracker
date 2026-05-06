# CivitAI Tracker v10.2.0

## Highlights

- Adds an in-app **Updates** dialog for checking GitHub Releases, reading release notes, downloading update packages, and applying compatible EXE updates.
- Adds optional background update checks on launch.
- Preserves local runtime data during automatic updates, including `config.json`, `api_key.txt`, `civitai_tracker.db`, `csv/`, `logs/`, `dashboard.html`, and `runtime_status.json`.
- Creates update backups under `updates\backup-<timestamp>\` and writes `updates\update_apply.log`.
- Adds visible app version information in the desktop UI.
- Adds an **Exit app** button for fully closing the tracker without using the tray menu.
- Hardens the Windows EXE build flow so project dependencies are installed into `.venv` before PyInstaller runs.
- Adds update-package validation so source ZIP files are not applied as EXE updates.
- Adds retry handling, manual **Select ZIP**, and release-note mirror support for networks where GitHub Release assets are unavailable.

## Update Package Mirror

If GitHub Release assets are unavailable on a network, publish the portable ZIP to a direct-download mirror and include a mirror line in the release notes.

Use a real direct ZIP URL before publishing:

```text
Update package mirror: https://example.com/CivitAITracker-v10.2.0-win64.zip
```

When this line is present, the EXE Update Center prefers the mirror over GitHub Release assets.

## Upgrade Notes

- EXE users can update through **Updates** once a compatible release package is available.
- Source-mode users should update through Git.
- Existing local configuration and database files are preserved by the automatic updater.
- A manual backup of `config.json`, `api_key.txt`, and `civitai_tracker.db` is still recommended before updating.

## Package

Expected portable package:

```text
CivitAITracker-v10.2.0-win64.zip
```

Build with:

```powershell
package_release.bat
```
