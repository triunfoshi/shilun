from __future__ import annotations

import unittest
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from shilun.trading.playbooks.ma5_trend import Ma5WatchPlan
from shilun.trading.replay import ReplayError, replay_ma5_session


TZ = ZoneInfo("Asia/Shanghai")


def _bars(closes: list[float], *, base: float = 100) -> list[dict]:
    start = datetime(2026, 7, 10, 9, 35, tzinfo=TZ)
    return [
        {
            "datetime": (start + timedelta(minutes=5 * index)).isoformat(),
            "open": close,
            "high": close,
            "low": close,
            "close": close,
            "volume": 100 + index,
            "amount": (100 + index) * close,
        }
        for index, close in enumerate(closes)
    ]


class TradingReplayTests(unittest.TestCase):
    def setUp(self) -> None:
        self.plan = Ma5WatchPlan(
            ticker="603986.SH",
            previous_close=100,
            open_price=100,
            frozen_ma5=100,
            hard_stop=95,
            previous_high=120,
            current_quantity=100,
        )

    def test_replay_tracks_scenario_switches_without_future_bars(self) -> None:
        result = replay_ma5_session(
            plan=self.plan,
            stock_bars=_bars([101, 99, 98, 101, 94]),
            sse_bars=_bars([4000, 4001, 4002, 4003, 4004]),
            chinext_bars=_bars([3000, 3001, 3002, 3003, 3004]),
        )

        self.assertEqual(result["bar_count"], 5)
        self.assertEqual([item["bar_index"] for item in result["frames"]], [1, 2, 3, 4, 5])
        actions = [item["decision"]["action"]["action_type"] for item in result["transitions"]]
        self.assertEqual(actions, ["hold", "wait_confirmation", "prepare_reduce", "hold", "risk_exit"])
        self.assertEqual(result["frames"][1]["decision"]["current_scenario"]["state"], "waiting_confirmation")
        self.assertEqual(result["frames"][-1]["decision"]["current_scenario"]["scenario_id"], "hard_stop")

    def test_requires_both_index_series_for_active_decisions(self) -> None:
        with self.assertRaisesRegex(ReplayError, "上证或创业板"):
            replay_ma5_session(
                plan=self.plan,
                stock_bars=_bars([101]),
                sse_bars=_bars([4000]),
                chinext_bars=[],
            )


if __name__ == "__main__":
    unittest.main()
