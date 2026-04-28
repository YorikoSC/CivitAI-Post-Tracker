# Release checklist for v10.0-rc1

## Source mode

- [ ] `python -m pip install -r requirements.txt`
- [ ] `python -m py_compile tracker_app.py tracker_runner.py tracker_service.py tracker_core.py buzz_ingest.py engagement_correlation.py engagement_dashboard.py`
- [ ] `python tracker_app.py`
- [ ] Settings opens and saves config.
- [ ] Run now works.
- [ ] Dashboard updates.
- [ ] Collections section renders.

## Collection tracking

- [ ] API key configured.
- [ ] `content_engagement_events` table appears.
- [ ] `collection_like` rows appear when collection events exist.
- [ ] `related_image_id` and `related_post_id` are populated where possible.

## No API key behavior

- [ ] App starts without crashing.
- [ ] Main tracker still works where public data is available.
- [ ] Collection tracking remains unavailable/empty.

## EXE mode

- [ ] `build_exe.bat` completes.
- [ ] `dist\CivitAITracker\CivitAITracker.exe` starts.
- [ ] Diagnostics opens.
- [ ] Run now works.
- [ ] Dashboard updates.
- [ ] Collections section renders when data exists.

## Repository hygiene

- [ ] `config.json` is not included.
- [ ] `api_key.txt` is not included.
- [ ] `*.db` is not included.
- [ ] `csv/`, `logs/`, `dashboard.html`, `runtime_status.json` are not included.
- [ ] `build/` and `dist/` are not included.
