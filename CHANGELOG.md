# Changelog

## v10.4.1

### Added
- Analysis-ready data export from the desktop app and CLI.
- `analytics_export/` output with posts, snapshots, snapshot-to-snapshot deltas, images, and export metadata.
- Optional image metadata storage for width, height, prompt, negative prompt, model, sampler, steps, CFG, and seed when those fields are available from CivitAI responses.

## v10.4.0

### Added
- Performance board on the dashboard with compact cards for recent momentum, collection movers, and fresh posts.
- Timing board with compact cards for best posting hours, weekdays, and recommendation basis.
- History board with compact cards for all-time and early-window leaders.
- Dashboard smoke coverage for the Performance, Timing, and History boards plus card-to-detail-drawer wiring.

### Changed
- Collection event storage and dashboard details now stay focused on image/post analytics and aggregate collection context.
- Project documentation now focuses on current usage, with old release-body drafts removed from the active doc set.
- Source-mode fallback launch scripts now fail with clearer setup guidance when Python or UI dependencies are missing.
- `install_requirements.bat` now validates Python 3.11+, backs up unusable local environments, installs dependencies into `.venv`, and verifies CustomTkinter.
- Dashboard guide now describes the Performance view as both a compact board and detailed table.

## v10.3.1

### Changed
- Simplified Collections activity rows in the Dashboard.
- Removed a low-value collection event metadata column from user-facing collection activity views.

## v10.3.0

### Added
- CustomTkinter-based desktop UI refresh.
- Bundled Exo 2 and Russo One font assets for consistent app typography.

### Changed
- Main window layout now keeps Activity and the status footer visible at the default size.
- Settings, Updates, and Diagnostics use the refreshed UI surfaces, larger readable text, and styled scrollable text areas.
- Diagnostics now opens with the health details visible without requiring the user to stretch the window.
- Secondary windows now use larger minimum sizes so important controls are visible on open.

## v10.2.1

### Fixed
- Fresh posts now appear in Dashboard Performance quick filters immediately after their first captured snapshot.
- Period filters now consider a post's publish date as well as reaction/comment/collection gains, so first-seen posts are not hidden from Day, Week, Month, or Year views.

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
