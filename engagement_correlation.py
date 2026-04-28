from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, Dict


def ensure_b2_2_indexes(db_path: str) -> None:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.cursor()
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_content_engagement_events_target_id
            ON content_engagement_events(target_id)
            """
        )
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_content_engagement_events_related_post_id
            ON content_engagement_events(related_post_id)
            """
        )
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_content_engagement_events_related_image_id
            ON content_engagement_events(related_image_id)
            """
        )
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_post_images_image_id
            ON post_images(image_id)
            """
        )
        conn.commit()
    finally:
        conn.close()


def run_b2_2_correlation(db_path: str) -> Dict[str, Any]:
    """
    Correlates incoming engagement events with already tracked images/posts.

    Strategy:
    - Only image-target events are correlated in B2.2.
    - target_id from content_engagement_events is matched to post_images.image_id.
    - related_image_id / related_post_id are filled where a match exists.
    """
    ensure_b2_2_indexes(db_path)

    conn = sqlite3.connect(db_path)
    try:
        cur = conn.cursor()

        total_before = cur.execute(
            "SELECT COUNT(*) FROM content_engagement_events"
        ).fetchone()[0]

        unresolved_before = cur.execute(
            """
            SELECT COUNT(*)
            FROM content_engagement_events
            WHERE target_type_candidate = 'image'
              AND target_id IS NOT NULL
              AND (related_image_id IS NULL OR related_post_id IS NULL)
            """
        ).fetchone()[0]

        matched_candidates = cur.execute(
            """
            SELECT COUNT(*)
            FROM content_engagement_events cee
            JOIN post_images pi
              ON pi.image_id = cee.target_id
            WHERE cee.target_type_candidate = 'image'
              AND cee.target_id IS NOT NULL
            """
        ).fetchone()[0]

        cur.execute(
            """
            UPDATE content_engagement_events
            SET
                related_image_id = (
                    SELECT pi.image_id
                    FROM post_images pi
                    WHERE pi.image_id = content_engagement_events.target_id
                    LIMIT 1
                ),
                related_post_id = (
                    SELECT pi.post_id
                    FROM post_images pi
                    WHERE pi.image_id = content_engagement_events.target_id
                    LIMIT 1
                )
            WHERE target_type_candidate = 'image'
              AND target_id IS NOT NULL
              AND EXISTS (
                  SELECT 1
                  FROM post_images pi
                  WHERE pi.image_id = content_engagement_events.target_id
              )
              AND (
                  related_image_id IS NULL
                  OR related_post_id IS NULL
                  OR related_image_id != target_id
              )
            """
        )
        linked_rows_changed = cur.rowcount if cur.rowcount is not None else 0
        conn.commit()

        correlated_after = cur.execute(
            """
            SELECT COUNT(*)
            FROM content_engagement_events
            WHERE related_image_id IS NOT NULL
            """
        ).fetchone()[0]

        unresolved_after = cur.execute(
            """
            SELECT COUNT(*)
            FROM content_engagement_events
            WHERE target_type_candidate = 'image'
              AND target_id IS NOT NULL
              AND (related_image_id IS NULL OR related_post_id IS NULL)
            """
        ).fetchone()[0]

        distinct_images_correlated = cur.execute(
            """
            SELECT COUNT(DISTINCT related_image_id)
            FROM content_engagement_events
            WHERE related_image_id IS NOT NULL
            """
        ).fetchone()[0]

        distinct_posts_correlated = cur.execute(
            """
            SELECT COUNT(DISTINCT related_post_id)
            FROM content_engagement_events
            WHERE related_post_id IS NOT NULL
            """
        ).fetchone()[0]

        return {
            "ok": True,
            "total_events": int(total_before or 0),
            "image_match_candidates": int(matched_candidates or 0),
            "rows_linked_changed": int(linked_rows_changed or 0),
            "correlated_events_total": int(correlated_after or 0),
            "unresolved_before": int(unresolved_before or 0),
            "unresolved_after": int(unresolved_after or 0),
            "distinct_images_correlated": int(distinct_images_correlated or 0),
            "distinct_posts_correlated": int(distinct_posts_correlated or 0),
        }
    except Exception as exc:
        return {
            "ok": False,
            "error": str(exc),
            "total_events": 0,
            "image_match_candidates": 0,
            "rows_linked_changed": 0,
            "correlated_events_total": 0,
            "unresolved_before": 0,
            "unresolved_after": 0,
            "distinct_images_correlated": 0,
            "distinct_posts_correlated": 0,
        }
    finally:
        conn.close()
