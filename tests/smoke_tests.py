from __future__ import annotations

import csv
import json
import sqlite3
import shutil
import subprocess
import sys
import unittest
import uuid
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
SMOKE_TMP = ROOT / ".smoke_tmp"
SMOKE_TMP.mkdir(exist_ok=True)


def smoke_path(stem: str, suffix: str) -> Path:
    return SMOKE_TMP / f"{stem}_{uuid.uuid4().hex}{suffix}"

import buzz_ingest
import update_manager
from collection_runtime import compute_collection_mode, normalize_collection_tracking_config
from collection_sync_state import ensure_collection_sync_schema, read_collection_sync_state, write_collection_sync_state
from config_utils import normalize_config, normalize_poll_minutes, run_startup_self_check
from engagement_dashboard import render_collection_tables_html
from tracker_app import build_first_run_config, format_elapsed_time, format_next_run_time
from tracker_runner import TrackerRunner
from tracker_service import (
    TimezoneHelper,
    build_post_performance_rows,
    export_analytics_dataset,
    get_current_posts,
    init_db,
    load_post_deltas,
    load_snapshots_by_post,
    make_image_payload,
    normalize_image,
    normalize_post,
    render_dashboard,
    write_dashboard_html,
)
from update_manager import (
    RUNTIME_PRESERVE_NAMES,
    ReleaseAsset,
    UpdateError,
    UpdateInfo,
    build_update_applier_script,
    choose_download_asset,
    download_asset,
    extract_mirror_assets,
    is_newer_version,
    safe_filename,
    validate_portable_update_package,
    version_key,
)


class DesktopStatusFormattingSmokeTests(unittest.TestCase):
    def test_next_run_time_shows_live_countdown(self) -> None:
        now = datetime(2026, 5, 7, 12, 0, 0)
        target = now + timedelta(minutes=2, seconds=5)

        self.assertIn("in 2m 05s", format_next_run_time(target, now=now))

    def test_last_success_time_shows_elapsed_label(self) -> None:
        now = datetime(2026, 5, 7, 12, 10, 0)
        last_success = now - timedelta(minutes=4, seconds=30)

        self.assertIn("4 min ago", format_elapsed_time(last_success, now=now))


class DesktopMotionSmokeTests(unittest.TestCase):
    def test_desktop_ui_has_motion_hooks(self) -> None:
        source = (ROOT / "tracker_app.py").read_text(encoding="utf-8")

        self.assertIn("desktop_motion_enabled", source)
        self.assertIn("SPI_GETCLIENTAREAANIMATION", source)
        self.assertIn("UI_FADE_DURATION_MS = 1500", source)
        self.assertIn("animate_window_open(self)", source)
        self.assertIn("animate_window_close", source)
        self.assertIn("animate_window_refresh", source)
        self.assertIn("_play_main_motion", source)
        self.assertIn("animate_pack_widget(row", source)
        self.assertIn("_pulse_updates_badge", source)
        self.assertIn("_animate_current_settings_tab", source)


class FirstRunConfigSmokeTests(unittest.TestCase):
    def test_first_run_limited_mode_builds_valid_date_config(self) -> None:
        cfg, materialized_key = build_first_run_config(
            username="creator",
            display_name="",
            timezone_name="Europe/Moscow",
            access_mode="limited",
            api_key="",
            api_key_file="api_key.txt",
            start_mode="date",
            start_post_value="",
            start_day="10",
            start_month="05",
            start_year="2026",
            poll_minutes="1",
            start_auto_polling_on_launch=True,
            check_updates_on_launch=True,
        )

        self.assertIsNone(materialized_key)
        self.assertEqual(cfg["profile"]["display_name"], "creator")
        self.assertEqual(cfg["auth"]["api_key"], "")
        self.assertEqual(cfg["auth"]["api_key_file"], "")
        self.assertEqual(cfg["tracking"]["start_date"], "2026-05-10")
        self.assertEqual(cfg["tracking"]["poll_minutes"], 5)
        self.assertTrue(cfg["options"]["start_auto_polling_on_launch"])

    def test_first_run_api_key_mode_uses_file_storage_and_post_url(self) -> None:
        cfg, materialized_key = build_first_run_config(
            username="creator",
            display_name="Creator Name",
            timezone_name="UTC",
            access_mode="api_key",
            api_key="secret-key",
            api_key_file="secrets/api_key.txt",
            start_mode="post_id",
            start_post_value="https://civitai.com/posts/12345",
            start_day="",
            start_month="",
            start_year="",
            poll_minutes="30",
            start_auto_polling_on_launch=False,
            check_updates_on_launch=False,
        )

        self.assertEqual(materialized_key, "secret-key")
        self.assertEqual(cfg["auth"]["api_key"], "")
        self.assertEqual(cfg["auth"]["api_key_file"], "secrets/api_key.txt")
        self.assertEqual(cfg["tracking"]["start_post_id"], 12345)
        self.assertFalse(cfg["options"]["check_updates_on_launch"])

    def test_first_run_requires_valid_start_post(self) -> None:
        with self.assertRaises(ValueError):
            build_first_run_config(
                username="creator",
                display_name="",
                timezone_name="UTC",
                access_mode="limited",
                api_key="",
                api_key_file="api_key.txt",
                start_mode="post_id",
                start_post_value="not a post",
                start_day="",
                start_month="",
                start_year="",
                poll_minutes="15",
                start_auto_polling_on_launch=False,
                check_updates_on_launch=True,
            )


class TrackerRunnerRuntimeStatusSmokeTests(unittest.TestCase):
    def test_runner_preserves_previous_success_status_on_startup(self) -> None:
        runtime_dir = smoke_path("runner_status", "")
        runtime_dir.mkdir(parents=True, exist_ok=True)
        try:
            previous_success = "2026-05-07T12:00:00+00:00"
            previous_started = "2026-05-07T11:59:00+00:00"
            (runtime_dir / "runtime_status.json").write_text(
                json.dumps(
                    {
                        "last_success_at": previous_success,
                        "last_started_at": previous_started,
                        "last_error": "Previous recoverable error",
                        "last_exit_code": 1,
                        "selected_host": "https://civitai.red",
                        "auto_polling": True,
                        "next_run_at": "2026-05-07T12:15:00+00:00",
                    }
                ),
                encoding="utf-8",
            )

            runner = TrackerRunner(runtime_dir, "config.json")
            snap = runner.snapshot()
            persisted = json.loads((runtime_dir / "runtime_status.json").read_text(encoding="utf-8"))

            self.assertEqual(snap.last_success_at.isoformat(), previous_success)
            self.assertEqual(snap.last_started_at.isoformat(), previous_started)
            self.assertEqual(snap.last_error, "Previous recoverable error")
            self.assertEqual(snap.last_exit_code, 1)
            self.assertEqual(snap.selected_host, "https://civitai.red")
            self.assertFalse(snap.auto_polling)
            self.assertIsNone(snap.next_run_at)
            self.assertEqual(persisted["last_success_at"], previous_success)
            self.assertFalse(persisted["auto_polling"])
            self.assertIsNone(persisted["next_run_at"])
        finally:
            shutil.rmtree(runtime_dir, ignore_errors=True)


class StartupSelfCheckSmokeTests(unittest.TestCase):
    def test_logs_dir_stays_inside_runtime_dir(self) -> None:
        runtime_dir = smoke_path("startup_self_check", "")
        try:
            runtime_dir.mkdir(parents=True, exist_ok=True)
            report = run_startup_self_check(runtime_dir, runtime_dir, runtime_dir / "config.json", {})

            self.assertEqual(report["details"]["logs_dir"], str(runtime_dir / "logs"))
            self.assertTrue(report["details"]["logs_dir_writable"])
        finally:
            shutil.rmtree(runtime_dir, ignore_errors=True)


class UpdateManagerSmokeTests(unittest.TestCase):
    def test_version_tags_are_compared_numerically(self) -> None:
        self.assertEqual(version_key("TrackerV10.1.1"), (10, 1, 1, 0))
        self.assertTrue(is_newer_version("TrackerV10.2", "10.1.9"))
        self.assertFalse(is_newer_version("V10.1.1", "10.1.1"))
        self.assertFalse(is_newer_version("v10.1.9", "10.2.0"))

    def test_choose_download_asset_prefers_portable_zip(self) -> None:
        info = UpdateInfo(
            current_version="10.1.1",
            latest_version="TrackerV10.2",
            latest_tag="TrackerV10.2",
            release_name="Tracker v10.2",
            release_url="https://example.test/release",
            release_notes="",
            published_at="",
            prerelease=False,
            update_available=True,
            assets=(
                ReleaseAsset("source.zip", "https://example.test/source.zip", 10),
                ReleaseAsset("CivitAITracker-v10.2-win64.zip", "https://example.test/app.zip", 100),
            ),
        )

        selected = choose_download_asset(info, "frozen")

        self.assertIsNotNone(selected)
        self.assertEqual(selected.name, "CivitAITracker-v10.2-win64.zip")

    def test_choose_download_asset_rejects_source_zip_for_frozen_build(self) -> None:
        info = UpdateInfo(
            current_version="10.1.1",
            latest_version="TrackerV10.2",
            latest_tag="TrackerV10.2",
            release_name="Tracker v10.2",
            release_url="https://example.test/release",
            release_notes="",
            published_at="",
            prerelease=False,
            update_available=True,
            assets=(
                ReleaseAsset("source.zip", "https://example.test/source.zip", 10),
                ReleaseAsset("CivitAITracker-source.zip", "https://example.test/source2.zip", 20),
            ),
        )

        self.assertIsNone(choose_download_asset(info, "frozen"))

    def test_release_notes_can_provide_update_package_mirror(self) -> None:
        mirrors = extract_mirror_assets(
            "Update package mirror: https://downloads.example.test/CivitAITracker-v10.2.0-win64.zip",
            "TrackerV10.2.0",
        )

        self.assertEqual(len(mirrors), 1)
        self.assertEqual(mirrors[0].source, "mirror")
        self.assertEqual(mirrors[0].name, "CivitAITracker-v10.2.0-win64.zip")
        self.assertEqual(
            mirrors[0].download_url,
            "https://downloads.example.test/CivitAITracker-v10.2.0-win64.zip",
        )

    def test_choose_download_asset_prefers_release_note_mirror(self) -> None:
        info = UpdateInfo(
            current_version="10.1.1",
            latest_version="TrackerV10.2.0",
            latest_tag="TrackerV10.2.0",
            release_name="Tracker v10.2",
            release_url="https://example.test/release",
            release_notes="",
            published_at="",
            prerelease=True,
            update_available=True,
            assets=(ReleaseAsset("CivitAITracker-v10.2.0-win64.zip", "https://github.test/app.zip", 100),),
            mirror_assets=(
                ReleaseAsset(
                    "CivitAITracker-v10.2.0-win64-mirror.zip",
                    "https://mirror.example.test/app.zip",
                    source="mirror",
                ),
            ),
        )

        selected = choose_download_asset(info, "frozen")

        self.assertIsNotNone(selected)
        self.assertEqual(selected.source, "mirror")
        self.assertEqual(selected.download_url, "https://mirror.example.test/app.zip")

    def test_download_filename_is_sanitized(self) -> None:
        self.assertEqual(safe_filename("../CivitAITracker:v10.2?.zip"), "CivitAITracker_v10.2_.zip")

    def test_download_asset_retries_reset_connection(self) -> None:
        target_dir = smoke_path("download_retry", "")
        calls = {"count": 0}

        class ResetResponse:
            headers = {"Content-Length": "4"}

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def read(self, size: int = -1) -> bytes:
                raise ConnectionResetError(10054, "connection reset")

        class GoodResponse:
            headers = {"Content-Length": "4"}

            def __init__(self):
                self._chunks = [b"data", b""]

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def read(self, size: int = -1) -> bytes:
                return self._chunks.pop(0)

        original_urlopen = update_manager.urlopen

        def fake_urlopen(request, timeout: int):
            calls["count"] += 1
            if calls["count"] == 1:
                return ResetResponse()
            return GoodResponse()

        try:
            update_manager.urlopen = fake_urlopen
            target = download_asset(
                ReleaseAsset("CivitAITracker-v10.2-win64.zip", "https://example.test/app.zip", 4),
                target_dir,
                retry_attempts=2,
                retry_delay_seconds=0,
            )
            self.assertEqual(target.read_bytes(), b"data")
            self.assertEqual(calls["count"], 2)
        finally:
            update_manager.urlopen = original_urlopen
            shutil.rmtree(target_dir, ignore_errors=True)

    def test_validate_portable_update_package_requires_exe_payload(self) -> None:
        good_package = smoke_path("portable_package", ".zip")
        bad_package = smoke_path("source_package", ".zip")
        try:
            with zipfile.ZipFile(good_package, "w") as package:
                package.writestr("CivitAITracker/CivitAITracker.exe", "exe")
                package.writestr("CivitAITracker/_internal/runtime.txt", "runtime")

            with zipfile.ZipFile(bad_package, "w") as package:
                package.writestr("README.md", "source archive")

            self.assertEqual(validate_portable_update_package(good_package), "CivitAITracker")
            with self.assertRaises(UpdateError):
                validate_portable_update_package(bad_package)
        finally:
            good_package.unlink(missing_ok=True)
            bad_package.unlink(missing_ok=True)

    def test_update_applier_preserves_runtime_data(self) -> None:
        script = build_update_applier_script()

        self.assertIn("Expand-Archive", script)
        self.assertIn("backup-", script)
        self.assertIn("CivitAITracker.exe", script)
        for name in ("config.json", "api_key.txt", "civitai_tracker.db", "csv", "logs", "updates"):
            self.assertIn(name, RUNTIME_PRESERVE_NAMES)
            self.assertIn(name, script)

    def test_update_applier_replaces_app_files_and_keeps_runtime_files(self) -> None:
        if not sys.platform.startswith("win") or shutil.which("powershell.exe") is None:
            self.skipTest("Windows PowerShell is required for the update applier smoke test.")

        root = smoke_path("update_apply", "")
        app_dir = root / "app"
        payload_parent = root / "payload"
        payload_root = payload_parent / "CivitAITracker"
        package_path = root / "CivitAITracker-update.zip"
        log_path = app_dir / "updates" / "update_apply.log"
        script_path = app_dir / "updates" / "apply_update.ps1"

        try:
            (app_dir / "_internal").mkdir(parents=True)
            (app_dir / "csv").mkdir()
            (app_dir / "logs").mkdir()
            (app_dir / "updates").mkdir()
            (app_dir / "CivitAITracker.exe").write_text("old exe", encoding="utf-8")
            (app_dir / "_internal" / "old.txt").write_text("old internal", encoding="utf-8")
            (app_dir / "README.md").write_text("old readme", encoding="utf-8")
            (app_dir / "config.json").write_text("keep config", encoding="utf-8")
            (app_dir / "api_key.txt").write_text("keep key", encoding="utf-8")
            (app_dir / "civitai_tracker.db").write_text("keep db", encoding="utf-8")
            (app_dir / "csv" / "snapshot.csv").write_text("keep csv", encoding="utf-8")
            (app_dir / "logs" / "app.log").write_text("keep log", encoding="utf-8")
            (app_dir / "dashboard.html").write_text("keep dashboard", encoding="utf-8")
            (app_dir / "runtime_status.json").write_text("keep status", encoding="utf-8")

            (payload_root / "_internal").mkdir(parents=True)
            (payload_root / "CivitAITracker.exe").write_text("new exe", encoding="utf-8")
            (payload_root / "_internal" / "new.txt").write_text("new internal", encoding="utf-8")
            (payload_root / "README.md").write_text("new readme", encoding="utf-8")
            (payload_root / "config.json").write_text("do not copy config", encoding="utf-8")

            with zipfile.ZipFile(package_path, "w") as package:
                for path in payload_parent.rglob("*"):
                    if path.is_file():
                        package.write(path, path.relative_to(payload_parent))

            script_path.write_text(build_update_applier_script(), encoding="utf-8")
            result = subprocess.run(
                [
                    "powershell.exe",
                    "-NoProfile",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(script_path),
                    "-PackagePath",
                    str(package_path),
                    "-AppDir",
                    str(app_dir),
                    "-PidToWait",
                    "0",
                    "-RestartPath",
                    str(app_dir / "does-not-exist.exe"),
                    "-LogPath",
                    str(log_path),
                ],
                capture_output=True,
                text=True,
                timeout=60,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual((app_dir / "CivitAITracker.exe").read_text(encoding="utf-8"), "new exe")
            self.assertEqual((app_dir / "_internal" / "new.txt").read_text(encoding="utf-8"), "new internal")
            self.assertFalse((app_dir / "_internal" / "old.txt").exists())
            self.assertEqual((app_dir / "README.md").read_text(encoding="utf-8"), "new readme")
            self.assertEqual((app_dir / "config.json").read_text(encoding="utf-8"), "keep config")
            self.assertEqual((app_dir / "api_key.txt").read_text(encoding="utf-8"), "keep key")
            self.assertEqual((app_dir / "civitai_tracker.db").read_text(encoding="utf-8"), "keep db")
            self.assertEqual((app_dir / "csv" / "snapshot.csv").read_text(encoding="utf-8"), "keep csv")
            self.assertEqual((app_dir / "logs" / "app.log").read_text(encoding="utf-8"), "keep log")
            self.assertEqual((app_dir / "dashboard.html").read_text(encoding="utf-8"), "keep dashboard")
            self.assertEqual((app_dir / "runtime_status.json").read_text(encoding="utf-8"), "keep status")

            backups = list((app_dir / "updates").glob("backup-*"))
            self.assertEqual(len(backups), 1)
            backup_dir = backups[0]
            self.assertEqual((backup_dir / "CivitAITracker.exe").read_text(encoding="utf-8"), "old exe")
            self.assertEqual((backup_dir / "_internal" / "old.txt").read_text(encoding="utf-8"), "old internal")
            self.assertEqual((backup_dir / "README.md").read_text(encoding="utf-8"), "old readme")
            self.assertIn("Update applied successfully", log_path.read_text(encoding="utf-8"))
        finally:
            try:
                shutil.rmtree(root, ignore_errors=True)
            except OSError:
                pass

    def test_build_flow_installs_runtime_requirements_before_pyinstaller(self) -> None:
        build_script = (ROOT / "build_exe.bat").read_text(encoding="utf-8")
        requirements = (ROOT / "requirements.txt").read_text(encoding="utf-8")
        spec = (ROOT / "civitai_tracker.spec").read_text(encoding="utf-8")

        self.assertIn("requests", requirements)
        self.assertIn("customtkinter", requirements)
        self.assertIn("set \"PYTHON_EXE=%VENV_DIR%\\Scripts\\python.exe\"", build_script)
        self.assertIn("from app_info import APP_TITLE", build_script)
        self.assertIn("-m venv", build_script)
        self.assertIn("-m pip install -r requirements.txt", build_script)
        self.assertIn("-m pip install --upgrade pyinstaller", build_script)
        self.assertIn("import requests, pystray, PIL, customtkinter", build_script)
        self.assertIn("-m PyInstaller --noconfirm --clean civitai_tracker.spec", build_script)
        self.assertIn("'requests'", spec)
        self.assertIn("collect_data_files('customtkinter')", spec)
        self.assertIn("collect_submodules('customtkinter')", spec)
        self.assertIn("assets/fonts", spec)


class CollectionParserSmokeTests(unittest.TestCase):
    def test_trpc_parser_accepts_known_shapes_and_rejects_date_marker_as_cursor(self) -> None:
        sample_tx = {
            "date": "2026-04-25T14:53:02.147Z",
            "details": {"type": "collectedContent:image"},
        }
        shapes = [
            {"result": {"data": {"json": {"transactions": [sample_tx]}}}},
            [{"result": {"data": {"json": {"transactions": [sample_tx]}}}}],
            {"result": {"data": {"json": {"items": [sample_tx]}}}},
        ]

        for payload in shapes:
            with self.subTest(payload=payload):
                transactions = buzz_ingest.extract_transactions(payload)
                self.assertEqual(len(transactions), 1)
                self.assertEqual(transactions[0]["details"]["type"], "collectedContent:image")

        date_marker_only = {"result": {"data": {"json": {"transactions": []}, "meta": {"values": {"cursor": ["Date"]}}}}}
        self.assertIsNone(buzz_ingest.extract_next_cursor(date_marker_only, []))

    def test_collection_events_do_not_store_personal_actor_fields(self) -> None:
        tx = {
            "date": "2026-05-08T12:00:00Z",
            "amount": 1,
            "description": "Collected content",
            "details": {
                "type": "collectedContent:image",
                "byUserId": 12345,
                "entityId": 98765,
                "entityType": "Image",
            },
            "toUser": {
                "id": 777,
                "username": "creator-name",
            },
        }

        event = buzz_ingest.core_event_from_transaction(tx, "https://civitai.red", "blue", "2026-05-08T12:01:00Z")

        self.assertIsNotNone(event)
        self.assertIsNone(event["by_user_id"])
        self.assertIsNone(event["to_user_id"])
        self.assertIsNone(event["to_username"])
        self.assertIsNone(event["description"])
        self.assertNotIn("byUserId", event["raw_json"])
        self.assertNotIn("toUser", event["raw_json"])
        self.assertNotIn("description", event["raw_json"])
        self.assertNotIn("creator-name", event["raw_json"])
        self.assertNotIn('"id":777', event["raw_json"])
        stored_payload = json.loads(event["raw_json"])
        self.assertEqual(stored_payload["details"]["entityId"], 98765)
        self.assertEqual(stored_payload["details"]["type"], "collectedContent:image")

    def test_collection_event_key_uses_target_not_actor_identity(self) -> None:
        base_tx = {
            "date": "2026-05-08T12:00:00Z",
            "amount": 1,
            "details": {
                "type": "collectedContent:image",
                "byUserId": 12345,
                "entityId": 98765,
                "entityType": "Image",
            },
            "toUser": {
                "id": 777,
                "username": "creator-name",
            },
        }
        same_target_other_actor = json.loads(json.dumps(base_tx))
        same_target_other_actor["details"]["byUserId"] = 54321
        same_target_other_actor["toUser"] = {"id": 888, "username": "other-name"}
        other_target = json.loads(json.dumps(base_tx))
        other_target["details"]["entityId"] = 98766

        first = buzz_ingest.core_event_from_transaction(base_tx, "https://civitai.red", "blue", "2026-05-08T12:01:00Z")
        second = buzz_ingest.core_event_from_transaction(same_target_other_actor, "https://civitai.red", "blue", "2026-05-08T12:01:00Z")
        third = buzz_ingest.core_event_from_transaction(other_target, "https://civitai.red", "blue", "2026-05-08T12:01:00Z")

        self.assertEqual(first["event_key"], second["event_key"])
        self.assertNotEqual(first["event_key"], third["event_key"])

    def test_collection_schema_cleanup_redacts_existing_personal_fields(self) -> None:
        db_path = smoke_path("collection_privacy_cleanup", ".db")
        try:
            buzz_ingest.init_content_engagement_schema(str(db_path))
            conn = sqlite3.connect(db_path)
            try:
                conn.execute(
                    """
                    INSERT INTO content_engagement_events (
                        event_key, captured_at, event_time, host, account_type,
                        raw_type, normalized_type, amount, description,
                        by_user_id, target_id, target_entity_id, target_entity_type,
                        target_type_candidate, to_user_id, to_username,
                        related_image_id, related_post_id, raw_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        "legacy-key",
                        "2026-05-08T12:01:00Z",
                        "2026-05-08T12:00:00Z",
                        "https://civitai.red",
                        "blue",
                        "collectedContent:image",
                        "collection_like",
                        1,
                        "Collected content",
                        12345,
                        98765,
                        98765,
                        "Image",
                        "image",
                        777,
                        "creator-name",
                        98765,
                        None,
                        '{"details":{"type":"collectedContent:image","byUserId":12345,"entityId":98765},"toUser":{"id":777,"username":"creator-name"}}',
                    ),
                )
                conn.commit()
            finally:
                conn.close()

            buzz_ingest.init_content_engagement_schema(str(db_path))
            conn = sqlite3.connect(db_path)
            try:
                row = conn.execute(
                    """
                    SELECT by_user_id, to_user_id, to_username, description, target_id, related_image_id, raw_json
                    FROM content_engagement_events
                    WHERE event_key = 'legacy-key'
                    """
                ).fetchone()
            finally:
                conn.close()

            self.assertIsNotNone(row)
            self.assertIsNone(row[0])
            self.assertIsNone(row[1])
            self.assertIsNone(row[2])
            self.assertIsNone(row[3])
            self.assertEqual(row[4], 98765)
            self.assertEqual(row[5], 98765)
            self.assertNotIn("byUserId", row[6])
            self.assertNotIn("toUser", row[6])
            self.assertNotIn("description", row[6])
            self.assertNotIn("creator-name", row[6])
            self.assertNotIn('"id":777', row[6])
            stored_payload = json.loads(row[6])
            self.assertEqual(stored_payload["details"]["entityId"], 98765)
            self.assertEqual(stored_payload["details"]["type"], "collectedContent:image")
        finally:
            try:
                db_path.unlink(missing_ok=True)
            except OSError:
                pass


class CollectionConfigSmokeTests(unittest.TestCase):
    def test_legacy_collection_config_is_normalized(self) -> None:
        cfg = normalize_config(
            {
                "collection_tracking": {
                    "backfill_days": 60,
                    "max_pages": 10,
                    "overlap_hours": 0,
                }
            }
        )
        normalized = normalize_collection_tracking_config(cfg)

        self.assertEqual(normalized["bootstrap_max_pages"], 10)
        self.assertEqual(normalized["maintenance_max_pages"], 10)
        self.assertEqual(normalized["max_history_days"], 60)
        self.assertEqual(normalized["overlap_hours"], 0)

    def test_completed_empty_history_uses_maintenance(self) -> None:
        state = {
            "bootstrap_completed": True,
            "target_start_time": "2026-04-10T00:00:00Z",
        }

        self.assertEqual(
            compute_collection_mode(0, state, "2026-04-10T00:00:00Z"),
            "maintenance",
        )
        self.assertEqual(
            compute_collection_mode(0, state, "2026-04-01T00:00:00Z"),
            "bootstrap",
        )

    def test_collection_tracking_option_accepts_new_and_legacy_names(self) -> None:
        modern = normalize_config({"options": {"enable_collection_tracking": False}})
        legacy = normalize_config({"options": {"enable_buzz_ingest": False}})

        self.assertFalse(modern["options"]["enable_collection_tracking"])
        self.assertFalse(modern["options"]["enable_buzz_ingest"])
        self.assertFalse(legacy["options"]["enable_collection_tracking"])
        self.assertFalse(legacy["options"]["enable_buzz_ingest"])

    def test_update_check_on_launch_defaults_to_enabled(self) -> None:
        cfg = normalize_config({})

        self.assertTrue(cfg["options"]["check_updates_on_launch"])

    def test_polling_interval_has_respectful_floor(self) -> None:
        self.assertEqual(normalize_poll_minutes(1), 5)
        self.assertEqual(normalize_poll_minutes(5), 5)
        self.assertEqual(normalize_poll_minutes(15), 15)
        self.assertEqual(normalize_poll_minutes("not-a-number"), 15)


class CollectionStateSmokeTests(unittest.TestCase):
    def test_sync_state_round_trip_and_legacy_schema_migration(self) -> None:
        conn = sqlite3.connect(":memory:")
        write_collection_sync_state(
            conn,
            mode="maintenance",
            bootstrap_completed=True,
            last_sync_at="2026-04-30T10:00:00Z",
            last_event_time_seen="2026-04-30T09:00:00Z",
            oldest_event_time_seen="2026-04-10T01:00:00Z",
            target_start_time="2026-04-10T00:00:00Z",
            coverage_complete=True,
            stop_reason="source_exhausted",
            pages_fetched_last_run=2,
        )
        state = read_collection_sync_state(conn)
        conn.close()

        self.assertIsNotNone(state)
        self.assertEqual(state["mode"], "maintenance")
        self.assertTrue(state["bootstrap_completed"])
        self.assertEqual(state["pages_fetched_last_run"], 2)

        legacy = sqlite3.connect(":memory:")
        legacy.execute(
            """
            CREATE TABLE collection_sync_state (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                mode TEXT NOT NULL,
                bootstrap_completed INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        legacy.execute("INSERT INTO collection_sync_state (id, mode, bootstrap_completed) VALUES (1, 'maintenance', 1)")
        ensure_collection_sync_schema(legacy)
        migrated = read_collection_sync_state(legacy)
        legacy.close()

        self.assertIsNotNone(migrated)
        self.assertEqual(migrated["mode"], "maintenance")
        self.assertTrue(migrated["bootstrap_completed"])


class CollectionIngestSmokeTests(unittest.TestCase):
    def test_bootstrap_then_maintenance_without_network(self) -> None:
        original_pass = buzz_ingest._run_transactions_pass

        def fake_pass(session, *, cfg, start_dt, end_dt, max_pages, stop_at_target_dt):
            return {
                "captured_at": "2026-04-30T10:00:00Z",
                "window_start": buzz_ingest.iso_z(start_dt),
                "window_end": buzz_ingest.iso_z(end_dt),
                "pages_fetched": max_pages,
                "events_seen": 0,
                "events_core": 0,
                "events_inserted": 0,
                "events_deduped": 0,
                "type_counts": {},
                "last_cursor": None,
                "last_page_url": None,
                "page_summaries": [],
                "stop_reason": "source_exhausted",
                "coverage_complete": True,
                "target_start_time": buzz_ingest.iso_z(stop_at_target_dt),
                "oldest_event_time_seen": None,
                "latest_event_time_seen": None,
            }

        db_path = smoke_path("collection_ingest", ".db")
        buzz_ingest._run_transactions_pass = fake_pass
        try:
            cfg = {
                "profile": {"username": "dummy"},
                "auth": {"api_key": "dummy"},
                "api": {"mode": "red"},
                "tracking": {"start_date": "2026-04-10"},
                "collection_tracking": {"max_pages": 2, "backfill_days": 3, "overlap_hours": 0},
            }
            first = buzz_ingest.run_b2_1_ingest(cfg, str(db_path))
            second = buzz_ingest.run_b2_1_ingest(cfg, str(db_path))
        finally:
            buzz_ingest._run_transactions_pass = original_pass
            try:
                db_path.unlink(missing_ok=True)
            except OSError:
                pass

        self.assertEqual(first["collection_mode"], "bootstrap")
        self.assertTrue(first["bootstrap_completed"])
        self.assertEqual(second["collection_mode"], "maintenance")
        self.assertTrue(second["bootstrap_completed"])


class AnalyticsExportSmokeTests(unittest.TestCase):
    def test_analytics_export_writes_clean_files_without_private_collection_fields(self) -> None:
        db_path = smoke_path("analytics_export", ".db")
        export_dir = smoke_path("analytics_export", "")
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row

        published_at = datetime(2026, 5, 7, 9, 0, 0, tzinfo=timezone.utc)
        first_capture = published_at + timedelta(hours=2)
        second_capture = published_at + timedelta(hours=26)

        try:
            init_db(conn)
            conn.execute(
                """
                INSERT INTO post_snapshots (
                    post_id, username, title, published_at, captured_at,
                    source_host, source_kind, stats_known,
                    like_count, heart_count, laugh_count, cry_count, comment_count,
                    reaction_total, engagement_total
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    1001,
                    "tester",
                    "Exported post",
                    published_at.isoformat(),
                    first_capture.isoformat(),
                    "https://civitai.red",
                    "test",
                    1,
                    2,
                    1,
                    0,
                    0,
                    1,
                    3,
                    4,
                ),
            )
            conn.execute(
                """
                INSERT INTO post_snapshots (
                    post_id, username, title, published_at, captured_at,
                    source_host, source_kind, stats_known,
                    like_count, heart_count, laugh_count, cry_count, comment_count,
                    reaction_total, engagement_total
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    1001,
                    "tester",
                    "Exported post",
                    published_at.isoformat(),
                    second_capture.isoformat(),
                    "https://civitai.red",
                    "test",
                    1,
                    6,
                    2,
                    1,
                    1,
                    2,
                    10,
                    12,
                ),
            )
            conn.execute(
                """
                INSERT INTO post_images (
                    post_id, image_id, position, image_created_at, nsfw, nsfw_level,
                    image_url, thumbnail_url, width, height, prompt_text, negative_prompt,
                    model_name, sampler, steps, cfg, seed, source_host, captured_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    1001,
                    2001,
                    1,
                    published_at.isoformat(),
                    "false",
                    "Soft",
                    "https://image.civitai.com/full.jpeg",
                    "https://image.civitai.com/thumb.jpeg",
                    512,
                    1024,
                    "a prompt",
                    "negative",
                    "Test Model",
                    "Euler",
                    30,
                    7.5,
                    "12345",
                    "https://civitai.red",
                    second_capture.isoformat(),
                ),
            )
            conn.execute(
                """
                CREATE TABLE content_engagement_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_time TEXT,
                    normalized_type TEXT,
                    related_post_id INTEGER,
                    by_user_id INTEGER,
                    to_user_id INTEGER,
                    to_username TEXT,
                    description TEXT
                )
                """
            )
            conn.execute(
                """
                INSERT INTO content_engagement_events (
                    event_time, normalized_type, related_post_id, by_user_id, to_user_id, to_username, description
                ) VALUES (?, ?, ?, ?, ?, ?, ?), (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    (published_at + timedelta(hours=1)).isoformat(),
                    "collection_like",
                    1001,
                    999,
                    111,
                    "collector_one",
                    "private one",
                    (published_at + timedelta(hours=25)).isoformat(),
                    "collection_like",
                    1001,
                    998,
                    112,
                    "collector_two",
                    "private two",
                ),
            )
            conn.commit()

            result = export_analytics_dataset(
                conn=conn,
                output_dir=str(export_dir),
                tz_helper=TimezoneHelper("Europe/Moscow"),
                username="tester",
                view_host="https://civitai.red",
            )

            with (export_dir / "posts_summary.csv").open(encoding="utf-8") as f:
                summary_rows = list(csv.DictReader(f))
            with (export_dir / "post_snapshots.csv").open(encoding="utf-8") as f:
                snapshot_rows = list(csv.DictReader(f))
            with (export_dir / "post_deltas.csv").open(encoding="utf-8") as f:
                delta_rows = list(csv.DictReader(f))
            with (export_dir / "post_images.csv").open(encoding="utf-8") as f:
                image_rows = list(csv.DictReader(f))
            metadata = json.loads((export_dir / "export_metadata.json").read_text(encoding="utf-8"))
            combined_csv = "\n".join(path.read_text(encoding="utf-8") for path in export_dir.glob("*.csv"))
        finally:
            conn.close()
            try:
                db_path.unlink(missing_ok=True)
            except OSError:
                pass
            shutil.rmtree(export_dir, ignore_errors=True)

        self.assertEqual(result["total_posts_exported"], 1)
        self.assertEqual(metadata["username"], "tester")
        self.assertEqual(metadata["total_snapshots_exported"], 2)
        self.assertEqual(metadata["views_source"], "unavailable")
        self.assertIn("view counts", metadata["field_notes"]["view_count"])
        self.assertEqual(summary_rows[0]["published_at_utc"], "2026-05-07T09:00:00Z")
        self.assertEqual(summary_rows[0]["published_at_local"], "2026-05-07T12:00:00+03:00")
        self.assertEqual(summary_rows[0]["publish_hour_utc"], "9")
        self.assertEqual(summary_rows[0]["publish_hour_local"], "12")
        self.assertEqual(summary_rows[0]["publish_weekday_local"], "Thu")
        self.assertEqual(summary_rows[0]["post_title"], "Exported post")
        self.assertEqual(summary_rows[0]["current_view_count"], "")
        self.assertEqual(summary_rows[0]["current_collection_count"], "2")
        self.assertEqual(summary_rows[0]["reactions_2h"], "3")
        self.assertEqual(summary_rows[0]["reactions_2h_is_estimated"], "false")
        self.assertEqual(summary_rows[0]["reactions_2h_sample_elapsed_hours"], "2.0")
        self.assertEqual(summary_rows[0]["reactions_2h_sample_distance_hours"], "0.0")
        self.assertEqual(summary_rows[0]["reactions_24h"], "10")
        self.assertEqual(summary_rows[0]["reactions_24h_is_estimated"], "true")
        self.assertEqual(summary_rows[0]["reactions_24h_sample_elapsed_hours"], "26.0")
        self.assertEqual(summary_rows[0]["reactions_24h_sample_distance_hours"], "2.0")
        self.assertEqual(summary_rows[0]["reactions_48h"], "")
        self.assertEqual(summary_rows[0]["reactions_48h_is_estimated"], "")
        self.assertEqual(summary_rows[0]["reactions_48h_sample_elapsed_hours"], "26.0")
        self.assertEqual(summary_rows[0]["reactions_48h_sample_distance_hours"], "22.0")
        self.assertEqual(summary_rows[0]["collections_24h"], "1")
        self.assertEqual(snapshot_rows[0]["post_title"], "Exported post")
        self.assertEqual(snapshot_rows[0]["collection_count"], "1")
        self.assertEqual(snapshot_rows[1]["collection_count"], "2")
        self.assertEqual(delta_rows[0]["post_title"], "Exported post")
        self.assertEqual(delta_rows[0]["delta_collections"], "1")
        self.assertEqual(image_rows[0]["post_title"], "Exported post")
        self.assertEqual(image_rows[0]["aspect_ratio"], "0.5")
        self.assertEqual(image_rows[0]["model_name"], "Test Model")
        self.assertNotIn("999", combined_csv)
        self.assertNotIn("collector_one", combined_csv)


class DashboardSmokeTests(unittest.TestCase):
    def test_dashboard_write_replaces_existing_file(self) -> None:
        dashboard_path = smoke_path("dashboard", ".html")
        try:
            write_dashboard_html(str(dashboard_path), "old")
            write_dashboard_html(str(dashboard_path), "new")

            self.assertEqual(dashboard_path.read_text(encoding="utf-8"), "new")
        finally:
            for path in (dashboard_path, dashboard_path.with_name(f"{dashboard_path.name}.tmp")):
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    pass

    def test_dashboard_visual_overview_and_hidden_preview_fallback_css(self) -> None:
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        init_db(conn)

        now = datetime.now(timezone.utc).replace(microsecond=0)
        published_at = (now - timedelta(days=1)).isoformat()
        captured_at = now.isoformat()
        dashboard_path = smoke_path("dashboard_visual", ".html")

        try:
            conn.execute(
                """
                INSERT INTO post_snapshots (
                    post_id, username, title, published_at, captured_at,
                    source_host, source_kind, stats_known,
                    like_count, heart_count, laugh_count, cry_count, comment_count,
                    reaction_total, engagement_total
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (1002, "tester", "Visual post", published_at, captured_at, "https://civitai.red", "test", 1, 5, 1, 0, 0, 0, 6, 6),
            )
            conn.execute(
                """
                INSERT INTO post_deltas (
                    post_id, username, title, published_at, detected_at, source_host,
                    like_delta, heart_delta, laugh_delta, cry_delta, comment_delta,
                    reaction_total_delta, engagement_total_delta
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (1002, "tester", "Visual post", published_at, captured_at, "https://civitai.red", 2, 1, 0, 0, 0, 3, 3),
            )
            conn.execute(
                """
                INSERT INTO post_images (
                    post_id, image_id, position, image_created_at, nsfw, nsfw_level,
                    image_url, thumbnail_url, source_host, captured_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    1002,
                    2002,
                    1,
                    published_at,
                    "false",
                    "None",
                    "https://image.civitai.com/full.jpeg",
                    "https://image.civitai.com/thumb.jpeg",
                    "https://civitai.red",
                    captured_at,
                ),
            )
            conn.commit()

            render_dashboard(
                conn=conn,
                html_path=str(dashboard_path),
                tz_helper=TimezoneHelper("UTC"),
                dashboard_name="Smoke",
                view_host="https://civitai.red",
                selected_host="https://civitai.red",
                min_post_id=None,
                start_date="2026-05-04",
                runtime_status_path=None,
                db_path=None,
            )
            rendered = dashboard_path.read_text(encoding="utf-8")
        finally:
            conn.close()
            try:
                dashboard_path.unlink(missing_ok=True)
            except OSError:
                pass

        self.assertIn("[hidden]{display:none!important}", rendered)
        self.assertIn(".thumb-missing[hidden]{display:none!important}", rendered)
        self.assertIn("class='motion-ready'", rendered)
        self.assertIn("motion-reveal", rendered)
        self.assertIn("IntersectionObserver", rendered)
        self.assertIn("captureMotion", rendered)
        self.assertIn("playMotion", rendered)
        self.assertIn("Element.prototype.animate", rendered)
        self.assertIn("getBoundingClientRect", rendered)
        self.assertIn("@media (prefers-reduced-motion:reduce)", rendered)
        self.assertIn("dashboard-enter", rendered)
        self.assertIn("dashboard-item-in", rendered)
        self.assertIn("animateDashboardNumbers", rendered)
        self.assertIn("Intl.NumberFormat", rendered)
        self.assertIn("requestAnimationFrame", rendered)
        self.assertIn("is-closing", rendered)
        self.assertIn("Visual overview", rendered)
        self.assertIn("Daily activity", rendered)
        self.assertIn("Reaction mix today", rendered)
        self.assertIn("Top 7-day movement", rendered)
        self.assertIn("Performance board", rendered)
        self.assertIn("Recent momentum", rendered)
        self.assertIn("Collection movers", rendered)
        self.assertIn("Fresh posts", rendered)
        self.assertIn("Full performance table", rendered)
        self.assertIn("class='performance-post-card'", rendered)
        self.assertIn("Timing board", rendered)
        self.assertIn("Best hours", rendered)
        self.assertIn("Best weekdays", rendered)
        self.assertIn("class='timing-card-panel'", rendered)
        self.assertIn("History board", rendered)
        self.assertIn("All-time leaders", rendered)
        self.assertIn("First-day leaders", rendered)
        self.assertIn("class='history-post-card'", rendered)
        self.assertIn("data-post-detail-id='1002'", rendered)
        self.assertIn("data-workspace-period='day'", rendered)
        self.assertIn("data-workspace-period='week'", rendered)
        self.assertIn("data-workspace-period='month'", rendered)
        self.assertIn("data-workspace-period='year'", rendered)
        self.assertIn("data-workspace-period='all'", rendered)
        self.assertIn("data-period-day='1'", rendered)
        self.assertIn("data-period-month='1'", rendered)

    def test_post_performance_rows_include_period_and_early_metrics(self) -> None:
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        init_db(conn)

        now = datetime.now(timezone.utc).replace(microsecond=0)
        published_at = (now - timedelta(hours=3)).isoformat()
        first_capture = (now - timedelta(hours=2)).isoformat()
        latest_capture = now.isoformat()

        conn.execute(
            """
            INSERT INTO post_snapshots (
                post_id, username, title, published_at, captured_at,
                source_host, source_kind, stats_known,
                like_count, heart_count, laugh_count, cry_count, comment_count,
                reaction_total, engagement_total
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (1001, "tester", "Early post", published_at, first_capture, "https://civitai.red", "test", 1, 4, 1, 0, 0, 1, 5, 6),
        )
        conn.execute(
            """
            INSERT INTO post_snapshots (
                post_id, username, title, published_at, captured_at,
                source_host, source_kind, stats_known,
                like_count, heart_count, laugh_count, cry_count, comment_count,
                reaction_total, engagement_total
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (1001, "tester", "Early post", published_at, latest_capture, "https://civitai.red", "test", 1, 6, 2, 0, 0, 2, 8, 10),
        )
        conn.execute(
            """
            INSERT INTO post_deltas (
                post_id, username, title, published_at, detected_at, source_host,
                like_delta, heart_delta, laugh_delta, cry_delta, comment_delta,
                reaction_total_delta, engagement_total_delta
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (1001, "tester", "Early post", published_at, latest_capture, "https://civitai.red", 2, 1, 0, 0, 1, 3, 4),
        )
        conn.execute(
            """
            INSERT INTO post_images (
                post_id, image_id, position, image_created_at, nsfw, nsfw_level, source_host, captured_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (1001, 2001, 1, published_at, "false", "None", "https://civitai.red", latest_capture),
        )
        conn.commit()

        tz_helper = TimezoneHelper("UTC")
        rows = build_post_performance_rows(
            conn,
            get_current_posts(conn),
            load_snapshots_by_post(conn),
            load_post_deltas(conn),
            tz_helper,
        )
        conn.close()

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["post_id"], 1001)
        self.assertEqual(rows[0]["reaction_total"], 8)
        self.assertEqual(rows[0]["reaction_today"], 3)
        self.assertEqual(rows[0]["reaction_week"], 3)
        self.assertEqual(rows[0]["reaction_month"], 3)
        self.assertEqual(rows[0]["reaction_year"], 3)
        self.assertEqual(rows[0]["comments_today"], 1)
        self.assertEqual(rows[0]["comments_month"], 1)
        self.assertEqual(rows[0]["first2_reactions"], 5)
        self.assertEqual(rows[0]["first24_reactions"], 8)
        self.assertEqual(rows[0]["image_count"], 1)
        self.assertTrue(rows[0]["period_day"])
        self.assertTrue(rows[0]["period_week"])

    def test_new_post_without_delta_still_matches_period_filters(self) -> None:
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        init_db(conn)

        now = datetime.now(timezone.utc).replace(microsecond=0)
        published_at = (now - timedelta(hours=1)).isoformat()

        conn.execute(
            """
            INSERT INTO post_snapshots (
                post_id, username, title, published_at, captured_at,
                source_host, source_kind, stats_known,
                like_count, heart_count, laugh_count, cry_count, comment_count,
                reaction_total, engagement_total
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (1003, "tester", "Fresh post", published_at, now.isoformat(), "https://civitai.red", "test", 1, 1, 0, 0, 0, 0, 1, 1),
        )
        conn.commit()

        tz_helper = TimezoneHelper("UTC")
        rows = build_post_performance_rows(
            conn,
            get_current_posts(conn),
            load_snapshots_by_post(conn),
            load_post_deltas(conn),
            tz_helper,
        )
        conn.close()

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["post_id"], 1003)
        self.assertEqual(rows[0]["reaction_today"], 0)
        self.assertTrue(rows[0]["period_day"])
        self.assertTrue(rows[0]["period_week"])
        self.assertTrue(rows[0]["period_month"])
        self.assertTrue(rows[0]["period_year"])

    def test_image_enrichment_keeps_preview_urls(self) -> None:
        payload = make_image_payload(username="tester")
        self.assertTrue(payload["json"]["withMeta"])

        normalized = normalize_image(
            {
                "id": 2001,
                "postId": 1001,
                "createdAt": "2026-05-04T09:00:00Z",
                "url": "https://image.civitai.com/full.jpeg",
                "urls": {"small": "https://image.civitai.com/small.jpeg"},
                "meta": {
                    "Size": "512x768",
                    "Prompt": "soft light",
                    "Negative prompt": "blur",
                    "Model": "Example Model",
                    "Sampler": "Euler",
                    "Steps": "24",
                    "cfgScale": "6.5",
                    "Seed": 1234,
                },
                "_source_host": "https://civitai.red",
            }
        )

        self.assertIsNotNone(normalized)
        self.assertEqual(normalized["image_url"], "https://image.civitai.com/full.jpeg")
        self.assertEqual(normalized["thumbnail_url"], "https://image.civitai.com/small.jpeg")
        self.assertEqual(normalized["width"], 512)
        self.assertEqual(normalized["height"], 768)
        self.assertEqual(normalized["prompt_text"], "soft light")
        self.assertEqual(normalized["negative_prompt"], "blur")
        self.assertEqual(normalized["model_name"], "Example Model")
        self.assertEqual(normalized["sampler"], "Euler")
        self.assertEqual(normalized["steps"], 24)
        self.assertEqual(normalized["cfg"], 6.5)
        self.assertEqual(normalized["seed"], "1234")

    def test_post_normalization_keeps_available_tags(self) -> None:
        normalized = normalize_post(
            {
                "id": 1001,
                "name": "Tagged post",
                "publishedAt": "2026-05-04T09:00:00Z",
                "tags": [{"name": "alpha"}, {"name": "beta"}, {"name": "alpha"}],
                "stats": {"likeCount": 1, "heartCount": 2, "laughCount": 0, "cryCount": 0, "commentCount": 1},
            },
            username="tester",
        )

        self.assertIsNotNone(normalized)
        self.assertEqual(normalized["title"], "Tagged post")
        self.assertEqual(normalized["tag_list"], "alpha|beta")

    def test_image_enrichment_builds_civitai_cache_urls_from_uuid(self) -> None:
        normalized = normalize_image(
            {
                "id": 2001,
                "postId": 1001,
                "url": "a3f490ae-99b4-4e52-bcc5-75e9820161e0",
                "user": {"image": "https://avatars.githubusercontent.com/u/47029214?v=4"},
            }
        )

        self.assertIsNotNone(normalized)
        self.assertIn("imagecache.civitai.com", normalized["image_url"])
        self.assertIn("/width=1024/2001.jpeg", normalized["image_url"])
        self.assertIn("/width=450/2001.jpeg", normalized["thumbnail_url"])
        self.assertNotIn("avatars.githubusercontent.com", normalized["thumbnail_url"])

    def test_collection_workspace_renders_cards_and_image_only_links(self) -> None:
        db_path = smoke_path("collection_dashboard", ".db")
        conn = sqlite3.connect(db_path)
        try:
            conn.execute(
                """
                CREATE TABLE content_engagement_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_time TEXT,
                    normalized_type TEXT,
                    target_id INTEGER,
                    related_image_id INTEGER,
                    related_post_id INTEGER,
                    by_user_id INTEGER
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE post_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    post_id INTEGER,
                    title TEXT,
                    published_at TEXT
                )
                """
            )
            conn.execute(
                """
                INSERT INTO content_engagement_events (
                    event_time, normalized_type, target_id, related_image_id, related_post_id, by_user_id
                ) VALUES (?, ?, ?, ?, ?, ?), (?, ?, ?, ?, ?, ?)
                """,
                (
                    datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
                    "collection_like",
                    222222,
                    222222,
                    1001,
                    42,
                    datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
                    "collection_like",
                    123456,
                    None,
                    None,
                    43,
                ),
            )
            conn.execute(
                """
                INSERT INTO post_snapshots (post_id, title, published_at)
                VALUES (?, ?, ?)
                """,
                (1001, "Mapped post", "2026-05-08T10:00:00+00:00"),
            )
            conn.commit()
        finally:
            conn.close()

        try:
            rendered = render_collection_tables_html(str(db_path), view_host="https://civitai.red")
        finally:
            try:
                db_path.unlink(missing_ok=True)
            except OSError:
                pass

        self.assertIn('href="https://civitai.red/images/123456"', rendered)
        self.assertIn('href="https://civitai.red/posts/1001"', rendered)
        self.assertIn("Collection overview", rendered)
        self.assertIn("Image-only events", rendered)
        self.assertIn("Collection board", rendered)
        self.assertIn("Recent collection flow", rendered)
        self.assertIn("Top affected posts", rendered)
        self.assertIn("Top collected images", rendered)
        self.assertIn("Image-only queue", rendered)
        self.assertIn("data-workspace-row", rendered)
        self.assertIn("data-collection-detail-id", rendered)
        self.assertIn("data-collection-detail-template", rendered)
        self.assertLess(rendered.index("Top collected images"), rendered.index("Collection board"))
        self.assertIn("class='preview-link'", rendered)
        self.assertIn("Preview unavailable or restricted", rendered)
        self.assertIn("data-period-all='1'", rendered)
        self.assertIn("data-period-day='1'", rendered)
        self.assertIn("Mapped to local post", rendered)
        self.assertIn("This view uses image and post identifiers only.", rendered)
        self.assertIn("Post mapping not found locally", rendered)
        self.assertNotIn("Actor ID", rendered)
        self.assertNotIn("by_user_id", rendered)
        self.assertNotIn("Image not matched", rendered)
        self.assertNotIn("Recent mapped activity", rendered)
        self.assertNotIn("<table", rendered)


if __name__ == "__main__":
    unittest.main(verbosity=2)
