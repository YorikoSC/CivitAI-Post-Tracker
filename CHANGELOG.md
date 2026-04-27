# Changelog

## v9
- added Python 3.11-compatible service-layer architecture
- extracted one-shot collection logic into `tracker_service.py`
- converted `tracker_core.py` into a thin CLI wrapper
- removed subprocess-based core execution from the runner
- added source/frozen path handling
- added PowerShell source launcher (`launch_tracker.ps1`)
- added PyInstaller `onedir` build flow and spec file
- added startup self-check and Diagnostics view
- stabilized autonomous tray-based runtime flow
- kept the runtime-aware dashboard and analytics pipeline intact

## v8.8
- moved detailed analytics tables into a cleaner single-column dashboard flow
- added collapsible analytics sections for heavy detail blocks
- added browser-side auto-refresh for the generated dashboard
- continued dashboard readability polish

## v8.7
- added `Start auto polling on launch`
- startup, minimized-to-tray, and Windows autostart can now begin polling automatically
- added `runtime_status.json` tracking for live runner state
- dashboard now shows runtime status cards

## v8.5.3
- cleaned up launcher strategy
- reduced reliance on batch files for normal startup
- added build-flow groundwork for EXE packaging
