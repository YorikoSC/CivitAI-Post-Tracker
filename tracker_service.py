import argparse
import csv
import html
import json
import os
import sqlite3
import sys
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple, Set
from urllib.parse import quote, urlencode

import requests

from buzz_ingest import run_b2_1_ingest
from engagement_correlation import run_b2_2_correlation
from engagement_dashboard import COLLECTION_SECTION_CSS, render_collection_dashboard_section

from config_utils import load_yaml_config, deep_get, choose, read_api_key

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None

DEFAULT_TIMEOUT = 30
DEFAULT_VIEW_HOST = "https://civitai.red"
DEFAULT_API_MODE = "red"
DEFAULT_NSFW_LEVEL = "X"
DEFAULT_POLL_MINUTES = 15
WEEKDAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
STAT_KEYS = ["likeCount", "heartCount", "laughCount", "cryCount", "commentCount"]


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def utc_now_iso() -> str:
    return utc_now().replace(microsecond=0).isoformat()


def ensure_dir(path: str) -> None:
    os.makedirs(path, exist_ok=True)


class TimezoneHelper:
    def __init__(self, tz_name: str):
        self.tz_name = tz_name
        if tz_name.upper() == "UTC":
            self.tz = timezone.utc
            return

        if ZoneInfo is None:
            raise RuntimeError(
                "IANA timezone support is unavailable in this Python build. "
                "On Windows, install it with: python -m pip install tzdata"
            )

        try:
            self.tz = ZoneInfo(tz_name)
        except Exception as exc:
            raise RuntimeError(
                f"No time zone found with key {tz_name!r}. "
                "On Windows, install it with: python -m pip install tzdata"
            ) from exc

    def parse_iso(self, dt_str: Optional[str]) -> Optional[datetime]:
        if not dt_str:
            return None
        try:
            return datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
        except ValueError:
            return None

    def fmt_dt(self, dt_str: Optional[str]) -> str:
        dt = self.parse_iso(dt_str)
        if not dt:
            return "—"
        return dt.astimezone(self.tz).strftime("%Y-%m-%d %H:%M %Z")

    def local_parts(self, dt_str: Optional[str]) -> Dict[str, Optional[object]]:
        dt = self.parse_iso(dt_str)
        if not dt:
            return {"dt": None, "hour": None, "weekday": None, "weekday_name": None, "date": None}
        local_dt = dt.astimezone(self.tz)
        weekday = local_dt.weekday()
        return {
            "dt": local_dt,
            "hour": local_dt.hour,
            "weekday": weekday,
            "weekday_name": WEEKDAY_NAMES[weekday],
            "date": local_dt.strftime("%Y-%m-%d"),
        }


def get_hosts_for_mode(api_mode: str) -> List[str]:
    mode = (api_mode or DEFAULT_API_MODE).lower()
    if mode == "red":
        return ["https://civitai.red"]
    if mode == "com":
        return ["https://civitai.com"]
    return ["https://civitai.red", "https://civitai.com"]


def db_connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def ensure_column(conn: sqlite3.Connection, table: str, column: str, definition: str) -> None:
    cur = conn.cursor()
    cur.execute(f"PRAGMA table_info({table})")
    existing = {row[1] for row in cur.fetchall()}
    if column not in existing:
        cur.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def init_db(conn: sqlite3.Connection) -> None:
    cur = conn.cursor()

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS post_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            post_id INTEGER NOT NULL,
            username TEXT,
            title TEXT,
            published_at TEXT,
            captured_at TEXT NOT NULL,
            source_host TEXT,
            source_kind TEXT,
            stats_known INTEGER NOT NULL DEFAULT 0,
            like_count INTEGER,
            heart_count INTEGER,
            laugh_count INTEGER,
            cry_count INTEGER,
            comment_count INTEGER,
            reaction_total INTEGER,
            engagement_total INTEGER
        )
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS post_deltas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            post_id INTEGER NOT NULL,
            username TEXT,
            title TEXT,
            published_at TEXT,
            detected_at TEXT NOT NULL,
            source_host TEXT,
            like_delta INTEGER,
            heart_delta INTEGER,
            laugh_delta INTEGER,
            cry_delta INTEGER,
            comment_delta INTEGER,
            reaction_total_delta INTEGER,
            engagement_total_delta INTEGER
        )
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS post_images (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            post_id INTEGER NOT NULL,
            image_id INTEGER NOT NULL,
            position INTEGER,
            image_created_at TEXT,
            nsfw TEXT,
            nsfw_level TEXT,
            source_host TEXT,
            captured_at TEXT NOT NULL,
            UNIQUE(post_id, image_id)
        )
        """
    )

    cur.execute("CREATE INDEX IF NOT EXISTS idx_post_snapshots_post_captured ON post_snapshots(post_id, captured_at)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_post_deltas_post_detected ON post_deltas(post_id, detected_at)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_post_images_post ON post_images(post_id, position, image_id)")

    ensure_column(conn, "post_snapshots", "source_kind", "TEXT")
    ensure_column(conn, "post_snapshots", "title", "TEXT")
    ensure_column(conn, "post_deltas", "title", "TEXT")

    conn.commit()


def build_headers(api_key: Optional[str]) -> Dict[str, str]:
    headers = {
        "User-Agent": "civitai-post-tracker-v8.3-core/1.0",
        "Accept": "application/json",
    }
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    return headers


def encode_input(payload: Dict[str, Any]) -> str:
    return quote(json.dumps(payload, separators=(",", ":"), ensure_ascii=False), safe="")


def trpc_url(host: str, procedure: str, payload: Dict[str, Any]) -> str:
    return f"{host.rstrip('/')}/api/trpc/{procedure}?input={encode_input(payload)}"


def get_batch_result(data: Any) -> Any:
    if isinstance(data, list) and data:
        return data[0]
    return data


def extract_trpc_json(data: Any) -> Dict[str, Any]:
    root = get_batch_result(data)
    if isinstance(root, dict):
        result = root.get("result")
        if isinstance(result, dict):
            data_node = result.get("data")
            if isinstance(data_node, dict) and isinstance(data_node.get("json"), dict):
                return data_node["json"]
            json_node = result.get("json")
            if isinstance(json_node, dict):
                return json_node
    return {}


def trpc_get(session: requests.Session, host: str, procedure: str, payload: Dict[str, Any], timeout: int) -> Dict[str, Any]:
    url = trpc_url(host, procedure, payload)
    response = session.get(url, timeout=timeout)
    response.raise_for_status()
    parsed = response.json()
    body = extract_trpc_json(parsed)
    if not isinstance(body, dict):
        raise RuntimeError(f"Unexpected tRPC payload shape for {procedure} on {host}")
    return body


def make_post_payload(username: str, cursor: Any = None) -> Dict[str, Any]:
    payload = {
        "json": {
            "browsingLevel": 31,
            "period": "AllTime",
            "periodMode": "published",
            "sort": "Newest",
            "followed": False,
            "draftOnly": False,
            "pending": False,
            "include": ["cosmetics"],
            "username": username,
            "cursor": cursor,
        }
    }
    if cursor is None:
        payload["meta"] = {"values": {"cursor": ["undefined"]}}
    return payload


def make_image_payload(username: str, cursor: Any = None, with_meta: bool = False) -> Dict[str, Any]:
    payload = {
        "json": {
            "useIndex": True,
            "period": "AllTime",
            "sort": "Newest",
            "withMeta": with_meta,
            "fromPlatform": False,
            "browsingLevel": 31,
            "include": ["cosmetics"],
            "types": ["image"],
            "username": username,
            "cursor": cursor,
        }
    }
    if cursor is None:
        payload["meta"] = {"values": {"cursor": ["undefined"]}}
    return payload


def fetch_trpc_infinite(
    session: requests.Session,
    host: str,
    procedure: str,
    payload_factory,
    timeout: int,
    max_pages: int = 100,
) -> List[dict]:
    items: List[dict] = []
    cursor: Any = None
    seen_cursors: set = set()

    for _ in range(max_pages):
        payload = payload_factory(cursor)
        body = trpc_get(session=session, host=host, procedure=procedure, payload=payload, timeout=timeout)
        batch = body.get("items", [])
        if not isinstance(batch, list):
            raise RuntimeError(f"Unexpected items type for {procedure} on {host}")
        for item in batch:
            if isinstance(item, dict):
                item.setdefault("_source_host", host)
        items.extend([item for item in batch if isinstance(item, dict)])

        next_cursor = body.get("nextCursor")
        if next_cursor is None:
            break
        cursor_key = json.dumps(next_cursor, sort_keys=True, ensure_ascii=False, default=str)
        if cursor_key in seen_cursors:
            break
        seen_cursors.add(cursor_key)
        cursor = next_cursor
        time.sleep(0.15)

    return items


def fetch_posts_trpc(session: requests.Session, host: str, username: str, timeout: int) -> List[dict]:
    return fetch_trpc_infinite(
        session=session,
        host=host,
        procedure="post.getInfinite",
        payload_factory=lambda cursor: make_post_payload(username=username, cursor=cursor),
        timeout=timeout,
    )


def fetch_images_trpc(session: requests.Session, host: str, username: str, timeout: int) -> List[dict]:
    return fetch_trpc_infinite(
        session=session,
        host=host,
        procedure="image.getInfinite",
        payload_factory=lambda cursor: make_image_payload(username=username, cursor=cursor, with_meta=False),
        timeout=timeout,
    )


def rest_fetch_images(session: requests.Session, host: str, username: str, timeout: int, nsfw_level: str) -> List[dict]:
    next_url = f"{host.rstrip('/')}/api/v1/images?{urlencode({'username': username, 'limit': 200, 'nsfw': nsfw_level})}"
    items: List[dict] = []
    while next_url:
        response = session.get(next_url, timeout=timeout)
        response.raise_for_status()
        data = response.json()
        batch = data.get("items", [])
        if not isinstance(batch, list):
            raise RuntimeError(f"Unexpected REST items type on {host}")
        for item in batch:
            if isinstance(item, dict):
                item.setdefault("_source_host", host)
        items.extend([item for item in batch if isinstance(item, dict)])
        metadata = data.get("metadata", {}) or {}
        next_url = metadata.get("nextPage")
        if next_url:
            time.sleep(0.15)
    return items


def choose_working_host(
    session: requests.Session,
    hosts: Sequence[str],
    username: str,
    timeout: int,
) -> Tuple[str, List[dict]]:
    errors: List[str] = []
    for host in hosts:
        try:
            posts = fetch_posts_trpc(session=session, host=host, username=username, timeout=timeout)
            if posts:
                return host, posts
            errors.append(f"{host}: returned 0 posts")
        except Exception as exc:
            errors.append(f"{host}: {exc}")
    raise RuntimeError("No tRPC host returned posts. " + "; ".join(errors))


def get_stat_or_none(stats: object, key: str) -> Optional[int]:
    if not isinstance(stats, dict):
        return None
    value = stats.get(key)
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def stats_are_known(stats: object) -> bool:
    if not isinstance(stats, dict):
        return False
    return any(get_stat_or_none(stats, key) is not None for key in STAT_KEYS)


def safe_int(value: Any) -> Optional[int]:
    try:
        if value is None:
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def normalize_post(item: dict, username: str) -> Optional[dict]:
    post_id = safe_int(item.get("id") or item.get("postId"))
    if post_id is None:
        return None

    stats = item.get("stats")
    like_count = get_stat_or_none(stats, "likeCount")
    heart_count = get_stat_or_none(stats, "heartCount")
    laugh_count = get_stat_or_none(stats, "laughCount")
    cry_count = get_stat_or_none(stats, "cryCount")
    comment_count = get_stat_or_none(stats, "commentCount")
    known = stats_are_known(stats)

    reaction_total = None
    engagement_total = None
    if all(v is not None for v in (like_count, heart_count, laugh_count, cry_count)):
        reaction_total = int(like_count or 0) + int(heart_count or 0) + int(laugh_count or 0) + int(cry_count or 0)
        if comment_count is not None:
            engagement_total = reaction_total + int(comment_count or 0)

    title = item.get("title") or item.get("name") or ""
    author = username
    if isinstance(item.get("user"), dict):
        author = item["user"].get("username") or author
    elif item.get("username"):
        author = item.get("username")

    published_at = item.get("publishedAt") or item.get("createdAt") or item.get("updatedAt")

    return {
        "post_id": post_id,
        "username": author,
        "title": str(title or ""),
        "published_at": published_at,
        "source_host": item.get("_source_host"),
        "stats_known": 1 if known else 0,
        "like_count": like_count,
        "heart_count": heart_count,
        "laugh_count": laugh_count,
        "cry_count": cry_count,
        "comment_count": comment_count,
        "reaction_total": reaction_total,
        "engagement_total": engagement_total,
    }


def normalize_image(item: dict) -> Optional[dict]:
    image_id = safe_int(item.get("id"))
    post_id = safe_int(item.get("postId") or item.get("post_id"))
    if image_id is None or post_id is None:
        return None
    return {
        "image_id": image_id,
        "post_id": post_id,
        "image_created_at": item.get("createdAt"),
        "nsfw": item.get("nsfw"),
        "nsfw_level": item.get("nsfwLevel"),
        "source_host": item.get("_source_host"),
    }


def get_latest_post_snapshot(conn: sqlite3.Connection, post_id: int) -> Optional[sqlite3.Row]:
    cur = conn.cursor()
    cur.execute(
        """
        SELECT *
        FROM post_snapshots
        WHERE post_id = ?
        ORDER BY id DESC
        LIMIT 1
        """,
        (post_id,),
    )
    return cur.fetchone()


def insert_post_snapshot(conn: sqlite3.Connection, row: dict, captured_at: str, source_kind: str) -> None:
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO post_snapshots (
            post_id, username, title, published_at, captured_at,
            source_host, source_kind, stats_known,
            like_count, heart_count, laugh_count, cry_count, comment_count,
            reaction_total, engagement_total
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            row["post_id"],
            row["username"],
            row["title"],
            row["published_at"],
            captured_at,
            row.get("source_host"),
            source_kind,
            row.get("stats_known", 0),
            row.get("like_count"),
            row.get("heart_count"),
            row.get("laugh_count"),
            row.get("cry_count"),
            row.get("comment_count"),
            row.get("reaction_total"),
            row.get("engagement_total"),
        ),
    )


def insert_post_delta(conn: sqlite3.Connection, row: dict) -> None:
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO post_deltas (
            post_id, username, title, published_at, detected_at, source_host,
            like_delta, heart_delta, laugh_delta, cry_delta, comment_delta,
            reaction_total_delta, engagement_total_delta
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            row["post_id"],
            row["username"],
            row["title"],
            row["published_at"],
            row["detected_at"],
            row.get("source_host"),
            row.get("like_delta"),
            row.get("heart_delta"),
            row.get("laugh_delta"),
            row.get("cry_delta"),
            row.get("comment_delta"),
            row.get("reaction_total_delta"),
            row.get("engagement_total_delta"),
        ),
    )


def passes_start_filter(
    post_id: int,
    published_at: Optional[str],
    tz_helper: TimezoneHelper,
    min_post_id: Optional[int],
    start_date: Optional[str],
) -> bool:
    if min_post_id is not None and post_id < int(min_post_id):
        return False
    if start_date:
        local_date = tz_helper.local_parts(published_at).get("date")
        if local_date is None:
            return False
        return str(local_date) >= str(start_date)
    return True


def process_posts(
    conn: sqlite3.Connection,
    posts: List[dict],
    tz_helper: TimezoneHelper,
    min_post_id: Optional[int],
    start_date: Optional[str],
    source_kind: str,
) -> Tuple[int, int, Set[int]]:
    captured_at = utc_now_iso()
    changed_count = 0
    tracked_count = 0
    tracked_post_ids: Set[int] = set()

    for item in posts:
        row = normalize_post(item=item, username=item.get("username") or "")
        if row is None:
            continue
        if not passes_start_filter(row["post_id"], row["published_at"], tz_helper, min_post_id, start_date):
            continue

        tracked_count += 1
        tracked_post_ids.add(int(row["post_id"]))
        prev = get_latest_post_snapshot(conn, row["post_id"])
        insert_post_snapshot(conn, row=row, captured_at=captured_at, source_kind=source_kind)

        if prev is None:
            continue
        if not prev["stats_known"] or not row["stats_known"]:
            continue

        delta = {
            "post_id": row["post_id"],
            "username": row["username"],
            "title": row["title"],
            "published_at": row["published_at"],
            "detected_at": captured_at,
            "source_host": row.get("source_host"),
        }
        any_change = False
        for curr_key, delta_key in [
            ("like_count", "like_delta"),
            ("heart_count", "heart_delta"),
            ("laugh_count", "laugh_delta"),
            ("cry_count", "cry_delta"),
            ("comment_count", "comment_delta"),
            ("reaction_total", "reaction_total_delta"),
            ("engagement_total", "engagement_total_delta"),
        ]:
            current_value = row.get(curr_key)
            prev_value = prev[curr_key]
            if current_value is None or prev_value is None:
                delta[delta_key] = None
                continue
            change = int(current_value) - int(prev_value)
            delta[delta_key] = change
            if change != 0:
                any_change = True

        if any_change:
            insert_post_delta(conn, delta)
            changed_count += 1
            print(
                f"[{captured_at}] post={row['post_id']} "
                f"likes={delta.get('like_delta', 0):+d} hearts={delta.get('heart_delta', 0):+d} "
                f"comments={delta.get('comment_delta', 0):+d} engagement={delta.get('engagement_total_delta', 0):+d}"
            )

    conn.commit()
    return tracked_count, changed_count, tracked_post_ids


def replace_post_images(conn: sqlite3.Connection, images: List[dict], allowed_post_ids: Set[int]) -> int:
    captured_at = utc_now_iso()
    normalized: List[dict] = []
    for item in images:
        row = normalize_image(item)
        if row is None:
            continue
        if allowed_post_ids and int(row["post_id"]) not in allowed_post_ids:
            continue
        normalized.append(row)

    grouped: Dict[int, List[dict]] = defaultdict(list)
    for row in normalized:
        grouped[row["post_id"]].append(row)

    cur = conn.cursor()
    touched_posts = list(grouped.keys())
    if touched_posts:
        cur.executemany("DELETE FROM post_images WHERE post_id = ?", [(pid,) for pid in touched_posts])

    inserted = 0
    for post_id, rows in grouped.items():
        rows.sort(key=lambda x: ((x.get("image_created_at") or ""), x["image_id"]))
        for pos, row in enumerate(rows, start=1):
            cur.execute(
                """
                INSERT OR REPLACE INTO post_images (
                    post_id, image_id, position, image_created_at, nsfw, nsfw_level, source_host, captured_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    post_id,
                    row["image_id"],
                    pos,
                    row.get("image_created_at"),
                    row.get("nsfw"),
                    row.get("nsfw_level"),
                    row.get("source_host"),
                    captured_at,
                ),
            )
            inserted += 1

    conn.commit()
    return inserted


def export_query_to_csv(conn: sqlite3.Connection, query: str, csv_path: str) -> None:
    cur = conn.cursor()
    cur.execute(query)
    rows = cur.fetchall()
    ensure_dir(os.path.dirname(csv_path) or ".")
    with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow([desc[0] for desc in cur.description])
        for row in rows:
            writer.writerow(list(row))


def get_current_posts(conn: sqlite3.Connection) -> List[sqlite3.Row]:
    cur = conn.cursor()
    cur.execute(
        """
        SELECT s.*
        FROM post_snapshots s
        JOIN (
            SELECT post_id, MAX(id) AS max_id
            FROM post_snapshots
            GROUP BY post_id
        ) latest ON latest.max_id = s.id
        ORDER BY s.published_at DESC, s.post_id DESC
        """
    )
    return cur.fetchall()


def get_post_images_map(conn: sqlite3.Connection) -> Dict[int, List[int]]:
    cur = conn.cursor()
    cur.execute(
        """
        SELECT post_id, image_id
        FROM post_images
        ORDER BY post_id ASC, position ASC, image_id ASC
        """
    )
    result: Dict[int, List[int]] = defaultdict(list)
    for row in cur.fetchall():
        result[int(row[0])].append(int(row[1]))
    return result


def estimate_window_metric(
    snapshots_by_post: Dict[int, List[sqlite3.Row]],
    tz_helper: TimezoneHelper,
    post_id: int,
    published_at: Optional[str],
    metric: str,
    hours: int,
) -> Optional[int]:
    published_dt = tz_helper.parse_iso(published_at)
    if published_dt is None:
        return None
    cutoff = published_dt + timedelta(hours=hours)
    snapshots = snapshots_by_post.get(post_id, [])
    if not snapshots:
        return None

    eligible: List[sqlite3.Row] = []
    for row in snapshots:
        captured_dt = tz_helper.parse_iso(row["captured_at"])
        if captured_dt is None:
            continue
        if captured_dt <= cutoff and row[metric] is not None:
            eligible.append(row)

    if eligible:
        return int(eligible[-1][metric])

    latest = snapshots[-1]
    latest_captured = tz_helper.parse_iso(latest["captured_at"])
    if latest_captured is not None and latest_captured <= cutoff and latest[metric] is not None:
        return int(latest[metric])
    return None


def load_snapshots_by_post(conn: sqlite3.Connection) -> Dict[int, List[sqlite3.Row]]:
    cur = conn.cursor()
    cur.execute(
        """
        SELECT *
        FROM post_snapshots
        ORDER BY post_id ASC, captured_at ASC, id ASC
        """
    )
    grouped: Dict[int, List[sqlite3.Row]] = defaultdict(list)
    for row in cur.fetchall():
        grouped[int(row["post_id"])] .append(row)
    return grouped


def avg_or_none(values: Iterable[Optional[int]]) -> Optional[float]:
    filtered = [float(v) for v in values if v is not None]
    if not filtered:
        return None
    return sum(filtered) / len(filtered)


def confidence_label(posts_count: int, known_count: int) -> str:
    score = min(posts_count, known_count)
    if score >= 8:
        return "medium"
    if score >= 3:
        return "low"
    return "low"


def build_hour_and_weekday_summaries(
    current_posts: List[sqlite3.Row],
    tz_helper: TimezoneHelper,
    snapshots_by_post: Dict[int, List[sqlite3.Row]],
) -> Tuple[List[dict], List[dict]]:
    hour_buckets: Dict[int, List[dict]] = defaultdict(list)
    weekday_buckets: Dict[int, List[dict]] = defaultdict(list)

    for row in current_posts:
        parts = tz_helper.local_parts(row["published_at"])
        if parts["hour"] is not None:
            hour_buckets[int(parts["hour"])] .append({"row": row, "parts": parts})
        if parts["weekday"] is not None:
            weekday_buckets[int(parts["weekday"])] .append({"row": row, "parts": parts})

    hour_summary: List[dict] = []
    for hour in range(24):
        bucket = hour_buckets.get(hour, [])
        rows = [x["row"] for x in bucket]
        known_rows = [r for r in rows if r["stats_known"]]
        hour_summary.append(
            {
                "hour": f"{hour:02d}:00",
                "posts": len(rows),
                "avg_2h_reactions": avg_or_none(
                    estimate_window_metric(snapshots_by_post, tz_helper, int(r["post_id"]), r["published_at"], "reaction_total", 2)
                    for r in known_rows
                ),
                "avg_24h_reactions": avg_or_none(
                    estimate_window_metric(snapshots_by_post, tz_helper, int(r["post_id"]), r["published_at"], "reaction_total", 24)
                    for r in known_rows
                ),
                "avg_total_reactions": avg_or_none(r["reaction_total"] for r in known_rows),
                "avg_total_engagement": avg_or_none(r["engagement_total"] for r in known_rows),
                "confidence": confidence_label(len(rows), len(known_rows)),
            }
        )

    weekday_summary: List[dict] = []
    for weekday in range(7):
        bucket = weekday_buckets.get(weekday, [])
        rows = [x["row"] for x in bucket]
        known_rows = [r for r in rows if r["stats_known"]]
        weekday_summary.append(
            {
                "weekday": WEEKDAY_NAMES[weekday],
                "posts": len(rows),
                "avg_2h_reactions": avg_or_none(
                    estimate_window_metric(snapshots_by_post, tz_helper, int(r["post_id"]), r["published_at"], "reaction_total", 2)
                    for r in known_rows
                ),
                "avg_24h_reactions": avg_or_none(
                    estimate_window_metric(snapshots_by_post, tz_helper, int(r["post_id"]), r["published_at"], "reaction_total", 24)
                    for r in known_rows
                ),
                "avg_total_reactions": avg_or_none(r["reaction_total"] for r in known_rows),
                "avg_total_engagement": avg_or_none(r["engagement_total"] for r in known_rows),
                "confidence": confidence_label(len(rows), len(known_rows)),
            }
        )

    return hour_summary, weekday_summary


def load_post_deltas(conn: sqlite3.Connection) -> List[sqlite3.Row]:
    cur = conn.cursor()
    cur.execute(
        """
        SELECT *
        FROM post_deltas
        ORDER BY detected_at DESC, id DESC
        """
    )
    return cur.fetchall()


def summarize_reaction_periods(
    deltas: List[sqlite3.Row],
    tz_helper: TimezoneHelper,
    current_posts: List[sqlite3.Row],
) -> Dict[str, Any]:
    current_by_post = {int(r["post_id"]): r for r in current_posts}
    now_local = utc_now().astimezone(tz_helper.tz)
    today_date = now_local.date()
    week_cutoff = now_local - timedelta(days=7)

    today_totals = {"like": 0, "heart": 0, "laugh": 0, "cry": 0}
    by_post_today: Dict[int, Dict[str, Any]] = defaultdict(lambda: {"like": 0, "heart": 0, "laugh": 0, "cry": 0, "title": None})
    by_post_week: Dict[int, Dict[str, Any]] = defaultdict(lambda: {"like": 0, "heart": 0, "laugh": 0, "cry": 0, "title": None})

    for row in deltas:
        detected_dt = tz_helper.parse_iso(row["detected_at"])
        if detected_dt is None:
            continue
        local_dt = detected_dt.astimezone(tz_helper.tz)
        values = {
            "like": int(row["like_delta"] or 0),
            "heart": int(row["heart_delta"] or 0),
            "laugh": int(row["laugh_delta"] or 0),
            "cry": int(row["cry_delta"] or 0),
        }
        post_id = int(row["post_id"])
        title = row["title"] or (current_by_post.get(post_id)["title"] if post_id in current_by_post else None)

        if local_dt.date() == today_date:
            for key, value in values.items():
                today_totals[key] += value
                by_post_today[post_id][key] += value
            by_post_today[post_id]["title"] = title

        if local_dt >= week_cutoff:
            for key, value in values.items():
                by_post_week[post_id][key] += value
            by_post_week[post_id]["title"] = title

    def finalize_best(bucket: Dict[int, Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        best = None
        for post_id, data in bucket.items():
            total = int(data["like"] or 0) + int(data["heart"] or 0) + int(data["laugh"] or 0) + int(data["cry"] or 0)
            if total <= 0:
                continue
            candidate = {
                "post_id": post_id,
                "title": data.get("title") or (current_by_post.get(post_id)["title"] if post_id in current_by_post else None),
                "like": int(data["like"] or 0),
                "heart": int(data["heart"] or 0),
                "laugh": int(data["laugh"] or 0),
                "cry": int(data["cry"] or 0),
                "total": total,
            }
            if best is None or (candidate["total"], candidate["heart"], candidate["post_id"]) > (best["total"], best["heart"], best["post_id"]):
                best = candidate
        return best

    return {
        "today_totals": today_totals,
        "best_today": finalize_best(by_post_today),
        "best_week": finalize_best(by_post_week),
        "today_label": str(today_date),
        "week_label": f"Last 7 days ending {now_local.strftime('%Y-%m-%d %H:%M %Z')}",
    }


def select_suggested_windows(hour_summary: List[dict]) -> List[dict]:
    candidates = [row for row in hour_summary if row.get("posts", 0) >= 3 and row.get("avg_24h_reactions") is not None]
    candidates.sort(key=lambda r: (float(r.get("avg_24h_reactions") or 0), float(r.get("avg_2h_reactions") or 0), int(r.get("posts") or 0)), reverse=True)
    return candidates[:3]


def fmt_num(value: Optional[float]) -> str:
    if value is None:
        return "n/a"
    return f"{value:.1f}"


def fmt_int(value: Optional[int]) -> str:
    if value is None:
        return "n/a"
    return str(int(value))


def export_csvs(conn: sqlite3.Connection, csv_dir: str, tz_helper: TimezoneHelper) -> None:
    ensure_dir(csv_dir)
    export_query_to_csv(
        conn,
        """
        SELECT id, post_id, username, title, published_at, captured_at, source_host, source_kind,
               stats_known, like_count, heart_count, laugh_count, cry_count, comment_count,
               reaction_total, engagement_total
        FROM post_snapshots
        ORDER BY captured_at DESC, post_id DESC
        """,
        os.path.join(csv_dir, "post_snapshots.csv"),
    )

    export_query_to_csv(
        conn,
        """
        SELECT id, post_id, username, title, published_at, detected_at, source_host,
               like_delta, heart_delta, laugh_delta, cry_delta, comment_delta,
               reaction_total_delta, engagement_total_delta
        FROM post_deltas
        ORDER BY detected_at DESC, post_id DESC
        """,
        os.path.join(csv_dir, "post_deltas.csv"),
    )

    export_query_to_csv(
        conn,
        """
        SELECT post_id, image_id, position, image_created_at, nsfw, nsfw_level, source_host, captured_at
        FROM post_images
        ORDER BY post_id DESC, position ASC, image_id ASC
        """,
        os.path.join(csv_dir, "post_images.csv"),
    )

    export_query_to_csv(
        conn,
        """
        SELECT s.*
        FROM post_snapshots s
        JOIN (
            SELECT post_id, MAX(id) AS max_id
            FROM post_snapshots
            GROUP BY post_id
        ) latest ON latest.max_id = s.id
        ORDER BY s.published_at DESC, s.post_id DESC
        """,
        os.path.join(csv_dir, "current_posts.csv"),
    )

    current_posts = get_current_posts(conn)
    snapshots_by_post = load_snapshots_by_post(conn)
    hour_summary, weekday_summary = build_hour_and_weekday_summaries(current_posts, tz_helper, snapshots_by_post)

    with open(os.path.join(csv_dir, "publish_hour_summary.csv"), "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(["hour", "posts", "avg_2h_reactions", "avg_24h_reactions", "avg_total_reactions", "avg_total_engagement", "confidence"])
        for row in hour_summary:
            writer.writerow([
                row["hour"], row["posts"], fmt_num(row["avg_2h_reactions"]), fmt_num(row["avg_24h_reactions"]),
                fmt_num(row["avg_total_reactions"]), fmt_num(row["avg_total_engagement"]), row["confidence"]
            ])

    with open(os.path.join(csv_dir, "weekday_summary.csv"), "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(["weekday", "posts", "avg_2h_reactions", "avg_24h_reactions", "avg_total_reactions", "avg_total_engagement", "confidence"])
        for row in weekday_summary:
            writer.writerow([
                row["weekday"], row["posts"], fmt_num(row["avg_2h_reactions"]), fmt_num(row["avg_24h_reactions"]),
                fmt_num(row["avg_total_reactions"]), fmt_num(row["avg_total_engagement"]), row["confidence"]
            ])


def html_table(headers: List[str], rows: List[List[str]]) -> str:
    head = "".join(f"<th>{html.escape(h)}</th>" for h in headers)
    body_rows = []
    for row in rows:
        body_rows.append("<tr>" + "".join(f"<td>{cell}</td>" for cell in row) + "</tr>")
    return f"<table><thead><tr>{head}</tr></thead><tbody>{''.join(body_rows)}</tbody></table>"


def post_link(view_host: str, post_id: int) -> str:
    url = f"{view_host.rstrip('/')}/posts/{post_id}"
    return f'<a href="{html.escape(url)}" target="_blank" rel="noopener">post #{post_id}</a>'


def load_runtime_status(runtime_status_path: str | None) -> Dict[str, Any]:
    if not runtime_status_path:
        return {}
    path = Path(runtime_status_path)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}



def render_dashboard(
    conn: sqlite3.Connection,
    html_path: str,
    tz_helper: TimezoneHelper,
    dashboard_name: str,
    view_host: str,
    selected_host: str,
    min_post_id: Optional[int],
    start_date: Optional[str],
    runtime_status_path: Optional[str] = None,
    db_path: Optional[str] = None,
) -> None:
    current_posts = get_current_posts(conn)
    images_map = get_post_images_map(conn)
    snapshots_by_post = load_snapshots_by_post(conn)
    deltas = load_post_deltas(conn)
    hour_summary, weekday_summary = build_hour_and_weekday_summaries(current_posts, tz_helper, snapshots_by_post)
    period_summary = summarize_reaction_periods(deltas, tz_helper, current_posts)
    suggested_windows = select_suggested_windows(hour_summary)

    tracked_posts = len(current_posts)
    known_totals = sum(1 for r in current_posts if r["stats_known"])
    unknown_totals = tracked_posts - known_totals
    latest_capture = current_posts[0]["captured_at"] if current_posts else None
    runtime_status = load_runtime_status(runtime_status_path)
    collections_html = render_collection_dashboard_section(db_path) if db_path else ""

    known_rows = [r for r in current_posts if r["stats_known"]]
    by_total_reactions = sorted(known_rows, key=lambda r: (int(r["reaction_total"] or 0), int(r["heart_count"] or 0), int(r["post_id"])), reverse=True)[:15]

    first24_rows = []
    first2_rows = []
    for r in known_rows:
        first24_reactions = estimate_window_metric(snapshots_by_post, tz_helper, int(r["post_id"]), r["published_at"], "reaction_total", 24)
        first2_reactions = estimate_window_metric(snapshots_by_post, tz_helper, int(r["post_id"]), r["published_at"], "reaction_total", 2)
        if first24_reactions is not None:
            first24_rows.append((r, first24_reactions))
        if first2_reactions is not None:
            first2_rows.append((r, first2_reactions))

    first24_rows.sort(key=lambda pair: (pair[1], int(pair[0]["post_id"])), reverse=True)
    first2_rows.sort(key=lambda pair: (pair[1], int(pair[0]["post_id"])), reverse=True)

    if min_post_id is not None:
        tracking_window = f"From post id ≥ {min_post_id}"
    elif start_date:
        tracking_window = f"From local date ≥ {start_date}"
    else:
        tracking_window = "All posts"

    refresh_seconds = 60

    def fmt_runtime_ts(key: str) -> str:
        value = runtime_status.get(key)
        if value in (None, ""):
            return "Not available"
        return html.escape(tz_helper.fmt_dt(value))

    def chip(label: str) -> str:
        key = str(label or "not available").strip().lower()
        cls = {
            "high": "good",
            "medium": "mid",
            "low": "warn",
            "not enough data": "na",
            "not available": "na",
            "on": "good",
            "off": "warn",
            "window": "mid",
            "tray": "mid",
        }.get(key, "na")
        return f"<span class='chip {cls}'>{html.escape(str(label))}</span>"

    def metric_card(label: str, value: str, detail: str, cls: str = "") -> str:
        return (
            f"<div class='metric-card {cls}'>"
            f"<div class='metric-label'>{html.escape(label)}</div>"
            f"<div class='metric-value'>{value}</div>"
            f"<div class='metric-detail'>{html.escape(detail)}</div>"
            f"</div>"
        )

    def reaction_badge(symbol: str, value: Optional[int], extra_cls: str = "") -> str:
        return f"<span class='rbadge {extra_cls}'><span class='ricon'>{symbol}</span><span class='rnum'>{int(value or 0)}</span></span>"

    def reaction_group(like: int, heart: int, laugh: int, cry: int) -> str:
        return (
            "<div class='rgroup'>"
            f"{reaction_badge('👍', like)}"
            f"{reaction_badge('❤️', heart)}"
            f"{reaction_badge('😂', laugh)}"
            f"{reaction_badge('😢', cry)}"
            "</div>"
        )

    def reaction_stat(symbol: str, label: str, value: int) -> str:
        return (
            "<div class='reaction-stat'>"
            f"<div class='reaction-head'><span class='ricon'>{symbol}</span><span>{html.escape(label)}</span></div>"
            f"<div class='reaction-value'>{int(value or 0)}</div>"
            "</div>"
        )

    def best_post_card(title: str, payload: Optional[Dict[str, Any]], subtitle: str, empty_text: str) -> str:
        if not payload:
            return (
                "<div class='feature-card'>"
                f"<div class='feature-title'>{html.escape(title)}</div>"
                f"<div class='feature-sub'>{html.escape(subtitle)}</div>"
                "<div class='empty-state'>—</div>"
                f"<div class='feature-note'>{html.escape(empty_text)}</div>"
                "</div>"
            )
        heading = post_link(view_host, int(payload['post_id']))
        post_title = html.escape(payload.get('title') or 'Untitled post')
        return (
            "<div class='feature-card'>"
            f"<div class='feature-title'>{html.escape(title)}</div>"
            f"<div class='feature-sub'>{html.escape(subtitle)}</div>"
            f"<div class='feature-score'>{int(payload['total'])}</div>"
            f"<div class='feature-post'>{heading}</div>"
            f"<div class='feature-note'>{post_title}</div>"
            f"{reaction_group(payload['like'], payload['heart'], payload['laugh'], payload['cry'])}"
            "</div>"
        )

    def render_windows_table(rows: List[dict]) -> str:
        if not rows:
            return "<div class='feature-note'>Not enough post history yet.</div>"
        body = []
        for row in rows:
            body.append(
                "<tr>"
                f"<td>{html.escape(str(row['hour']))}</td>"
                f"<td class='num'>{int(row['posts'])}</td>"
                f"<td class='num'>{fmt_num(row['avg_2h_reactions'])}</td>"
                f"<td class='num'>{fmt_num(row['avg_24h_reactions'])}</td>"
                f"<td>{chip(row['confidence'])}</td>"
                "</tr>"
            )
        return (
            "<table class='clean-table'>"
            "<thead><tr><th>Hour</th><th class='num'>Posts</th><th class='num'>Avg 2h reactions</th><th class='num'>Avg 24h reactions</th><th>Confidence</th></tr></thead>"
            f"<tbody>{''.join(body)}</tbody></table>"
        )

    def render_leaders_table(rows: List[sqlite3.Row]) -> str:
        if not rows:
            return "<div class='feature-note'>No post totals captured yet.</div>"
        body = []
        for idx, row in enumerate(rows, start=1):
            body.append(
                "<tr>"
                f"<td class='num'>{idx}</td>"
                f"<td>{post_link(view_host, int(row['post_id']))}<div class='row-sub'>{html.escape(row['title'] or 'Untitled post')}</div></td>"
                f"<td class='num'>{fmt_int(row['reaction_total'])}</td>"
                f"<td>{reaction_group(int(row['like_count'] or 0), int(row['heart_count'] or 0), int(row['laugh_count'] or 0), int(row['cry_count'] or 0))}</td>"
                f"<td>{html.escape(tz_helper.fmt_dt(row['published_at']))}</td>"
                "</tr>"
            )
        return (
            "<table class='clean-table'>"
            "<thead><tr><th>#</th><th>Post</th><th class='num'>Reactions</th><th>Breakdown</th><th>Published</th></tr></thead>"
            f"<tbody>{''.join(body)}</tbody></table>"
        )

    def render_window_table(title: str, rows: List[Tuple[sqlite3.Row, int]], window_label: str) -> str:
        if not rows:
            return f"<div class='panel'><h2>{html.escape(title)}</h2><div class='feature-note'>Not enough early snapshots yet.</div></div>"
        body = []
        for idx, (row, score) in enumerate(rows[:15], start=1):
            body.append(
                "<tr>"
                f"<td class='num'>{idx}</td>"
                f"<td>{post_link(view_host, int(row['post_id']))}</td>"
                f"<td class='num'>{score}</td>"
                f"<td>{html.escape(window_label)}</td>"
                f"<td>{html.escape(tz_helper.fmt_dt(row['published_at']))}</td>"
                "</tr>"
            )
        return (
            f"<div class='panel table-panel'><h2>{html.escape(title)}</h2>"
            "<table class='clean-table'>"
            "<thead><tr><th>#</th><th>Post</th><th class='num'>Score</th><th>Details</th><th>Published</th></tr></thead>"
            f"<tbody>{''.join(body)}</tbody></table></div>"
        )

    def render_summary_table(title: str, rows: List[List[str]], headers: List[str]) -> str:
        body = []
        for row in rows:
            rendered = []
            for idx, cell in enumerate(row):
                cls = " class='num'" if idx in (1,2,3,4,5) and any(ch.isdigit() for ch in str(cell)) else ""
                rendered.append(f"<td{cls}>{cell}</td>")
            body.append("<tr>" + "".join(rendered) + "</tr>")
        head_cells = []
        for i, h in enumerate(headers):
            cls = " class='num'" if i and i < len(headers) - 1 else ""
            head_cells.append(f"<th{cls}>{html.escape(h)}</th>")
        head = "".join(head_cells)
        return f"<div class='panel table-panel'><h2>{html.escape(title)}</h2><table class='clean-table'><thead><tr>{head}</tr></thead><tbody>{''.join(body)}</tbody></table></div>"

    def render_recent_posts(rows: List[sqlite3.Row]) -> str:
        if not rows:
            return "<div class='feature-note'>No tracked posts yet.</div>"
        body = []
        for row in rows[:20]:
            imgs = images_map.get(int(row['post_id']), [])
            img_line = ', '.join(f"#{img_id}" for img_id in imgs[:4]) + (' …' if len(imgs) > 4 else '')
            total = fmt_int(row['reaction_total']) if row['stats_known'] else 'n/a'
            comments = fmt_int(row['comment_count']) if row['stats_known'] else 'n/a'
            body.append(
                "<tr>"
                f"<td>{post_link(view_host, int(row['post_id']))}<div class='row-sub'>{html.escape(row['title'] or 'Untitled post')}</div></td>"
                f"<td>{html.escape(tz_helper.fmt_dt(row['published_at']))}</td>"
                f"<td class='num'>{total}</td>"
                f"<td class='num'>{comments}</td>"
                f"<td>{html.escape(img_line or '—')}</td>"
                "</tr>"
            )
        return (
            "<table class='clean-table'>"
            "<thead><tr><th>Post</th><th>Published</th><th class='num'>Reactions</th><th class='num'>Comments</th><th>Images</th></tr></thead>"
            f"<tbody>{''.join(body)}</tbody></table>"
        )

    data_source_label = html.escape(selected_host.replace("https://", ""))
    polling_interval_value = runtime_status.get("poll_minutes")
    polling_interval = f"{polling_interval_value} min" if polling_interval_value not in (None, "") else "Not available"
    auto_polling = runtime_status.get("auto_polling")
    auto_polling_label = chip("On" if auto_polling is True else ("Off" if runtime_status else "Not available"))
    app_mode_label_raw = str(runtime_status.get("app_mode") or "Not available")
    app_mode_label = chip(app_mode_label_raw.title() if app_mode_label_raw != "Not available" else app_mode_label_raw)
    runtime_connected = bool(runtime_status)

    today = period_summary['today_totals']

    hour_rows = [[html.escape(str(r['hour'])), str(r['posts']), fmt_num(r['avg_2h_reactions']), fmt_num(r['avg_24h_reactions']), fmt_num(r['avg_total_reactions']), fmt_num(r['avg_total_engagement']), chip(r['confidence'])] for r in hour_summary]
    weekday_rows = [[html.escape(str(r['weekday'])), str(r['posts']), fmt_num(r['avg_2h_reactions']), fmt_num(r['avg_24h_reactions']), fmt_num(r['avg_total_reactions']), fmt_num(r['avg_total_engagement']), chip(r['confidence'])] for r in weekday_summary]

    def render_collapsible_section(title: str, inner_html: str, section_id: str, open_default: bool = False, hint: Optional[str] = None) -> str:
        open_attr = " open" if open_default else ""
        hint_html = f"<div class='acc-hint'>{html.escape(hint)}</div>" if hint else ""
        return (
            f"<details class='accordion-panel' data-acc-id='{html.escape(section_id, quote=True)}'{open_attr}>"
            "<summary class='acc-summary'>"
            f"<span class='acc-title'>{html.escape(title)}</span>"
            "<span class='acc-icon' aria-hidden='true'></span>"
            "</summary>"
            f"<div class='acc-body'>{hint_html}{inner_html}</div>"
            "</details>"
        )

    css = """
    :root{--bg:#0b1020;--panel:#121a2f;--panel2:#161f38;--border:#263353;--text:#ebf1ff;--muted:#9baacf;--accent:#7fb3ff;--good:#2ea043;--warn:#d29922;--na:#5b657f;--shadow:0 10px 24px rgba(0,0,0,.18)}
    *{box-sizing:border-box} body{margin:0;padding:24px;font-family:Segoe UI,Arial,sans-serif;background:linear-gradient(180deg,#091126 0%,#0d1323 100%);color:var(--text)}
    .wrap{max-width:1560px;margin:0 auto} .hero{display:flex;justify-content:space-between;align-items:flex-start;gap:24px;margin-bottom:18px} h1{margin:0;font-size:26px} .sub{margin:8px 0 0;color:var(--muted)} .toolbar{display:flex;gap:10px;align-items:center;flex-wrap:wrap}
    .toolbar .live{font-size:13px;color:var(--muted)} .toolbar button{border:1px solid var(--border);background:var(--panel2);color:var(--text);padding:10px 14px;border-radius:10px;cursor:pointer} .toolbar button:hover{border-color:#45629c}
    .section-title{margin:24px 0 12px;font-size:12px;letter-spacing:.12em;text-transform:uppercase;color:var(--muted)}
    .metrics{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:16px}.metric-card,.panel,.feature-card{background:linear-gradient(180deg,var(--panel) 0%,#12192c 100%);border:1px solid var(--border);border-radius:16px;box-shadow:var(--shadow)}
    .metric-card{padding:18px;min-height:146px}.metric-label{font-size:13px;color:var(--muted);margin-bottom:10px}.metric-value{font-size:22px;font-weight:800;line-height:1.25}.metric-detail{margin-top:12px;font-size:12px;color:var(--muted)}
    .feature-grid{display:grid;grid-template-columns:1fr 1fr 1fr;gap:16px;margin-top:18px}.feature-card{padding:18px;min-height:185px}.feature-title{font-size:22px;font-weight:700;margin-bottom:6px}.feature-sub{font-size:12px;color:var(--muted);margin-bottom:14px}.feature-score{font-size:38px;font-weight:800;margin-bottom:10px}.feature-post{font-size:16px;font-weight:700;margin-bottom:8px}.feature-note{font-size:13px;color:var(--muted);line-height:1.45}.empty-state{font-size:34px;font-weight:800;margin:18px 0 8px}
    .reaction-row{display:grid;grid-template-columns:repeat(4,1fr);gap:12px}.reaction-stat{background:#10182b;border:1px solid #31446f;border-radius:14px;padding:14px}.reaction-head{display:flex;gap:8px;align-items:center;font-size:12px;color:var(--muted);text-transform:uppercase;letter-spacing:.04em}.reaction-value{margin-top:10px;font-size:34px;font-weight:800;text-align:center;font-variant-numeric:tabular-nums}
    .rgroup{display:flex;gap:6px;flex-wrap:wrap;margin-top:14px}.rbadge{display:inline-flex;align-items:center;justify-content:center;gap:4px;white-space:nowrap;background:#10182b;border:1px solid #31446f;border-radius:999px;padding:5px 9px;box-sizing:border-box}.ricon{font-size:14px;line-height:1}.rnum{font-weight:700;font-variant-numeric:tabular-nums;font-size:14px}
    .chip{display:inline-flex;align-items:center;padding:4px 10px;border-radius:999px;font-size:12px;font-weight:700;background:#1e2742;color:#d8e5ff;border:1px solid transparent}.chip.good{background:rgba(46,160,67,.15);color:#7ee787;border-color:rgba(46,160,67,.35)}.chip.mid{background:rgba(56,139,253,.16);color:#9cc3ff;border-color:rgba(56,139,253,.35)}.chip.warn{background:rgba(210,153,34,.16);color:#f2cc60;border-color:rgba(210,153,34,.35)}.chip.na{background:rgba(91,101,127,.18);color:#c8d1e8;border-color:rgba(91,101,127,.35)}
    .panel{padding:18px}.panel h2{margin:0 0 8px;font-size:18px}.panel .hint{margin:0 0 12px;color:var(--muted);font-size:13px;line-height:1.45}.clean-table{width:100%;border-collapse:collapse}.clean-table th,.clean-table td{padding:12px 10px;border-bottom:1px solid var(--border);text-align:center;vertical-align:top}.clean-table th{font-size:12px;letter-spacing:.05em;text-transform:uppercase;color:var(--muted)}.clean-table td.num,.clean-table th.num{text-align:center}.table-panel{overflow:auto}
    .accordion-stack{display:grid;grid-template-columns:1fr;gap:16px;margin-top:18px}.accordion-panel{background:linear-gradient(180deg,var(--panel) 0%,#12192c 100%);border:1px solid var(--border);border-radius:16px;box-shadow:var(--shadow);overflow:hidden}.accordion-panel[open]{overflow:visible}.acc-summary{list-style:none;cursor:pointer;display:flex;align-items:center;justify-content:space-between;padding:16px 18px;user-select:none}.acc-summary::-webkit-details-marker{display:none}.acc-title{font-size:18px;font-weight:700}.acc-icon{width:12px;height:12px;position:relative;flex:0 0 12px}.acc-icon::before,.acc-icon::after{content:'';position:absolute;background:var(--muted);border-radius:2px;transition:transform .18s ease,opacity .18s ease}.acc-icon::before{left:0;right:0;top:5px;height:2px}.acc-icon::after{top:0;bottom:0;left:5px;width:2px}.accordion-panel[open] .acc-icon::after{opacity:0;transform:scaleY(.2)}.acc-body{padding:0 18px 18px}.acc-hint{margin:0 0 12px;color:var(--muted);font-size:13px;line-height:1.45}
    a{color:var(--accent);text-decoration:none} a:hover{text-decoration:underline}.small-note{margin-top:14px;color:var(--muted);font-size:12px}
    @media (max-width:1100px){.feature-grid,.reaction-row{grid-template-columns:1fr}.hero{flex-direction:column}.metrics{grid-template-columns:1fr}}
    """

    parts: List[str] = []
    parts.append("<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width, initial-scale=1'>")
    parts.append("<title>CivitAI Tracker v10.0</title>")
    parts.append(f"<style>{css}</style>")
    parts.append(COLLECTION_SECTION_CSS)
    parts.append(
        f"<script>function refreshNow(){{location.reload();}}setInterval(function(){{location.reload();}}, {refresh_seconds*1000});"
        "document.addEventListener('DOMContentLoaded', function(){"
        "document.querySelectorAll('details[data-acc-id]').forEach(function(el){"
        "var key='civitaiTrackerAcc:'+el.dataset.accId;"
        "var saved=localStorage.getItem(key);"
        "if(saved==='open'){el.open=true;}else if(saved==='closed'){el.open=false;}"
        "el.addEventListener('toggle', function(){localStorage.setItem(key, el.open ? 'open' : 'closed');});"
        "});"
        "});</script>"
    )
    parts.append("</head><body><div class='wrap'>")
    parts.append(
        "<div class='hero'>"
        f"<div><h1>CivitAI Tracker v10.0</h1><p class='sub'>tRPC post-based analytics for <strong>{html.escape(dashboard_name)}</strong></p></div>"
        f"<div class='toolbar'><span class='live'>Auto-refresh every {refresh_seconds}s</span><button onclick='refreshNow()'>Refresh now</button><span class='live'>{'Runtime status connected' if runtime_connected else 'No live runner status yet'}</span></div>"
        "</div>"
    )

    parts.append("<div class='section-title'>Data snapshot</div><div class='metrics'>")
    parts.append(metric_card("Tracked posts", str(tracked_posts), tracking_window))
    parts.append(metric_card("Known totals", str(known_totals), "Posts with usable stats"))
    parts.append(metric_card("Unknown totals", str(unknown_totals), "Posts without usable stats"))
    parts.append(metric_card("Data source", data_source_label, "tRPC post.getInfinite"))
    parts.append(metric_card("Last capture", html.escape(tz_helper.fmt_dt(latest_capture)), "Latest snapshot time"))
    parts.append("</div>")

    parts.append("<div class='section-title'>Runtime state</div><div class='metrics'>")
    parts.append(metric_card("Last successful run", fmt_runtime_ts("last_success_at"), "Runner status"))
    parts.append(metric_card("Next scheduled run", fmt_runtime_ts("next_run_at"), "Based on auto polling"))
    parts.append(metric_card("Polling interval", html.escape(polling_interval), "Configured in the app"))
    parts.append(metric_card("Auto polling", auto_polling_label, "Background polling state"))
    parts.append(metric_card("App mode", app_mode_label, "Window or tray"))
    parts.append("</div>")

    if known_totals == 0:
        parts.append("<div class='panel' style='border-color:#7a3a3a;background:#2a1b1b'><h2>Totals unavailable</h2><div class='hint'>The tracker found posts, but none of the latest snapshots have usable stats. Keep collecting snapshots or verify the tRPC response shape again.</div></div>")

    parts.append("<div class='feature-grid'>")
    parts.append(
        "<div class='feature-card'>"
        "<div class='feature-title'>Reactions today by type</div>"
        f"<div class='feature-sub'>Local day: {html.escape(period_summary['today_label'])}</div>"
        "<div class='reaction-row'>"
        f"{reaction_stat('👍', 'Likes', today['like'])}"
        f"{reaction_stat('❤️', 'Hearts', today['heart'])}"
        f"{reaction_stat('😂', 'Laughs', today['laugh'])}"
        f"{reaction_stat('😢', 'Cries', today['cry'])}"
        "</div></div>"
    )
    parts.append(best_post_card("Best post today", period_summary['best_today'], period_summary['today_label'], "No reaction gains captured yet for today."))
    parts.append(best_post_card("Best post this week", period_summary['best_week'], period_summary['week_label'], "No reaction gains captured yet for the last 7 days."))
    parts.append("</div>")

    if collections_html:
        parts.append(collections_html)

    parts.append(
        "<div class='panel table-panel' style='margin-top:18px'>"
        "<h2>Suggested posting windows</h2>"
        "<div class='hint'>Advisory only. Based on your historical performance. Content strength still matters more than timing alone.</div>"
        f"{render_windows_table(suggested_windows)}"
        "</div>"
    )

    parts.append("<div class='accordion-stack'>")
    parts.append(render_collapsible_section("Leaders by total reactions", render_leaders_table(by_total_reactions), "leaders", open_default=True))
    parts.append(render_collapsible_section("Best first 24h", render_window_table("Best first 24h", first24_rows, "Reactions captured within first 24h window").replace("<div class='panel table-panel'><h2>Best first 24h</h2>", "").replace("</div>", "", 1), "best24", hint="Early performance snapshot based on collected first-day windows."))
    parts.append(render_collapsible_section("Best first 2h", render_window_table("Best first 2h", first2_rows, "Reactions captured within first 2h window").replace("<div class='panel table-panel'><h2>Best first 2h</h2>", "").replace("</div>", "", 1), "best2", hint="Very early momentum based on the first two hours of captured data."))
    parts.append(render_collapsible_section("Publish hour summary", render_summary_table(
        "Publish hour summary",
        hour_rows,
        ["Hour", "Posts", "Avg 2h reactions", "Avg 24h reactions", "Avg total reactions", "Avg total engagement", "Confidence"],
    ).replace("<div class='panel table-panel'><h2>Publish hour summary</h2>", "").replace("</div>", "", 1), "hours", hint="Average performance grouped by publication hour in your local timezone."))
    parts.append(render_collapsible_section("Weekday summary", render_summary_table(
        "Weekday summary",
        weekday_rows,
        ["Weekday", "Posts", "Avg 2h reactions", "Avg 24h reactions", "Avg total reactions", "Avg total engagement", "Confidence"],
    ).replace("<div class='panel table-panel'><h2>Weekday summary</h2>", "").replace("</div>", "", 1), "weekdays", hint="Average performance grouped by weekday in your local timezone."))
    parts.append(render_collapsible_section("Recent tracked posts", render_recent_posts(current_posts), "recent", open_default=True, hint="Latest posts included in the tracker after the configured start point."))
    parts.append("</div>")

    parts.append("</div></body></html>")
    Path(html_path).write_text(''.join(parts), encoding='utf-8')

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="CivitAI post-based tracker using tRPC post.getInfinite (dashboard architecture polish v8.8).")
    parser.add_argument("--config", default="config.json", help="Path to JSON config file")
    parser.add_argument("--username", default=None, help="CivitAI username")
    parser.add_argument("--display-name", default=None, help="Display name shown in dashboard")
    parser.add_argument("--db", default=None, help="Path to SQLite DB")
    parser.add_argument("--csv-dir", default=None, help="Directory for CSV exports")
    parser.add_argument("--html", default=None, help="Path to output HTML dashboard")
    parser.add_argument("--tz", default=None, help="IANA timezone name, e.g. Europe/Moscow")
    parser.add_argument("--api-key", default=None, help="API key")
    parser.add_argument("--api-key-file", default=None, help="Path to a text file containing the API key")
    parser.add_argument("--api-mode", default=None, choices=["auto", "red", "com"], help="Preferred API host mode")
    parser.add_argument("--view-host", default=None, help="Base host for post links in dashboard")
    parser.add_argument("--nsfw-level", default=None, choices=["None", "Soft", "Mature", "X"], help="NSFW level for REST fallback")
    parser.add_argument("--min-post-id", type=int, default=None, help="Ignore posts older than this post ID")
    parser.add_argument("--start-date", default=None, help="Ignore posts older than this local date YYYY-MM-DD")
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT)
    parser.add_argument(
        "--allow-rest-fallback",
        action="store_const",
        const=True,
        default=None,
        help="Use old REST image listing only if tRPC image fetch fails",
    )
    return parser.parse_args()


def run_once(
    username: str,
    dashboard_name: str,
    db_path: str,
    csv_dir: str,
    html_path: str,
    tz_name: str,
    api_key: Optional[str],
    api_mode: str,
    view_host: str,
    nsfw_level: str,
    min_post_id: Optional[int],
    start_date: Optional[str],
    timeout: int,
    allow_rest_fallback: bool,
    runtime_status_path: Optional[str] = None,
) -> Dict[str, Any]:
    tz_helper = TimezoneHelper(tz_name)
    conn = db_connect(db_path)
    session = requests.Session()
    session.headers.update(build_headers(api_key))
    hosts = get_hosts_for_mode(api_mode)

    try:
        init_db(conn)
        selected_host, post_items = choose_working_host(session=session, hosts=hosts, username=username, timeout=timeout)
        tracked_posts, changed_posts, tracked_post_ids = process_posts(
            conn=conn,
            posts=post_items,
            tz_helper=tz_helper,
            min_post_id=min_post_id,
            start_date=start_date,
            source_kind="trpc_post.getInfinite",
        )

        image_source = "trpc_image.getInfinite"
        try:
            image_items = fetch_images_trpc(session=session, host=selected_host, username=username, timeout=timeout)
        except Exception as exc:
            if not allow_rest_fallback:
                print(f"Image enrichment skipped: {exc}")
                image_items = []
            else:
                print(f"tRPC image fetch failed, trying REST fallback: {exc}")
                image_items = rest_fetch_images(session=session, host=selected_host, username=username, timeout=timeout, nsfw_level=nsfw_level)
                image_source = "rest_api_v1_images"

        image_rows = replace_post_images(conn=conn, images=image_items, allowed_post_ids=tracked_post_ids)
        export_csvs(conn=conn, csv_dir=csv_dir, tz_helper=tz_helper)
        render_dashboard(
            conn=conn,
            html_path=html_path,
            tz_helper=tz_helper,
            dashboard_name=dashboard_name,
            view_host=view_host,
            selected_host=selected_host,
            min_post_id=min_post_id,
            start_date=start_date,
            runtime_status_path=str(Path(runtime_status_path).resolve()) if runtime_status_path else None,
            db_path=db_path,
        )

        current_posts = get_current_posts(conn)
        known_totals = sum(1 for row in current_posts if row["stats_known"])
        return {
            "selected_host": selected_host,
            "tracked_posts": tracked_posts,
            "changed_posts": changed_posts,
            "known_totals": known_totals,
            "image_rows": image_rows,
            "image_source": image_source,
            "current_posts": len(current_posts),
            "dashboard_name": dashboard_name,
        }
    finally:
        conn.close()
        session.close()


def resolve_runtime_config(args: argparse.Namespace) -> Dict[str, Any]:
    config_path = Path(args.config)
    if not config_path.exists():
        raise FileNotFoundError(
            f"Config file not found: {config_path}\n"
            f"Run: python setup_config.py"
        )

    cfg = load_yaml_config(str(config_path))

    username = choose(args.username, deep_get(cfg, "profile.username"))
    dashboard_name = choose(args.display_name, deep_get(cfg, "profile.display_name"), username)
    tz_name = choose(args.tz, deep_get(cfg, "profile.timezone"), "UTC")

    db_path = choose(args.db, deep_get(cfg, "paths.db"), "civitai_tracker_v8.db")
    csv_dir = choose(args.csv_dir, deep_get(cfg, "paths.csv_dir"), "csv")
    html_path = choose(args.html, deep_get(cfg, "paths.html"), "dashboard.html")

    api_mode = choose(args.api_mode, deep_get(cfg, "api.mode"), DEFAULT_API_MODE)
    view_host = choose(args.view_host, deep_get(cfg, "api.view_host"), DEFAULT_VIEW_HOST)
    nsfw_level = choose(args.nsfw_level, deep_get(cfg, "api.nsfw_level"), DEFAULT_NSFW_LEVEL)

    cfg_start_mode = deep_get(cfg, "tracking.start_mode", "post_id")
    cfg_min_post_id = deep_get(cfg, "tracking.start_post_id")
    cfg_start_date = deep_get(cfg, "tracking.start_date")
    poll_minutes = deep_get(cfg, "tracking.poll_minutes", DEFAULT_POLL_MINUTES)

    if args.start_date is not None:
        start_mode = "date"
    elif args.min_post_id is not None:
        start_mode = "post_id"
    else:
        start_mode = cfg_start_mode

    if start_mode == "date":
        min_post_id = None
        start_date = choose(args.start_date, cfg_start_date)
    else:
        min_post_id = choose(args.min_post_id, cfg_min_post_id)
        start_date = None

    allow_rest_fallback = choose(
        args.allow_rest_fallback,
        deep_get(cfg, "options.allow_rest_fallback"),
        False,
    )

    inline_api_key = choose(args.api_key, deep_get(cfg, "auth.api_key"))
    api_key_file = choose(args.api_key_file, deep_get(cfg, "auth.api_key_file"), "api_key.txt")
    api_key = read_api_key(inline_api_key, api_key_file)

    if not username:
        raise ValueError("Username is not set. Provide it in config.json or via --username")
    if start_mode == "post_id" and not min_post_id:
        raise ValueError("For start_mode=post_id you must set tracking.start_post_id or --min-post-id")
    if start_mode == "date" and not start_date:
        raise ValueError("For start_mode=date you must set tracking.start_date or --start-date")

    return {
        "username": username,
        "dashboard_name": dashboard_name,
        "tz_name": tz_name,
        "db_path": db_path,
        "csv_dir": csv_dir,
        "html_path": html_path,
        "api_mode": api_mode,
        "view_host": view_host,
        "nsfw_level": nsfw_level,
        "min_post_id": min_post_id,
        "start_date": start_date,
        "poll_minutes": poll_minutes,
        "allow_rest_fallback": bool(allow_rest_fallback),
        "api_key": api_key,
        "api_key_file": api_key_file,
        "enable_buzz_ingest": bool(deep_get(cfg, "options.enable_buzz_ingest", True)),
        "buzz_account_type": deep_get(cfg, "collection_tracking.account_type", "blue"),
        "buzz_backfill_days": deep_get(cfg, "collection_tracking.backfill_days", 60),
        "buzz_overlap_hours": deep_get(cfg, "collection_tracking.overlap_hours", 24),
        "buzz_max_pages": deep_get(cfg, "collection_tracking.max_pages", 10),
        "buzz_bootstrap_max_pages": deep_get(cfg, "collection_tracking.bootstrap_max_pages", deep_get(cfg, "collection_tracking.max_pages", 100)),
        "buzz_maintenance_max_pages": deep_get(cfg, "collection_tracking.maintenance_max_pages", 10),
        "buzz_max_history_days": deep_get(cfg, "collection_tracking.max_history_days", deep_get(cfg, "collection_tracking.backfill_days", 120)),
        "buzz_http_timeout_seconds": deep_get(cfg, "collection_tracking.http_timeout_seconds", 60),
        "mode": api_mode,
        "host": view_host,
        "runtime_status_path": str(config_path.resolve().parent / "runtime_status.json"),
    }


def make_default_namespace(config_path: str = "config.json", timeout: int = DEFAULT_TIMEOUT) -> argparse.Namespace:
    return argparse.Namespace(
        config=config_path,
        username=None,
        display_name=None,
        db=None,
        csv_dir=None,
        html=None,
        tz=None,
        api_key=None,
        api_key_file=None,
        api_mode=None,
        view_host=None,
        nsfw_level=None,
        min_post_id=None,
        start_date=None,
        timeout=timeout,
        allow_rest_fallback=None,
    )


def refresh_dashboard_from_config(config_path: str = "config.json") -> None:
    args = make_default_namespace(config_path=config_path, timeout=DEFAULT_TIMEOUT)
    runtime = resolve_runtime_config(args)
    if not Path(runtime["db_path"]).exists():
        return
    conn = db_connect(runtime["db_path"])
    try:
        tz_helper = TimezoneHelper(runtime["tz_name"])
        render_dashboard(
            conn=conn,
            html_path=runtime["html_path"],
            tz_helper=tz_helper,
            dashboard_name=runtime["dashboard_name"],
            view_host=runtime["view_host"],
            selected_host=load_runtime_status(runtime["runtime_status_path"]).get("selected_host") or "https://civitai.red",
            min_post_id=runtime["min_post_id"],
            start_date=runtime["start_date"],
            runtime_status_path=runtime["runtime_status_path"],
            db_path=runtime["db_path"],
        )
    finally:
        conn.close()


def run_from_config(config_path: str = "config.json", timeout: int = DEFAULT_TIMEOUT) -> Dict[str, Any]:
    args = make_default_namespace(config_path=config_path, timeout=timeout)
    runtime = resolve_runtime_config(args)
    return run_once(
        username=runtime["username"],
        dashboard_name=runtime["dashboard_name"],
        db_path=runtime["db_path"],
        csv_dir=runtime["csv_dir"],
        html_path=runtime["html_path"],
        tz_name=runtime["tz_name"],
        api_key=runtime["api_key"],
        api_mode=runtime["api_mode"],
        view_host=runtime["view_host"],
        nsfw_level=runtime["nsfw_level"],
        min_post_id=runtime["min_post_id"],
        start_date=runtime["start_date"],
        timeout=timeout,
        allow_rest_fallback=runtime["allow_rest_fallback"],
        runtime_status_path=runtime["runtime_status_path"],
    )



def _resolve_runtime_from_config_dict(config: Dict[str, Any], config_path: str = "config.json", timeout: int = DEFAULT_TIMEOUT) -> Dict[str, Any]:
    cfg = config or {}
    config_base = Path(config_path).resolve().parent

    username = deep_get(cfg, "profile.username")
    dashboard_name = deep_get(cfg, "profile.display_name") or username
    tz_name = deep_get(cfg, "profile.timezone", "UTC")

    db_path = deep_get(cfg, "paths.db", "civitai_tracker_v8.db")
    csv_dir = deep_get(cfg, "paths.csv_dir", "csv")
    html_path = deep_get(cfg, "paths.html", "dashboard.html")

    api_mode = deep_get(cfg, "api.mode", DEFAULT_API_MODE)
    view_host = deep_get(cfg, "api.view_host", DEFAULT_VIEW_HOST)
    nsfw_level = deep_get(cfg, "api.nsfw_level", DEFAULT_NSFW_LEVEL)

    start_mode = deep_get(cfg, "tracking.start_mode", "post_id")
    min_post_id = deep_get(cfg, "tracking.start_post_id") if start_mode != "date" else None
    start_date = deep_get(cfg, "tracking.start_date") if start_mode == "date" else None
    poll_minutes = deep_get(cfg, "tracking.poll_minutes", DEFAULT_POLL_MINUTES)

    allow_rest_fallback = bool(deep_get(cfg, "options.allow_rest_fallback", False))
    inline_api_key = deep_get(cfg, "auth.api_key")
    api_key_file = deep_get(cfg, "auth.api_key_file", "api_key.txt")
    api_key = read_api_key(inline_api_key, api_key_file)

    if not username:
        raise ValueError("Username is not set. Provide it in config.json or via --username")
    if start_mode == "post_id" and not min_post_id:
        raise ValueError("For start_mode=post_id you must set tracking.start_post_id or --min-post-id")
    if start_mode == "date" and not start_date:
        raise ValueError("For start_mode=date you must set tracking.start_date or --start-date")

    return {
        "username": username,
        "dashboard_name": dashboard_name,
        "tz_name": tz_name,
        "db_path": db_path,
        "csv_dir": csv_dir,
        "html_path": html_path,
        "api_mode": api_mode,
        "view_host": view_host,
        "nsfw_level": nsfw_level,
        "min_post_id": min_post_id,
        "start_date": start_date,
        "poll_minutes": poll_minutes,
        "allow_rest_fallback": allow_rest_fallback,
        "api_key": api_key,
        "api_key_file": api_key_file,
        "enable_buzz_ingest": bool(deep_get(cfg, "options.enable_buzz_ingest", True)),
        "buzz_account_type": deep_get(cfg, "collection_tracking.account_type", "blue"),
        "buzz_backfill_days": deep_get(cfg, "collection_tracking.backfill_days", 60),
        "buzz_overlap_hours": deep_get(cfg, "collection_tracking.overlap_hours", 24),
        "buzz_max_pages": deep_get(cfg, "collection_tracking.max_pages", 10),
        "buzz_bootstrap_max_pages": deep_get(cfg, "collection_tracking.bootstrap_max_pages", deep_get(cfg, "collection_tracking.max_pages", 100)),
        "buzz_maintenance_max_pages": deep_get(cfg, "collection_tracking.maintenance_max_pages", 10),
        "buzz_max_history_days": deep_get(cfg, "collection_tracking.max_history_days", deep_get(cfg, "collection_tracking.backfill_days", 120)),
        "buzz_http_timeout_seconds": deep_get(cfg, "collection_tracking.http_timeout_seconds", 60),
        "mode": api_mode,
        "host": view_host,
        "runtime_status_path": str(config_base / "runtime_status.json"),
    }


def run_collection_once(
    config_path: str | None = None,
    config: Dict[str, Any] | None = None,
    timeout: int = DEFAULT_TIMEOUT,
) -> Dict[str, Any]:
    runtime: Dict[str, Any] = {}
    try:
        if config is not None:
            runtime = _resolve_runtime_from_config_dict(config, config_path or "config.json", timeout=timeout)
        else:
            runtime = resolve_runtime_config(make_default_namespace(config_path=config_path or "config.json", timeout=timeout))

        core_result = run_once(
            username=runtime["username"],
            dashboard_name=runtime["dashboard_name"],
            db_path=runtime["db_path"],
            csv_dir=runtime["csv_dir"],
            html_path=runtime["html_path"],
            tz_name=runtime["tz_name"],
            api_key=runtime["api_key"],
            api_mode=runtime["api_mode"],
            view_host=runtime["view_host"],
            nsfw_level=runtime["nsfw_level"],
            min_post_id=runtime["min_post_id"],
            start_date=runtime["start_date"],
            timeout=timeout,
            allow_rest_fallback=runtime["allow_rest_fallback"],
            runtime_status_path=runtime["runtime_status_path"],
        )

        service_result: Dict[str, Any] = {
            "ok": True,
            "error": "",
            "selected_host": core_result.get("selected_host", ""),
            "data_source_label": "trpc post.getInfinite",
            "dashboard_path": runtime.get("html_path", "dashboard.html"),
            "db_path": runtime.get("db_path", "civitai_tracker.db"),
            "posts_tracked": core_result.get("tracked_posts", 0),
            "changed_posts": core_result.get("changed_posts", 0),
            "known_totals": core_result.get("known_totals", 0),
            "unknown_totals": max(0, int(core_result.get("current_posts", 0) or 0) - int(core_result.get("known_totals", 0) or 0)),
            "captured_at": utc_now_iso(),
            **core_result,
        }

        engagement_enabled = bool(runtime.get("enable_buzz_ingest", True))
        buzz_summary = {
            "ok": False,
            "disabled": True,
            "reason": "API key required" if not runtime.get("api_key") else "disabled by config",
            "events_inserted": 0,
            "events_deduped": 0,
            "pages_fetched": 0,
        }

        if engagement_enabled and runtime.get("api_key"):
            try:
                buzz_summary = run_b2_1_ingest(runtime, runtime["db_path"])
            except Exception as exc:
                buzz_summary = {
                    "ok": False,
                    "error": str(exc),
                    "events_inserted": 0,
                    "events_deduped": 0,
                    "pages_fetched": 0,
                }

        service_result["collection_ingest"] = buzz_summary
        service_result["buzz_sync"] = buzz_summary  # legacy/internal compatibility

        if buzz_summary and buzz_summary.get("ok"):
            service_result["collection_events_new"] = buzz_summary.get("events_inserted", 0)
            service_result["collection_events_deduped"] = buzz_summary.get("events_deduped", 0)
            service_result["collection_pages_fetched"] = buzz_summary.get("pages_fetched", 0)
        else:
            service_result["collection_events_new"] = 0
            service_result["collection_events_deduped"] = 0
            service_result["collection_pages_fetched"] = 0

        service_result["collection_coverage_complete"] = bool(buzz_summary.get("coverage_complete")) if isinstance(buzz_summary, dict) else False
        service_result["collection_stop_reason"] = buzz_summary.get("stop_reason") if isinstance(buzz_summary, dict) else None
        service_result["collection_target_start"] = buzz_summary.get("target_start_time") if isinstance(buzz_summary, dict) else None
        service_result["collection_oldest_event_time"] = buzz_summary.get("oldest_event_time_seen") if isinstance(buzz_summary, dict) else None
        service_result["collection_latest_event_time"] = buzz_summary.get("latest_event_time_seen") if isinstance(buzz_summary, dict) else None
        service_result["collection_mode"] = buzz_summary.get("collection_mode") if isinstance(buzz_summary, dict) else None
        service_result["collection_bootstrap_completed"] = bool(buzz_summary.get("bootstrap_completed")) if isinstance(buzz_summary, dict) else False

        correlation_summary = None
        try:
            correlation_summary = run_b2_2_correlation(runtime["db_path"])
        except Exception as exc:
            correlation_summary = {
                "ok": False,
                "error": str(exc),
                "correlated_events_total": 0,
                "distinct_images_correlated": 0,
                "distinct_posts_correlated": 0,
            }

        service_result["engagement_correlation"] = correlation_summary

        if correlation_summary and correlation_summary.get("ok"):
            service_result["engagement_correlated_events"] = correlation_summary.get("correlated_events_total", 0)
            service_result["engagement_distinct_images"] = correlation_summary.get("distinct_images_correlated", 0)
            service_result["engagement_distinct_posts"] = correlation_summary.get("distinct_posts_correlated", 0)
        else:
            service_result["engagement_correlated_events"] = 0
            service_result["engagement_distinct_images"] = 0
            service_result["engagement_distinct_posts"] = 0

        try:
            conn = db_connect(runtime["db_path"])
            try:
                render_dashboard(
                    conn=conn,
                    html_path=runtime["html_path"],
                    tz_helper=TimezoneHelper(runtime["tz_name"]),
                    dashboard_name=runtime["dashboard_name"],
                    view_host=runtime["view_host"],
                    selected_host=core_result.get("selected_host", runtime.get("view_host", "https://civitai.red")),
                    min_post_id=runtime["min_post_id"],
                    start_date=runtime["start_date"],
                    runtime_status_path=runtime["runtime_status_path"],
                    db_path=runtime["db_path"],
                )
            finally:
                conn.close()
        except Exception as exc:
            service_result["dashboard_refresh_warning"] = str(exc)

        return service_result

    except requests.HTTPError as exc:
        return {
            "ok": False,
            "error": f"HTTP error: {exc}",
            "selected_host": runtime.get("api_mode", ""),
            "data_source_label": "trpc post.getInfinite",
            "dashboard_path": runtime.get("html_path", "dashboard.html"),
            "db_path": runtime.get("db_path", "civitai_tracker.db"),
            "posts_tracked": 0,
            "known_totals": 0,
            "unknown_totals": 0,
            "captured_at": utc_now_iso(),
        }
    except Exception as exc:
        return {
            "ok": False,
            "error": str(exc),
            "selected_host": runtime.get("api_mode", ""),
            "data_source_label": "trpc post.getInfinite",
            "dashboard_path": runtime.get("html_path", "dashboard.html"),
            "db_path": runtime.get("db_path", "civitai_tracker.db"),
            "posts_tracked": 0,
            "known_totals": 0,
            "unknown_totals": 0,
            "captured_at": utc_now_iso(),
        }


def main() -> int:
    args = parse_args()
    try:
        runtime = resolve_runtime_config(args)
        result = run_once(
            username=runtime["username"],
            dashboard_name=runtime["dashboard_name"],
            db_path=runtime["db_path"],
            csv_dir=runtime["csv_dir"],
            html_path=runtime["html_path"],
            tz_name=runtime["tz_name"],
            api_key=runtime["api_key"],
            api_mode=runtime["api_mode"],
            view_host=runtime["view_host"],
            nsfw_level=runtime["nsfw_level"],
            min_post_id=runtime["min_post_id"],
            start_date=runtime["start_date"],
            timeout=args.timeout,
            allow_rest_fallback=runtime["allow_rest_fallback"],
            runtime_status_path=runtime["runtime_status_path"],
        )
    except requests.HTTPError as exc:
        print(f"HTTP error: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    print(
        "Done. "
        f"host={result['selected_host']} "
        f"tracked_posts={result['tracked_posts']} "
        f"changed_posts={result['changed_posts']} "
        f"known_totals={result['known_totals']} "
        f"images={result['image_rows']} ({result['image_source']})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())