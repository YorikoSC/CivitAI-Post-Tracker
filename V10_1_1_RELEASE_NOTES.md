# CivitAI Tracker v10.1.1

v10.1.1 is a stability patch for the v10.1 dashboard monitoring release.

## Highlights

- Replaces the removed PowerShell source launcher with `launch_tracker.pyw`, a no-console launcher intended for Explorer/right-click startup.
- Keeps `launch_tracker.bat` as a visible-console fallback for troubleshooting.
- Improves PyInstaller builds by preferring the local `.venv` Python environment and isolating user-site packages during the build.
- Adds frozen Tcl/Tk startup preparation to improve EXE reliability from non-ASCII Windows paths.
- Prevents multiple app instances from running from the same folder at the same time, protecting the shared config, API key file, and local database.
- Keeps private Codex context notes out of tracked release files.

## Upgrade Notes

- Existing v10.1 users can update by replacing the app files while keeping their local `config.json`, `api_key.txt`, and `civitai_tracker.db`.
- To track another CivitAI account, make a separate copy of the app folder. Each folder should have its own config, API key, and database.
- v10.2 remains the better target for broader UI changes, an in-app Exit button, and a more formal multi-account/profile experience.

## Verification

- Source launcher opens normally from Explorer.
- Built EXE opens normally from `dist\CivitAITracker\CivitAITracker.exe`.
- Smoke tests pass.
- PyInstaller build completes successfully.
