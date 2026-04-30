from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional


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


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def parse_iso_maybe(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    raw = str(value).strip()
    if not raw:
        return None
    try:
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        dt = datetime.fromisoformat(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def iso_z(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _cfg_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except Exception:
        parsed = default
    return max(minimum, min(maximum, parsed))


def normalize_collection_tracking_config(config: Dict[str, Any]) -> Dict[str, Any]:
    max_pages = _cfg_get(
        config,
        "buzz_max_pages",
        "max_pages",
        ("collection_tracking", "max_pages"),
        default=None,
    )
    return {
        "account_type": str(
            _cfg_get(config, "buzz_account_type", ("collection_tracking", "account_type"), default="blue")
        ).strip() or "blue",
        "bootstrap_max_pages": _cfg_int(
            _cfg_get(
                config,
                "buzz_bootstrap_max_pages",
                "bootstrap_max_pages",
                ("collection_tracking", "bootstrap_max_pages"),
                default=max_pages if max_pages not in (None, "") else 100,
            ),
            100,
            1,
            500,
        ),
        "maintenance_max_pages": _cfg_int(
            _cfg_get(
                config,
                "buzz_maintenance_max_pages",
                "maintenance_max_pages",
                ("collection_tracking", "maintenance_max_pages"),
                default=max_pages if max_pages not in (None, "") else 10,
            ),
            10,
            1,
            100,
        ),
        "overlap_hours": _cfg_int(
            _cfg_get(config, "buzz_overlap_hours", ("collection_tracking", "overlap_hours"), default=24),
            24,
            0,
            168,
        ),
        "max_history_days": _cfg_int(
            _cfg_get(
                config,
                "buzz_max_history_days",
                "max_history_days",
                ("collection_tracking", "max_history_days"),
                "buzz_backfill_days",
                "backfill_days",
                ("collection_tracking", "backfill_days"),
                default=120,
            ),
            120,
            1,
            365,
        ),
        "http_timeout_seconds": _cfg_int(
            _cfg_get(
                config,
                "buzz_http_timeout_seconds",
                "http_timeout_seconds",
                ("collection_tracking", "http_timeout_seconds"),
                default=60,
            ),
            60,
            5,
            300,
        ),
    }


def resolve_tracking_start(config: Dict[str, Any]) -> Optional[datetime]:
    start_date = _cfg_get(config, "start_date", "startDate", ("tracking", "start_date"))
    if not start_date:
        return None
    return parse_iso_maybe(str(start_date))


def resolve_safe_collection_start(config: Dict[str, Any], *, now_utc: Optional[datetime] = None) -> datetime:
    now_utc = now_utc or utc_now()
    normalized = normalize_collection_tracking_config(config)
    safe_floor = now_utc - timedelta(days=int(normalized["max_history_days"]))
    tracking_start = resolve_tracking_start(config)
    if tracking_start is None:
        return safe_floor
    return max(tracking_start.astimezone(timezone.utc), safe_floor)


def compute_collection_mode(
    existing_event_count: int,
    state: Optional[Dict[str, Any]],
    requested_target_start: Optional[str] = None,
) -> str:
    if not state:
        return "bootstrap"
    requested_target = parse_iso_maybe(requested_target_start)
    state_target = parse_iso_maybe(state.get("target_start_time"))
    if requested_target and state_target and requested_target < state_target:
        return "bootstrap"
    if not bool(state.get("bootstrap_completed", False)):
        return "bootstrap"
    if existing_event_count <= 0:
        return "maintenance"
    return "maintenance"


def compute_maintenance_start(last_event_time_seen: Optional[str], overlap_hours: int, hard_floor: datetime) -> datetime:
    last_dt = parse_iso_maybe(last_event_time_seen)
    if last_dt is None:
        return hard_floor
    candidate = last_dt - timedelta(hours=max(0, int(overlap_hours)))
    return max(candidate, hard_floor)
