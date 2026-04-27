from __future__ import annotations

import sys

from tracker_service import (
    DEFAULT_TIMEOUT,
    parse_args,
    refresh_dashboard_from_config,
    run_collection_once,
    run_from_config,
)


def main() -> int:
    args = parse_args()
    result = run_collection_once(config_path=args.config, timeout=args.timeout)
    if not result.get("ok"):
        print(f"Error: {result.get('error', 'Unknown error')}", file=sys.stderr)
        return 1

    print(
        "Done. "
        f"host={result.get('selected_host', '')} "
        f"tracked_posts={result.get('tracked_posts', 0)} "
        f"changed_posts={result.get('changed_posts', 0)} "
        f"known_totals={result.get('known_totals', 0)} "
        f"images={result.get('image_rows', 0)} ({result.get('image_source', 'n/a')})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
