# CivitAI Tracker v10.0-rc1 — Collection Tracking

This release candidate adds a new collection-aware analytics layer.

## Highlights

- Tracks when your images are added to collections.
- Correlates collection events from image IDs back to tracked post IDs.
- Adds a new Collections section to the dashboard.
- Keeps existing reaction analytics separate to avoid duplicate reaction reporting.

## New modules

- `buzz_ingest.py`
- `engagement_correlation.py`
- `engagement_dashboard.py`

## New database table

- `content_engagement_events`

## API key behavior

Collection tracking requires API key access. Without an API key, the main tracker can still run in limited mode, but collection events will not be collected.

## Pre-release checks

Before final `v10.0`, verify:

- Source mode starts.
- `Run now` updates the dashboard.
- Collection events populate `content_engagement_events`.
- Collections section renders correctly.
- EXE build starts and runs diagnostics.
- No personal files are included in the release archive.
