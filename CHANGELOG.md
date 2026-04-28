# Changelog

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
