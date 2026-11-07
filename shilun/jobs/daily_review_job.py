"""Post-close materialization job for the facts-first review dashboard."""

from __future__ import annotations

import argparse

from shilun.common.config import load_config
from shilun.common.db import MongoSnapshotStore
from shilun.review.service import DailyReviewService, REVIEW_SOCKET_TIMEOUT_MS


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Materialize one Shilun daily review snapshot")
    parser.add_argument("--date", default="", help="YYYY-MM-DD; defaults to latest market_daily_bars date")
    parser.add_argument("--skip-source-sync", action="store_true", help="Only rebuild from local evidence; no external requests")
    args = parser.parse_args(argv)
    config = load_config()
    if not config.mongo_uri:
        raise RuntimeError("SHILUN_MONGO_URI not set")
    store = MongoSnapshotStore(config.mongo_uri, config.mongo_db, socket_timeout_ms=REVIEW_SOCKET_TIMEOUT_MS)
    try:
        from shilun.review.strong_stock_data import materialize, resolve_date
        strong_stocks = materialize(store, resolve_date(store, args.date or None))
        payload = DailyReviewService(store).build_and_save(strong_stocks['date'], sync_sources=not args.skip_source_sync)
    finally:
        store.close()
    action = payload.get("action") or {}
    sentiment = payload.get("sentiment") or {}
    print(
        "daily_review "
        f"date={payload.get('analysis_date')} headline={action.get('headline')} "
        f"limit_up={sentiment.get('limit_up_count')} "
        f"limit_down={sentiment.get('limit_down_count')} "
        f"strong_qualified={strong_stocks['counts'].get('qualified', 0)} "
        f"generated_at={payload.get('generated_at')}",
        flush=True,
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())


__all__ = ["main"]
