# EXE Build Guide

The supported Windows build is a PyInstaller `onedir` package driven by the project-local `.venv`.

This keeps build dependencies out of Microsoft Store Python and other global Python installs.

## Build

```powershell
build_exe.bat
```

The script:

- creates `.venv` if needed;
- installs `requirements.txt` into `.venv`;
- installs or updates PyInstaller in `.venv`;
- verifies runtime imports such as `requests` and `customtkinter`;
- runs PyInstaller from the same `.venv`.

Output:

```text
dist\CivitAITracker\CivitAITracker.exe
```

If dependency installation fails, fix the local `.venv` first:

```powershell
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe -m pip install --upgrade pyinstaller
```

## Release Package

```powershell
package_release.bat
```

Output:

```text
release\CivitAITracker-v<version>-win64.zip
```

The package must contain `CivitAITracker.exe` and `_internal/` in the same app folder. Keep the `CivitAITracker-v<version>-win64.zip` naming pattern so the updater can distinguish the portable package from source archives.

If GitHub Release assets are unreliable for the target audience, upload the same ZIP to a direct-download mirror and include this line in the GitHub Release notes:

```text
Update package mirror: https://example.com/CivitAITracker-v<version>-win64.zip
```

## Build Smoke Test

After building:

1. Run `dist\CivitAITracker\CivitAITracker.exe --version`.
2. Launch `dist\CivitAITracker\CivitAITracker.exe`.
3. Open **Diagnostics**.
4. Save **Settings** if testing from a fresh folder.
5. Run **Run now**.
6. Open the dashboard and confirm the `generated ...` timestamp changed.
7. Confirm dashboard previews, workspace tabs, filters, and detail drawers render correctly.
8. Confirm the main window Activity area and status footer are visible at the default size.
9. Confirm Settings, Updates, and Diagnostics open with readable text.
10. Check `logs\core_last.log` for fatal errors.

When testing collection tracking, use a configured API key and confirm `collection_ingest.ok` is true or that any reported reason is expected.

## Do Not Ship Runtime Data

Do not include:

- `config.json`
- `api_key.txt`
- `*.db`
- `csv/`
- `logs/`
- `dashboard.html`
- `runtime_status.json`
- `updates/`
