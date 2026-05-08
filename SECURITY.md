# Security Policy

## Supported Versions

Security fixes are handled for the latest public release and the current `main` branch.

| Version | Supported |
| --- | --- |
| 10.3.x | Yes |
| Older releases | No |

This table should be updated when a new public release becomes the maintained version.

## Reporting A Vulnerability

Please do not publish exploit details, API keys, database files, or private logs in a public issue.

If GitHub private vulnerability reporting is enabled for this repository, use **Report a vulnerability** from the repository Security page.

If private reporting is not available, open a short public issue saying that you need to report a security problem. Do not include sensitive details in that issue.

Useful high-level information:

- app version;
- source mode or packaged EXE mode;
- Windows version;
- whether the issue affects API key handling, update packages, local files, or network requests;
- a minimal description of the impact.

## Sensitive Local Data

Do not share these files publicly unless you have reviewed and redacted them:

- `config.json`
- `api_key.txt`
- `civitai_tracker.db`
- files under `logs/`
- files under `csv/`
- `dashboard.html`
- `runtime_status.json`

The app stores runtime data locally. Reports should include only the minimum data needed to reproduce a problem.

## Update Package Safety

Official portable builds are distributed as `CivitAITracker-v<version>-win64.zip` packages. The in-app updater validates that an update package contains the expected portable app layout before applying it.

Do not apply ZIP files from untrusted sources.

