# EXE build notes

Recommended build mode: **PyInstaller onedir**.

## Build

```powershell
build_exe.bat
```

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

## Do not ship personal runtime files

Do not include:

- `config.json`
- `api_key.txt`
- `*.db`
- `csv/`
- `logs/`
- `dashboard.html`
- `runtime_status.json`
