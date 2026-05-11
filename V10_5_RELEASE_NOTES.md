# CivitAI Tracker v10.5.0

## What This Release Is

This is a small quality-of-life release. It makes a fresh setup easier and makes available updates more visible inside the app.

## Added

- First-run setup wizard for new app folders.
- Simple setup flow for profile, access mode, tracking start point, and first scan.
- Background update checks while the app stays open.
- Small marker on the main **Updates** action when a newer release is available.

## Upgrade Notes

- EXE users can update through **Updates** once the portable package is available.
- Source-mode users should update through Git, then run `install_requirements.bat`.
- Existing local configuration, API key, database, CSV files, logs, dashboard output, and update backups are preserved by the automatic updater.
- Existing users do not need to rerun setup; the wizard is mainly for new or incomplete local configs.

## Package

Expected portable package:

```text
CivitAITracker-v10.5.0-win64.zip
```

Build with:

```powershell
package_release.bat
```

If GitHub Release assets are unavailable on your network, publish the same ZIP to a direct-download mirror and include:

```text
Update package mirror: https://sourceforge.net/projects/civitai-post-tracker/files/CivitAITracker-v10.5.0-win64.zip/download
```
