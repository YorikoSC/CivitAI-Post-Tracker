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

        unmapped_events = conn.execute(
            """
            SELECT COUNT(*)
            FROM content_engagement_events
            WHERE normalized_type = 'collection_like'
              AND related_post_id IS NULL
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
            "unmapped_events": int(unmapped_events or 0),
            "recent_collection_adds": [
                {
                    "event_time": row[0],
                    "image_id": row[1],
                    "post_id": row[2],
                    "title": row[3],
                    "published_at": row[4],
                    "thumbnail_url": row[5],
                    "image_url": row[6],
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
            "unmapped_events": 0,
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


def _collection_metric(label: str, value: Any, detail: str) -> str:
    return (
        "<div class='collection-kpi'>"
        f"<div class='collection-kpi-label'>{html.escape(label)}</div>"
        f"<div class='collection-kpi-value'>{_fmt(value)}</div>"
        f"<div class='collection-kpi-detail'>{html.escape(detail)}</div>"
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


def _period_flags(value: Any) -> Dict[str, bool]:
    now = datetime.now(timezone.utc)
    dt = _parse_event_dt(value)
    flags = {"all": True, "day": False, "week": False, "month": False, "year": False}
    if dt is not None:
        flags["day"] = dt.date() == now.date()
        flags["week"] = dt >= now - timedelta(days=7)
        flags["month"] = dt >= now - timedelta(days=30)
        flags["year"] = dt >= now - timedelta(days=365)
    return flags


def _period_attrs(value: Any) -> str:
    flags = _period_flags(value)
    return " ".join(f"data-period-{key}='1'" for key, enabled in flags.items() if enabled)


def _image_preview_cell(view_host: str, image_id: Any, thumbnail_url: Any = None, image_url: Any = None) -> str:
    return _image_preview(view_host, image_id, thumbnail_url, image_url, "post-thumb")


def _image_preview(view_host: str, image_id: Any, thumbnail_url: Any = None, image_url: Any = None, cls: str = "post-thumb") -> str:
    src = _safe_url(thumbnail_url) or _safe_url(image_url)
    link = ""
    if image_id not in (None, "") and view_host:
        link = f"{view_host.rstrip('/')}/images/{int(image_id)}"
    if not src:
        if link:
            missing = f"<div class='{html.escape(cls, quote=True)} thumb-missing'>Open image</div>"
            return (
                f"<a class='preview-link' href='{html.escape(link, quote=True)}' target='_blank' rel='noopener' "
                "title='Preview unavailable or restricted; open the image page'>"
                f"{missing}</a>"
            )
        return f"<div class='{html.escape(cls, quote=True)} thumb-missing'>No preview</div>"
    image = (
        f"<img class='{html.escape(cls, quote=True)}' src='{html.escape(src, quote=True)}' alt='image preview' "
        "loading='lazy' referrerpolicy='no-referrer'>"
        f"<div class='{html.escape(cls, quote=True)} thumb-missing' hidden style='display:none'>No preview</div>"
    )
    if link:
        return f"<a class='preview-link' href='{html.escape(link, quote=True)}' target='_blank' rel='noopener'>{image}</a>"
    return image


def _post_cell(view_host: str, post_id: Any, title: Any = None, image_id: Any = None) -> str:
    if post_id in (None, ""):
        return f"{_image_link(view_host, image_id)}<div class='row-sub'>Post mapping not found locally</div>"
    title_text = html.escape(str(title or "Untitled post"))
    return f"{_post_link(view_host, int(post_id))}<div class='row-sub'>{title_text}</div>"


def _collection_empty_state(title: str, text: str) -> str:
    return (
        "<div class='collection-empty'>"
        f"<strong>{html.escape(title)}</strong>"
        f"<span>{html.escape(text)}</span>"
        "</div>"
    )


def _collection_sync_hint(data: Dict[str, Any], time_formatter: Optional[Callable[[Optional[str]], str]] = None) -> str:
    sync_state = data.get("sync_state") or {}
    parts: List[str] = []
    collection_mode = str(sync_state.get("mode") or "").strip()
    if collection_mode:
        parts.append(f"Mode: {collection_mode}")
    if sync_state.get("last_sync_at"):
        parts.append(f"Last sync: {_fmt_time(sync_state.get('last_sync_at'), time_formatter)}")
    if sync_state.get("oldest_event_time_seen"):
        parts.append(f"Oldest loaded: {_fmt_time(sync_state.get('oldest_event_time_seen'), time_formatter)}")
    if sync_state.get("target_start_time"):
        parts.append(f"Target start: {_fmt_time(sync_state.get('target_start_time'), time_formatter)}")
    return " · ".join(parts)


def _render_collection_overview(
    data: Dict[str, Any],
    time_formatter: Optional[Callable[[Optional[str]], str]] = None,
) -> str:
    sync_hint = _collection_sync_hint(data, time_formatter)
    hint_html = f"<div class='hint'>{sync_hint}</div>" if sync_hint else ""
    return (
        "<section class='workspace-block collection-overview'>"
        "<h3>Collection overview</h3>"
        f"{hint_html}"
        "<div class='collection-kpis'>"
        f"{_collection_metric('Collection adds', data.get('total_collection_adds', 0), 'Detected additions')}"
        f"{_collection_metric('Affected posts', data.get('affected_posts', 0), 'Mapped to local posts')}"
        f"{_collection_metric('Affected images', data.get('affected_images', 0), 'Unique images collected')}"
        f"{_collection_metric('Image-only events', data.get('unmapped_events', 0), 'Not mapped to a local post yet')}"
        f"{_collection_metric('Last event', _fmt_time(data.get('last_collection_event'), time_formatter), 'Latest collection add')}"
        "</div>"
        "</section>"
    )


def _collection_event_target(item: Dict[str, Any], view_host: str) -> str:
    post_id = item.get("post_id")
    if post_id not in (None, ""):
        return _post_cell(view_host, post_id, item.get("title"), item.get("image_id"))
    return f"{_image_link(view_host, item.get('image_id'))}<div class='row-sub'>Post mapping not found locally</div>"


def _collection_image_post_summary(item: Dict[str, Any], view_host: str) -> str:
    post_id = item.get("post_id")
    if post_id in (None, ""):
        return "<div class='row-sub'>Post mapping not found locally</div>"
    title_text = html.escape(str(item.get("title") or "Untitled post"))
    return f"<div class='row-sub'>{_post_link(view_host, int(post_id))} · {title_text}</div>"


def _collection_drawer_id(prefix: str, item: Dict[str, Any], index: int) -> str:
    image_id = item.get("image_id") or "image"
    post_id = item.get("post_id") or "unmapped"
    return f"{prefix}-{index}-{image_id}-{post_id}".replace(" ", "-")


def _drawer_stat(label: str, value: Any, detail: str = "") -> str:
    detail_html = f"<div class='drawer-stat-detail'>{html.escape(detail)}</div>" if detail else ""
    return (
        "<div class='drawer-stat'>"
        f"<div class='drawer-stat-label'>{html.escape(label)}</div>"
        f"<div class='drawer-stat-value'>{_fmt(value)}</div>"
        f"{detail_html}"
        "</div>"
    )


def _collection_detail_template(
    drawer_id: str,
    item: Dict[str, Any],
    view_host: str,
    time_formatter: Optional[Callable[[Optional[str]], str]],
    context: str,
) -> str:
    image_id = item.get("image_id")
    post_id = item.get("post_id")
    mapped = post_id not in (None, "")
    title = str(item.get("title") or ("Mapped collection event" if mapped else "Image-only collection event"))
    collection_adds = item.get("collection_adds", 1)
    event_time = item.get("last_event_time") or item.get("event_time")
    mapping = "Mapped to a local post" if mapped else "Post mapping not found locally"
    links = [_image_cell(view_host, image_id)]
    if mapped:
        links.append(_post_link(view_host, int(post_id)))
    stats = "".join(
        [
            _drawer_stat("Collection adds", collection_adds, context),
            _drawer_stat("Image", f"#{image_id}" if image_id not in (None, "") else "—"),
            _drawer_stat("Mapping", "Mapped" if mapped else "Image-only", mapping),
            _drawer_stat("Last add", _fmt_time(event_time, time_formatter)),
        ]
    )
    return (
        f"<template data-collection-detail-template='{html.escape(drawer_id, quote=True)}'>"
        "<div class='drawer-hero'>"
        f"{_image_preview(view_host, image_id, item.get('thumbnail_url'), item.get('image_url'), 'drawer-preview')}"
        "<div>"
        f"<h2 id='post-drawer-title'>{html.escape(title)}</h2>"
        f"<div class='drawer-links'>{' · '.join(links)}</div>"
        f"<div class='drawer-muted'>{html.escape(mapping)} · {html.escape(_fmt_time(event_time, time_formatter))}</div>"
        "</div>"
        "</div>"
        f"<div class='drawer-stats'>{stats}</div>"
        "<div class='drawer-section'>"
        "<h3>Collection context</h3>"
        "<div class='drawer-muted'>This view uses image and post identifiers only. It does not expose who added the image to a collection.</div>"
        "</div>"
        "</template>"
    )


def _render_collection_activity_board(
    data: Dict[str, Any],
    view_host: str,
    time_formatter: Optional[Callable[[Optional[str]], str]] = None,
    templates: Optional[List[str]] = None,
) -> str:
    recent_items = list(data.get("recent_collection_adds", []))
    top_posts = list(data.get("top_posts_by_collection_adds", []))

    if not recent_items and not top_posts:
        return _collection_empty_state(
            "No collection activity yet",
            "Run the tracker with collection tracking enabled after your images are added to collections.",
        )

    event_cards: List[str] = []
    for idx, item in enumerate(recent_items[:12], start=1):
        image_only = item.get("post_id") in (None, "")
        attrs = _period_attrs(item.get("event_time"))
        drawer_id = _collection_drawer_id("collection-event", item, idx)
        if templates is not None:
            templates.append(_collection_detail_template(drawer_id, item, view_host, time_formatter, "Recent event"))
        card_class = "collection-event-card collection-image-only" if image_only else "collection-event-card"
        status = "Image-only event" if image_only else "Mapped to local post"
        event_cards.append(
            f"<article class='{card_class}' data-workspace-row data-active-row='1' data-collection-detail-id='{html.escape(drawer_id, quote=True)}' {attrs}>"
            f"{_image_preview_cell(view_host, item.get('image_id'), item.get('thumbnail_url'), item.get('image_url'))}"
            "<div class='collection-event-body'>"
            f"<div class='collection-event-time'>{_fmt_time(item.get('event_time'), time_formatter)}</div>"
            f"<div class='collection-event-target'>{_collection_event_target(item, view_host)}</div>"
            "</div>"
            f"<div class='collection-event-status'>{html.escape(status)}</div>"
            "</article>"
        )

    rank_rows: List[str] = []
    for idx, item in enumerate(top_posts[:8], start=1):
        attrs = _period_attrs(item.get("last_event_time"))
        rank_rows.append(
            f"<article class='collection-rank-row' data-workspace-row data-active-row='1' {attrs}>"
            f"<div class='collection-rank-num'>{idx}</div>"
            f"<div class='collection-rank-main'>{_post_cell(view_host, item.get('post_id'), item.get('title'))}</div>"
            "<div class='collection-rank-score'>"
            f"<strong>{_fmt(item.get('collection_adds'))}</strong>"
            f"<span>{_fmt(item.get('distinct_images_affected'))} images</span>"
            "</div>"
            "</article>"
        )

    flow_html = "".join(event_cards) if event_cards else "<div class='collection-mini-empty'>No recent collection events.</div>"
    rank_html = "".join(rank_rows) if rank_rows else "<div class='collection-mini-empty'>No mapped posts yet.</div>"

    return (
        "<section class='workspace-block collection-board-block'>"
        "<h3>Collection board</h3>"
        "<div class='hint'>Recent adds and strongest affected posts without the full table wall.</div>"
        "<div class='collection-board-grid'>"
        "<div class='collection-card-panel'>"
        "<div class='collection-panel-head'><h4>Recent collection flow</h4><span>Latest detected adds</span></div>"
        f"<div class='collection-flow-list'>{flow_html}</div>"
        "</div>"
        "<div class='collection-card-panel'>"
        "<div class='collection-panel-head'><h4>Top affected posts</h4><span>Ranked by collection adds</span></div>"
        f"<div class='collection-rank-list'>{rank_html}</div>"
        "</div>"
        "</div>"
        "</section>"
    )


def _render_collection_image_grid(
    data: Dict[str, Any],
    view_host: str,
    time_formatter: Optional[Callable[[Optional[str]], str]] = None,
    templates: Optional[List[str]] = None,
) -> str:
    image_cards: List[str] = []
    for idx, item in enumerate(data.get("top_images_by_collection_adds", [])[:12], start=1):
        attrs = _period_attrs(item.get("last_event_time"))
        drawer_id = _collection_drawer_id("collection-image", item, idx)
        if templates is not None:
            templates.append(_collection_detail_template(drawer_id, item, view_host, time_formatter, "Top image"))
        image_cards.append(
            f"<article class='collection-image-card' data-workspace-row data-active-row='1' data-collection-detail-id='{html.escape(drawer_id, quote=True)}' {attrs}>"
            f"{_image_preview_cell(view_host, item.get('image_id'), item.get('thumbnail_url'), item.get('image_url'))}"
            "<div class='collection-image-body'>"
            f"<div class='collection-image-score'>{_fmt(item.get('collection_adds'))} adds</div>"
            f"<div class='collection-image-link'>{_image_cell(view_host, item.get('image_id'))}</div>"
            f"{_collection_image_post_summary(item, view_host)}"
            f"<div class='collection-image-time'>Last add: {_fmt_time(item.get('last_event_time'), time_formatter)}</div>"
            "</div>"
            "</article>"
        )

    if not image_cards:
        return ""

    return (
        "<section class='workspace-block collection-image-grid-block'>"
        "<h3>Top collected images</h3>"
        "<div class='hint'>Image-level view for spotting which specific previews are being saved most often.</div>"
        f"<div class='collection-image-grid'>{''.join(image_cards)}</div>"
        "</section>"
    )


def _render_collection_image_only_queue(
    data: Dict[str, Any],
    view_host: str,
    time_formatter: Optional[Callable[[Optional[str]], str]] = None,
    templates: Optional[List[str]] = None,
) -> str:
    image_only_items = [item for item in data.get("recent_collection_adds", []) if item.get("post_id") in (None, "")]
    if not image_only_items:
        return ""

    cards: List[str] = []
    for idx, item in enumerate(image_only_items[:10], start=1):
        attrs = _period_attrs(item.get("event_time"))
        drawer_id = _collection_drawer_id("collection-queue", item, idx)
        if templates is not None:
            templates.append(_collection_detail_template(drawer_id, item, view_host, time_formatter, "Image-only event"))
        cards.append(
            f"<article class='collection-queue-card' data-workspace-row data-active-row='1' data-collection-detail-id='{html.escape(drawer_id, quote=True)}' {attrs}>"
            f"{_image_preview_cell(view_host, item.get('image_id'), item.get('thumbnail_url'), item.get('image_url'))}"
            "<div>"
            f"<div class='collection-image-link'>{_image_cell(view_host, item.get('image_id'))}</div>"
            "<div class='row-sub'>Post mapping not found locally</div>"
            f"<div class='collection-image-time'>{_fmt_time(item.get('event_time'), time_formatter)}</div>"
            "</div>"
            "</article>"
        )

    return (
        "<section class='workspace-block collection-queue-block'>"
        "<h3>Image-only queue</h3>"
        "<div class='hint'>Known image events that are not mapped to a locally tracked post yet.</div>"
        f"<div class='collection-queue-grid'>{''.join(cards)}</div>"
        "</section>"
    )


def _render_collection_workspace(
    data: Dict[str, Any],
    view_host: str,
    time_formatter: Optional[Callable[[Optional[str]], str]] = None,
) -> str:
    templates: List[str] = []
    body = "".join(
        [
            _render_collection_overview(data, time_formatter),
            _render_collection_image_grid(data, view_host, time_formatter, templates),
            _render_collection_activity_board(data, view_host, time_formatter, templates),
            _render_collection_image_only_queue(data, view_host, time_formatter, templates),
        ]
    )
    if templates:
        body += f"<div hidden>{''.join(templates)}</div>"
    return body


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
        parts.append(_render_collection_workspace(data, view_host, time_formatter))

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

    return _render_collection_workspace(data, view_host, time_formatter)


COLLECTION_SECTION_CSS = """
<style>
.collection-overview{overflow:visible}
.collection-kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:12px;margin-top:12px}
.collection-kpi{border:1px solid var(--border);background:#0d1528;border-radius:12px;padding:14px;min-height:112px}
.collection-kpi-label{font-size:11px;text-transform:uppercase;letter-spacing:.06em;color:var(--muted);margin-bottom:8px}
.collection-kpi-value{font-size:22px;font-weight:800;line-height:1.2}
.collection-kpi-detail{margin-top:8px;color:var(--muted);font-size:12px;line-height:1.35}
.collection-empty{border:1px dashed var(--border);background:#0d1528;border-radius:12px;padding:16px;margin:16px 0;color:var(--muted);display:flex;flex-direction:column;gap:6px}
.collection-empty strong{color:var(--text);font-size:16px}
.collection-board-grid{display:grid;grid-template-columns:minmax(0,1.2fr) minmax(320px,.8fr);gap:16px;margin-top:14px}
.collection-card-panel{border:1px solid var(--border);background:#0d1528;border-radius:14px;padding:14px;min-width:0}
.collection-panel-head{display:flex;justify-content:space-between;gap:12px;align-items:flex-start;margin-bottom:12px}
.collection-panel-head h4{margin:0;font-size:16px}.collection-panel-head span{color:var(--muted);font-size:12px;text-align:right}
.collection-flow-list,.collection-rank-list{display:flex;flex-direction:column;gap:10px}
.collection-event-card{display:grid;grid-template-columns:68px minmax(0,1fr) auto;gap:12px;align-items:center;border:1px solid #263353;background:#10182b;border-radius:12px;padding:10px;min-width:0}
.collection-event-card.collection-image-only{border-style:dashed}
.collection-event-card[data-collection-detail-id],.collection-image-card[data-collection-detail-id],.collection-queue-card[data-collection-detail-id]{cursor:pointer}
.collection-event-card[data-collection-detail-id]:hover,.collection-image-card[data-collection-detail-id]:hover,.collection-queue-card[data-collection-detail-id]:hover{border-color:#45629c;background:#121c33}
.collection-event-card .preview-link,.collection-image-card .preview-link,.collection-queue-card .preview-link{width:64px;height:64px}
.collection-event-card .post-thumb,.collection-image-card .post-thumb,.collection-queue-card .post-thumb{width:64px;height:64px;flex-basis:64px}
.collection-event-body,.collection-rank-main,.collection-image-body{min-width:0}.collection-event-time,.collection-image-time{color:var(--muted);font-size:12px;margin-bottom:5px}.collection-event-target{font-size:14px}
.collection-event-status{justify-self:end;border:1px solid var(--border);border-radius:999px;padding:5px 8px;color:var(--muted);font-size:11px;white-space:nowrap;background:#0d1528}
.collection-rank-row{display:grid;grid-template-columns:34px minmax(0,1fr) minmax(86px,auto);gap:10px;align-items:center;border:1px solid #263353;background:#10182b;border-radius:12px;padding:10px}
.collection-rank-num{width:28px;height:28px;border-radius:999px;display:inline-flex;align-items:center;justify-content:center;background:#17233f;color:var(--accent);font-weight:800}
.collection-rank-score{text-align:right}.collection-rank-score strong{display:block;font-size:20px}.collection-rank-score span{display:block;color:var(--muted);font-size:11px;margin-top:2px}
.collection-image-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(210px,1fr));gap:12px;margin-top:14px}
.collection-image-card{display:grid;grid-template-columns:68px minmax(0,1fr);gap:12px;align-items:start;border:1px solid var(--border);background:#0d1528;border-radius:14px;padding:12px;min-width:0}
.collection-image-score{font-size:18px;font-weight:800;margin-bottom:4px}.collection-image-link{font-size:13px;margin-bottom:4px}.collection-image-body .row-sub{font-size:11px}
.collection-queue-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:12px;margin-top:14px}
.collection-queue-card{display:grid;grid-template-columns:68px minmax(0,1fr);gap:12px;align-items:center;border:1px dashed var(--border);background:#0d1528;border-radius:14px;padding:12px}
.collection-mini-empty{border:1px dashed var(--border);border-radius:12px;padding:12px;color:var(--muted);font-size:13px}
@media (max-width:900px){.collection-board-grid{grid-template-columns:1fr}.collection-event-card{grid-template-columns:68px minmax(0,1fr)}.collection-event-status{grid-column:2;justify-self:start}.collection-panel-head{flex-direction:column}.collection-panel-head span{text-align:left}}
</style>
"""
