# Dashboard Guide

The dashboard is a local HTML analytics view generated from the tracker database. It reflects collected snapshots and events; it is not a live CivitAI page.

## Main Sections

### Summary

The top summary shows the current tracking scope, known/unknown totals, daily reaction movement, best post today, and best post over the last 7 days.

Daily and weekly blocks use period gain, not lifetime totals.

### Visual Overview

The chart area shows:

- daily reaction gains and collection additions for recent local days;
- reaction mix for the current local day;
- top post movement based on recent reaction and collection activity.

Charts are rendered inside the generated HTML and do not require external scripts.

### Suggested Posting Windows

Suggested windows are calculated from historical performance. Treat them as hints, not rules. Small samples, unusual posts, content mix changes, and platform behavior can skew the result.

### Analytics Workspace

The workspace groups detailed tables into tabs:

- **Performance**
- **Collections**
- **Timing**
- **History**

The workspace includes table search, recent-activity filtering, image-only row filtering, and quick period filters for Performance and Collections:

- Day
- Week
- Month
- Year
- All time

## Performance Table

The Performance table is a per-post monitoring view. It usually includes:

- post link;
- thumbnail preview when a stored preview URL is available;
- published time;
- current reactions and comments;
- average reactions per day;
- reaction/comment gain today and over the last 7 days;
- first 2h and first 24h snapshots when enough early data exists;
- collection additions;
- image count;
- last seen / last update.

Clicking a row opens a detail drawer with a larger preview, compact metrics, post link, primary image link, and stored image links.

Older local image rows may not have preview URLs yet. If an image ID is known, the dashboard falls back to an `Open image` link. Otherwise it shows `No preview` until a later tracker run stores more image metadata.

## Collections

Collection tracking uses authenticated transaction data and maps image-level collection events back to tracked posts when the image is known locally.

The Collections views show:

- collection additions;
- affected images;
- affected posts;
- recent collection events;
- top posts and images by collection additions.
- image-only events that are not mapped to a local post yet.

Rows marked `Post mapping not found locally` are image-level events that could not be mapped to a local post in `post_images`. The dashboard still links those rows to the CivitAI image page.

Collection image previews use the same thumbnail-sized slot as normal previews. If a preview URL is missing or blocked by the current browser/session, the fallback opens the image page without shifting the table layout.

## API Key Effects

Without an API key, collection tracking is unavailable and restricted or NSFW content may be incomplete.

Removing the key does not hide rows already stored in the local database. It only affects what the tracker can fetch on future runs.

## Time And Freshness

Dashboard periods use the configured local timezone.

The header includes a `generated ...` timestamp. If the dashboard looks stale, run the tracker again and confirm that timestamp changed.

## Limits

The dashboard is a decision aid. It does not predict future performance, judge content quality, or guarantee that a suggested time window will outperform another one.
