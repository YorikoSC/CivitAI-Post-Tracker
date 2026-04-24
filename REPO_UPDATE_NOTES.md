# Repo Update Notes

This package is a **clean repository update** for the current project state.

## What is included
- The latest desktop app files
- The current dashboard generator (`tracker_core.py`)
- Documentation and publishing helpers
- A safe `config.example.json` template

## What is intentionally not included
- `config.json`
- `api_key.txt`
- SQLite databases
- CSV exports
- `dashboard.html`
- `runtime_status.json`
- logs or any user-specific data

## Recommended update flow
1. Back up your existing repository folder.
2. Copy these files over the repository working tree.
3. Review the diff, especially:
   - `tracker_core.py`
   - `README.md`
   - `CHANGELOG.md`
4. Commit and push the update.
