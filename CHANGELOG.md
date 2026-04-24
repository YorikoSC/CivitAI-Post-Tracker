# Changelog

## v8.8
- Moved detailed analytics tables into a cleaner **single-column dashboard flow**
- Added **collapsible analytics sections** for heavy detail blocks such as leaders, timing summaries, and recent posts
- Added browser-side **auto-refresh every 60 seconds** for the generated dashboard
- Continued dashboard readability polish for reaction breakdowns and table presentation

## v8.7
- Added `Start auto polling on launch` to the desktop app
- Startup, minimized-to-tray, and Windows autostart can now begin polling automatically
- Added `runtime_status.json` tracking for live runner state
- Dashboard now shows runtime status cards for last successful run, next scheduled run, polling interval, auto polling state, and app mode
- Preserved existing analytics blocks without reworking the whole dashboard

## v8.5.3
- Added `launch_tracker.vbs` as the main desktop launcher
- Switched Windows autostart to the VBS launcher flow
- Reduced reliance on `.bat` for normal user startup
- Added `build_exe.bat` and `EXE_BUILD.md` as a foundation for future EXE packaging
- Kept tray mode, close-to-tray behavior, timezone validation, and auto polling intact

## v8.5.1
- Rebuilt the settings layout into a stable multi-tab dialog
- Restored working config entry and save flow
- Preserved tray mode and auto polling

## v8.5
- Improved desktop app layout with clearer sections and less technical UI
- System tray support with quick actions
- Close-to-tray behavior instead of closing the app immediately
- Better timezone guidance and validation
- Cleaner settings flow for API key storage
- Auto polling uses the configured `poll_minutes` while the app is running
