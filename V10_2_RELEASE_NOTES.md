# CivitAI Tracker v10.2.0

v10.2.0 adds the first production-ready Update Center for the portable Windows build.

## Highlights

- Adds an in-app **Updates** dialog for checking GitHub Releases, reading release notes, downloading update packages, and applying EXE updates.
- Adds optional background update checks on launch.
- Adds automatic update apply for the packaged EXE build.
- Preserves local runtime data during updates, including `config.json`, `api_key.txt`, `civitai_tracker.db`, `csv/`, `logs/`, `dashboard.html`, and `runtime_status.json`.
- Creates update backups under `updates\backup-<timestamp>\`.
- Writes updater logs to `updates\update_apply.log`.
- Adds visible app version information in the desktop UI.
- Adds an **Exit app** button for fully closing the tracker without using the tray menu.
- Hardens the Windows EXE build flow so project dependencies are installed into `.venv` before PyInstaller runs.
- Fixes missing runtime dependency failures such as `ModuleNotFoundError: requests`.
- Adds validation so source ZIP files are not applied as EXE updates.
- Adds retry handling, manual **Select ZIP**, and release-note mirror support for networks where GitHub Release assets are unavailable.

## Update Package Mirror

Some networks can download GitHub source ZIP files but time out on GitHub Release assets. v10.2.0 supports mirror URLs in release notes.

Replace the URL below with the real direct ZIP URL before publishing the GitHub Release:

```text
Update package mirror: https://sourceforge.net/projects/civitai-post-tracker/files/CivitAITracker-v10.2.0-win64.zip/download
```

When this line is present, the EXE Update Center prefers the mirror over GitHub Release assets.

## Upgrade Notes

- EXE users can update through **Updates** once a release package and mirror are available.
- Source-mode users should update through Git.
- Existing local configuration and database files are preserved by the automatic updater.
- Keeping a manual backup of `config.json`, `api_key.txt`, and `civitai_tracker.db` is still recommended before major updates.

## Package

Build the portable ZIP with:

```powershell
package_release.bat
```

Expected package name:

```text
CivitAITracker-v10.2.0-win64.zip
```

## Verification

- Smoke tests pass.
- PyInstaller package builds from `.venv`.
- Release ZIP validates as a portable package.
- Field testing confirmed update apply works through a mirror package URL.
