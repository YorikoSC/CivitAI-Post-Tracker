# EXE build notes

Recommended build mode: **PyInstaller onedir**.

The build is intentionally driven by the project-local `.venv`. This avoids installing packages into Microsoft Store Python or any other global Python environment.

## Build

```powershell
build_exe.bat
```

`build_exe.bat` will:

- create `.venv` if it does not exist;
- install `requirements.txt` into `.venv`;
- install or update PyInstaller in `.venv`;
- verify runtime imports such as `requests`;
- run PyInstaller from the same `.venv`.

The script limits pip retry/timeout behavior so an offline or restricted machine fails quickly instead of hanging on long PyPI retries.

Output:

```text
dist\CivitAITracker\CivitAITracker.exe
```

## Release ZIP

```powershell
package_release.bat
```

Output:

```text
release\CivitAITracker-v<version>-win64.zip
```

Attach this ZIP to the GitHub Release. The in-app Update Center looks for ZIP assets on the latest release.

The EXE updater expects a portable package with `CivitAITracker.exe` and `_internal/` in the same app folder. Keep the generated `CivitAITracker-v<version>-win64.zip` naming pattern so the app can distinguish it from source archives.

If GitHub Release assets are unavailable on the target network, upload the same ZIP to a mirror and add this line to the GitHub Release notes:

```text
Update package mirror: https://example.com/CivitAITracker-v<version>-win64.zip
```

The EXE Update Center prefers mirror package URLs over GitHub Release assets.

## What to test after build

1. Launch `CivitAITracker.exe`.
2. Open Diagnostics.
3. Save Settings if this is a fresh folder.
4. Run now.
5. Open dashboard.
6. Confirm the Collections section appears when collection data exists.
7. Confirm the Post performance table appears in the Analytics workspace.
8. Confirm the Performance and Collections period filters work: day, week, month, year, all time.
9. Confirm post thumbnails load, or fall back to `Open image` when only an image ID is known.
10. Confirm `Open image` collection fallbacks stay aligned inside the preview column.
11. Confirm the Visual overview charts appear above the posting recommendations.
12. Confirm clicking a post performance row opens the post detail drawer.
13. Confirm collection image rows link to `/images/{image_id}` when no local post mapping exists.
14. Confirm the dashboard header `generated ...` timestamp changes after a new run.
15. Check `logs\core_last.log` and confirm `collection_ingest.ok` is true when an API key is configured.

## v10 / v10.1 modules

The v10 collection tracking and v10.1 dashboard monitoring layers use:

- `buzz_ingest.py`
- `collection_runtime.py`
- `collection_sync_state.py`
- `engagement_correlation.py`
- `engagement_dashboard.py`
- `tracker_service.py`

The collection modules are imported by `tracker_service.py` and should be picked up by PyInstaller. The spec also lists them explicitly as hidden imports for safety.

`requests` is also listed explicitly as a hidden import as a defensive check, but the primary guarantee is that `requirements.txt` is installed into the same `.venv` used by PyInstaller.

## Do not ship personal runtime files

Do not include:

- `config.json`
- `api_key.txt`
- `*.db`
- `csv/`
- `logs/`
- `dashboard.html`
- `runtime_status.json`
