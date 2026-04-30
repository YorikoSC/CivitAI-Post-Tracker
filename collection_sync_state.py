from __future__ import annotations

import sqlite3
from typing import Any, Dict, Optional


SYNC_STATE_KEY = "default"


SYNC_STATE_SCHEMA = """
CREATE TABLE IF NOT EXISTS collection_sync_state (
    sync_key TEXT PRIMARY KEY,
    mode TEXT NOT NULL,
    last_sync_at TEXT,
    last_event_time_seen TEXT,
    oldest_event_time_seen TEXT,
    target_start_time TEXT,
    coverage_complete INTEGER NOT NULL DEFAULT 0,
    stop_reason TEXT,
    pages_fetched_last_run INTEGER NOT NULL DEFAULT 0,
    bootstrap_completed INTEGER NOT NULL DEFAULT 0
)
"""


def ensure_collection_sync_schema(conn: sqlite3.Connection) -> None:
    conn.execute(SYNC_STATE_SCHEMA)
    existing_columns = {row[1] for row in conn.execute("PRAGMA table_info(collection_sync_state)").fetchall()}
    if "sync_key" not in existing_columns and "id" in existing_columns:
        conn.execute("ALTER TABLE collection_sync_state RENAME TO collection_sync_state_legacy")
        conn.execute(SYNC_STATE_SCHEMA)
        legacy_columns = {
            row[1] for row in conn.execute("PRAGMA table_info(collection_sync_state_legacy)").fetchall()
        }
        selectable = {
            "mode": "'bootstrap'",
            "last_sync_at": "NULL",
            "last_event_time_seen": "NULL",
            "oldest_event_time_seen": "NULL",
            "target_start_time": "NULL",
            "coverage_complete": "0",
            "stop_reason": "NULL",
            "pages_fetched_last_run": "0",
            "bootstrap_completed": "0",
        }
        select_parts = [selectable[column] if column not in legacy_columns else column for column in selectable]
        conn.execute(
            f"""
            INSERT OR REPLACE INTO collection_sync_state (
                sync_key, mode, last_sync_at, last_event_time_seen, oldest_event_time_seen,
                target_start_time, coverage_complete, stop_reason, pages_fetched_last_run,
                bootstrap_completed
            )
            SELECT ?, {", ".join(select_parts)}
            FROM collection_sync_state_legacy
            LIMIT 1
            """,
            (SYNC_STATE_KEY,),
        )
        conn.execute("DROP TABLE collection_sync_state_legacy")
        existing_columns = {
            row[1] for row in conn.execute("PRAGMA table_info(collection_sync_state)").fetchall()
        }
    for column, definition in {
        "last_sync_at": "TEXT",
        "last_event_time_seen": "TEXT",
        "oldest_event_time_seen": "TEXT",
        "target_start_time": "TEXT",
        "coverage_complete": "INTEGER NOT NULL DEFAULT 0",
        "stop_reason": "TEXT",
        "pages_fetched_last_run": "INTEGER NOT NULL DEFAULT 0",
        "bootstrap_completed": "INTEGER NOT NULL DEFAULT 0",
    }.items():
        if column not in existing_columns:
            conn.execute(f"ALTER TABLE collection_sync_state ADD COLUMN {column} {definition}")
    conn.commit()


def read_collection_sync_state(conn: sqlite3.Connection) -> Optional[Dict[str, Any]]:
    ensure_collection_sync_schema(conn)
    row = conn.execute(
        """
        SELECT
            mode,
            last_sync_at,
            last_event_time_seen,
            oldest_event_time_seen,
            target_start_time,
            coverage_complete,
            stop_reason,
            pages_fetched_last_run,
            bootstrap_completed
        FROM collection_sync_state
        WHERE sync_key = ?
        """,
        (SYNC_STATE_KEY,),
    ).fetchone()
    if row is None:
        return None
    return {
        "mode": row[0],
        "last_sync_at": row[1],
        "last_event_time_seen": row[2],
        "oldest_event_time_seen": row[3],
        "target_start_time": row[4],
        "coverage_complete": bool(row[5]),
        "stop_reason": row[6],
        "pages_fetched_last_run": int(row[7] or 0),
        "bootstrap_completed": bool(row[8]),
    }


def write_collection_sync_state(
    conn: sqlite3.Connection,
    *,
    mode: str,
    bootstrap_completed: bool,
    last_sync_at: Optional[str],
    last_event_time_seen: Optional[str],
    oldest_event_time_seen: Optional[str],
    target_start_time: Optional[str],
    coverage_complete: bool,
    stop_reason: Optional[str],
    pages_fetched_last_run: int,
) -> None:
    ensure_collection_sync_schema(conn)
    conn.execute(
        """
        INSERT INTO collection_sync_state (
            sync_key,
            mode,
            last_sync_at,
            last_event_time_seen,
            oldest_event_time_seen,
            target_start_time,
            coverage_complete,
            stop_reason,
            pages_fetched_last_run,
            bootstrap_completed
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(sync_key) DO UPDATE SET
            mode = excluded.mode,
            last_sync_at = excluded.last_sync_at,
            last_event_time_seen = excluded.last_event_time_seen,
            oldest_event_time_seen = excluded.oldest_event_time_seen,
            target_start_time = excluded.target_start_time,
            coverage_complete = excluded.coverage_complete,
            stop_reason = excluded.stop_reason,
            pages_fetched_last_run = excluded.pages_fetched_last_run,
            bootstrap_completed = excluded.bootstrap_completed
        """,
        (
            SYNC_STATE_KEY,
            mode,
            last_sync_at,
            last_event_time_seen,
            oldest_event_time_seen,
            target_start_time,
            1 if coverage_complete else 0,
            stop_reason,
            int(pages_fetched_last_run or 0),
            1 if bootstrap_completed else 0,
        ),
    )
    conn.commit()


def reset_collection_sync_state(conn: sqlite3.Connection) -> None:
    ensure_collection_sync_schema(conn)
    conn.execute("DELETE FROM collection_sync_state")
    conn.commit()


def count_collection_events(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT COUNT(*) FROM content_engagement_events").fetchone()
    return int(row[0] or 0) if row else 0
