"""Job 8：`POST /api/v1/data/precompute-breakout-events` 端点集成测试。

不起 FastAPI，直接调用 `precompute_breakout_events_endpoint` 函数；用 patch
把 `MongoSnapshotStore` 换成 FakeStore（Job 4 的 test 已经用过同一套桩），
验证 skip_detect / skip_backfill / 正常路径等分支。
"""

from __future__ import annotations

import unittest
from unittest.mock import patch

from tests.test_breakout_events import (
    FakeCollection,
    FakeRawMarket,
    FakeStore,
)
from shilun.api import precompute_breakout_events_endpoint
from shilun.common.config import AppConfig


class _StoreHolder:
    """把一个共享的 FakeStore 实例通过 MongoSnapshotStore(...) 构造函数交出去。"""

    def __init__(self, store: FakeStore) -> None:
        self.store = store

    def __call__(self, *_args, **_kwargs) -> FakeStore:
        return self.store


def _patch_store(store: FakeStore):
    """把 shilun.common.db.MongoSnapshotStore patch 成返回 store 的桩。"""
    return patch(
        "shilun.common.db.MongoSnapshotStore",
        side_effect=_StoreHolder(store),
    )


def _patch_config():
    """load_config() 返回一个带 mongo_uri 的假配置。"""
    return patch(
        "shilun.api.load_config",
        return_value=AppConfig(mongo_uri="mongodb://fake", mongo_db="fake"),
    )


class PrecomputeBreakoutEventsEndpointTests(unittest.TestCase):
    def _make_store(self, bars: list[dict] | None = None) -> FakeStore:
        col = FakeCollection()
        # 让 store.close() 存在
        store = FakeStore(col, FakeRawMarket(bars or []))
        store.close = lambda: None
        # 让 store.collection("breakout_events") 走 FakeStore.collection，但也允许
        # precompute_breakout_events 里的 store.raw_market.find_stock_basic
        store.raw_market.find_stock_basic = lambda: []
        return store

    def test_skip_both_returns_no_op(self) -> None:
        store = self._make_store()
        with _patch_config(), _patch_store(store):
            result = precompute_breakout_events_endpoint(
                date="2026-06-25",
                skip_detect=True,
                skip_backfill=True,
            )
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["analysis_date"], "2026-06-25")
        self.assertIsNone(result["detect"])
        self.assertIsNone(result["backfill"])
        self.assertIn("已跳过检测", result["message"])
        self.assertIn("已跳过回填", result["message"])

    def test_skip_detect_still_runs_backfill(self) -> None:
        """只跑回填，且 backfill 结果字段完整。"""
        from shilun.market.breakout_events import BreakoutBaseline, upsert_breakout_event
        store = self._make_store()
        # 预先塞一条 pending 事件，验证 backfill 会扫到
        upsert_breakout_event(
            store.collection("breakout_events"),
            BreakoutBaseline(
                ticker="600519.SH",
                breakout_date="2026-06-20",
                breakout_close=100.0, breakout_ma5=98.0,
                breakout_volume=1_000_000, breakout_volume_ratio=1.8,
                previous_high_20=99.0, previous_high_60=105.0,
                box_upper=99.0, close_position=0.85, real_body_ratio=0.7,
            ),
        )
        # 塞一根 T+1 日线
        store.raw_market._bars = [{
            "ticker": "600519.SH", "date": "2026-06-23",
            "close": 101.0, "low": 99.5, "high": 101.5, "volume": 700_000,
        }]

        with _patch_config(), _patch_store(store):
            result = precompute_breakout_events_endpoint(
                date="2026-06-25",
                skip_detect=True,
            )
        self.assertIsNone(result["detect"])
        self.assertIsNotNone(result["backfill"])
        self.assertEqual(result["backfill"]["scanned"], 1)
        self.assertEqual(result["backfill"]["appended"], 1)
        self.assertIn("回填", result["message"])

    def test_returns_400_when_mongo_not_configured(self) -> None:
        from fastapi import HTTPException
        with patch("shilun.api.load_config", return_value=AppConfig(mongo_uri="", mongo_db="")):
            with self.assertRaises(HTTPException) as ctx:
                precompute_breakout_events_endpoint(date="2026-06-25")
        self.assertEqual(ctx.exception.status_code, 400)

    def test_message_when_no_events_yet(self) -> None:
        """事件表全空，回填扫描 0，message 应显式说明。"""
        store = self._make_store()
        with _patch_config(), _patch_store(store):
            result = precompute_breakout_events_endpoint(
                date="2026-06-25",
                skip_detect=True,
            )
        self.assertEqual(result["backfill"]["scanned"], 0)
        self.assertIn("扫描 0", result["message"])


if __name__ == "__main__":
    unittest.main()
