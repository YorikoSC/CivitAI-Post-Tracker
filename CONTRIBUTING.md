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

The app is a local personal analytics tool for a user's own CivitAI account and content.

Maintainers will not accept issues or pull requests that add, encourage, or normalize behavior that violates platform rules or turns the project into an abusive automation tool.

Avoid changes that:

- bypass authentication or platform access controls;
- bypass age gates, restricted-content visibility, or account-level permissions;
- encourage abusive scraping, high-volume account discovery, or bulk harvesting of unrelated users' data;
- automate likes, comments, collections, follows, Buzz activity, rankings, or other engagement manipulation;
- collect personal data without consent;
- remove reasonable polling limits, timeouts, or user-controlled tracking scope;
- weaken API key handling;
- turn the project into a hosted multi-tenant service, paid analytics product, or commercial redistribution path;
- imply affiliation with or endorsement by CivitAI;
- apply update packages without validating their portable app layout.

The repository uses the MIT License, so this section is not a substitute for legal license terms. It is the maintainer policy for what this project will accept, support, and distribute.
