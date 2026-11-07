from __future__ import annotations

import unittest

from shilun.trading import (
    build_p0_source_snapshots,
    minute_bars_snapshot,
    quote_snapshot,
)


class TradingSourceAdapterTests(unittest.TestCase):
    def test_quote_uses_source_time_not_fetch_time(self) -> None:
        snapshot = quote_snapshot("stock_quote", {
            "current": 612.0,
            "quote_at": "2026-07-10T15:00:00+08:00",
            "fetched_at": "2026-07-11T18:00:00+08:00",
        })
        self.assertEqual(snapshot.as_of.isoformat(), "2026-07-10T15:00:00+08:00")

    def test_quote_without_aware_source_time_is_missing(self) -> None:
        snapshot = quote_snapshot("stock_quote", {
            "current": 612.0,
            "update_at": "2026-07-11T18:00:00",
        })
        self.assertIsNone(snapshot.as_of)

    def test_minute_bars_assume_exchange_timezone(self) -> None:
        snapshot = minute_bars_snapshot("stock_5m", [
            {"datetime": "2026-07-10T09:35:00", "close": 610.0},
            {"datetime": "2026-07-10T09:40:00", "close": 612.0},
        ])
        self.assertEqual(snapshot.as_of.isoformat(), "2026-07-10T09:40:00+08:00")

    def test_daily_sector_snapshot_is_not_treated_as_realtime(self) -> None:
        snapshots = build_p0_source_snapshots(
            stock_quote={"quote_at": "2026-07-10T10:00:00+08:00"},
            stock_5m_bars=[{"datetime": "2026-07-10T10:00:00", "close": 612.0}],
            sse_quote={"quote_at": "2026-07-10T10:00:00+08:00"},
            chinext_quote={"quote_at": "2026-07-10T10:00:00+08:00"},
            sector_quote={"snapshot_date": "20260710", "pct_chg": 1.2},
        )
        sector = next(item for item in snapshots if item.source == "sector_quote")
        self.assertIsNone(sector.as_of)


if __name__ == "__main__":
    unittest.main()
