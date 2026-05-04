# Codex Handoff

## Project

CivitAI Post Tracker

## Branch

`feature/dashboard-monitoring`

## Working Agreement

Use this file as the lightweight handoff between Codex sessions on different PCs. Keep it current when a meaningful task is finished or when work pauses.

Do not store secrets, API keys, local database contents, `config.json`, logs, or machine-specific paths here.

## Current Goal

Finish the v10.1 dashboard monitoring release candidate.

## Done In This Session

- Added the `Post performance` dashboard table.
- Added per-post performance row calculation in `tracker_service.py`.
- Included current reactions/comments, reactions per day, today and 7-day gains, first 2h/24h reaction windows, collection activity, image count, and last seen.
- Improved browser-side table sorting for cells with explicit numeric/date sort values.
- Updated dashboard documentation and README feature list.
- Added a smoke test for post performance row metrics.
- Replaced the lower stack of collapsible tables with a tabbed `Analytics workspace`.
- Added workspace search, active-row filtering, and a filter for image-only rows.
- Moved collection detail tables into the workspace while keeping collection summary cards in the main dashboard flow.
- Changed image-only collection rows to link directly to `/images/{image_id}` and show `Post mapping not found locally`.
- Added a smoke test for image-only collection rows linking to image pages.
- Added `image_url` and `thumbnail_url` columns to `post_images`.
- Added thumbnail URL extraction from image metadata.
- Added lazy thumbnail previews to the Post performance table.
- Added a post detail drawer with larger preview, key metrics, post link, primary image link, and stored image links.
- Existing local image rows without preview URLs fall back to `Open image` when an image ID is known; rows without stored image IDs can still show `No preview` until a normal tracker run refreshes metadata.
- Added preview columns to collection image tables; if preview URL is missing, the fallback opens the CivitAI image page.
- Strengthened image URL extraction with recursive URL candidate scanning.
- Fixed CivitAI tRPC image URL handling: `image.getInfinite` returns a UUID token in `url`, so the tracker now builds `imagecache.civitai.com/.../width=450|1024/{image_id}.jpeg` URLs instead of accidentally picking nested user avatar URLs.
- Bumped visible app/dashboard labels and tracker user agents to v10.1.
- Added v10.1 changelog notes.
- Added an `Open image` fallback when a post preview URL is missing but the primary image ID is known.
- Updated EXE dashboard test notes for the v10.1 workspace, thumbnails, drawer, and image-only collection links.
- Fixed the visible `No preview` fallback bug by forcing `[hidden]` preview fallbacks to stay hidden until image load errors.
- Added inline `display:none` on hidden preview fallbacks and JS `showPreviewFallback` so the fallback appears only on actual image load failure.
- Added a Visual overview dashboard section with daily activity, reaction mix, and top movement charts.
- Added workspace period filters for Performance and Collections: day, week, month, year, all time.
- Aligned collection `Open image` fallbacks inside the same thumbnail-sized preview slot and added a restricted/unavailable preview tooltip.
- Added `UPDATE_GUIDE.md` for portable update and rollback flow.
- Included `UPDATE_GUIDE.md` in the PyInstaller data files.
- Closed SQLite connections explicitly in `buzz_ingest.py` to avoid ResourceWarning during smoke tests.

## Files Touched

- `CHANGELOG.md`
- `.gitignore`
- `tracker_service.py`
- `buzz_ingest.py`
- `engagement_dashboard.py`
- `tests/smoke_tests.py`
- `DASHBOARD_GUIDE.md`
- `README.md`
- `EXE_BUILD.md`
- `UPDATE_GUIDE.md`
- `civitai_tracker.spec`
- `CODEX_CONTEXT.md`

## Verified

- `python -m py_compile tracker_app.py tracker_runner.py tracker_service.py tracker_core.py buzz_ingest.py collection_runtime.py collection_sync_state.py engagement_correlation.py engagement_dashboard.py tests\smoke_tests.py`
- `python tests\smoke_tests.py`
- Temporary in-memory dashboard render containing `Post performance`
- `git diff --check`
- `refresh_dashboard_from_config("config.json")`
- `python -m py_compile tracker_service.py engagement_dashboard.py tests\smoke_tests.py`
- `python -m py_compile tracker_app.py tracker_runner.py tracker_service.py tracker_core.py buzz_ingest.py collection_runtime.py collection_sync_state.py engagement_correlation.py engagement_dashboard.py tests\smoke_tests.py`
- `python tests\smoke_tests.py`
- Dashboard workspace HTML sanity check for tabs, filters, and image-only row links
- `refresh_dashboard_from_config("config.json")`
- Dashboard thumbnail/drawer HTML sanity check
- Local `post_images` schema check for `image_url` and `thumbnail_url`
- Network `run_from_config("config.json")` after the image URL fix populated 25/25 local `post_images` rows with CivitAI imagecache thumbnail URLs.
- Verified generated `dashboard.html` contains imagecache previews and no GitHub avatar URLs.
- `python -m py_compile tracker_app.py tracker_runner.py tracker_service.py tracker_core.py buzz_ingest.py collection_runtime.py collection_sync_state.py engagement_correlation.py engagement_dashboard.py tests\smoke_tests.py`
- `python tests\smoke_tests.py` passes 11 smoke tests.
- `refresh_dashboard_from_config("config.json")`
- Dashboard 10.1 HTML sanity check: v10.1 title, Analytics workspace, post drawer, 72 imagecache references, 0 GitHub avatar URLs, no old `Unlinked image` / `Image not matched` labels.
- `git diff --check`
- `python -m py_compile tracker_app.py tracker_runner.py tracker_service.py tracker_core.py buzz_ingest.py collection_runtime.py collection_sync_state.py engagement_correlation.py engagement_dashboard.py tests\smoke_tests.py`
- `python tests\smoke_tests.py` passes 12 smoke tests.
- `refresh_dashboard_from_config("config.json")`
- Dashboard HTML sanity check: Visual overview, Daily activity, Reaction mix today, Top 7-day movement, workspace period filters, forced hidden preview fallback CSS, inline hidden fallback style, fallback JS, aligned `Open image` fallback, 72 imagecache references, 0 GitHub avatar URLs.

## Current Status

- The v10.1 dashboard monitoring release candidate is complete locally.
- Local `dashboard.html` has been refreshed from `config.json`.
- Changes are not committed yet.
- EXE build smoke was attempted through a temporary `.venv`, but local Python `ensurepip` failed because sandbox-created `tempfile` subdirectories are not writable/readable. Local Python also has no PyInstaller module installed.
- `.tmp_build/` is ignored because the failed build-smoke attempt created temporary sandbox folders there.
- The built-in Node visual render check previously could not run because the local Node runtime is v22.17.0 and the tool requires >= v22.22.0.
- Next useful step is a manual browser visual check of the generated dashboard, then an EXE build on a machine with PyInstaller available or approved pip install, then commit the feature.

## Next

- Open the dashboard and inspect thumbnails, the post detail drawer, Analytics workspace tabs, search, filters, column width, and mobile overflow.
- Run `build_exe.bat` after PyInstaller is available or pip/network install is approved.
- Commit the v10.1 dashboard monitoring changes when the visual/build checks look good.

## Resume Prompt

When continuing on another PC, use:

```text
Прочитай CODEX_CONTEXT.md, затем проверь git status и продолжи работу с пункта Next.
```
