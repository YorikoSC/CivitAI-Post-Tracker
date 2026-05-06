# CivitAI Tracker v10.2.1

## Hotfix

This patch fixes a Dashboard Performance filtering issue that could hide newly published posts from the Day, Week, Month, and Year quick filters.

The affected posts were already collected and stored locally, but their first snapshot often had no reaction/comment delta yet. Because the period filters looked only at recent gains, a fresh post could appear only under **All time** until a later snapshot produced movement.

## Fixed

- Freshly published posts now match Dashboard Performance period filters based on `published_at`.
- New posts remain visible in Day, Week, Month, and Year views even when their first captured snapshot has zero gain.
- Added a smoke test covering first-seen posts without delta activity.

## Updating

EXE users can update through **Updates** once the portable package is available.

Source-mode users should update through Git.

Expected portable package:

```text
CivitAITracker-v10.2.1-win64.zip
```

If GitHub Release assets are unavailable on your network, use the mirror package link from the release notes or download the ZIP manually and choose **Select ZIP** in the Updates dialog.

Mirror line format:

```text
Update package mirror: https://example.com/CivitAITracker-v10.2.1-win64.zip
```
