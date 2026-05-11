# CivitAI Tracker v10.6.0

## What This Release Is

This is a UI polish release. It makes the generated dashboard and desktop app feel smoother without changing the tracking model, local data format, or update flow.

## Added

- Dashboard motion for cards, charts, counters, workspace switches, filters, and the post detail drawer.
- Scroll-reveal and smoother reflow when changing dashboard workspace tabs or period filters.
- Desktop fade-in and fade-out for the main window, first setup, Settings, Diagnostics, and Updates.
- Subtle motion for new Activity rows and the Updates marker.
- Reduced-motion handling. If Windows or the browser requests reduced motion, the app keeps the UI mostly static.

## Upgrade Notes

- EXE users can update through **Updates** once the portable package is available.
- Source-mode users should update through Git, then run `install_requirements.bat`.
- Existing local configuration, API key, database, CSV files, logs, dashboard output, analytics exports, and update backups are preserved by the automatic updater.
- If animations are not visible, check Windows **Accessibility > Visual effects > Animation effects**.

## Package

Expected portable package:

```text
CivitAITracker-v10.6.0-win64.zip
```

Build with:

```powershell
package_release.bat
```

If GitHub Release assets are unavailable on your network, publish the same ZIP to a direct-download mirror and include:

```text
Update package mirror: https://sourceforge.net/projects/civitai-post-tracker/files/CivitAITracker-v10.6.0-win64.zip/download
```
