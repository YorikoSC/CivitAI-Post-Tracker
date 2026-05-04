# Changelog

## v10.1

### Added
- Added a Post performance table with current totals, recent gains, early-window reaction snapshots, collection activity, image count, and last seen data.
- Added lazy thumbnail previews to post performance rows.
- Added a post detail drawer with larger preview, compact metrics, post link, primary image link, and stored image links.
- Added a Visual overview section with daily activity, reaction mix, and top movement charts.
- Added a tabbed Analytics workspace for performance, collections, timing, and history tables.
- Added workspace search plus active-row and image-only row filters.
- Added quick period filters for Performance and Collections tables: day, week, month, year, and all time.
- Added preview columns for collection image tables when local preview URLs are available.
- Added image URL storage in `post_images` via `image_url` and `thumbnail_url`.
- Added `CODEX_CONTEXT.md` as a cross-machine Codex handoff note.
- Added smoke tests for post performance metrics, image-only collection links, and CivitAI imagecache preview URL handling.

### Changed
- Replaced the lower stack of collapsible dashboard tables with the Analytics workspace.
- Collection image-only rows now link directly to CivitAI image pages and show `Post mapping not found locally` instead of `Unlinked image`.
- Missing post preview URLs now fall back to an `Open image` link when an image ID is known.
- Collection `Open image` fallbacks now use the same thumbnail-sized preview slot as image previews.
- Hidden preview fallback blocks are now forced hidden until an image load error occurs.
- Image metadata enrichment now builds CivitAI imagecache URLs from `image.getInfinite` UUID tokens and avoids nested profile/avatar URLs.
- Dashboard browser sorting now honors explicit numeric/date sort values.
- Visible app/dashboard labels and tracker user agents now report v10.1.

### Notes
- Existing local image rows without stored image IDs may still show `No preview` until a normal tracker run refreshes image metadata from CivitAI.
- The v10.1 release scope is dashboard monitoring polish. Broader application UI redesign remains a good candidate for v10.2.
- Portable update and rollback guidance is documented in `UPDATE_GUIDE.md`.

## v10.0.1

### Added
- Added collection sync state storage via `collection_sync_state`.
- Added collection runtime helpers via `collection_runtime`.
- Added bootstrap vs maintenance collection sync modes.
- Added collection coverage metadata and partial-history warnings for the dashboard.
- Added collection-history rebuild helper for safe collections-only resets.
- Added collection diagnostics to `logs/core_last.log`, including page summaries and raw transaction type counts.
- Added self-contained smoke tests for collection parser, config compatibility, sync state, ingest mode switching, and dashboard file writes.

### Changed
- Collection sync now honors nested config and legacy/flat config compatibility.
- Collection history now uses a safe backfill window and clear stop reasons (`reached_control_point`, `source_exhausted`, `page_limit_reached`, `error`).
- Cursor handling is now validated to prevent bad `cursor=Date` requests.
- tRPC transaction parsing now handles batched response shapes and both `transactions` and `items` payload keys.
- Dashboard HTML is rewritten atomically, includes a generated timestamp, and is opened with a cache-busting version parameter from the app.
- Runtime paths for DB, CSV, dashboard HTML, and API key file are resolved relative to `config.json`.
- New configs use `options.enable_collection_tracking`; the old `options.enable_buzz_ingest` key is still accepted.

## v10.0

### Added
- Added collection tracking for images.
- Added incoming content engagement storage in `content_engagement_events`.
- Added image-to-post correlation for collection events.
- Added a new Collections section to the dashboard.
- Added collection-focused summary cards, recent events, top posts and top images.

### Changed
- Dashboard now includes collection-focused analytics without duplicating the existing reaction statistics.
- User-facing terminology now focuses on collections and content engagement rather than internal transaction/reward wording.

### Notes
- Collection tracking requires API key access.
- Without an API key, the app runs in limited public mode: collection tracking is unavailable and restricted / NSFW posts may be missing or incomplete.
- Internal config names are kept compatible with the existing implementation.

## v9

### Added
- Added Python 3.11-compatible service-layer architecture.
- Extracted one-shot collection logic into `tracker_service.py`.
- Converted `tracker_core.py` into a thin CLI wrapper.
- Removed subprocess-based core execution from the runner.
- Added source/frozen path handling.
- Added PowerShell source launcher (`launch_tracker.ps1`).
- Added PyInstaller `onedir` build flow and spec file.
- Added startup self-check and Diagnostics view.
- Stabilized autonomous tray-based runtime flow.

## v8.8

### Changed
- Moved detailed analytics tables into a cleaner single-column dashboard flow.
- Added collapsible analytics sections for heavy detail blocks.
- Added browser-side auto-refresh for the generated dashboard.
- Continued dashboard readability polish.

## v8.7

### Added
- Added `Start auto polling on launch`.
- Startup, minimized-to-tray, and Windows autostart can now begin polling automatically.
- Added `runtime_status.json` tracking for live runner state.
- Dashboard now shows runtime status cards.

## v8.5.3

### Changed
- Cleaned up launcher strategy.
- Reduced reliance on batch files for normal startup.
- Added build-flow groundwork for EXE packaging.
