import argparse
import csv
import html
import json
import os
import re
import sqlite3
import sys
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple, Set
from urllib.parse import quote, urlencode

import requests

from app_info import APP_TITLE, APP_VERSION
from buzz_ingest import run_b2_1_ingest
from engagement_correlation import run_b2_2_correlation
from engagement_dashboard import COLLECTION_SECTION_CSS, render_collection_dashboard_section, render_collection_tables_html

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
CIVITAI_IMAGE_CACHE_ROOT = "https://imagecache.civitai.com/xG1nkqKTMzGDvpLrqFT7WA"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def utc_now_iso() -> str:
    return utc_now().replace(microsecond=0).isoformat()


def ensure_dir(path: str) -> None:
    os.makedirs(path, exist_ok=True)


def resolve_runtime_path(path_value: Any, base_dir: Path) -> str:
    path = Path(str(path_value or "")).expanduser()
    if not path.is_absolute():
        path = base_dir / path
    return str(path.resolve())


def write_dashboard_html(html_path: str, content: str) -> None:
    path = Path(html_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f"{path.name}.tmp")
    temp_path.write_text(content, encoding="utf-8")
    temp_path.replace(path)


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
            image_url TEXT,
            thumbnail_url TEXT,
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
    ensure_column(conn, "post_images", "image_url", "TEXT")
    ensure_column(conn, "post_images", "thumbnail_url", "TEXT")

    conn.commit()


def build_headers(api_key: Optional[str]) -> Dict[str, str]:
    headers = {
        "User-Agent": f"civitai-post-tracker-v{APP_VERSION}-core/1.0",
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


def safe_url(value: Any) -> Optional[str]:
    if not isinstance(value, str):
        return None
    value = value.strip()
    if value.startswith(("https://", "http://")):
        return value
    return None


def first_url(*values: Any) -> Optional[str]:
    for value in values:
        url = safe_url(value)
        if url:
            return url
    return None


def uuid_token(value: Any) -> Optional[str]:
    if not isinstance(value, str):
        return None
    value = value.strip()
    if re.fullmatch(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}", value):
        return value
    return None


def civitai_cache_url(image_uuid: str, image_id: int, width: int) -> str:
    return f"{CIVITAI_IMAGE_CACHE_ROOT}/{image_uuid}/width={int(width)}/{int(image_id)}.jpeg"


def url_candidates(value: Any, path: str = "") -> List[Tuple[str, str]]:
    if isinstance(value, dict):
        found: List[Tuple[str, str]] = []
        for key, nested_value in value.items():
            next_path = f"{path}.{key}" if path else str(key)
            found.extend(url_candidates(nested_value, next_path))
        return found
    if isinstance(value, list):
        found = []
        for idx, nested_value in enumerate(value):
            found.extend(url_candidates(nested_value, f"{path}[{idx}]"))
        return found
    url = safe_url(value)
    return [(path, url)] if url else []


def best_image_url(candidates: List[Tuple[str, str]], prefer_thumbnail: bool = False) -> Optional[str]:
    media_candidates = [
        candidate
        for candidate in candidates
        if any(token in candidate[1].lower() for token in ("imagecache.civitai", "image.civitai", "/file/civitai"))
    ]
    if not media_candidates:
        return None

    def score(candidate: Tuple[str, str]) -> Tuple[int, int]:
        path, url = candidate
        haystack = f"{path} {url}".lower()
        value = 0
        if "image.civitai" in haystack:
            value += 50
        if any(ext in haystack for ext in (".jpg", ".jpeg", ".png", ".webp", ".gif")):
            value += 30
        if any(token in haystack for token in ("image", "url", "src")):
            value += 5
        if prefer_thumbnail:
            if any(token in haystack for token in ("thumb", "thumbnail", "preview", "small", "medium", "width=")):
                value += 40
            if any(token in haystack for token in ("original", "full", "download")):
                value -= 10
        else:
            if any(token in haystack for token in ("original", "full", "download", "large")):
                value += 20
        return value, -len(url)

    return sorted(media_candidates, key=score, reverse=True)[0][1]


def extract_image_urls(item: dict) -> Tuple[Optional[str], Optional[str]]:
    urls = item.get("urls") if isinstance(item.get("urls"), dict) else {}
    meta = item.get("meta") if isinstance(item.get("meta"), dict) else {}
    image = item.get("image") if isinstance(item.get("image"), dict) else {}
    candidates = url_candidates(item)
    image_id = safe_int(item.get("id"))
    image_uuid = uuid_token(item.get("url"))
    cached_full_url = civitai_cache_url(image_uuid, image_id, 1024) if image_uuid and image_id is not None else None
    cached_thumbnail_url = civitai_cache_url(image_uuid, image_id, 450) if image_uuid and image_id is not None else None

    image_url = first_url(
        item.get("url"),
        item.get("imageUrl"),
        item.get("downloadUrl"),
        urls.get("original"),
        urls.get("full"),
        urls.get("large"),
        image.get("url"),
        meta.get("url"),
        cached_full_url,
    ) or best_image_url(candidates, prefer_thumbnail=False)
    thumbnail_url = first_url(
        item.get("thumbnailUrl"),
        item.get("thumbUrl"),
        item.get("previewUrl"),
        item.get("smallUrl"),
        urls.get("thumbnail"),
        urls.get("thumb"),
        urls.get("small"),
        urls.get("preview"),
        urls.get("medium"),
        image.get("thumbnailUrl"),
        image.get("url"),
        meta.get("thumbnailUrl"),
        cached_thumbnail_url,
        image_url,
    ) or best_image_url(candidates, prefer_thumbnail=True) or image_url
    return image_url, thumbnail_url


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
    image_url, thumbnail_url = extract_image_urls(item)
    return {
        "image_id": image_id,
        "post_id": post_id,
        "image_created_at": item.get("createdAt"),
        "nsfw": item.get("nsfw"),
        "nsfw_level": item.get("nsfwLevel"),
        "image_url": image_url,
        "thumbnail_url": thumbnail_url,
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
                    post_id, image_id, position, image_created_at, nsfw, nsfw_level,
                    image_url, thumbnail_url, source_host, captured_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    post_id,
                    row["image_id"],
                    pos,
                    row.get("image_created_at"),
                    row.get("nsfw"),
                    row.get("nsfw_level"),
                    row.get("image_url"),
                    row.get("thumbnail_url"),
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


def get_post_image_details_map(conn: sqlite3.Connection) -> Dict[int, List[Dict[str, Any]]]:
    cur = conn.cursor()
    cur.execute(
        """
        SELECT post_id, image_id, position, image_url, thumbnail_url
        FROM post_images
        ORDER BY post_id ASC, position ASC, image_id ASC
        """
    )
    result: Dict[int, List[Dict[str, Any]]] = defaultdict(list)
    for row in cur.fetchall():
        image_url = row["image_url"] if "image_url" in row.keys() else None
        thumbnail_url = row["thumbnail_url"] if "thumbnail_url" in row.keys() else None
        result[int(row["post_id"])].append(
            {
                "image_id": int(row["image_id"]),
                "position": safe_int(row["position"]),
                "image_url": image_url,
                "thumbnail_url": thumbnail_url,
            }
        )
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
    if score >= 12:
        return "high"
    if score >= 6:
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


def summarize_collection_periods(
    conn: sqlite3.Connection,
    tz_helper: TimezoneHelper,
    current_posts: List[sqlite3.Row],
) -> Dict[str, Any]:
    current_by_post = {int(r["post_id"]): r for r in current_posts}
    now_local = utc_now().astimezone(tz_helper.tz)
    today_date = now_local.date()
    week_cutoff = now_local - timedelta(days=7)

    today_total = 0
    week_total = 0
    by_post_today: Dict[int, Dict[str, Any]] = defaultdict(lambda: {"count": 0, "images": set(), "title": None})
    by_post_week: Dict[int, Dict[str, Any]] = defaultdict(lambda: {"count": 0, "images": set(), "title": None})

    try:
        rows = conn.execute(
            """
            SELECT event_time, related_post_id, COALESCE(related_image_id, target_id) AS image_id
            FROM content_engagement_events
            WHERE normalized_type = 'collection_like'
            ORDER BY event_time DESC
            """
        ).fetchall()
    except sqlite3.OperationalError:
        rows = []

    for row in rows:
        event_dt = tz_helper.parse_iso(row["event_time"])
        if event_dt is None:
            continue
        local_dt = event_dt.astimezone(tz_helper.tz)
        post_id = row["related_post_id"]
        image_id = row["image_id"]

        if local_dt.date() == today_date:
            today_total += 1
            if post_id is not None:
                post_key = int(post_id)
                by_post_today[post_key]["count"] += 1
                by_post_today[post_key]["title"] = current_by_post.get(post_key)["title"] if post_key in current_by_post else None
                if image_id is not None:
                    by_post_today[post_key]["images"].add(int(image_id))

        if local_dt >= week_cutoff:
            week_total += 1
            if post_id is not None:
                post_key = int(post_id)
                by_post_week[post_key]["count"] += 1
                by_post_week[post_key]["title"] = current_by_post.get(post_key)["title"] if post_key in current_by_post else None
                if image_id is not None:
                    by_post_week[post_key]["images"].add(int(image_id))

    def finalize_best(bucket: Dict[int, Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        best = None
        for post_id, data in bucket.items():
            total = int(data.get("count") or 0)
            if total <= 0:
                continue
            candidate = {
                "post_id": post_id,
                "title": data.get("title") or (current_by_post.get(post_id)["title"] if post_id in current_by_post else None),
                "total": total,
                "distinct_images": len(data.get("images") or []),
            }
            if best is None or (candidate["total"], candidate["distinct_images"], candidate["post_id"]) > (
                best["total"], best["distinct_images"], best["post_id"]
            ):
                best = candidate
        return best

    return {
        "today_total": today_total,
        "week_total": week_total,
        "best_today": finalize_best(by_post_today),
        "best_week": finalize_best(by_post_week),
        "today_label": str(today_date),
        "week_label": f"Last 7 days ending {now_local.strftime('%Y-%m-%d %H:%M %Z')}",
    }


def reaction_delta_value(row: sqlite3.Row) -> int:
    total = safe_int(row["reaction_total_delta"])
    if total is not None:
        return total
    return sum(safe_int(row[key]) or 0 for key in ("like_delta", "heart_delta", "laugh_delta", "cry_delta"))


def build_post_performance_rows(
    conn: sqlite3.Connection,
    current_posts: List[sqlite3.Row],
    snapshots_by_post: Dict[int, List[sqlite3.Row]],
    deltas: List[sqlite3.Row],
    tz_helper: TimezoneHelper,
) -> List[Dict[str, Any]]:
    now_local = utc_now().astimezone(tz_helper.tz)
    today_date = now_local.date()
    week_cutoff = now_local - timedelta(days=7)
    month_cutoff = now_local - timedelta(days=30)
    year_cutoff = now_local - timedelta(days=365)

    reaction_today: Dict[int, int] = defaultdict(int)
    reaction_week: Dict[int, int] = defaultdict(int)
    reaction_month: Dict[int, int] = defaultdict(int)
    reaction_year: Dict[int, int] = defaultdict(int)
    comments_today: Dict[int, int] = defaultdict(int)
    comments_week: Dict[int, int] = defaultdict(int)
    comments_month: Dict[int, int] = defaultdict(int)
    comments_year: Dict[int, int] = defaultdict(int)

    for row in deltas:
        detected_dt = tz_helper.parse_iso(row["detected_at"])
        if detected_dt is None:
            continue
        local_dt = detected_dt.astimezone(tz_helper.tz)
        post_id = int(row["post_id"])
        reaction_delta = reaction_delta_value(row)
        comment_delta = safe_int(row["comment_delta"]) or 0

        if local_dt.date() == today_date:
            reaction_today[post_id] += reaction_delta
            comments_today[post_id] += comment_delta

        if local_dt >= week_cutoff:
            reaction_week[post_id] += reaction_delta
            comments_week[post_id] += comment_delta

        if local_dt >= month_cutoff:
            reaction_month[post_id] += reaction_delta
            comments_month[post_id] += comment_delta

        if local_dt >= year_cutoff:
            reaction_year[post_id] += reaction_delta
            comments_year[post_id] += comment_delta

    collections_today: Dict[int, int] = defaultdict(int)
    collections_week: Dict[int, int] = defaultdict(int)
    collections_month: Dict[int, int] = defaultdict(int)
    collections_year: Dict[int, int] = defaultdict(int)
    try:
        collection_rows = conn.execute(
            """
            SELECT event_time, related_post_id
            FROM content_engagement_events
            WHERE normalized_type = 'collection_like'
              AND related_post_id IS NOT NULL
            """
        ).fetchall()
    except sqlite3.OperationalError:
        collection_rows = []

    for row in collection_rows:
        event_dt = tz_helper.parse_iso(row["event_time"])
        if event_dt is None:
            continue
        local_dt = event_dt.astimezone(tz_helper.tz)
        post_id = int(row["related_post_id"])

        if local_dt.date() == today_date:
            collections_today[post_id] += 1

        if local_dt >= week_cutoff:
            collections_week[post_id] += 1

        if local_dt >= month_cutoff:
            collections_month[post_id] += 1

        if local_dt >= year_cutoff:
            collections_year[post_id] += 1

    image_details_map = get_post_image_details_map(conn)
    rows: List[Dict[str, Any]] = []
    for row in current_posts:
        post_id = int(row["post_id"])
        images = image_details_map.get(post_id, [])
        primary_image = images[0] if images else {}
        reaction_total = safe_int(row["reaction_total"])
        comment_count = safe_int(row["comment_count"])
        published_dt = tz_helper.parse_iso(row["published_at"])
        published_sort = published_dt.timestamp() if published_dt is not None else 0.0
        reactions_per_day: Optional[float] = None
        if reaction_total is not None and published_dt is not None:
            age_days = (now_local - published_dt.astimezone(tz_helper.tz)).total_seconds() / 86400
            if age_days > 0:
                reactions_per_day = reaction_total / max(age_days, 1 / 24)

        first2_reactions = None
        first24_reactions = None
        if reaction_total is not None:
            first2_reactions = estimate_window_metric(snapshots_by_post, tz_helper, post_id, row["published_at"], "reaction_total", 2)
            first24_reactions = estimate_window_metric(snapshots_by_post, tz_helper, post_id, row["published_at"], "reaction_total", 24)

        rows.append(
            {
                "post_id": post_id,
                "title": row["title"],
                "published_at": row["published_at"],
                "captured_at": row["captured_at"],
                "reaction_total": reaction_total,
                "comment_count": comment_count,
                "reactions_per_day": reactions_per_day,
                "reaction_today": int(reaction_today.get(post_id, 0)),
                "reaction_week": int(reaction_week.get(post_id, 0)),
                "reaction_month": int(reaction_month.get(post_id, 0)),
                "reaction_year": int(reaction_year.get(post_id, 0)),
                "comments_today": int(comments_today.get(post_id, 0)),
                "comments_week": int(comments_week.get(post_id, 0)),
                "comments_month": int(comments_month.get(post_id, 0)),
                "comments_year": int(comments_year.get(post_id, 0)),
                "first2_reactions": first2_reactions,
                "first24_reactions": first24_reactions,
                "collections_today": int(collections_today.get(post_id, 0)),
                "collections_week": int(collections_week.get(post_id, 0)),
                "collections_month": int(collections_month.get(post_id, 0)),
                "collections_year": int(collections_year.get(post_id, 0)),
                "image_count": len(images),
                "images": images,
                "primary_image_id": primary_image.get("image_id"),
                "primary_image_url": primary_image.get("image_url"),
                "primary_thumbnail_url": primary_image.get("thumbnail_url"),
                "published_sort": published_sort,
            }
        )

    rows.sort(
        key=lambda item: (
            int(item.get("reaction_week") or 0),
            int(item.get("collections_week") or 0),
            int(item.get("reaction_today") or 0),
            int(item.get("reaction_total") if item.get("reaction_total") is not None else -1),
            float(item.get("published_sort") or 0),
            int(item.get("post_id") or 0),
        ),
        reverse=True,
    )
    return rows


def recommendation_score(row: Dict[str, Any]) -> Optional[Tuple[float, str, str]]:
    if row.get("avg_24h_reactions") is not None:
        return float(row["avg_24h_reactions"]), "Avg 24h reactions", "first-day performance"
    if row.get("avg_total_reactions") is not None:
        return float(row["avg_total_reactions"]), "Avg total reactions", "lifetime totals"
    if row.get("avg_total_engagement") is not None:
        return float(row["avg_total_engagement"]), "Avg total engagement", "lifetime engagement"
    return None


def select_recommendations(rows: List[dict], label_key: str, min_posts: int = 3, limit: int = 3) -> List[dict]:
    candidates: List[dict] = []
    for row in rows:
        score = recommendation_score(row)
        if int(row.get("posts") or 0) < min_posts or score is None:
            continue
        value, metric_label, basis = score
        candidate = dict(row)
        candidate["label"] = row.get(label_key)
        candidate["recommendation_score"] = value
        candidate["recommendation_metric"] = metric_label
        candidate["recommendation_basis"] = basis
        candidates.append(candidate)
    candidates.sort(
        key=lambda r: (
            float(r.get("recommendation_score") or 0),
            int(r.get("posts") or 0),
            str(r.get("label") or ""),
        ),
        reverse=True,
    )
    return candidates[:limit]


def select_suggested_windows(hour_summary: List[dict]) -> List[dict]:
    return select_recommendations(hour_summary, "hour", min_posts=3, limit=3)


def select_suggested_weekdays(weekday_summary: List[dict]) -> List[dict]:
    return select_recommendations(weekday_summary, "weekday", min_posts=3, limit=3)


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
        SELECT post_id, image_id, position, image_created_at, nsfw, nsfw_level,
               image_url, thumbnail_url, source_host, captured_at
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
    collection_period_summary = summarize_collection_periods(conn, tz_helper, current_posts)
    suggested_windows = select_suggested_windows(hour_summary)
    suggested_weekdays = select_suggested_weekdays(weekday_summary)

    tracked_posts = len(current_posts)
    known_totals = sum(1 for r in current_posts if r["stats_known"])
    unknown_totals = tracked_posts - known_totals
    latest_capture = current_posts[0]["captured_at"] if current_posts else None
    runtime_status = load_runtime_status(runtime_status_path)
    collections_html = (
        render_collection_dashboard_section(db_path, view_host=view_host, time_formatter=tz_helper.fmt_dt, include_tables=False)
        if db_path
        else ""
    )
    collection_tables_html = render_collection_tables_html(db_path, view_host=view_host, time_formatter=tz_helper.fmt_dt) if db_path else ""

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
    post_performance_rows = build_post_performance_rows(conn, current_posts, snapshots_by_post, deltas, tz_helper)

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

    def best_reaction_card(title: str, payload: Optional[Dict[str, Any]], subtitle: str, empty_text: str) -> str:
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

    def best_collection_card(title: str, payload: Optional[Dict[str, Any]], subtitle: str, empty_text: str) -> str:
        if not payload:
            return (
                "<div class='feature-card'>"
                f"<div class='feature-title'>{html.escape(title)}</div>"
                f"<div class='feature-sub'>{html.escape(subtitle)}</div>"
                "<div class='empty-state'>—</div>"
                f"<div class='feature-note'>{html.escape(empty_text)}</div>"
                "</div>"
            )
        heading = post_link(view_host, int(payload["post_id"]))
        post_title = html.escape(payload.get("title") or "Untitled post")
        image_count = int(payload.get("distinct_images") or 0)
        return (
            "<div class='feature-card'>"
            f"<div class='feature-title'>{html.escape(title)}</div>"
            f"<div class='feature-sub'>{html.escape(subtitle)}</div>"
            f"<div class='feature-score'>{int(payload['total'])}</div>"
            f"<div class='feature-post'>{heading}</div>"
            f"<div class='feature-note'>{post_title}</div>"
            f"<div class='feature-note'>{image_count} affected image{'s' if image_count != 1 else ''}</div>"
            "</div>"
        )

    def render_recommendation_card(title: str, row: Optional[dict], empty_text: str) -> str:
        if not row:
            return (
                "<div class='feature-card'>"
                f"<div class='feature-title'>{html.escape(title)}</div>"
                "<div class='empty-state'>—</div>"
                f"<div class='feature-note'>{html.escape(empty_text)}</div>"
                "</div>"
            )
        return (
            "<div class='feature-card'>"
            f"<div class='feature-title'>{html.escape(title)}</div>"
            f"<div class='feature-score'>{html.escape(str(row.get('label') or '—'))}</div>"
            f"<div class='feature-note'>{fmt_num(row.get('recommendation_score'))} {html.escape(str(row.get('recommendation_metric') or '').lower())}</div>"
            f"<div class='feature-note'>{int(row.get('posts') or 0)} tracked posts · basis: {html.escape(str(row.get('recommendation_basis') or 'available data'))}</div>"
            f"<div class='feature-note'>Confidence: {chip(row.get('confidence') or 'low')}</div>"
            "</div>"
        )

    def render_recommendation_table(title: str, rows: List[dict], label_header: str) -> str:
        if not rows:
            return f"<div class='feature-note'>Not enough post history yet for {html.escape(title.lower())}.</div>"
        body = []
        for idx, row in enumerate(rows, start=1):
            body.append(
                "<tr>"
                f"<td class='num'>{idx}</td>"
                f"<td>{html.escape(str(row.get('label') or '—'))}</td>"
                f"<td class='num'>{int(row.get('posts') or 0)}</td>"
                f"<td class='num'>{fmt_num(row.get('recommendation_score'))}</td>"
                f"<td>{html.escape(str(row.get('recommendation_metric') or 'n/a'))}</td>"
                f"<td>{html.escape(str(row.get('recommendation_basis') or 'available data'))}</td>"
                f"<td>{chip(row.get('confidence') or 'low')}</td>"
                "</tr>"
            )
        return (
            "<table class='clean-table'>"
            f"<thead><tr><th>#</th><th>{html.escape(label_header)}</th><th class='num'>Posts</th><th class='num'>Score</th><th>Metric</th><th>Basis</th><th>Confidence</th></tr></thead>"
            f"<tbody>{''.join(body)}</tbody></table>"
        )

    def render_posting_recommendations(hour_rows: List[dict], weekday_rows: List[dict]) -> str:
        basis_values = {str(row.get("recommendation_basis") or "") for row in [*hour_rows, *weekday_rows] if row}
        if "first-day performance" in basis_values:
            basis_title = "First-day data"
            basis_detail = "Recommendations prefer captured 24h performance where available."
        elif basis_values:
            basis_title = "Lifetime totals"
            basis_detail = "Early 2h/24h windows are not available yet, so this uses current totals."
        else:
            basis_title = "Not enough data"
            basis_detail = "Keep collecting snapshots before treating timing advice seriously."

        parts_rec: List[str] = []
        parts_rec.append("<div class='section-title'>Posting recommendations</div>")
        parts_rec.append("<div class='feature-grid'>")
        parts_rec.append(render_recommendation_card("Best posting hour", hour_rows[0] if hour_rows else None, "No reliable hour candidate yet."))
        parts_rec.append(render_recommendation_card("Best weekday", weekday_rows[0] if weekday_rows else None, "No reliable weekday candidate yet."))
        parts_rec.append(
            "<div class='feature-card'>"
            "<div class='feature-title'>Recommendation basis</div>"
            f"<div class='feature-score'>{html.escape(basis_title)}</div>"
            f"<div class='feature-note'>{html.escape(basis_detail)}</div>"
            "<div class='feature-note'>Content strength still matters more than timing alone.</div>"
            "</div>"
        )
        parts_rec.append("</div>")
        return "".join(parts_rec)

    def short_text(value: Any, limit: int = 42) -> str:
        text = str(value or "Untitled post").strip() or "Untitled post"
        return text if len(text) <= limit else text[: max(0, limit - 1)].rstrip() + "..."

    def build_daily_activity(days: int = 14) -> List[Dict[str, int | str]]:
        today_local = utc_now().astimezone(tz_helper.tz).date()
        dates = [today_local - timedelta(days=days - 1 - idx) for idx in range(days)]
        by_date: Dict[Any, Dict[str, int]] = {date: {"reactions": 0, "collections": 0} for date in dates}

        for row in deltas:
            detected_dt = tz_helper.parse_iso(row["detected_at"])
            if detected_dt is None:
                continue
            local_date = detected_dt.astimezone(tz_helper.tz).date()
            if local_date in by_date:
                by_date[local_date]["reactions"] += max(0, reaction_delta_value(row))

        try:
            collection_rows = conn.execute(
                """
                SELECT event_time
                FROM content_engagement_events
                WHERE normalized_type = 'collection_like'
                """
            ).fetchall()
        except sqlite3.OperationalError:
            collection_rows = []

        for row in collection_rows:
            event_dt = tz_helper.parse_iso(row["event_time"])
            if event_dt is None:
                continue
            local_date = event_dt.astimezone(tz_helper.tz).date()
            if local_date in by_date:
                by_date[local_date]["collections"] += 1

        return [
            {
                "date": str(date),
                "label": date.strftime("%m-%d"),
                "reactions": by_date[date]["reactions"],
                "collections": by_date[date]["collections"],
            }
            for date in dates
        ]

    def render_daily_activity_chart(rows: List[Dict[str, int | str]]) -> str:
        width = 720
        height = 230
        plot_left = 36
        plot_top = 18
        plot_width = 660
        plot_height = 150
        base_y = plot_top + plot_height
        max_value = max([1] + [int(row["reactions"]) for row in rows] + [int(row["collections"]) for row in rows])
        step = plot_width / max(1, len(rows))
        bar_width = max(8.0, min(18.0, step * 0.28))

        grid = []
        for idx in range(4):
            y = base_y - (plot_height * idx / 3)
            value = round(max_value * idx / 3)
            grid.append(
                f"<line x1='{plot_left}' y1='{y:.1f}' x2='{plot_left + plot_width}' y2='{y:.1f}' class='chart-grid'></line>"
                f"<text x='8' y='{y + 4:.1f}' class='chart-axis'>{value}</text>"
            )

        bars = []
        labels = []
        for idx, row in enumerate(rows):
            center_x = plot_left + (idx * step) + (step / 2)
            reactions = int(row["reactions"])
            collections = int(row["collections"])
            reaction_height = (reactions / max_value) * plot_height
            collection_height = (collections / max_value) * plot_height
            bars.append(
                f"<rect x='{center_x - bar_width - 2:.1f}' y='{base_y - reaction_height:.1f}' width='{bar_width:.1f}' height='{reaction_height:.1f}' rx='3' class='chart-bar reaction'><title>{html.escape(str(row['date']))}: {reactions} reactions</title></rect>"
                f"<rect x='{center_x + 2:.1f}' y='{base_y - collection_height:.1f}' width='{bar_width:.1f}' height='{collection_height:.1f}' rx='3' class='chart-bar collection'><title>{html.escape(str(row['date']))}: {collections} collection adds</title></rect>"
            )
            if idx % 2 == 0 or idx == len(rows) - 1:
                labels.append(f"<text x='{center_x:.1f}' y='{height - 18}' text-anchor='middle' class='chart-axis'>{html.escape(str(row['label']))}</text>")

        return (
            "<svg class='chart-svg' viewBox='0 0 720 230' role='img' aria-label='Daily reactions and collection adds'>"
            f"{''.join(grid)}{''.join(bars)}"
            f"<line x1='{plot_left}' y1='{base_y}' x2='{plot_left + plot_width}' y2='{base_y}' class='chart-axis-line'></line>"
            f"{''.join(labels)}"
            "</svg>"
            "<div class='chart-legend'><span><i class='legend-dot reaction'></i>Reactions</span><span><i class='legend-dot collection'></i>Collections</span></div>"
        )

    def render_reaction_mix_chart(totals: Dict[str, int]) -> str:
        items = [
            ("Likes", int(totals.get("like") or 0), "#7fb3ff"),
            ("Hearts", int(totals.get("heart") or 0), "#ff7aa2"),
            ("Laughs", int(totals.get("laugh") or 0), "#f2cc60"),
            ("Cries", int(totals.get("cry") or 0), "#9baacf"),
        ]
        total = sum(value for _, value, _ in items)
        if total <= 0:
            return "<div class='chart-empty'>No reaction gains captured today.</div>"
        rows_html = []
        for label, value, color in items:
            pct = (value / total) * 100 if total else 0
            rows_html.append(
                "<div class='mix-row'>"
                f"<div class='mix-label'>{html.escape(label)}</div>"
                "<div class='mix-track'>"
                f"<span class='mix-fill' style='width:{pct:.1f}%;background:{color}'></span>"
                "</div>"
                f"<div class='mix-value'>{value}</div>"
                "</div>"
            )
        return "".join(rows_html)

    def render_top_movement_chart(rows: List[Dict[str, Any]]) -> str:
        candidates = []
        for row in rows:
            reactions = int(row.get("reaction_week") or 0)
            collections = int(row.get("collections_week") or 0)
            score = reactions + collections
            if score > 0:
                candidates.append((score, reactions, collections, row))

        label = "7-day movement"
        if not candidates:
            for row in rows[:6]:
                total = safe_int(row.get("reaction_total")) or 0
                if total > 0:
                    candidates.append((total, total, 0, row))
            label = "Lifetime reactions"

        if not candidates:
            return "<div class='chart-empty'>No post movement to chart yet.</div>"

        candidates = sorted(candidates, key=lambda item: (item[0], item[1], int(item[3].get("post_id") or 0)), reverse=True)[:6]
        max_score = max(1, max(item[0] for item in candidates))
        rows_html = []
        for score, reactions, collections, row in candidates:
            pct = max(4, (score / max_score) * 100)
            detail = f"+{reactions} reactions · +{collections} collections" if label == "7-day movement" else f"{score} reactions"
            rows_html.append(
                "<div class='top-chart-row'>"
                f"<div class='top-chart-label'>{post_link(view_host, int(row['post_id']))}<span>{html.escape(short_text(row.get('title'), 36))}</span></div>"
                "<div class='top-chart-track'>"
                f"<span class='top-chart-fill' style='width:{pct:.1f}%'></span>"
                "</div>"
                f"<div class='top-chart-value'>{html.escape(detail)}</div>"
                "</div>"
            )
        return f"<div class='chart-mode'>{html.escape(label)}</div>{''.join(rows_html)}"

    def render_visual_overview() -> str:
        daily_rows = build_daily_activity()
        return (
            "<div class='section-title'>Visual overview</div>"
            "<div class='visual-grid'>"
            "<section class='visual-card visual-wide'>"
            "<h2>Daily activity</h2>"
            "<p class='hint'>Reaction gains and collection adds over the last 14 local days.</p>"
            f"{render_daily_activity_chart(daily_rows)}"
            "</section>"
            "<section class='visual-card'>"
            "<h2>Reaction mix today</h2>"
            "<p class='hint'>How today's reaction gain is distributed by type.</p>"
            f"{render_reaction_mix_chart(today)}"
            "</section>"
            "<section class='visual-card'>"
            "<h2>Top 7-day movement</h2>"
            "<p class='hint'>Posts currently carrying the most visible momentum.</p>"
            f"{render_top_movement_chart(post_performance_rows)}"
            "</section>"
            "</div>"
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

    def render_window_table_content(rows: List[Tuple[sqlite3.Row, int]], window_label: str) -> str:
        if not rows:
            return "<div class='feature-note'>Not enough early snapshots yet.</div>"
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
            "<table class='clean-table'>"
            "<thead><tr><th>#</th><th>Post</th><th class='num'>Score</th><th>Details</th><th>Published</th></tr></thead>"
            f"<tbody>{''.join(body)}</tbody></table>"
        )

    def render_summary_table_content(rows: List[List[str]], headers: List[str]) -> str:
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
        return f"<table class='clean-table'><thead><tr>{head}</tr></thead><tbody>{''.join(body)}</tbody></table>"

    def sortable_cell(content: str, sort_value: Any = None, cls: str = "") -> str:
        class_attr = f" class='{html.escape(cls, quote=True)}'" if cls else ""
        sort_attr = "" if sort_value is None else f" data-sort-value='{html.escape(str(sort_value), quote=True)}'"
        return f"<td{class_attr}{sort_attr}>{content}</td>"

    def fmt_signed(value: int) -> str:
        return f"+{int(value)}" if int(value) > 0 else str(int(value))

    def render_delta(value: int) -> str:
        cls = "delta-pos" if int(value) > 0 else ("delta-neg" if int(value) < 0 else "")
        label = html.escape(fmt_signed(int(value)))
        return f"<span class='{cls}'>{label}</span>" if cls else label

    def period_attrs(flags: Dict[str, bool]) -> str:
        attrs = []
        for key in ("day", "week", "month", "year", "all"):
            if flags.get(key):
                attrs.append(f"data-period-{key}='1'")
        return " " + " ".join(attrs) if attrs else ""

    def image_page_link(image_id: Any, label: Optional[str] = None) -> str:
        if image_id in (None, ""):
            return "—"
        image_id_int = int(image_id)
        text = label or f"image #{image_id_int}"
        url = image_page_url(image_id_int)
        return f'<a href="{html.escape(url, quote=True)}" target="_blank" rel="noopener">{html.escape(text)}</a>'

    def image_page_url(image_id: Any) -> str:
        return f"{view_host.rstrip('/')}/images/{int(image_id)}"

    def preview_html(src: Any, alt: str, cls: str = "post-thumb", fallback_href: Optional[str] = None) -> str:
        url = safe_url(src)
        if not url:
            missing = f"<div class='{html.escape(cls, quote=True)} thumb-missing'>{'Open image' if fallback_href else 'No preview'}</div>"
            if fallback_href:
                return f"<a class='preview-link' href='{html.escape(fallback_href, quote=True)}' target='_blank' rel='noopener'>{missing}</a>"
            return missing
        return (
            f"<img class='{html.escape(cls, quote=True)}' src='{html.escape(url, quote=True)}' "
            f"alt='{html.escape(alt, quote=True)}' loading='lazy' referrerpolicy='no-referrer'>"
            f"<div class='{html.escape(cls, quote=True)} thumb-missing' hidden style='display:none'>No preview</div>"
        )

    def stat_tile(label: str, value: str, detail: str = "") -> str:
        detail_html = f"<div class='drawer-stat-detail'>{html.escape(detail)}</div>" if detail else ""
        return (
            "<div class='drawer-stat'>"
            f"<div class='drawer-stat-label'>{html.escape(label)}</div>"
            f"<div class='drawer-stat-value'>{value}</div>"
            f"{detail_html}"
            "</div>"
        )

    def render_post_detail_template(row: Dict[str, Any]) -> str:
        post_id = int(row["post_id"])
        title = str(row.get("title") or "Untitled post")
        images = row.get("images") or []
        primary_src = row.get("primary_thumbnail_url") or row.get("primary_image_url")
        primary_image_id = row.get("primary_image_id")
        primary_href = image_page_url(primary_image_id) if primary_image_id else None
        image_links = []
        for image in images[:8]:
            image_links.append(image_page_link(image.get("image_id")))
        if len(images) > 8:
            image_links.append(f"<span class='drawer-muted'>+{len(images) - 8} more</span>")
        if not image_links:
            image_links.append("<span class='drawer-muted'>No image IDs stored yet</span>")

        today_comments = int(row.get("comments_today") or 0)
        week_comments = int(row.get("comments_week") or 0)
        collections_today = int(row.get("collections_today") or 0)
        collections_week = int(row.get("collections_week") or 0)
        image_count = int(row.get("image_count") or 0)
        reaction_total = row.get("reaction_total")
        comment_count = row.get("comment_count")

        stats = "".join(
            [
                stat_tile("Current", fmt_int(reaction_total), f"{fmt_int(comment_count)} comments"),
                stat_tile("Avg/day", fmt_num(row.get("reactions_per_day"))),
                stat_tile("Today", render_delta(int(row.get("reaction_today") or 0)), f"comments {fmt_signed(today_comments)}"),
                stat_tile("7 days", render_delta(int(row.get("reaction_week") or 0)), f"comments {fmt_signed(week_comments)}"),
                stat_tile("Collections", str(collections_week), f"today {collections_today}"),
                stat_tile("First 2h", fmt_int(row.get("first2_reactions"))),
                stat_tile("First 24h", fmt_int(row.get("first24_reactions"))),
                stat_tile("Images", str(image_count)),
            ]
        )

        return (
            f"<template data-post-detail-template='{post_id}'>"
            "<div class='drawer-hero'>"
            f"{preview_html(primary_src, title, 'drawer-preview', primary_href)}"
            "<div>"
            f"<h2 id='post-drawer-title'>{html.escape(title)}</h2>"
            f"<div class='drawer-links'>{post_link(view_host, post_id)}"
            f"{' · ' + image_page_link(primary_image_id, 'primary image') if primary_image_id else ''}</div>"
            f"<div class='drawer-muted'>Published {html.escape(tz_helper.fmt_dt(row.get('published_at')))} · Last seen {html.escape(tz_helper.fmt_dt(row.get('captured_at')))}</div>"
            "</div>"
            "</div>"
            f"<div class='drawer-stats'>{stats}</div>"
            "<div class='drawer-section'>"
            "<h3>Images</h3>"
            f"<div class='drawer-image-links'>{' '.join(image_links)}</div>"
            "</div>"
            "</template>"
        )

    def render_post_detail_drawer(rows: List[Dict[str, Any]]) -> str:
        templates = "".join(render_post_detail_template(row) for row in rows)
        return (
            "<div class='post-drawer-backdrop' data-post-drawer hidden>"
            "<aside class='post-drawer' role='dialog' aria-modal='true' aria-labelledby='post-drawer-title'>"
            "<button type='button' class='drawer-close' data-post-drawer-close aria-label='Close'>&times;</button>"
            "<div class='drawer-content' data-post-drawer-content>"
            "<h2 id='post-drawer-title'>Post details</h2>"
            "</div>"
            "</aside>"
            "</div>"
            f"<div hidden>{templates}</div>"
        )

    def render_post_performance_table(rows: List[Dict[str, Any]]) -> str:
        if not rows:
            return "<div class='feature-note'>No tracked posts yet.</div>"
        body = []
        for row in rows:
            reaction_total = row.get("reaction_total")
            comment_count = row.get("comment_count")
            reactions_per_day = row.get("reactions_per_day")
            first2 = row.get("first2_reactions")
            first24 = row.get("first24_reactions")
            today_reactions = int(row.get("reaction_today") or 0)
            week_reactions = int(row.get("reaction_week") or 0)
            today_comments = int(row.get("comments_today") or 0)
            week_comments = int(row.get("comments_week") or 0)
            month_reactions = int(row.get("reaction_month") or 0)
            year_reactions = int(row.get("reaction_year") or 0)
            month_comments = int(row.get("comments_month") or 0)
            year_comments = int(row.get("comments_year") or 0)
            collections_today = int(row.get("collections_today") or 0)
            collections_week = int(row.get("collections_week") or 0)
            collections_month = int(row.get("collections_month") or 0)
            collections_year = int(row.get("collections_year") or 0)
            image_count = int(row.get("image_count") or 0)

            current_detail = f"{fmt_int(comment_count)} comments"
            current_html = f"{fmt_int(reaction_total)}<div class='row-sub'>{html.escape(current_detail)}</div>"
            today_html = f"{render_delta(today_reactions)}<div class='row-sub'>comments {html.escape(fmt_signed(today_comments))}</div>"
            week_html = f"{render_delta(week_reactions)}<div class='row-sub'>comments {html.escape(fmt_signed(week_comments))}</div>"
            collections_html = f"{collections_week}<div class='row-sub'>today {collections_today}</div>"
            period_flags = {
                "day": bool(today_reactions or today_comments or collections_today),
                "week": bool(week_reactions or week_comments or collections_week),
                "month": bool(month_reactions or month_comments or collections_month),
                "year": bool(year_reactions or year_comments or collections_year),
                "all": True,
            }
            active_attr = " data-active-row='1'" if any(period_flags[key] for key in ("day", "week", "month", "year")) else ""
            row_period_attrs = period_attrs(period_flags)
            detail_attr = f" data-post-detail-id='{int(row['post_id'])}'"
            artwork_html = (
                "<div class='artwork-cell'>"
                f"{preview_html(row.get('primary_thumbnail_url') or row.get('primary_image_url'), str(row.get('title') or 'Post preview'), fallback_href=image_page_url(row.get('primary_image_id')) if row.get('primary_image_id') else None)}"
                "<div>"
                f"{post_link(view_host, int(row['post_id']))}"
                f"<div class='row-sub'>{html.escape(row.get('title') or 'Untitled post')}</div>"
                "</div>"
                "</div>"
            )

            body.append(
                f"<tr{active_attr}{row_period_attrs}{detail_attr}>"
                + sortable_cell(
                    artwork_html,
                    row.get("post_id"),
                )
                + sortable_cell(html.escape(tz_helper.fmt_dt(row.get("published_at"))), row.get("published_sort"))
                + sortable_cell(current_html, reaction_total if reaction_total is not None else -1, "num")
                + sortable_cell(fmt_num(reactions_per_day), reactions_per_day if reactions_per_day is not None else -1, "num")
                + sortable_cell(today_html, today_reactions, "num")
                + sortable_cell(week_html, week_reactions, "num")
                + sortable_cell(fmt_int(first2), first2 if first2 is not None else -1, "num")
                + sortable_cell(fmt_int(first24), first24 if first24 is not None else -1, "num")
                + sortable_cell(collections_html, collections_week, "num")
                + sortable_cell(str(image_count), image_count, "num")
                + sortable_cell(html.escape(tz_helper.fmt_dt(row.get("captured_at"))), row.get("captured_at"))
                + "</tr>"
            )
        return (
            "<table class='clean-table'>"
            "<thead><tr><th>Artwork</th><th>Published</th><th class='num'>Current</th><th class='num'>Avg/day</th><th class='num'>+ Today</th><th class='num'>+ 7d</th><th class='num'>First 2h</th><th class='num'>First 24h</th><th class='num'>Collections</th><th class='num'>Images</th><th>Last seen</th></tr></thead>"
            f"<tbody>{''.join(body)}</tbody></table>"
        )

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

    def render_workspace_block(title: str, hint: str, inner_html: str) -> str:
        hint_html = f"<div class='hint'>{html.escape(hint)}</div>" if hint else ""
        return (
            "<section class='workspace-block'>"
            f"<h3>{html.escape(title)}</h3>"
            f"{hint_html}{inner_html}"
            "</section>"
        )

    def render_workspace_section(section_id: str, title: str, inner_html: str, active: bool = False) -> str:
        hidden_attr = "" if active else " hidden"
        return (
            f"<section class='workspace-section' data-workspace-section='{html.escape(section_id, quote=True)}'{hidden_attr}>"
            f"{inner_html}"
            "<div class='workspace-empty'>No rows match the current filters.</div>"
            "</section>"
        )

    def render_analytics_workspace(sections: List[Dict[str, Any]]) -> str:
        if not sections:
            return ""
        tabs = []
        panels = []
        for idx, section in enumerate(sections):
            section_id = str(section["id"])
            title = str(section["title"])
            active = idx == 0
            active_cls = " is-active" if active else ""
            selected = "true" if active else "false"
            tabs.append(
                f"<button type='button' class='workspace-tab{active_cls}' data-workspace-tab='{html.escape(section_id, quote=True)}' aria-selected='{selected}'>{html.escape(title)}</button>"
            )
            panels.append(render_workspace_section(section_id, title, str(section["html"]), active=active))
        return (
            "<div class='section-title'>Analytics workspace</div>"
            "<div class='workspace-panel' data-workspace>"
            "<div class='workspace-head'>"
            "<div class='workspace-tabs' role='tablist'>"
            f"{''.join(tabs)}"
            "</div>"
            "<div class='workspace-tools'>"
            "<input type='search' class='workspace-search' data-workspace-search placeholder='Search active tables'>"
            "<div class='workspace-periods' aria-label='Period filter'>"
            "<span class='workspace-period-label'>Period</span>"
            "<button type='button' class='workspace-period' data-workspace-period='day'>Day</button>"
            "<button type='button' class='workspace-period' data-workspace-period='week'>Week</button>"
            "<button type='button' class='workspace-period' data-workspace-period='month'>Month</button>"
            "<button type='button' class='workspace-period' data-workspace-period='year'>Year</button>"
            "<button type='button' class='workspace-period is-active' data-workspace-period='all' aria-pressed='true'>All time</button>"
            "</div>"
            "<label class='workspace-check'><input type='checkbox' data-workspace-active-only> Active rows only</label>"
            "<label class='workspace-check'><input type='checkbox' data-workspace-hide-unmatched> Hide image-only rows</label>"
            "</div>"
            "</div>"
            f"{''.join(panels)}"
            "</div>"
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
    today_reaction_total = int(today["like"] or 0) + int(today["heart"] or 0) + int(today["laugh"] or 0) + int(today["cry"] or 0)
    today_collection_total = int(collection_period_summary.get("today_total") or 0)

    hour_rows = [[html.escape(str(r['hour'])), str(r['posts']), fmt_num(r['avg_2h_reactions']), fmt_num(r['avg_24h_reactions']), fmt_num(r['avg_total_reactions']), fmt_num(r['avg_total_engagement']), chip(r['confidence'])] for r in hour_summary]
    weekday_rows = [[html.escape(str(r['weekday'])), str(r['posts']), fmt_num(r['avg_2h_reactions']), fmt_num(r['avg_24h_reactions']), fmt_num(r['avg_total_reactions']), fmt_num(r['avg_total_engagement']), chip(r['confidence'])] for r in weekday_summary]

    css = """
    :root{--bg:#0b1020;--panel:#121a2f;--panel2:#161f38;--border:#263353;--text:#ebf1ff;--muted:#9baacf;--accent:#7fb3ff;--good:#2ea043;--warn:#d29922;--na:#5b657f;--shadow:0 10px 24px rgba(0,0,0,.18)}
    *{box-sizing:border-box} body{margin:0;padding:24px;font-family:Segoe UI,Arial,sans-serif;background:linear-gradient(180deg,#091126 0%,#0d1323 100%);color:var(--text)}
    .wrap{max-width:1560px;margin:0 auto} .hero{display:flex;justify-content:space-between;align-items:flex-start;gap:24px;margin-bottom:18px} h1{margin:0;font-size:26px} .sub{margin:8px 0 0;color:var(--muted)} .toolbar{display:flex;gap:10px;align-items:center;flex-wrap:wrap}
    .toolbar .live{font-size:13px;color:var(--muted)} .toolbar button{border:1px solid var(--border);background:var(--panel2);color:var(--text);padding:10px 14px;border-radius:10px;cursor:pointer} .toolbar button:hover{border-color:#45629c}
    .section-title{margin:24px 0 12px;font-size:12px;letter-spacing:.12em;text-transform:uppercase;color:var(--muted)}
    .metrics{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:16px}.metric-card,.panel,.feature-card{background:linear-gradient(180deg,var(--panel) 0%,#12192c 100%);border:1px solid var(--border);border-radius:16px;box-shadow:var(--shadow)}
    .metric-card{padding:18px;min-height:146px}.metric-label{font-size:13px;color:var(--muted);margin-bottom:10px}.metric-value{font-size:22px;font-weight:800;line-height:1.25}.metric-detail{margin-top:12px;font-size:12px;color:var(--muted)}
    .feature-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:16px;margin-top:18px}.feature-card{padding:18px;min-height:185px}.feature-title{font-size:22px;font-weight:700;margin-bottom:6px}.feature-sub{font-size:12px;color:var(--muted);margin-bottom:14px}.feature-score{font-size:38px;font-weight:800;margin-bottom:10px;font-variant-numeric:tabular-nums}.feature-post{font-size:16px;font-weight:700;margin-bottom:8px}.feature-note{font-size:13px;color:var(--muted);line-height:1.45}.empty-state{font-size:34px;font-weight:800;margin:18px 0 8px}
    .visual-grid{display:grid;grid-template-columns:minmax(0,2fr) minmax(320px,1fr);grid-auto-rows:minmax(0,auto);gap:16px}.visual-card{background:linear-gradient(180deg,var(--panel) 0%,#12192c 100%);border:1px solid var(--border);border-radius:16px;box-shadow:var(--shadow);padding:18px;overflow:hidden}.visual-card h2{margin:0 0 8px;font-size:18px}.visual-card .hint{margin:0 0 12px;color:var(--muted);font-size:13px;line-height:1.45}.visual-wide{grid-row:span 2}.chart-svg{display:block;width:100%;height:auto;min-height:220px}.chart-grid{stroke:#263353;stroke-width:1}.chart-axis-line{stroke:#42537a;stroke-width:1.2}.chart-axis{fill:var(--muted);font-size:11px}.chart-bar.reaction{fill:#7fb3ff}.chart-bar.collection{fill:#7ee787}.chart-legend{display:flex;gap:16px;flex-wrap:wrap;color:var(--muted);font-size:12px}.chart-legend span{display:inline-flex;gap:7px;align-items:center}.legend-dot{display:inline-block;width:9px;height:9px;border-radius:50%}.legend-dot.reaction{background:#7fb3ff}.legend-dot.collection{background:#7ee787}.chart-empty{display:flex;align-items:center;min-height:110px;color:var(--muted);font-size:13px}.chart-mode{margin-bottom:10px;color:var(--muted);font-size:12px}.mix-row{display:grid;grid-template-columns:72px minmax(0,1fr) 42px;gap:10px;align-items:center;margin:12px 0}.mix-label,.mix-value{color:var(--muted);font-size:12px}.mix-value{text-align:right;color:var(--text);font-weight:800}.mix-track,.top-chart-track{height:10px;border-radius:999px;background:#0d1528;border:1px solid var(--border);overflow:hidden}.mix-fill,.top-chart-fill{display:block;height:100%;border-radius:999px}.top-chart-row{display:grid;grid-template-columns:minmax(120px,1.1fr) minmax(90px,.9fr) auto;gap:10px;align-items:center;margin:10px 0}.top-chart-label{min-width:0}.top-chart-label a{font-weight:800}.top-chart-label span{display:block;color:var(--muted);font-size:11px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.top-chart-fill{background:linear-gradient(90deg,#7fb3ff,#7ee787)}.top-chart-value{color:var(--muted);font-size:11px;text-align:right;white-space:nowrap}
    .reaction-row{display:grid;grid-template-columns:repeat(4,1fr);gap:12px}.reaction-stat{background:#10182b;border:1px solid #31446f;border-radius:14px;padding:14px}.reaction-head{display:flex;gap:8px;align-items:center;font-size:12px;color:var(--muted);text-transform:uppercase;letter-spacing:.04em}.reaction-value{margin-top:10px;font-size:34px;font-weight:800;text-align:center;font-variant-numeric:tabular-nums}
    .rgroup{display:flex;gap:6px;flex-wrap:wrap;margin-top:14px}.rbadge{display:inline-flex;align-items:center;justify-content:center;gap:4px;white-space:nowrap;background:#10182b;border:1px solid #31446f;border-radius:999px;padding:5px 9px;box-sizing:border-box}.ricon{font-size:14px;line-height:1}.rnum{font-weight:700;font-variant-numeric:tabular-nums;font-size:14px}
    .chip{display:inline-flex;align-items:center;padding:4px 10px;border-radius:999px;font-size:12px;font-weight:700;background:#1e2742;color:#d8e5ff;border:1px solid transparent}.chip.good{background:rgba(46,160,67,.15);color:#7ee787;border-color:rgba(46,160,67,.35)}.chip.mid{background:rgba(56,139,253,.16);color:#9cc3ff;border-color:rgba(56,139,253,.35)}.chip.warn{background:rgba(210,153,34,.16);color:#f2cc60;border-color:rgba(210,153,34,.35)}.chip.na{background:rgba(91,101,127,.18);color:#c8d1e8;border-color:rgba(91,101,127,.35)}
    .panel{padding:18px}.panel h2{margin:0 0 8px;font-size:18px}.panel .hint{margin:0 0 12px;color:var(--muted);font-size:13px;line-height:1.45}.clean-table{width:100%;border-collapse:collapse}.clean-table th,.clean-table td{padding:12px 10px;border-bottom:1px solid var(--border);text-align:center;vertical-align:top}.clean-table th{font-size:12px;letter-spacing:.05em;text-transform:uppercase;color:var(--muted);cursor:pointer;user-select:none}.clean-table th.sort-asc,.clean-table th.sort-desc{color:var(--accent)}.clean-table td.num,.clean-table th.num{text-align:center}.table-panel{overflow:auto}
    [hidden]{display:none!important}.artwork-cell{display:flex;align-items:center;gap:12px;min-width:230px;text-align:left}.preview-link{display:inline-flex;align-items:center;justify-content:center;text-decoration:none;vertical-align:top}.clean-table .preview-link{width:56px;height:56px}.post-thumb{width:56px;height:56px;border-radius:8px;object-fit:cover;background:#0d1528;border:1px solid var(--border);flex:0 0 56px}.thumb-missing{display:flex;align-items:center;justify-content:center;color:var(--muted);font-size:10px;line-height:1.15;text-align:center;padding:6px}.thumb-missing[hidden]{display:none!important}.clean-table tr[data-post-detail-id]{cursor:pointer}.clean-table tr[data-post-detail-id]:hover td{background:rgba(127,179,255,.06)}
    .workspace-panel{background:linear-gradient(180deg,var(--panel) 0%,#12192c 100%);border:1px solid var(--border);border-radius:16px;box-shadow:var(--shadow);overflow:hidden}.workspace-head{position:sticky;top:0;z-index:3;background:rgba(18,26,47,.96);backdrop-filter:blur(8px);border-bottom:1px solid var(--border);padding:14px 16px}.workspace-tabs{display:flex;gap:8px;flex-wrap:wrap}.workspace-tab{border:1px solid var(--border);background:#10182b;color:var(--muted);padding:9px 12px;border-radius:8px;cursor:pointer;font-weight:700}.workspace-tab:hover,.workspace-tab.is-active{color:var(--text);border-color:#45629c;background:#17233f}.workspace-tools{display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin-top:12px}.workspace-search{min-width:260px;max-width:420px;flex:1;border:1px solid var(--border);background:#0d1528;color:var(--text);padding:10px 12px;border-radius:8px}.workspace-search::placeholder{color:var(--muted)}.workspace-check{display:inline-flex;gap:7px;align-items:center;color:var(--muted);font-size:13px}.workspace-check input{accent-color:var(--accent)}.workspace-periods{display:inline-flex;gap:4px;align-items:center;flex-wrap:wrap}.workspace-period-label{color:var(--muted);font-size:12px;margin-right:2px}.workspace-period{border:1px solid var(--border);background:#0d1528;color:var(--muted);padding:7px 9px;border-radius:8px;cursor:pointer;font-size:12px;font-weight:800}.workspace-period:hover,.workspace-period.is-active{color:var(--text);border-color:#45629c;background:#17233f}.workspace-section{padding:0 16px 16px}.workspace-section[hidden]{display:none}.workspace-block{padding:18px 0;border-bottom:1px solid var(--border);overflow:auto}.workspace-block:last-child{border-bottom:0}.workspace-block h3{margin:0 0 8px;font-size:18px}.workspace-empty{display:none;margin:12px 0;color:var(--muted);font-size:13px}.workspace-section.is-filter-empty .workspace-empty{display:block}
    .post-drawer-backdrop{position:fixed;inset:0;z-index:20;background:rgba(3,7,18,.62);display:flex;justify-content:flex-end}.post-drawer-backdrop[hidden]{display:none}.post-drawer{width:min(560px,100vw);height:100%;overflow:auto;background:#10182b;border-left:1px solid var(--border);box-shadow:-18px 0 40px rgba(0,0,0,.35);padding:22px;position:relative}.drawer-close{position:absolute;top:14px;right:14px;width:36px;height:36px;border:1px solid var(--border);border-radius:8px;background:#17233f;color:var(--text);font-size:22px;line-height:1;cursor:pointer}.drawer-content h2{margin:0 44px 8px 0;font-size:22px}.drawer-hero{display:grid;grid-template-columns:168px 1fr;gap:16px;align-items:start;margin-bottom:18px}.drawer-preview{width:168px;height:168px;border-radius:8px;object-fit:cover;background:#0d1528;border:1px solid var(--border)}.drawer-muted{color:var(--muted);font-size:13px;line-height:1.45}.drawer-links{margin:8px 0;color:var(--muted)}.drawer-stats{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px;margin:16px 0}.drawer-stat{border:1px solid var(--border);background:#0d1528;border-radius:8px;padding:12px}.drawer-stat-label{font-size:11px;text-transform:uppercase;letter-spacing:.05em;color:var(--muted);margin-bottom:7px}.drawer-stat-value{font-size:22px;font-weight:800}.drawer-stat-detail{margin-top:4px;color:var(--muted);font-size:12px}.drawer-section{border-top:1px solid var(--border);padding-top:16px;margin-top:16px}.drawer-section h3{margin:0 0 10px;font-size:16px}.drawer-image-links{display:flex;gap:8px;flex-wrap:wrap}.drawer-image-links a,.drawer-image-links span{border:1px solid var(--border);border-radius:999px;padding:6px 10px;background:#0d1528}
    a{color:var(--accent);text-decoration:none} a:hover{text-decoration:underline}.row-sub{margin-top:5px;color:var(--muted);font-size:12px;line-height:1.35}.delta-pos{color:#7ee787;font-weight:800}.delta-neg{color:#ff9b9b;font-weight:800}.small-note{margin-top:14px;color:var(--muted);font-size:12px}
    @media (max-width:1300px){.feature-grid{grid-template-columns:repeat(2,minmax(0,1fr))}}
    @media (max-width:900px){.feature-grid,.reaction-row,.visual-grid{grid-template-columns:1fr}.visual-wide{grid-row:auto}.top-chart-row{grid-template-columns:1fr}.top-chart-value{text-align:left}.hero{flex-direction:column}.metrics{grid-template-columns:1fr}.workspace-search{min-width:100%}.workspace-tab{flex:1 1 auto}.drawer-hero{grid-template-columns:1fr}.drawer-preview{width:100%;height:auto;aspect-ratio:1/1}.drawer-stats{grid-template-columns:1fr}}
    """

    generated_at = utc_now_iso()
    parts: List[str] = []
    parts.append("<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width, initial-scale=1'>")
    parts.append("<meta http-equiv='Cache-Control' content='no-store, no-cache, must-revalidate, max-age=0'>")
    parts.append("<meta http-equiv='Pragma' content='no-cache'><meta http-equiv='Expires' content='0'>")
    parts.append(f"<meta name='generated-at' content='{html.escape(generated_at, quote=True)}'>")
    parts.append(f"<title>{html.escape(APP_TITLE)}</title>")
    parts.append(f"<style>{css}</style>")
    parts.append(COLLECTION_SECTION_CSS)
    parts.append(
        f"<script>function refreshNow(){{location.reload();}}setInterval(function(){{location.reload();}}, {refresh_seconds*1000});"
        "document.addEventListener('DOMContentLoaded', function(){"
        "function sortValue(text){"
        "text=(text||'').trim().replace(/^#/, '');"
        "if(text==='—'||text==='n/a'||text==='') return {kind:'empty', value:''};"
        "var numeric=text.replace(/[, ]/g,'');"
        "if(/^[-+]?\\d+(\\.\\d+)?$/.test(numeric)) return {kind:'number', value:parseFloat(numeric)};"
        "var localDate=text.match(/^(\\d{4})-(\\d{2})-(\\d{2})\\s+(\\d{2}):(\\d{2})/);"
        "if(localDate) return {kind:'date', value:Date.UTC(+localDate[1], +localDate[2]-1, +localDate[3], +localDate[4], +localDate[5])};"
        "var parsed=Date.parse(text);"
        "if(!Number.isNaN(parsed)) return {kind:'date', value:parsed};"
        "return {kind:'text', value:text.toLowerCase()};"
        "}"
        "function cellSortText(row,index){var cell=row.children[index];if(!cell)return '';return cell.dataset.sortValue||cell.textContent||'';}"
        "document.querySelectorAll('table.clean-table').forEach(function(table){"
        "var headers=table.querySelectorAll('thead th');"
        "headers.forEach(function(th,index){"
        "th.addEventListener('click', function(){"
        "var tbody=table.querySelector('tbody'); if(!tbody) return;"
        "var rows=Array.from(tbody.querySelectorAll('tr'));"
        "var nextDir=th.classList.contains('sort-asc') ? 'desc' : 'asc';"
        "headers.forEach(function(h){h.classList.remove('sort-asc','sort-desc');});"
        "th.classList.add(nextDir==='asc' ? 'sort-asc' : 'sort-desc');"
        "rows.sort(function(a,b){"
        "var av=sortValue(cellSortText(a,index));"
        "var bv=sortValue(cellSortText(b,index));"
        "var cmp=0;"
        "if(av.kind===bv.kind && (av.kind==='number'||av.kind==='date')) cmp=av.value-bv.value;"
        "else cmp=String(av.value).localeCompare(String(bv.value), undefined, {numeric:true, sensitivity:'base'});"
        "return nextDir==='asc' ? cmp : -cmp;"
        "});"
        "rows.forEach(function(row){tbody.appendChild(row);});"
        "});"
        "});"
        "});"
        "function applyWorkspaceFilters(workspace){"
        "var section=workspace.querySelector('.workspace-section:not([hidden])'); if(!section) return;"
        "var search=workspace.querySelector('[data-workspace-search]');"
        "var activeOnly=workspace.querySelector('[data-workspace-active-only]');"
        "var hideUnmatched=workspace.querySelector('[data-workspace-hide-unmatched]');"
        "var period=workspace.dataset.workspacePeriod||'all';"
        "var query=((search&&search.value)||'').trim().toLowerCase();"
        "var rows=Array.from(section.querySelectorAll('tbody tr'));"
        "var hasActiveRows=rows.some(function(row){return row.dataset.activeRow==='1';});"
        "var hasPeriodRows=rows.some(function(row){return row.dataset.periodAll==='1'||!!row.querySelector('[data-period-all=\"1\"]');});"
        "function periodMatches(row,value){if(value==='all')return true;var key='period'+value.charAt(0).toUpperCase()+value.slice(1);return row.dataset[key]==='1'||!!row.querySelector('[data-period-'+value+'=\"1\"]');}"
        "var visible=0;"
        "rows.forEach(function(row){"
        "var text=(row.textContent||'').toLowerCase();"
        "var show=!query||text.indexOf(query)!==-1;"
        "if(show&&period!=='all'&&hasPeriodRows) show=periodMatches(row,period);"
        "if(show&&activeOnly&&activeOnly.checked&&hasActiveRows) show=row.dataset.activeRow==='1';"
        "if(show&&hideUnmatched&&hideUnmatched.checked&&text.indexOf('post mapping not found locally')!==-1) show=false;"
        "row.hidden=!show; if(show) visible+=1;"
        "});"
        "section.classList.toggle('is-filter-empty', rows.length>0&&visible===0);"
        "}"
        "document.querySelectorAll('[data-workspace]').forEach(function(workspace){"
        "var key='civitaiTrackerWorkspaceTab';"
        "var periodKey='civitaiTrackerWorkspacePeriod';"
        "var buttons=Array.from(workspace.querySelectorAll('[data-workspace-tab]'));"
        "var sections=Array.from(workspace.querySelectorAll('[data-workspace-section]'));"
        "var periodButtons=Array.from(workspace.querySelectorAll('[data-workspace-period]'));"
        "function setPeriod(value){value=value||'all';workspace.dataset.workspacePeriod=value;periodButtons.forEach(function(button){var selected=button.dataset.workspacePeriod===value;button.classList.toggle('is-active',selected);button.setAttribute('aria-pressed',selected?'true':'false');});localStorage.setItem(periodKey,value);applyWorkspaceFilters(workspace);}"
        "function setTab(id){"
        "var found=sections.some(function(section){return section.dataset.workspaceSection===id;});"
        "if(!found&&sections[0]) id=sections[0].dataset.workspaceSection;"
        "buttons.forEach(function(button){var selected=button.dataset.workspaceTab===id;button.classList.toggle('is-active',selected);button.setAttribute('aria-selected',selected?'true':'false');});"
        "sections.forEach(function(section){section.hidden=section.dataset.workspaceSection!==id;});"
        "localStorage.setItem(key,id);"
        "applyWorkspaceFilters(workspace);"
        "}"
        "buttons.forEach(function(button){button.addEventListener('click',function(){setTab(button.dataset.workspaceTab);});});"
        "periodButtons.forEach(function(button){button.addEventListener('click',function(){setPeriod(button.dataset.workspacePeriod);});});"
        "workspace.querySelectorAll('[data-workspace-search],[data-workspace-active-only],[data-workspace-hide-unmatched]').forEach(function(control){"
        "control.addEventListener('input',function(){applyWorkspaceFilters(workspace);});"
        "control.addEventListener('change',function(){applyWorkspaceFilters(workspace);});"
        "});"
        "setPeriod(localStorage.getItem(periodKey)||'all');"
        "setTab(localStorage.getItem(key)||'');"
        "});"
        "function showPreviewFallback(img){var fallback=img.nextElementSibling;if(fallback){img.hidden=true;fallback.hidden=false;fallback.style.display='flex';}}"
        "document.querySelectorAll('img.post-thumb,img.drawer-preview').forEach(function(img){"
        "img.addEventListener('error',function(){showPreviewFallback(img);});"
        "});"
        "var drawer=document.querySelector('[data-post-drawer]');"
        "var drawerContent=document.querySelector('[data-post-drawer-content]');"
        "function closeDrawer(){if(drawer){drawer.hidden=true;document.body.style.overflow='';}}"
        "function openDrawer(id){"
        "if(!drawer||!drawerContent)return;"
        "var tpl=document.querySelector('[data-post-detail-template=\"'+id+'\"]');"
        "if(!tpl)return;"
        "drawerContent.innerHTML=tpl.innerHTML;"
        "drawer.hidden=false;document.body.style.overflow='hidden';"
        "drawer.querySelectorAll('img.drawer-preview').forEach(function(img){img.addEventListener('error',function(){showPreviewFallback(img);});});"
        "var close=drawer.querySelector('[data-post-drawer-close]'); if(close) close.focus();"
        "}"
        "document.querySelectorAll('[data-post-detail-id]').forEach(function(row){"
        "row.addEventListener('click',function(event){if(event.target.closest('a,button,input,label'))return;openDrawer(row.dataset.postDetailId);});"
        "row.addEventListener('keydown',function(event){if(event.key==='Enter'||event.key===' '){event.preventDefault();openDrawer(row.dataset.postDetailId);}});"
        "row.tabIndex=0;"
        "});"
        "document.querySelectorAll('[data-post-drawer-close]').forEach(function(button){button.addEventListener('click',closeDrawer);});"
        "if(drawer){drawer.addEventListener('click',function(event){if(event.target===drawer)closeDrawer();});}"
        "document.addEventListener('keydown',function(event){if(event.key==='Escape')closeDrawer();});"
        "});</script>"
    )
    parts.append("</head><body><div class='wrap'>")
    parts.append(
        "<div class='hero'>"
        f"<div><h1>{html.escape(APP_TITLE)}</h1><p class='sub'>tRPC post-based analytics for <strong>{html.escape(dashboard_name)}</strong> · generated {html.escape(tz_helper.fmt_dt(generated_at))}</p></div>"
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

    parts.append("<div class='section-title'>Monitoring overview</div>")
    parts.append("<div class='feature-grid'>")
    parts.append(
        "<div class='feature-card'>"
        "<div class='feature-title'>Reactions today</div>"
        f"<div class='feature-sub'>Local day: {html.escape(period_summary['today_label'])}</div>"
        f"<div class='feature-score'>{today_reaction_total}</div>"
        f"{reaction_group(today['like'], today['heart'], today['laugh'], today['cry'])}"
        "</div>"
    )
    parts.append(
        "<div class='feature-card'>"
        "<div class='feature-title'>Collections today</div>"
        f"<div class='feature-sub'>Local day: {html.escape(collection_period_summary['today_label'])}</div>"
        f"<div class='feature-score'>{today_collection_total}</div>"
        "<div class='feature-note'>New collection adds detected today.</div>"
        "</div>"
    )
    parts.append(best_reaction_card("Best art today by reactions", period_summary['best_today'], period_summary['today_label'], "No reaction gains captured yet for today."))
    parts.append(best_collection_card("Best art today by collections", collection_period_summary['best_today'], collection_period_summary['today_label'], "No collection adds captured yet for today."))
    parts.append(best_reaction_card("Best art this week by reactions", period_summary['best_week'], period_summary['week_label'], "No reaction gains captured yet for the last 7 days."))
    parts.append(best_collection_card("Best art this week by collections", collection_period_summary['best_week'], collection_period_summary['week_label'], "No collection adds captured yet for the last 7 days."))
    parts.append("</div>")

    parts.append(render_visual_overview())

    parts.append(render_posting_recommendations(suggested_windows, suggested_weekdays))

    if collections_html:
        parts.append(collections_html)

    analytics_sections: List[Dict[str, Any]] = [
        {
            "id": "performance",
            "title": "Performance",
            "html": render_workspace_block(
                "Post performance",
                "Per-post monitoring view sorted by recent reaction and collection activity.",
                render_post_performance_table(post_performance_rows),
            ),
        }
    ]
    if collection_tables_html:
        analytics_sections.append(
            {
                "id": "collections",
                "title": "Collections",
                "html": collection_tables_html,
            }
        )
    analytics_sections.extend(
        [
            {
                "id": "timing",
                "title": "Timing",
                "html": "".join(
                    [
                        render_workspace_block(
                            "Suggested posting windows",
                            "Ranked timing candidates from posts already tracked in this database.",
                            render_recommendation_table("hour recommendations", suggested_windows, "Hour"),
                        ),
                        render_workspace_block(
                            "Suggested weekdays",
                            "Weekday candidates use the same scoring basis as the hour recommendations.",
                            render_recommendation_table("weekday recommendations", suggested_weekdays, "Weekday"),
                        ),
                        render_workspace_block(
                            "Publish hour summary",
                            "Average performance grouped by publication hour in your local timezone.",
                            render_summary_table_content(
                                hour_rows,
                                ["Hour", "Posts", "Avg 2h reactions", "Avg 24h reactions", "Avg total reactions", "Avg total engagement", "Confidence"],
                            ),
                        ),
                        render_workspace_block(
                            "Weekday summary",
                            "Average performance grouped by weekday in your local timezone.",
                            render_summary_table_content(
                                weekday_rows,
                                ["Weekday", "Posts", "Avg 2h reactions", "Avg 24h reactions", "Avg total reactions", "Avg total engagement", "Confidence"],
                            ),
                        ),
                    ]
                ),
            },
            {
                "id": "history",
                "title": "History",
                "html": "".join(
                    [
                        render_workspace_block("Leaders by total reactions", "", render_leaders_table(by_total_reactions)),
                        render_workspace_block(
                            "Best first 24h",
                            "Early performance snapshot based on collected first-day windows.",
                            render_window_table_content(first24_rows, "Reactions captured within first 24h window"),
                        ),
                        render_workspace_block(
                            "Best first 2h",
                            "Very early momentum based on the first two hours of captured data.",
                            render_window_table_content(first2_rows, "Reactions captured within first 2h window"),
                        ),
                        render_workspace_block(
                            "Recent tracked posts",
                            "Latest posts included in the tracker after the configured start point.",
                            render_recent_posts(current_posts),
                        ),
                    ]
                ),
            },
        ]
    )
    parts.append(render_analytics_workspace(analytics_sections))

    parts.append(render_post_detail_drawer(post_performance_rows))
    parts.append("</div></body></html>")
    write_dashboard_html(html_path, ''.join(parts))

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="CivitAI post-based tracker using tRPC post.getInfinite.")
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
    config_path = Path(args.config).resolve()
    if not config_path.exists():
        raise FileNotFoundError(
            f"Config file not found: {config_path}\n"
            f"Run: python setup_config.py"
        )
    config_base = config_path.parent

    cfg = load_yaml_config(str(config_path))

    username = choose(args.username, deep_get(cfg, "profile.username"))
    dashboard_name = choose(args.display_name, deep_get(cfg, "profile.display_name"), username)
    tz_name = choose(args.tz, deep_get(cfg, "profile.timezone"), "UTC")

    db_path = resolve_runtime_path(choose(args.db, deep_get(cfg, "paths.db"), "civitai_tracker.db"), config_base)
    csv_dir = resolve_runtime_path(choose(args.csv_dir, deep_get(cfg, "paths.csv_dir"), "csv"), config_base)
    html_path = resolve_runtime_path(choose(args.html, deep_get(cfg, "paths.html"), "dashboard.html"), config_base)

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
    api_key_file = resolve_runtime_path(choose(args.api_key_file, deep_get(cfg, "auth.api_key_file"), "api_key.txt"), config_base)
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
        "buzz_maintenance_max_pages": deep_get(cfg, "collection_tracking.maintenance_max_pages", deep_get(cfg, "collection_tracking.max_pages", 10)),
        "buzz_max_history_days": deep_get(cfg, "collection_tracking.max_history_days", deep_get(cfg, "collection_tracking.backfill_days", 120)),
        "buzz_http_timeout_seconds": deep_get(cfg, "collection_tracking.http_timeout_seconds", 60),
        "mode": api_mode,
        "host": view_host,
        "runtime_status_path": str(config_base / "runtime_status.json"),
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
        init_db(conn)
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

    db_path = resolve_runtime_path(deep_get(cfg, "paths.db", "civitai_tracker.db"), config_base)
    csv_dir = resolve_runtime_path(deep_get(cfg, "paths.csv_dir", "csv"), config_base)
    html_path = resolve_runtime_path(deep_get(cfg, "paths.html", "dashboard.html"), config_base)

    api_mode = deep_get(cfg, "api.mode", DEFAULT_API_MODE)
    view_host = deep_get(cfg, "api.view_host", DEFAULT_VIEW_HOST)
    nsfw_level = deep_get(cfg, "api.nsfw_level", DEFAULT_NSFW_LEVEL)

    start_mode = deep_get(cfg, "tracking.start_mode", "post_id")
    min_post_id = deep_get(cfg, "tracking.start_post_id") if start_mode != "date" else None
    start_date = deep_get(cfg, "tracking.start_date") if start_mode == "date" else None
    poll_minutes = deep_get(cfg, "tracking.poll_minutes", DEFAULT_POLL_MINUTES)

    allow_rest_fallback = bool(deep_get(cfg, "options.allow_rest_fallback", False))
    inline_api_key = deep_get(cfg, "auth.api_key")
    api_key_file = resolve_runtime_path(deep_get(cfg, "auth.api_key_file", "api_key.txt"), config_base)
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
        "buzz_maintenance_max_pages": deep_get(cfg, "collection_tracking.maintenance_max_pages", deep_get(cfg, "collection_tracking.max_pages", 10)),
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
        service_result["collection_last_sync_at"] = buzz_summary.get("captured_at") if isinstance(buzz_summary, dict) else None
        service_result["collection_unavailable_reason"] = (
            buzz_summary.get("reason") if isinstance(buzz_summary, dict) else None
        )
        if (
            engagement_enabled
            and runtime.get("api_key")
            and isinstance(buzz_summary, dict)
            and not buzz_summary.get("ok")
        ):
            service_result["collection_warning"] = (
                buzz_summary.get("error")
                or buzz_summary.get("reason")
                or "Collection tracking did not complete."
            )

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
