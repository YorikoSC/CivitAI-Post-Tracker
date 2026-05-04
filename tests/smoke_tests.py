from __future__ import annotations

import sqlite3
import sys
import unittest
import uuid
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
from collection_runtime import compute_collection_mode, normalize_collection_tracking_config
from collection_sync_state import ensure_collection_sync_schema, read_collection_sync_state, write_collection_sync_state
from config_utils import normalize_config
from engagement_dashboard import render_collection_tables_html
from tracker_service import (
    TimezoneHelper,
    build_post_performance_rows,
    get_current_posts,
    init_db,
    load_post_deltas,
    load_snapshots_by_post,
    normalize_image,
    render_dashboard,
    write_dashboard_html,
)


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
        self.assertIn("Visual overview", rendered)
        self.assertIn("Daily activity", rendered)
        self.assertIn("Reaction mix today", rendered)
        self.assertIn("Top 7-day movement", rendered)
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

    def test_image_enrichment_keeps_preview_urls(self) -> None:
        normalized = normalize_image(
            {
                "id": 2001,
                "postId": 1001,
                "createdAt": "2026-05-04T09:00:00Z",
                "url": "https://image.civitai.com/full.jpeg",
                "urls": {"small": "https://image.civitai.com/small.jpeg"},
                "_source_host": "https://civitai.red",
            }
        )

        self.assertIsNotNone(normalized)
        self.assertEqual(normalized["image_url"], "https://image.civitai.com/full.jpeg")
        self.assertEqual(normalized["thumbnail_url"], "https://image.civitai.com/small.jpeg")

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

    def test_collection_tables_link_image_only_rows_to_image_page(self) -> None:
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
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (datetime.now(timezone.utc).replace(microsecond=0).isoformat(), "collection_like", 123456, None, None, 42),
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
        self.assertIn("class='preview-link'", rendered)
        self.assertIn("Preview unavailable or restricted", rendered)
        self.assertIn("data-period-all='1'", rendered)
        self.assertIn("data-period-day='1'", rendered)
        self.assertIn("Post mapping not found locally", rendered)
        self.assertNotIn("Image not matched", rendered)


if __name__ == "__main__":
    unittest.main(verbosity=2)
