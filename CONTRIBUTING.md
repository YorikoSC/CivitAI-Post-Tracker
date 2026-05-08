# Contributing

Thanks for helping improve CivitAI Tracker.

This is a Windows-first local desktop app. Contributions should keep the app practical, privacy-conscious, and understandable for non-technical users.

## Before Opening An Issue

Please check:

- the latest release is installed;
- **Diagnostics** opens without a startup error;
- `logs/core_last.log` does not show an obvious configuration problem;
- `config.json`, `api_key.txt`, database files, and logs are not attached publicly without redaction.

For security-sensitive reports, follow `SECURITY.md`.

## Bug Reports

Good bug reports include:

- app version;
- source mode or packaged EXE mode;
- Windows version;
- what you expected to happen;
- what happened instead;
- reproduction steps;
- relevant redacted log lines.

Do not include API keys, private database files, or unredacted local paths unless they are required and safe to share.

## Development Setup

Install dependencies:

```powershell
python -m pip install -r requirements.txt
```

Run the app from source:

```powershell
python tracker_app.py
```

Run smoke tests:

```powershell
python tests\smoke_tests.py
```

Build the portable EXE package:

```powershell
package_release.bat
```

## Pull Requests

Pull requests should:

- keep changes focused;
- avoid committing runtime data or local secrets;
- update docs when behavior changes;
- include or update smoke tests for behavior that can regress;
- preserve source-mode and packaged-EXE workflows when possible.

Before opening a pull request, run:

```powershell
python tests\smoke_tests.py
git diff --check
```

## Project Boundaries

The app is a local analytics tool. Avoid changes that:

- bypass authentication or platform access controls;
- encourage abusive scraping or high-volume automated requests;
- weaken API key handling;
- apply update packages without validating their portable app layout.

