"""阶段 7 Job 3：daily-check 与持仓事件历史 API 测试。"""

from __future__ import annotations

import unittest
from unittest.mock import patch

from fastapi import HTTPException

from shilun.api import app, daily_check_holdings_endpoint, get_holding_events_endpoint
from shilun.common.config import AppConfig
from shilun.market import PART1_ENGINE_VERSION, SECTOR_ENGINE_VERSION
from shilun.market.holdings import HoldingRecord, upsert_holding
from tests.test_holding_events import FakeStore, _bars


def _patch_config():
    return patch(
        "shilun.api.load_config",
        return_value=AppConfig(mongo_uri="mongodb://fake", mongo_db="fake"),
    )


def _patch_store(store: FakeStore):
    return patch("shilun.common.db.MongoSnapshotStore", return_value=store)


class HoldingEventsApiTest(unittest.TestCase):
    def setUp(self) -> None:
        self.store = FakeStore(_bars([10.0 + index * 0.1 for index in range(25)]))
        upsert_holding(
            self.store.collection("holdings"),
            HoldingRecord(
                ticker="603986.SH",
                name="兆易创新",
                sector_name="半导体",
                entry_date="2026-06-20",
                entry_price=10.0,
                entry_signal="ma5_pullback_confirm",
                entry_size=0.3,
                signal_candle_open=9.5,
            ),
        )
        self.store.collection("market_part1_cache").update_one(
            {"analysis_date": "2026-07-10", "benchmark_ticker": "000001.SH"},
            {
                "$set": {
                    "analysis_date": "2026-07-10",
                    "benchmark_ticker": "000001.SH",
                    "engine_version": PART1_ENGINE_VERSION,
                    "payload": {"market_gate": {"permission": "hold"}},
                }
            },
            upsert=True,
        )
        self.store.collection("sector_trends_cache").update_one(
            {
                "analysis_date": "2026-07-10",
                "benchmark_ticker": "000001.SH",
                "trend_lookback_days": 60,
                "engine_version": SECTOR_ENGINE_VERSION,
            },
            {
                "$set": {
                    "analysis_date": "2026-07-10",
                    "benchmark_ticker": "000001.SH",
                    "trend_lookback_days": 60,
                    "engine_version": SECTOR_ENGINE_VERSION,
                    "payload": {
                        "top_sectors": [
                            {"sector_name": "半导体", "sector_state": "mainline_top3"}
                        ]
                    },
                }
            },
            upsert=True,
        )
        self.config_patch = _patch_config()
        self.store_patch = _patch_store(self.store)
        self.config_patch.start()
        self.store_patch.start()

    def tearDown(self) -> None:
        self.store_patch.stop()
        self.config_patch.stop()

    def test_routes_are_registered(self) -> None:
        routes = {
            (route.path, method)
            for route in app.routes
            for method in (getattr(route, "methods", None) or set())
        }
        self.assertIn(("/api/v1/holdings/daily-check", "POST"), routes)
        self.assertIn(("/api/v1/holdings/{ticker}/events", "GET"), routes)

    def test_daily_check_uses_market_and_sector_context(self) -> None:
        result = daily_check_holdings_endpoint(date="2026-07-10")
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["market_permission"], "hold")
        self.assertEqual(result["sector_context_count"], 1)
        self.assertEqual(result["inserted"], 1)
        self.assertEqual(result["state_counts"], {"strong_hold": 1})
        self.assertEqual(self.store.close_count, 1)

    def test_rerun_updates_same_daily_event(self) -> None:
        first = daily_check_holdings_endpoint(date="2026-07-10")
        second = daily_check_holdings_endpoint(date="2026-07-10")
        self.assertEqual(first["inserted"], 1)
        self.assertEqual(second["updated"], 1)
        self.assertEqual(len(self.store.collection("holding_events")._docs), 1)

    def test_event_history_endpoint(self) -> None:
        daily_check_holdings_endpoint(date="2026-07-10")
        result = get_holding_events_endpoint("603986.sh", limit=10)
        self.assertEqual(result["ticker"], "603986.SH")
        self.assertEqual(result["count"], 1)
        self.assertEqual(result["events"][0]["holding_state"], "strong_hold")

    def test_event_history_validates_date_range_and_limit(self) -> None:
        with self.assertRaises(HTTPException) as range_ctx:
            get_holding_events_endpoint(
                "603986.SH",
                start_date="2026-07-11",
                end_date="2026-07-10",
            )
        self.assertEqual(range_ctx.exception.status_code, 400)
        with self.assertRaises(HTTPException) as limit_ctx:
            get_holding_events_endpoint("603986.SH", limit=500)
        self.assertEqual(limit_ctx.exception.status_code, 400)


if __name__ == "__main__":
    unittest.main()
