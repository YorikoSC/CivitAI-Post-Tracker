# Release Checklist

Use this checklist before publishing the repository or creating a release archive.

## Safety
- [ ] `config.json` is NOT present
- [ ] `api_key.txt` is NOT present
- [ ] No personal usernames remain in templates or docs
- [ ] No local `.db` files are present
- [ ] No `csv/` output is present
- [ ] No generated `dashboard.html` is present

## Repository files
- [ ] `README.md` is up to date
- [ ] `DASHBOARD_GUIDE.md` is up to date
- [ ] `CHANGELOG.md` is up to date
- [ ] `.gitignore` is present
- [ ] `config.example.json` contains placeholders only
- [ ] `LICENSE` is present

## Release prep
- [ ] Run the tracker once from a clean checkout
- [ ] Verify setup wizard creates a fresh local config
- [ ] Verify the dashboard renders correctly
- [ ] Tag the version
- [ ] Create a release archive from clean files only
