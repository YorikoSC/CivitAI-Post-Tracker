# CivitAI Post Tracker

A local self-hosted tracker for CivitAI post analytics.

This project collects post-level statistics from CivitAI and generates a local HTML dashboard with historical tracking, daily reaction summaries, best-performing posts, and suggested posting windows.

## Features

- Tracks **posts**, not just individual images
- Uses **tRPC `post.getInfinite`** as the primary source
- Supports tracking start from:
  - a **post ID / post URL**
  - or a **start date**
- Stores history in **SQLite**
- Exports **CSV**
- Builds a local **`dashboard.html`**
- Supports **`civitai.red`** as the recommended primary host
- Includes a setup wizard for first-time configuration

## Requirements

- Python 3.11+
- A valid CivitAI API key

## Quick start

1. Install dependencies:

```powershell
python -m pip install -r requirements.txt
```

2. Run the setup wizard:

```powershell
python setup_config.py
```

3. If you selected file-based API key storage, create `api_key.txt` and put your API key there as a single line.

4. Run the tracker:

```powershell
python tracker_v8_2.py
```

Or use:

```text
run_tracker_v8_2.bat
```

## Configuration

The project uses a local `config.json` file created by the setup wizard.

Do not share your personal `config.json` or `api_key.txt`.

Use `config.example.json` as a template only.

## Main config fields

- `profile.username` — your CivitAI username
- `profile.display_name` — optional friendly display name for the dashboard
- `profile.timezone` — your IANA timezone, for example `Europe/Moscow`
- `tracking.start_mode` — `post_id` or `date`
- `tracking.start_post_id` — tracking start post ID
- `tracking.start_date` — tracking start date in `YYYY-MM-DD`
- `tracking.poll_minutes` — intended polling interval for external schedulers
- `api.mode` — recommended value: `red`
- `api.view_host` — recommended value: `https://civitai.red`
- `api.nsfw_level` — recommended value: `X`

## Recommended host mode

Because `civitai.com` may exclude content above PG-13, the recommended mode for full tracking is:

```json
"mode": "red"
```

## Output files

After a successful run, the tracker generates:

- a SQLite database
- CSV exports
- `dashboard.html`

## Running on a schedule

The tracker performs a **single collection run** and exits.

To update it automatically, use an external scheduler such as:

- Windows Task Scheduler
- `cron`
- `systemd` timer

## Security notes

- Never publish your `config.json`
- Never publish your `api_key.txt`
- Never commit generated local databases or CSV files
- Never commit your generated `dashboard.html` if it contains personal data or private analytics

## Repository hygiene

Before creating a public release:

- verify that `config.example.json` contains only placeholders
- remove local DB files, CSV exports, and generated HTML
- make sure `.gitignore` is present
- create a GitHub release from a clean working tree

## Suggested repository structure

```text
civitai-post-tracker/
├─ tracker_v8_2.py
├─ setup_config.py
├─ config_utils.py
├─ config.example.json
├─ requirements.txt
├─ run_tracker_v8_2.bat
├─ README.md
├─ DASHBOARD_GUIDE.md
├─ CHANGELOG.md
├─ LICENSE
└─ .gitignore
```

## License

MIT for public distribution.
