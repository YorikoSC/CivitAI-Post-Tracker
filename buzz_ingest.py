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
MAX_HISTORY_DAYS_HARD_CEILING = 365
SYNC_STATE_KEY = "default"


@dataclass
class BuzzIngestConfig:
    username: str
    api_key: str
    db_path: str
    host: str = HOST_RED
    account_type: str = "blue"
    overlap_hours: int = 24
    bootstrap_max_pages: int = 100
    maintenance_max_pages: int = 10
    max_history_days: int = 120
    timeout_seconds: int = 60
    target_start_time: Optional[str] = None


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
        cur.execute(
            """
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
        )
        conn.commit()
    finally:
        conn.close()


def get_latest_content_engagement_event_time(db_path: str) -> Optional[str]:
    if not Path(db_path).exists():
        return None
    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute("SELECT MAX(event_time) FROM content_engagement_events").fetchone()
        return row[0] if row and row[0] else None
    finally:
        conn.close()


def get_oldest_content_engagement_event_time(db_path: str) -> Optional[str]:
    if not Path(db_path).exists():
        return None
    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute("SELECT MIN(event_time) FROM content_engagement_events").fetchone()
        return row[0] if row and row[0] else None
    finally:
        conn.close()


def get_content_engagement_event_count(db_path: str) -> int:
    if not Path(db_path).exists():
        return 0
    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute("SELECT COUNT(*) FROM content_engagement_events").fetchone()
        return int(row[0] or 0)
    finally:
        conn.close()


def get_collection_sync_state(db_path: str) -> Dict[str, Any]:
    if not Path(db_path).exists():
        return {}
    conn = sqlite3.connect(db_path)
    try:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM collection_sync_state WHERE sync_key = ?",
            (SYNC_STATE_KEY,),
        ).fetchone()
        return dict(row) if row else {}
    finally:
        conn.close()


def save_collection_sync_state(db_path: str, state: Dict[str, Any]) -> None:
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            """
            INSERT INTO collection_sync_state (
                sync_key, mode, last_sync_at, last_event_time_seen, oldest_event_time_seen,
                target_start_time, coverage_complete, stop_reason, pages_fetched_last_run,
                bootstrap_completed
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(sync_key) DO UPDATE SET
                mode=excluded.mode,
                last_sync_at=excluded.last_sync_at,
                last_event_time_seen=excluded.last_event_time_seen,
                oldest_event_time_seen=excluded.oldest_event_time_seen,
                target_start_time=excluded.target_start_time,
                coverage_complete=excluded.coverage_complete,
                stop_reason=excluded.stop_reason,
                pages_fetched_last_run=excluded.pages_fetched_last_run,
                bootstrap_completed=excluded.bootstrap_completed
            """,
            (
                SYNC_STATE_KEY,
                state.get("mode", "maintenance"),
                state.get("last_sync_at"),
                state.get("last_event_time_seen"),
                state.get("oldest_event_time_seen"),
                state.get("target_start_time"),
                1 if state.get("coverage_complete") else 0,
                state.get("stop_reason"),
                int(state.get("pages_fetched_last_run") or 0),
                1 if state.get("bootstrap_completed") else 0,
            ),
        )
        conn.commit()
    finally:
        conn.close()


def rebuild_collection_history(db_path: str) -> None:
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("DELETE FROM content_engagement_events")
        conn.execute("DELETE FROM collection_sync_state WHERE sync_key = ?", (SYNC_STATE_KEY,))
        conn.commit()
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


def _cfg_get(config: Dict[str, Any], *keys: Any, default: Any = None) -> Any:
    for key in keys:
        if isinstance(key, tuple):
            cur: Any = config
            ok = True
            for part in key:
                if not isinstance(cur, dict) or part not in cur:
                    ok = False
                    break
                cur = cur[part]
            if ok and cur not in (None, ""):
                return cur
        else:
            if isinstance(config, dict):
                value = config.get(key)
                if value not in (None, ""):
                    return value
    return default


def infer_host_from_config(config: Dict[str, Any]) -> str:
    explicit_host = _cfg_get(config, "host", "base_url", ("api", "view_host"))
    if explicit_host:
        return str(explicit_host).rstrip("/")

    mode = str(_cfg_get(config, "mode", "host_mode", ("api", "mode"), default="auto")).strip().lower()
    if mode == "red":
        return HOST_RED
    return HOST_COM


def read_api_key_from_config(config: Dict[str, Any]) -> str:
    inline = str(_cfg_get(config, "api_key", ("auth", "api_key"), default="")).strip()
    if inline:
        return inline

    candidate_paths = [
        _cfg_get(config, "api_key_file", ("auth", "api_key_file")),
        _cfg_get(config, "apiKeyFile"),
        _cfg_get(config, "apiKeyPath"),
        _cfg_get(config, "api_key_path"),
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


def build_transaction_input(account_type: str, start_iso: str, end_iso: str, cursor: Optional[str]) -> Dict[str, Any]:
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
    if isinstance(explicit, str) and parse_iso_maybe(explicit):
        return explicit

    meta_cursor = root.get("meta", {}).get("values", {}).get("cursor")
    if isinstance(meta_cursor, list) and meta_cursor:
        first = meta_cursor[0]
        if isinstance(first, str) and parse_iso_maybe(first):
            return first

    if transactions:
        last_date = transactions[-1].get("date")
        if isinstance(last_date, str) and parse_iso_maybe(last_date):
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


def build_event_key(event_time: str, raw_type: str, amount: Optional[int], target_id: Optional[int], by_user_id: Optional[int], to_user_id: Optional[int]) -> str:
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


def clamp_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(value)
    except Exception:
        value = default
    return max(minimum, min(maximum, value))


def resolve_target_start_time(config: Dict[str, Any], max_history_days: int) -> datetime:
    safe_floor = utc_now() - timedelta(days=max_history_days)
    start_date = _cfg_get(config, "start_date", "startDate", ("tracking", "start_date"))
    parsed = parse_iso_maybe(str(start_date)) if start_date else None
    if parsed is not None:
        parsed_utc = parsed.astimezone(timezone.utc)
        return max(parsed_utc, safe_floor)
    return safe_floor


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
        "User-Agent": "CivitAI-Tracker-v10.0.1/1.0",
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


def _run_transactions_pass(
    session: requests.Session,
    *,
    cfg: BuzzIngestConfig,
    start_dt: datetime,
    end_dt: datetime,
    max_pages: int,
    stop_at_target_dt: datetime,
) -> Dict[str, Any]:
    pages_fetched = 0
    events_seen = 0
    events_core = 0
    events_inserted = 0
    events_deduped = 0
    cursor: Optional[str] = None
    stop_reason = "page_limit_reached"
    type_counts: Dict[str, int] = {"reaction_like": 0, "collection_like": 0}
    last_page_url: Optional[str] = None
    captured_at = iso_z(utc_now())
    oldest_event_time_seen: Optional[str] = None
    latest_event_time_seen: Optional[str] = None

    start_iso = iso_z(start_dt)
    end_iso = iso_z(end_dt)

    for _ in range(max_pages):
        _, transactions, next_cursor, request_url = call_buzz_transactions_page(
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
            stop_reason = "source_exhausted"
            break

        events_seen += len(transactions)
        tx_dates = [parse_iso_maybe(str(tx.get("date") or "")) for tx in transactions]
        tx_dates = [dt for dt in tx_dates if dt is not None]
        if tx_dates:
            if latest_event_time_seen is None:
                latest_event_time_seen = iso_z(max(tx_dates))
            oldest_on_page = min(tx_dates)
            oldest_event_time_seen = iso_z(oldest_on_page)
        else:
            oldest_on_page = None

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

        if oldest_on_page is not None and oldest_on_page <= stop_at_target_dt:
            stop_reason = "reached_control_point"
            cursor = next_cursor
            break

        if not next_cursor or next_cursor == cursor:
            stop_reason = "source_exhausted"
            break
        cursor = next_cursor
    else:
        stop_reason = "page_limit_reached"

    coverage_complete = stop_reason in {"reached_control_point", "source_exhausted"}
    return {
        "captured_at": captured_at,
        "window_start": start_iso,
        "window_end": end_iso,
        "pages_fetched": pages_fetched,
        "events_seen": events_seen,
        "events_core": events_core,
        "events_inserted": events_inserted,
        "events_deduped": events_deduped,
        "type_counts": type_counts,
        "last_cursor": cursor,
        "last_page_url": last_page_url,
        "stop_reason": stop_reason,
        "coverage_complete": coverage_complete,
        "target_start_time": iso_z(stop_at_target_dt),
        "oldest_event_time_seen": oldest_event_time_seen,
        "latest_event_time_seen": latest_event_time_seen,
    }


def determine_collection_mode(cfg: BuzzIngestConfig) -> Tuple[str, Dict[str, Any]]:
    state = get_collection_sync_state(cfg.db_path)
    count = get_content_engagement_event_count(cfg.db_path)
    if count == 0:
        return "bootstrap", state
    if not state:
        return "bootstrap", state
    if not bool(state.get("bootstrap_completed")):
        return "bootstrap", state
    state_target = parse_iso_maybe(state.get("target_start_time"))
    requested_target = parse_iso_maybe(cfg.target_start_time)
    if requested_target and state_target and requested_target < state_target:
        return "bootstrap", state
    return "maintenance", state


def run_b2_1_ingest(config: Dict[str, Any], db_path: str) -> Dict[str, Any]:
    username = str(_cfg_get(config, "username", ("profile", "username"), default="")).strip()
    if not username:
        raise ValueError("username is required for B2.1 buzz ingest")

    api_key = read_api_key_from_config(config)
    host = infer_host_from_config(config)

    account_type = str(_cfg_get(config, "buzz_account_type", ("collection_tracking", "account_type"), default="blue")).strip() or "blue"
    overlap_hours = clamp_int(_cfg_get(config, "buzz_overlap_hours", ("collection_tracking", "overlap_hours"), default=24), 24, 0, 168)
    bootstrap_max_pages = clamp_int(_cfg_get(config, "buzz_bootstrap_max_pages", ("collection_tracking", "bootstrap_max_pages"), ("collection_tracking", "max_pages"), default=100), 100, 1, 500)
    maintenance_max_pages = clamp_int(_cfg_get(config, "buzz_maintenance_max_pages", ("collection_tracking", "maintenance_max_pages"), default=10), 10, 1, 100)
    max_history_days = clamp_int(_cfg_get(config, "buzz_max_history_days", ("collection_tracking", "max_history_days"), ("collection_tracking", "backfill_days"), default=120), 120, 1, MAX_HISTORY_DAYS_HARD_CEILING)
    timeout_seconds = clamp_int(_cfg_get(config, "http_timeout_seconds", "buzz_http_timeout_seconds", ("collection_tracking", "http_timeout_seconds"), default=60), 60, 5, 300)

    target_start_dt = resolve_target_start_time(config, max_history_days)

    runtime_cfg = BuzzIngestConfig(
        username=username,
        api_key=api_key,
        db_path=db_path,
        host=host,
        account_type=account_type,
        overlap_hours=overlap_hours,
        bootstrap_max_pages=bootstrap_max_pages,
        maintenance_max_pages=maintenance_max_pages,
        max_history_days=max_history_days,
        timeout_seconds=timeout_seconds,
        target_start_time=iso_z(target_start_dt),
    )
    return ingest_content_engagement(runtime_cfg)


def ingest_content_engagement(cfg: BuzzIngestConfig) -> Dict[str, Any]:
    init_content_engagement_schema(cfg.db_path)

    latest_event_time = get_latest_content_engagement_event_time(cfg.db_path)
    oldest_event_time = get_oldest_content_engagement_event_time(cfg.db_path)
    latest_dt = parse_iso_maybe(latest_event_time)
    oldest_dt = parse_iso_maybe(oldest_event_time)
    target_start_dt = parse_iso_maybe(cfg.target_start_time) or (utc_now() - timedelta(days=cfg.max_history_days))

    mode, state = determine_collection_mode(cfg)
    now_dt = utc_now()

    if mode == "bootstrap":
        start_dt = target_start_dt
        end_dt = oldest_dt if oldest_dt else now_dt
        max_pages = cfg.bootstrap_max_pages
        if end_dt < start_dt:
            end_dt = start_dt
    else:
        start_dt = latest_dt - timedelta(hours=cfg.overlap_hours) if latest_dt else target_start_dt
        if start_dt < target_start_dt:
            start_dt = target_start_dt
        end_dt = now_dt
        max_pages = cfg.maintenance_max_pages

    session = requests.Session()
    back = _run_transactions_pass(
        session,
        cfg=cfg,
        start_dt=start_dt,
        end_dt=end_dt,
        max_pages=max_pages,
        stop_at_target_dt=target_start_dt,
    )

    bootstrap_completed = bool(state.get("bootstrap_completed"))
    if mode == "bootstrap":
        bootstrap_completed = bool(back.get("coverage_complete"))

    save_collection_sync_state(
        cfg.db_path,
        {
            "mode": "maintenance" if bootstrap_completed else "bootstrap",
            "last_sync_at": back.get("captured_at"),
            "last_event_time_seen": back.get("latest_event_time_seen") or latest_event_time,
            "oldest_event_time_seen": back.get("oldest_event_time_seen") or oldest_event_time,
            "target_start_time": back.get("target_start_time"),
            "coverage_complete": bool(back.get("coverage_complete")),
            "stop_reason": back.get("stop_reason"),
            "pages_fetched_last_run": back.get("pages_fetched", 0),
            "bootstrap_completed": bootstrap_completed,
        },
    )

    return {
        "ok": True,
        "host": cfg.host,
        "account_type": cfg.account_type,
        "db_path": cfg.db_path,
        "captured_at": back.get("captured_at"),
        "window_start": back.get("window_start"),
        "window_end": back.get("window_end"),
        "last_existing_event_time": latest_event_time,
        "oldest_existing_event_time": oldest_event_time,
        "pages_fetched": back.get("pages_fetched", 0),
        "events_seen": back.get("events_seen", 0),
        "events_core": back.get("events_core", 0),
        "events_inserted": back.get("events_inserted", 0),
        "events_deduped": back.get("events_deduped", 0),
        "type_counts": back.get("type_counts", {}),
        "last_cursor": back.get("last_cursor"),
        "last_page_url": back.get("last_page_url"),
        "stop_reason": back.get("stop_reason"),
        "coverage_complete": bool(back.get("coverage_complete")),
        "target_start_time": back.get("target_start_time"),
        "oldest_event_time_seen": back.get("oldest_event_time_seen"),
        "latest_event_time_seen": back.get("latest_event_time_seen"),
        "collection_mode": mode,
        "bootstrap_completed": bootstrap_completed,
        "max_history_days": cfg.max_history_days,
    }
