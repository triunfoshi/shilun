"""阶段 7 Job 3：持仓日检 CLI/调度任务核心测试。"""

from __future__ import annotations

import unittest

from shilun.jobs.holding_daily_check_job import HoldingDailyCheckJob, resolve_check_date
from shilun.market.holdings import HoldingRecord, upsert_holding
from tests.test_holding_events import FakeStore, _bars


class JobFakeStore(FakeStore):
    def __init__(self, bars: list[dict]) -> None:
        super().__init__(bars)
        self.sync_states: list[dict] = []
        self.raw_market.find_trade_calendar = lambda **_kwargs: [
            {"cal_date": "20260709", "is_open": 1},
            {"cal_date": "20260710", "is_open": 1},
        ]

    def upsert_sync_state(self, payload: dict) -> int:
        self.sync_states.append(dict(payload))
        return 1


class HoldingDailyCheckJobTest(unittest.TestCase):
    def test_resolve_check_date_uses_trade_calendar(self) -> None:
        store = JobFakeStore([])
        self.assertEqual(resolve_check_date(store, "20260710"), "2026-07-10")

    def test_job_checks_holdings_and_records_sync_state(self) -> None:
        store = JobFakeStore(_bars([10.0 + index * 0.1 for index in range(25)]))
        upsert_holding(
            store.collection("holdings"),
            HoldingRecord(
                ticker="603986.SH",
                entry_date="2026-06-20",
                entry_price=10.0,
                entry_signal="manual",
                entry_size=0.2,
                signal_candle_open=9.5,
            ),
        )
        result = HoldingDailyCheckJob(store).run("2026-07-10")
        self.assertEqual(result["analysis_date"], "2026-07-10")
        self.assertEqual(result["checked"], 1)
        self.assertEqual(result["dataset"], "holding_events")
        self.assertEqual(result["scope"], "20260710")
        self.assertEqual(len(store.sync_states), 1)

    def test_empty_holdings_is_successful_no_op(self) -> None:
        store = JobFakeStore([])
        result = HoldingDailyCheckJob(store).run("2026-07-10")
        self.assertEqual(result["checked"], 0)
        self.assertEqual(result["failed"], 0)
        self.assertEqual(result["state_counts"], {})
        self.assertEqual(len(store.sync_states), 1)


if __name__ == "__main__":
    unittest.main()
