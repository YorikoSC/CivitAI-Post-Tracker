# Changelog

## v10.2.0

### Added
- In-app Update Center for release checks, release notes, package downloads, and compatible EXE updates.
- Optional background update check on launch.
- Automatic update applier for packaged EXE builds, including backups and updater logs.
- Visible app version in the desktop UI.
- `Exit app` button for fully closing the tracker from the main window.
- `package_release.bat` for creating portable Windows release ZIPs.
- Manual **Select ZIP** fallback, retry handling, and release-note mirror support for update packages.

### Changed
- EXE builds now install project dependencies into `.venv` before PyInstaller runs.
- EXE update package selection rejects source ZIP files and requires the portable app layout.
- Mirror package URLs are preferred over GitHub Release assets when a release provides one.

## v10.1.1

### Added
- `launch_tracker.pyw` as the supported no-console source launcher.
- `launch_tracker.bat` as a visible-console troubleshooting fallback.
- Single-instance guard per app folder.

### Changed
- Source-mode autostart uses the windowed launcher.
- EXE build flow prefers the local `.venv` and isolates user-site packages.
- Frozen startup prepares bundled Tcl/Tk paths before Tkinter imports.

## v10.1

### Added
- Post performance table with current totals, recent gains, early snapshots, collection activity, image count, and last-seen data.
- Lazy thumbnail previews and post detail drawer.
- Visual overview charts.
- Tabbed Analytics workspace for Performance, Collections, Timing, and History.
- Search, recent-activity filtering, image-only filtering, and period filters.
- Preview columns for collection image tables.
- Image URL storage in `post_images`.

### Changed
- Replaced the lower stack of collapsible dashboard tables with the Analytics workspace.
- Image-only collection rows link directly to CivitAI image pages and use `Post mapping not found locally`.
- Preview fallbacks use aligned thumbnail-sized slots.
- Dashboard sorting uses explicit numeric/date sort values.

## v10.0.1

### Added
- Collection sync state storage.
- Bootstrap and maintenance sync modes.
- Collection coverage metadata and partial-history warnings.
- Collection diagnostics in `logs/core_last.log`.
- Smoke tests for collection parsing, config compatibility, sync state, ingest mode switching, and dashboard file writes.

### Changed
- Collection sync supports nested and legacy config shapes.
- Cursor handling avoids invalid `cursor=Date` requests.
- Runtime paths for database, CSV, dashboard HTML, and API key file are resolved relative to `config.json`.
- New configs use `options.enable_collection_tracking`; `options.enable_buzz_ingest` remains a compatibility alias.

## v10.0

### Added
- Collection tracking for images.
- Incoming content engagement storage in `content_engagement_events`.
- Image-to-post correlation for collection events.
- Collections dashboard section.

### Notes
- Collection tracking requires API key access.
- Without an API key, the app runs in limited public mode: collection tracking is unavailable and restricted or NSFW posts may be missing or incomplete.
