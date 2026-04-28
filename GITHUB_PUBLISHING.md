# Publishing to GitHub

## Suggested version

Start with:

```text
v10.0-rc1
```

After source and EXE verification:

```text
v10.0
```

## Suggested release title

```text
CivitAI Tracker v10.0 — Collection Tracking
```

## Before commit

Check that these files are not staged:

- `config.json`
- `api_key.txt`
- `*.db`
- `csv/`
- `logs/`
- `dashboard.html`
- `runtime_status.json`
- `build/`
- `dist/`

## Minimal commands

```powershell
git status
git add .
git commit -m "Release v10.0-rc1 collection tracking"
git tag v10.0-rc1
git push
git push --tags
```
