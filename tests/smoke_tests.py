from __future__ import annotations

import sqlite3
import sys
import unittest
import uuid
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
from tracker_service import write_dashboard_html


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


if __name__ == "__main__":
    unittest.main(verbosity=2)
