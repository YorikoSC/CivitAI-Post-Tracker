from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.parse import quote

import requests


HOST_RED = "https://civitai.red"
HOST_COM = "https://civitai.com"
TRPC_PROC = "buzz.getUserTransactions"
CORE_TYPE_MAP = {
    "goodContent:image": "reaction_like",
    "collectedContent:image": "collection_like",
}


@dataclass
class BuzzIngestConfig:
    username: str
    api_key: str
    db_path: str
    host: str = HOST_RED
    account_type: str = "blue"
    backfill_days: int = 60
    overlap_hours: int = 24
    max_pages: int = 10
    timeout_seconds: int = 30


# ------------------------------
# DB schema / state
# ------------------------------

def init_content_engagement_schema(db_path: str) -> None:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS content_engagement_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_key TEXT NOT NULL UNIQUE,
                captured_at TEXT NOT NULL,
                event_time TEXT NOT NULL,
                host TEXT NOT NULL,
                account_type TEXT NOT NULL,
                raw_type TEXT NOT NULL,
                normalized_type TEXT NOT NULL,
                amount INTEGER,
                description TEXT,
                by_user_id INTEGER,
                target_id INTEGER,
                target_entity_id INTEGER,
                target_entity_type TEXT,
                target_type_candidate TEXT,
                to_user_id INTEGER,
                to_username TEXT,
                related_image_id INTEGER,
                related_post_id INTEGER,
                raw_json TEXT NOT NULL
            )
            """
        )
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_content_engagement_events_event_time
            ON content_engagement_events(event_time)
            """
        )
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_content_engagement_events_target
            ON content_engagement_events(target_type_candidate, target_id)
            """
        )
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_content_engagement_events_normalized_type
            ON content_engagement_events(normalized_type)
            """
        )
        conn.commit()
    finally:
        conn.close()


def get_latest_content_engagement_event_time(db_path: str) -> Optional[str]:
    if not Path(db_path).exists():
        return None
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.cursor()
        cur.execute("SELECT MAX(event_time) FROM content_engagement_events")
        row = cur.fetchone()
        return row[0] if row and row[0] else None
    finally:
        conn.close()


# ------------------------------
# Helpers
# ------------------------------

def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso_z(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_iso_maybe(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        if value.endswith("Z"):
            value = value[:-1] + "+00:00"
        return datetime.fromisoformat(value)
    except Exception:
        return None


def infer_host_from_config(config: Dict[str, Any]) -> str:
    mode = str(config.get("mode") or config.get("host_mode") or "auto").strip().lower()
    explicit_host = config.get("host") or config.get("base_url")
    if explicit_host:
        return str(explicit_host).rstrip("/")
    if mode == "red":
        return HOST_RED
    return HOST_COM


def read_api_key_from_config(config: Dict[str, Any]) -> str:
    inline = str(config.get("api_key") or "").strip()
    if inline:
        return inline

    candidate_paths = [
        config.get("api_key_file"),
        config.get("apiKeyFile"),
        config.get("apiKeyPath"),
        config.get("api_key_path"),
    ]
    for raw in candidate_paths:
        if not raw:
            continue
        path = Path(str(raw))
        if path.exists():
            return path.read_text(encoding="utf-8").strip()

    default_path = Path("api_key.txt")
    if default_path.exists():
        return default_path.read_text(encoding="utf-8").strip()

    raise FileNotFoundError("API key not found. Provide api_key in config or api_key_file path.")


def build_transaction_input(
    account_type: str,
    start_iso: str,
    end_iso: str,
    cursor: Optional[str],
) -> Dict[str, Any]:
    meta_cursor = ["undefined"] if cursor is None else ["Date"]
    return {
        "json": {
            "accountType": account_type,
            "start": start_iso,
            "end": end_iso,
            "cursor": cursor,
            "authed": True,
        },
        "meta": {
            "values": {
                "start": ["Date"],
                "end": ["Date"],
                "cursor": meta_cursor,
            }
        },
    }


def make_trpc_url(host: str, proc: str, payload: Dict[str, Any]) -> str:
    encoded = quote(json.dumps(payload, separators=(",", ":")), safe="")
    return f"{host.rstrip('/')}/api/trpc/{proc}?input={encoded}"


def extract_response_root(resp_json: Dict[str, Any]) -> Dict[str, Any]:
    root = resp_json.get("result", {}).get("data", {})
    return root if isinstance(root, dict) else {}


def extract_transactions(resp_json: Dict[str, Any]) -> List[Dict[str, Any]]:
    root = extract_response_root(resp_json)
    items = root.get("json", {}).get("transactions", [])
    return items if isinstance(items, list) else []


def extract_next_cursor(resp_json: Dict[str, Any], transactions: List[Dict[str, Any]]) -> Optional[str]:
    root = extract_response_root(resp_json)
    explicit = root.get("json", {}).get("nextCursor")
    if isinstance(explicit, str) and explicit.strip():
        return explicit

    meta_cursor = root.get("meta", {}).get("values", {}).get("cursor")
    if isinstance(meta_cursor, list) and meta_cursor:
        first = meta_cursor[0]
        if isinstance(first, str) and first.strip():
            return first

    if transactions:
        last_date = transactions[-1].get("date")
        if isinstance(last_date, str) and last_date.strip():
            return last_date
    return None


def normalized_type_from_raw(raw_type: Optional[str]) -> Optional[str]:
    if not raw_type:
        return None
    return CORE_TYPE_MAP.get(raw_type)


def target_type_candidate(details: Dict[str, Any], raw_type: Optional[str]) -> str:
    entity_type = details.get("entityType")
    if isinstance(entity_type, str) and entity_type.strip():
        val = entity_type.strip().lower()
        if val == "image":
            return "image"
        if val == "post":
            return "post"

    if isinstance(raw_type, str) and ":" in raw_type:
        suffix = raw_type.split(":", 1)[1].strip().lower()
        if suffix in {"image", "post"}:
            return suffix
    return "unknown"


def build_event_key(
    event_time: str,
    raw_type: str,
    amount: Optional[int],
    target_id: Optional[int],
    by_user_id: Optional[int],
    to_user_id: Optional[int],
) -> str:
    payload = f"{event_time}|{raw_type}|{amount}|{target_id}|{by_user_id}|{to_user_id}"
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()


def core_event_from_transaction(tx: Dict[str, Any], host: str, account_type: str, captured_at: str) -> Optional[Dict[str, Any]]:
    details = tx.get("details") or {}
    if not isinstance(details, dict):
        details = {}
    raw_type = details.get("type")
    normalized = normalized_type_from_raw(raw_type)
    if not normalized:
        return None

    event_time = str(tx.get("date") or "").strip()
    amount = tx.get("amount")
    description = tx.get("description")
    by_user_id = details.get("byUserId")
    target_entity_id = details.get("entityId")
    target_id = target_entity_id if target_entity_id is not None else details.get("forId")
    target_entity_type = details.get("entityType")
    target_type = target_type_candidate(details, raw_type)
    to_user = tx.get("toUser") or {}
    if not isinstance(to_user, dict):
        to_user = {}
    to_user_id = to_user.get("id")
    to_username = to_user.get("username")

    event_key = build_event_key(
        event_time=event_time,
        raw_type=str(raw_type),
        amount=int(amount) if isinstance(amount, int) else None,
        target_id=int(target_id) if isinstance(target_id, int) else None,
        by_user_id=int(by_user_id) if isinstance(by_user_id, int) else None,
        to_user_id=int(to_user_id) if isinstance(to_user_id, int) else None,
    )

    return {
        "event_key": event_key,
        "captured_at": captured_at,
        "event_time": event_time,
        "host": host,
        "account_type": account_type,
        "raw_type": raw_type,
        "normalized_type": normalized,
        "amount": amount,
        "description": description,
        "by_user_id": by_user_id,
        "target_id": target_id,
        "target_entity_id": target_entity_id,
        "target_entity_type": target_entity_type,
        "target_type_candidate": target_type,
        "to_user_id": to_user_id,
        "to_username": to_username,
        "related_image_id": None,
        "related_post_id": None,
        "raw_json": json.dumps(tx, ensure_ascii=False, separators=(",", ":")),
    }


# ------------------------------
# Network
# ------------------------------

def call_buzz_transactions_page(
    session: requests.Session,
    *,
    host: str,
    api_key: str,
    account_type: str,
    start_iso: str,
    end_iso: str,
    cursor: Optional[str],
    timeout_seconds: int,
) -> Tuple[Dict[str, Any], List[Dict[str, Any]], Optional[str], str]:
    payload = build_transaction_input(account_type, start_iso, end_iso, cursor)
    url = make_trpc_url(host, TRPC_PROC, payload)
    headers = {
        "Accept": "application/json",
        "Authorization": f"Bearer {api_key}",
        "Referer": f"{host.rstrip('/')}/user/transactions?accountType={account_type}",
        "User-Agent": "CivitAI-Tracker-B2.1/1.0",
    }
    resp = session.get(url, headers=headers, timeout=timeout_seconds)
    resp.raise_for_status()
    resp_json = resp.json()
    transactions = extract_transactions(resp_json)
    next_cursor = extract_next_cursor(resp_json, transactions)
    return resp_json, transactions, next_cursor, url


# ------------------------------
# Insert / ingest
# ------------------------------

def insert_content_engagement_events(db_path: str, events: Iterable[Dict[str, Any]]) -> Tuple[int, int]:
    rows = list(events)
    if not rows:
        return 0, 0
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.cursor()
        inserted = 0
        duplicates = 0
        for e in rows:
            cur.execute(
                """
                INSERT OR IGNORE INTO content_engagement_events (
                    event_key, captured_at, event_time, host, account_type,
                    raw_type, normalized_type, amount, description,
                    by_user_id, target_id, target_entity_id, target_entity_type,
                    target_type_candidate, to_user_id, to_username,
                    related_image_id, related_post_id, raw_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    e["event_key"],
                    e["captured_at"],
                    e["event_time"],
                    e["host"],
                    e["account_type"],
                    e["raw_type"],
                    e["normalized_type"],
                    e["amount"],
                    e["description"],
                    e["by_user_id"],
                    e["target_id"],
                    e["target_entity_id"],
                    e["target_entity_type"],
                    e["target_type_candidate"],
                    e["to_user_id"],
                    e["to_username"],
                    e["related_image_id"],
                    e["related_post_id"],
                    e["raw_json"],
                ),
            )
            if cur.rowcount == 1:
                inserted += 1
            else:
                duplicates += 1
        conn.commit()
        return inserted, duplicates
    finally:
        conn.close()


# ------------------------------
# Public entrypoints
# ------------------------------

def run_b2_1_ingest(config: Dict[str, Any], db_path: str) -> Dict[str, Any]:
    username = str(config.get("username") or "").strip()
    if not username:
        raise ValueError("username is required for B2.1 buzz ingest")

    api_key = read_api_key_from_config(config)
    host = infer_host_from_config(config)
    account_type = str(config.get("buzz_account_type") or "blue").strip() or "blue"
    backfill_days = int(config.get("buzz_backfill_days") or 60)
    overlap_hours = int(config.get("buzz_overlap_hours") or 24)
    max_pages = int(config.get("buzz_max_pages") or 10)
    timeout_seconds = int(config.get("http_timeout_seconds") or 30)

    runtime_cfg = BuzzIngestConfig(
        username=username,
        api_key=api_key,
        db_path=db_path,
        host=host,
        account_type=account_type,
        backfill_days=backfill_days,
        overlap_hours=overlap_hours,
        max_pages=max_pages,
        timeout_seconds=timeout_seconds,
    )
    return ingest_content_engagement(runtime_cfg)


def ingest_content_engagement(cfg: BuzzIngestConfig) -> Dict[str, Any]:
    init_content_engagement_schema(cfg.db_path)

    latest_event_time = get_latest_content_engagement_event_time(cfg.db_path)
    latest_dt = parse_iso_maybe(latest_event_time)
    if latest_dt is not None:
        start_dt = latest_dt - timedelta(hours=cfg.overlap_hours)
    else:
        start_dt = utc_now() - timedelta(days=cfg.backfill_days)
    end_dt = utc_now()

    captured_at = iso_z(utc_now())
    start_iso = iso_z(start_dt)
    end_iso = iso_z(end_dt)

    session = requests.Session()

    pages_fetched = 0
    events_seen = 0
    events_core = 0
    events_inserted = 0
    events_deduped = 0
    cursor = None
    stop_reason = "max_pages"
    type_counts: Dict[str, int] = {"reaction_like": 0, "collection_like": 0}
    last_page_url = None

    for _ in range(cfg.max_pages):
        resp_json, transactions, next_cursor, request_url = call_buzz_transactions_page(
            session,
            host=cfg.host,
            api_key=cfg.api_key,
            account_type=cfg.account_type,
            start_iso=start_iso,
            end_iso=end_iso,
            cursor=cursor,
            timeout_seconds=cfg.timeout_seconds,
        )
        pages_fetched += 1
        last_page_url = request_url

        if not transactions:
            stop_reason = "empty_page"
            break

        events_seen += len(transactions)
        core_events: List[Dict[str, Any]] = []
        for tx in transactions:
            core = core_event_from_transaction(tx, cfg.host, cfg.account_type, captured_at)
            if not core:
                continue
            core_events.append(core)
            events_core += 1
            type_counts[core["normalized_type"]] = type_counts.get(core["normalized_type"], 0) + 1

        inserted, duplicates = insert_content_engagement_events(cfg.db_path, core_events)
        events_inserted += inserted
        events_deduped += duplicates

        if not next_cursor or next_cursor == cursor:
            stop_reason = "cursor_stopped"
            break
        cursor = next_cursor
    else:
        stop_reason = "max_pages_reached"

    return {
        "ok": True,
        "host": cfg.host,
        "account_type": cfg.account_type,
        "db_path": cfg.db_path,
        "captured_at": captured_at,
        "window_start": start_iso,
        "window_end": end_iso,
        "last_existing_event_time": latest_event_time,
        "pages_fetched": pages_fetched,
        "events_seen": events_seen,
        "events_core": events_core,
        "events_inserted": events_inserted,
        "events_deduped": events_deduped,
        "type_counts": type_counts,
        "last_cursor": cursor,
        "last_page_url": last_page_url,
        "stop_reason": stop_reason,
    }
