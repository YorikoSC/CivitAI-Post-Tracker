from __future__ import annotations

import html
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

from collection_sync_state import read_collection_sync_state


def _fetch_all(conn: sqlite3.Connection, sql: str, params: Tuple[Any, ...] = ()) -> List[tuple]:
    cur = conn.execute(sql, params)
    return cur.fetchall()


def _table_has_columns(conn: sqlite3.Connection, table: str, columns: List[str]) -> bool:
    try:
        existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    except sqlite3.OperationalError:
        return False
    return all(column in existing for column in columns)


def _load_collection_sync_state(conn: sqlite3.Connection) -> Dict[str, Any]:
    try:
        row = read_collection_sync_state(conn)
    except sqlite3.OperationalError:
        return {}
    if not row:
        return {}
    return row


def get_collection_dashboard_data(db_path: str, recent_limit: int = 20, top_limit: int = 10) -> Dict[str, Any]:
    conn = sqlite3.connect(db_path)
    try:
        sync_state = _load_collection_sync_state(conn)
        has_image_previews = _table_has_columns(conn, "post_images", ["image_id", "image_url", "thumbnail_url"])
        preview_select = "pi.thumbnail_url, pi.image_url" if has_image_previews else "NULL AS thumbnail_url, NULL AS image_url"
        top_image_preview_select = (
            "MAX(pi.thumbnail_url) AS thumbnail_url, MAX(pi.image_url) AS image_url"
            if has_image_previews
            else "NULL AS thumbnail_url, NULL AS image_url"
        )
        preview_join = "LEFT JOIN post_images pi ON pi.image_id = COALESCE(cee.related_image_id, cee.target_id)" if has_image_previews else ""

        total_adds = conn.execute(
            "SELECT COUNT(*) FROM content_engagement_events WHERE normalized_type = 'collection_like'"
        ).fetchone()[0]

        affected_images = conn.execute(
            """
            SELECT COUNT(DISTINCT COALESCE(related_image_id, target_id))
            FROM content_engagement_events
            WHERE normalized_type = 'collection_like'
            """
        ).fetchone()[0]

        affected_posts = conn.execute(
            """
            SELECT COUNT(DISTINCT related_post_id)
            FROM content_engagement_events
            WHERE normalized_type = 'collection_like'
              AND related_post_id IS NOT NULL
            """
        ).fetchone()[0]

        last_event_time = conn.execute(
            """
            SELECT MAX(event_time)
            FROM content_engagement_events
            WHERE normalized_type = 'collection_like'
            """
        ).fetchone()[0]

        recent_rows = _fetch_all(
            conn,
            """
            WITH latest_posts AS (
                SELECT s.*
                FROM post_snapshots s
                JOIN (
                    SELECT post_id, MAX(id) AS max_id
                    FROM post_snapshots
                    GROUP BY post_id
                ) latest ON latest.max_id = s.id
            )
            SELECT
                cee.event_time,
                COALESCE(cee.related_image_id, cee.target_id) AS image_id,
                cee.related_post_id,
                cee.by_user_id,
                latest_posts.title,
                latest_posts.published_at,
                {preview_select}
            FROM content_engagement_events cee
            LEFT JOIN latest_posts ON latest_posts.post_id = cee.related_post_id
            {preview_join}
            WHERE cee.normalized_type = 'collection_like'
            ORDER BY cee.event_time DESC
            LIMIT ?
            """.format(preview_select=preview_select, preview_join=preview_join),
            (recent_limit,),
        )

        top_posts_rows = _fetch_all(
            conn,
            """
            WITH latest_posts AS (
                SELECT s.*
                FROM post_snapshots s
                JOIN (
                    SELECT post_id, MAX(id) AS max_id
                    FROM post_snapshots
                    GROUP BY post_id
                ) latest ON latest.max_id = s.id
            )
            SELECT
                cee.related_post_id,
                COUNT(*) AS collection_adds,
                COUNT(DISTINCT COALESCE(cee.related_image_id, cee.target_id)) AS distinct_images_affected,
                MAX(cee.event_time) AS last_event_time,
                latest_posts.title,
                latest_posts.published_at
            FROM content_engagement_events cee
            LEFT JOIN latest_posts ON latest_posts.post_id = cee.related_post_id
            WHERE cee.normalized_type = 'collection_like'
              AND cee.related_post_id IS NOT NULL
            GROUP BY cee.related_post_id, latest_posts.title, latest_posts.published_at
            ORDER BY collection_adds DESC, cee.related_post_id DESC
            LIMIT ?
            """,
            (top_limit,),
        )

        top_images_rows = _fetch_all(
            conn,
            """
            WITH latest_posts AS (
                SELECT s.*
                FROM post_snapshots s
                JOIN (
                    SELECT post_id, MAX(id) AS max_id
                    FROM post_snapshots
                    GROUP BY post_id
                ) latest ON latest.max_id = s.id
            )
            SELECT
                COALESCE(cee.related_image_id, cee.target_id) AS image_id,
                MAX(cee.related_post_id) AS related_post_id,
                COUNT(*) AS collection_adds,
                MAX(cee.event_time) AS last_event_time,
                latest_posts.title,
                {top_image_preview_select}
            FROM content_engagement_events cee
            LEFT JOIN latest_posts ON latest_posts.post_id = cee.related_post_id
            {preview_join}
            WHERE cee.normalized_type = 'collection_like'
            GROUP BY COALESCE(cee.related_image_id, cee.target_id), latest_posts.title
            ORDER BY collection_adds DESC, image_id DESC
            LIMIT ?
            """.format(preview_join=preview_join, top_image_preview_select=top_image_preview_select),
            (top_limit,),
        )

        return {
            "ok": True,
            "sync_state": sync_state,
            "total_collection_adds": int(total_adds or 0),
            "affected_images": int(affected_images or 0),
            "affected_posts": int(affected_posts or 0),
            "last_collection_event": last_event_time,
            "recent_collection_adds": [
                {
                    "event_time": row[0],
                    "image_id": row[1],
                    "post_id": row[2],
                    "by_user_id": row[3],
                    "title": row[4],
                    "published_at": row[5],
                    "thumbnail_url": row[6],
                    "image_url": row[7],
                }
                for row in recent_rows
            ],
            "top_posts_by_collection_adds": [
                {
                    "post_id": row[0],
                    "collection_adds": row[1],
                    "distinct_images_affected": row[2],
                    "last_event_time": row[3],
                    "title": row[4],
                    "published_at": row[5],
                }
                for row in top_posts_rows
            ],
            "top_images_by_collection_adds": [
                {
                    "image_id": row[0],
                    "post_id": row[1],
                    "collection_adds": row[2],
                    "last_event_time": row[3],
                    "title": row[4],
                    "thumbnail_url": row[5],
                    "image_url": row[6],
                }
                for row in top_images_rows
            ],
        }

    except sqlite3.OperationalError as exc:
        return {
            "ok": False,
            "error": str(exc),
            "sync_state": {},
            "total_collection_adds": 0,
            "affected_images": 0,
            "affected_posts": 0,
            "last_collection_event": None,
            "recent_collection_adds": [],
            "top_posts_by_collection_adds": [],
            "top_images_by_collection_adds": [],
        }

    finally:
        conn.close()


def _fmt(value: Any) -> str:
    if value is None or value == "":
        return "—"
    return html.escape(str(value))


def _fmt_time(value: Any, time_formatter: Optional[Callable[[Optional[str]], str]] = None) -> str:
    if value is None or value == "":
        return "—"
    if time_formatter:
        return html.escape(time_formatter(str(value)))
    return html.escape(str(value))


def _metric_card(label: str, value: Any, detail: str) -> str:
    return (
        "<div class='metric-card'>"
        f"<div class='metric-label'>{html.escape(label)}</div>"
        f"<div class='metric-value'>{_fmt(value)}</div>"
        f"<div class='metric-detail'>{html.escape(detail)}</div>"
        "</div>"
    )


def _post_link(view_host: str, post_id: int) -> str:
    if not view_host:
        return f"post #{int(post_id)}"
    url = f"{view_host.rstrip('/')}/posts/{int(post_id)}"
    return f'<a href="{html.escape(url)}" target="_blank" rel="noopener">post #{int(post_id)}</a>'


def _image_link(view_host: str, image_id: Any) -> str:
    if image_id in (None, ""):
        return "image —"
    if not view_host:
        return f"image #{int(image_id)}"
    url = f"{view_host.rstrip('/')}/images/{int(image_id)}"
    return f'<a href="{html.escape(url)}" target="_blank" rel="noopener">image #{int(image_id)}</a>'


def _image_cell(view_host: str, image_id: Any) -> str:
    if image_id in (None, ""):
        return "—"
    return _image_link(view_host, image_id)


def _safe_url(value: Any) -> Optional[str]:
    if not isinstance(value, str):
        return None
    value = value.strip()
    if value.startswith(("https://", "http://")):
        return value
    return None


def _parse_event_dt(value: Any) -> Optional[datetime]:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _period_marker(value: Any) -> str:
    now = datetime.now(timezone.utc)
    dt = _parse_event_dt(value)
    flags = {"all": True, "day": False, "week": False, "month": False, "year": False}
    if dt is not None:
        flags["day"] = dt.date() == now.date()
        flags["week"] = dt >= now - timedelta(days=7)
        flags["month"] = dt >= now - timedelta(days=30)
        flags["year"] = dt >= now - timedelta(days=365)
    attrs = " ".join(f"data-period-{key}='1'" for key, enabled in flags.items() if enabled)
    return f"<span class='period-marker' hidden {attrs}></span>"


def _image_preview_cell(view_host: str, image_id: Any, thumbnail_url: Any = None, image_url: Any = None) -> str:
    src = _safe_url(thumbnail_url) or _safe_url(image_url)
    link = ""
    if image_id not in (None, "") and view_host:
        link = f"{view_host.rstrip('/')}/images/{int(image_id)}"
    if not src:
        if link:
            missing = "<div class='post-thumb thumb-missing'>Open image</div>"
            return (
                f"<a class='preview-link' href='{html.escape(link, quote=True)}' target='_blank' rel='noopener' "
                "title='Preview unavailable or restricted; open the image page'>"
                f"{missing}</a>"
            )
        return "<div class='post-thumb thumb-missing'>No preview</div>"
    image = (
        f"<img class='post-thumb' src='{html.escape(src, quote=True)}' alt='image preview' "
        "loading='lazy' referrerpolicy='no-referrer'>"
        "<div class='post-thumb thumb-missing' hidden style='display:none'>No preview</div>"
    )
    if link:
        return f"<a class='preview-link' href='{html.escape(link, quote=True)}' target='_blank' rel='noopener'>{image}</a>"
    return image


def _post_cell(view_host: str, post_id: Any, title: Any = None, image_id: Any = None) -> str:
    if post_id in (None, ""):
        return f"{_image_link(view_host, image_id)}<div class='row-sub'>Post mapping not found locally</div>"
    title_text = html.escape(str(title or "Untitled post"))
    return f"{_post_link(view_host, int(post_id))}<div class='row-sub'>{title_text}</div>"


def _render_clean_table(headers: List[str], rows: List[List[Any]], empty_text: str = "No data", escape_cells: bool = True) -> str:
    head = "".join(f"<th>{html.escape(header)}</th>" for header in headers)

    if not rows:
        body = f"<tr><td colspan='{len(headers)}'>{html.escape(empty_text)}</td></tr>"
    else:
        body_rows = []
        for row in rows:
            if escape_cells:
                cells = "".join(f"<td>{_fmt(cell)}</td>" for cell in row)
            else:
                cells = "".join(f"<td>{cell}</td>" for cell in row)
            body_rows.append("<tr>" + cells + "</tr>")
        body = "".join(body_rows)

    return f"<table class='clean-table'><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"


def _render_panel_table(title: str, hint: str, headers: List[str], rows: List[List[Any]], escape_cells: bool = True) -> str:
    return (
        "<div class='panel table-panel' style='margin-top:18px'>"
        f"<h2>{html.escape(title)}</h2>"
        f"<div class='hint'>{html.escape(hint)}</div>"
        f"{_render_clean_table(headers, rows, escape_cells=escape_cells)}"
        "</div>"
    )


def _render_workspace_table(title: str, hint: str, headers: List[str], rows: List[List[Any]], escape_cells: bool = True) -> str:
    return (
        "<section class='workspace-block'>"
        f"<h3>{html.escape(title)}</h3>"
        f"<div class='hint'>{html.escape(hint)}</div>"
        f"{_render_clean_table(headers, rows, escape_cells=escape_cells)}"
        "</section>"
    )


def _collection_table_rows(
    data: Dict[str, Any],
    view_host: str,
    time_formatter: Optional[Callable[[Optional[str]], str]] = None,
) -> Tuple[List[List[Any]], List[List[Any]], List[List[Any]]]:
    recent_rows = [
        [
            _period_marker(item.get("event_time")) + _image_preview_cell(view_host, item.get("image_id"), item.get("thumbnail_url"), item.get("image_url")),
            _fmt_time(item.get("event_time"), time_formatter),
            _post_cell(view_host, item.get("post_id"), item.get("title"), item.get("image_id")),
            _image_cell(view_host, item.get("image_id")),
            _fmt(item.get("by_user_id")),
        ]
        for item in data.get("recent_collection_adds", [])
    ]

    top_post_rows = [
        [
            _period_marker(item.get("last_event_time")) + _post_cell(view_host, item.get("post_id"), item.get("title")),
            _fmt(item.get("collection_adds")),
            _fmt(item.get("distinct_images_affected")),
            _fmt_time(item.get("last_event_time"), time_formatter),
        ]
        for item in data.get("top_posts_by_collection_adds", [])
    ]

    top_image_rows = [
        [
            _period_marker(item.get("last_event_time")) + _image_preview_cell(view_host, item.get("image_id"), item.get("thumbnail_url"), item.get("image_url")),
            _image_cell(view_host, item.get("image_id")),
            _post_cell(view_host, item.get("post_id"), item.get("title"), item.get("image_id")),
            _fmt(item.get("collection_adds")),
            _fmt_time(item.get("last_event_time"), time_formatter),
        ]
        for item in data.get("top_images_by_collection_adds", [])
    ]

    return recent_rows, top_post_rows, top_image_rows


def render_collection_dashboard_section(
    db_path: str,
    recent_limit: int = 20,
    top_limit: int = 10,
    view_host: str = "",
    time_formatter: Optional[Callable[[Optional[str]], str]] = None,
    include_tables: bool = True,
) -> str:
    data = get_collection_dashboard_data(db_path, recent_limit=recent_limit, top_limit=top_limit)

    if not data.get("ok"):
        return (
            "<div class='section-title'>Collections</div>"
            "<div class='panel' style='margin-top:18px'>"
            "<h2>Collections unavailable</h2>"
            f"<div class='hint'>{_fmt(data.get('error'))}</div>"
            "</div>"
        )

    sync_state = data.get("sync_state") or {}
    stop_reason = str(sync_state.get("stop_reason") or "").strip()
    coverage_complete = bool(sync_state.get("coverage_complete"))
    collection_mode = str(sync_state.get("mode") or "").strip()

    warning_html = ""
    if stop_reason in {"page_limit_reached", "error"} or (sync_state and not coverage_complete):
        warning_html = (
            "<div class='panel' style='margin-top:18px'>"
            "<div class='hint'><strong>Collection history may be incomplete.</strong> "
            "The current collection totals were loaded only for part of the selected tracking window.</div>"
            "</div>"
        )

    state_hint_parts: List[str] = []
    if collection_mode:
        state_hint_parts.append(f"Mode: {collection_mode}")
    if sync_state.get("target_start_time"):
        state_hint_parts.append(f"Target start: {sync_state.get('target_start_time')}")
    if sync_state.get("oldest_event_time_seen"):
        state_hint_parts.append(f"Oldest loaded: {sync_state.get('oldest_event_time_seen')}")
    if sync_state.get("last_sync_at"):
        state_hint_parts.append(f"Last sync: {sync_state.get('last_sync_at')}")
    state_hint = " · ".join(state_hint_parts)

    recent_rows, top_post_rows, top_image_rows = _collection_table_rows(data, view_host, time_formatter)

    parts: List[str] = []
    parts.append("<div class='section-title'>Collections</div>")
    if state_hint:
        parts.append(f"<div class='hint' style='margin-top:4px'>{html.escape(state_hint)}</div>")

    parts.append("<div class='metrics'>")
    parts.append(_metric_card("Added to collections", data.get("total_collection_adds", 0), "Detected collection additions"))
    parts.append(_metric_card("Affected images", data.get("affected_images", 0), "Images added to collections"))
    parts.append(_metric_card("Affected posts", data.get("affected_posts", 0), "Posts affected through images"))
    parts.append(_metric_card("Last collection event", data.get("last_collection_event") or "—", "Latest detected collection add"))
    parts.append("</div>")

    if warning_html:
        parts.append(warning_html)

    if include_tables:
        parts.append(
            _render_panel_table(
                "Recent collection adds",
                "Latest detected additions of your images to collections.",
                ["Preview", "Time", "Post", "Image ID", "Actor ID"],
                recent_rows,
                escape_cells=False,
            )
        )

        parts.append(
            _render_panel_table(
                "Top posts by collection adds",
                "Posts whose images were added to collections most often.",
                ["Post", "Collection adds", "Distinct images", "Last add"],
                top_post_rows,
                escape_cells=False,
            )
        )

        parts.append(
            _render_panel_table(
                "Top images by collection adds",
                "Images most often added to collections.",
                ["Preview", "Image ID", "Post", "Collection adds", "Last add"],
                top_image_rows,
                escape_cells=False,
            )
        )

    return "".join(parts)


def render_collection_tables_html(
    db_path: str,
    recent_limit: int = 20,
    top_limit: int = 10,
    view_host: str = "",
    time_formatter: Optional[Callable[[Optional[str]], str]] = None,
) -> str:
    data = get_collection_dashboard_data(db_path, recent_limit=recent_limit, top_limit=top_limit)

    if not data.get("ok"):
        return f"<div class='feature-note'>{_fmt(data.get('error'))}</div>"

    recent_rows, top_post_rows, top_image_rows = _collection_table_rows(data, view_host, time_formatter)
    return "".join(
        [
            _render_workspace_table(
                "Recent collection adds",
                "Latest detected additions of your images to collections.",
                ["Preview", "Time", "Post", "Image ID", "Actor ID"],
                recent_rows,
                escape_cells=False,
            ),
            _render_workspace_table(
                "Top posts by collection adds",
                "Posts whose images were added to collections most often.",
                ["Post", "Collection adds", "Distinct images", "Last add"],
                top_post_rows,
                escape_cells=False,
            ),
            _render_workspace_table(
                "Top images by collection adds",
                "Images most often added to collections.",
                ["Preview", "Image ID", "Post", "Collection adds", "Last add"],
                top_image_rows,
                escape_cells=False,
            ),
        ]
    )


COLLECTION_SECTION_CSS = ""
