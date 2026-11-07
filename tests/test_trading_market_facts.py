from __future__ import annotations

import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd

from shilun.trading.market_facts import (
    MAX_TREND_REQUESTS_PER_MINUTE,
    ExecutionMonitorItem,
    ExecutionMonitorPool,
    aggregate_sector_resonance,
    intraday_average_from_quote,
    latest_intraday_trend_snapshot,
    trend_target_tickers,
)
from shilun.trading.price_limits import build_price_limit_facts


MARKET_TZ = ZoneInfo("Asia/Shanghai")
NOW = datetime(2026, 7, 13, 10, 17, tzinfo=MARKET_TZ)


class TradingMarketFactsTests(unittest.TestCase):
    def test_realtime_turnover_builds_stock_intraday_average_and_rejects_index_units(self) -> None:
        average = intraday_average_from_quote({
            "current": 75.95,
            "amount": 18_228_549.05247,
            "amount_unit": "thousand_cny",
            "volume": 240_507_456,
        })
        rejected = intraday_average_from_quote({
            "current": 3882.41,
            "amount": 1_124_208_166.21,
            "amount_unit": "thousand_cny",
            "volume": 53_528_187_300,
        })

        self.assertAlmostEqual(average, 75.792033, places=6)
        self.assertIsNone(rejected)

    def test_trend_quota_covers_eight_samples_for_six_priority_sectors(self) -> None:
        sectors = tuple(f"板块{index}" for index in range(6))
        samples = {
            sector: tuple(
                {"ticker": f"{sector_index + 1}{item_index:05d}.SZ", "role": "industry_peer"}
                for item_index in range(12)
            )
            for sector_index, sector in enumerate(sectors)
        }
        tickers = [row["ticker"] for rows in samples.values() for row in rows]
        pool = ExecutionMonitorPool(
            items=tuple(
                ExecutionMonitorItem(ticker=ticker, priority=1, sources=("sector_sample",))
                for ticker in tickers
            ),
            sector_samples=samples,
            holding_tickers=(tickers[0],),
            priority_sectors=sectors,
        )

        selected = set(trend_target_tickers(pool))

        self.assertEqual(len(selected), MAX_TREND_REQUESTS_PER_MINUTE)
        self.assertTrue(all(len({row["ticker"] for row in rows} & selected) >= 8 for rows in samples.values()))

    def test_latest_trend_snapshot_matches_source_average_price(self) -> None:
        frame = pd.DataFrame([
            {
                "datetime": pd.Timestamp("2026-07-13T10:15:00+08:00"),
                "price": 10.2,
                "avg_price": 10.08,
                "price_unit": "cny_per_share",
                "source": "klineshare",
            }
        ])

        result = latest_intraday_trend_snapshot(
            frame,
            ticker="000001.SZ",
            trade_date="20260713",
            fetched_at=NOW,
        )

        self.assertEqual(result["avg_price"], 10.08)
        self.assertEqual(result["price_unit"], "cny_per_share")

    def test_trend_snapshot_rejects_unit_scale_mismatch(self) -> None:
        frame = pd.DataFrame([{
            "datetime": pd.Timestamp("2026-07-13T10:15:00+08:00"),
            "price": 10.2,
            "avg_price": 10_080,
        }])
        with self.assertRaisesRegex(ValueError, "单位不一致"):
            latest_intraday_trend_snapshot(
                frame,
                ticker="000001.SZ",
                trade_date="20260713",
                fetched_at=NOW,
            )

    def test_sector_insufficient_coverage_has_no_directional_conclusion(self) -> None:
        samples = {"半导体": tuple(
            {"ticker": f"0000{i:02d}.SZ", "role": "top20"}
            for i in range(10)
        )}
        quotes = {
            "000001.SH": {"current": 101, "prev_close": 100},
            **{
                f"0000{i:02d}.SZ": {"current": 10.2, "prev_close": 10}
                for i in range(5)
            },
        }
        trends = {
            f"0000{i:02d}.SZ": {"avg_price": 10.1}
            for i in range(5)
        }

        fact = aggregate_sector_resonance(
            samples,
            quotes=quotes,
            trends=trends,
            trade_date="20260713",
            computed_at=NOW,
        )[0]

        self.assertEqual(fact["state"], "unavailable")
        self.assertIsNone(fact["directional_conclusion"])
        self.assertEqual(fact["valid_sample_count"], 5)

    def test_sector_recovery_requires_core_strengthening(self) -> None:
        samples = {"半导体": tuple([
            {"ticker": "600000.SH", "role": "leader"},
            *(
                {"ticker": f"6000{i:02d}.SH", "role": "top20"}
                for i in range(1, 8)
            ),
        ])}
        quotes = {
            "000001.SH": {"current": 100.2, "prev_close": 100},
            **{
                item["ticker"]: {"current": 10.5, "prev_close": 10}
                for item in samples["半导体"]
            },
        }
        trends = {item["ticker"]: {"avg_price": 10.2} for item in samples["半导体"]}

        fact = aggregate_sector_resonance(
            samples,
            quotes=quotes,
            trends=trends,
            trade_date="20260713",
            computed_at=NOW,
        )[0]

        self.assertEqual(fact["state"], "recovery_resonance")
        self.assertEqual(fact["directional_conclusion"], "positive")
        self.assertTrue(fact["core_strengthening"])

    def test_price_limit_facts_detect_open_and_coarse_reseal(self) -> None:
        fact = build_price_limit_facts(
            trade_date="20260713",
            ticker="000001.SZ",
            limit_document={"pre_close": 10, "up_limit": 11, "down_limit": 9},
            quote={"current": 11, "high": 11, "low": 10.1},
            closed_5m_bars=[
                {"datetime": "2026-07-13T09:35:00+08:00", "high": 11, "low": 10, "close": 10.8},
                {"datetime": "2026-07-13T09:40:00+08:00", "high": 11, "low": 10.7, "close": 11},
            ],
            computed_at=NOW,
        )

        self.assertTrue(fact["up_limit_touched"])
        self.assertTrue(fact["up_limit_opened"])
        self.assertTrue(fact["up_limit_resealed"])
        self.assertTrue(fact["up_limit_sealed"])
        self.assertEqual(fact["precision"], "coarse_5m")

    def test_trend_quota_constant_is_hard_capped(self) -> None:
        self.assertEqual(MAX_TREND_REQUESTS_PER_MINUTE, 60)


if __name__ == "__main__":
    unittest.main()
