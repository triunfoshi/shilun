"""阶段 7 Job 3：持仓每日判定引擎测试。"""

from __future__ import annotations

import unittest
from datetime import date, timedelta

from shilun.market.holding_events import (
    HOLDING_STATES,
    build_sector_states_lookup,
    daily_check_holdings,
    evaluate_holding_event,
    list_holding_events,
    upsert_holding_event,
)
from shilun.market.holdings import HoldingRecord, upsert_holding
from tests.test_holdings import FakeCollection


def _bars(
    closes: list[float],
    *,
    volumes: list[float] | None = None,
    highs: list[float] | None = None,
    end_date: str = "2026-07-10",
    ticker: str = "603986.SH",
) -> list[dict]:
    end = date.fromisoformat(end_date)
    volumes = volumes or [100.0] * len(closes)
    highs = highs or [close * 1.01 for close in closes]
    start = end - timedelta(days=len(closes) - 1)
    return [
        {
            "ticker": ticker,
            "date": (start + timedelta(days=index)).isoformat(),
            "open": close * 0.995,
            "high": highs[index],
            "low": close * 0.99,
            "close": close,
            "volume": volumes[index],
        }
        for index, close in enumerate(closes)
    ]


def _holding(**overrides) -> dict:
    payload = HoldingRecord(
        ticker="603986.SH",
        name="兆易创新",
        sector_name="半导体",
        entry_date="2026-06-20",
        entry_price=10.0,
        entry_signal="ma5_pullback_confirm",
        entry_size=0.3,
        target_price=13.0,
        signal_candle_open=9.5,
    ).to_document()
    payload.update(overrides)
    return payload


class HoldingStateClassificationTest(unittest.TestCase):
    def test_manual_holding_dispatches_without_ma5_sell_rules(self) -> None:
        event = evaluate_holding_event(
            _holding(strategy_id="manual", strategy_version="1.0.0"),
            _bars([10.0] * 25),
            analysis_date="2026-07-10",
            market_gate={"permission": "defense"},
            sector_state="retreat",
        )
        self.assertEqual(event["strategy_id"], "manual")
        self.assertEqual(event["recommended_action"], "hold")
        self.assertEqual(event["triggered_sell_signals"], [])

    def test_strong_hold(self) -> None:
        closes = [10.0 + index * 0.1 for index in range(25)]
        event = evaluate_holding_event(
            _holding(),
            _bars(closes),
            analysis_date="2026-07-10",
            market_gate={"permission": "hold"},
            sector_state="mainline_top3",
        )
        self.assertEqual(event["holding_state"], "strong_hold")
        self.assertEqual(event["recommended_action"], "hold")
        self.assertEqual(event["triggered_sell_signals"], [])

    def test_normal_shrink_pullback(self) -> None:
        closes = [10.0] * 24 + [10.1]
        volumes = [100.0] * 24 + [40.0]
        event = evaluate_holding_event(
            _holding(),
            _bars(closes, volumes=volumes),
            analysis_date="2026-07-10",
            market_gate={"permission": "hold"},
            sector_state="hot",
        )
        self.assertEqual(event["holding_state"], "normal_pullback")
        self.assertTrue(event["metrics"]["shrink_pullback_flag"])

    def test_close_below_ma5_reduces(self) -> None:
        closes = [10.0 + index * 0.1 for index in range(24)] + [12.0]
        event = evaluate_holding_event(
            _holding(),
            _bars(closes),
            analysis_date="2026-07-10",
            market_gate={"permission": "hold"},
            sector_state="hot",
        )
        self.assertEqual(event["holding_state"], "weaken_reduce")
        self.assertEqual(event["recommended_action"], "reduce_20pct")
        self.assertIn("close_below_ma5", event["triggered_sell_signals"])

    def test_volume_break_ma5_exits(self) -> None:
        closes = [10.0 + index * 0.1 for index in range(24)] + [11.5]
        volumes = [100.0] * 24 + [500.0]
        event = evaluate_holding_event(
            _holding(),
            _bars(closes, volumes=volumes),
            analysis_date="2026-07-10",
            market_gate={"permission": "hold"},
            sector_state="hot",
        )
        self.assertEqual(event["holding_state"], "invalid_exit")
        self.assertEqual(event["recommended_action"], "sell_all")
        self.assertIn("volume_break_ma5_flag", event["triggered_sell_signals"])

    def test_signal_candle_open_lost_exits(self) -> None:
        event = evaluate_holding_event(
            _holding(signal_candle_open=10.5),
            _bars([10.0] * 25),
            analysis_date="2026-07-10",
            market_gate={"permission": "hold"},
            sector_state="hot",
        )
        self.assertEqual(event["holding_state"], "invalid_exit")
        self.assertIn("signal_candle_open_lost", event["triggered_sell_signals"])

    def test_breakeven_stop_after_prior_profit(self) -> None:
        closes = [10.0] * 20 + [11.2, 11.0, 10.6, 10.3, 10.05]
        highs = [close * 1.01 for close in closes]
        highs[-5] = 12.0
        event = evaluate_holding_event(
            _holding(signal_candle_open=9.0),
            _bars(closes, highs=highs),
            analysis_date="2026-07-10",
            market_gate={"permission": "hold"},
            sector_state="hot",
        )
        self.assertEqual(event["holding_state"], "invalid_exit")
        self.assertIn("breakeven_stop_triggered", event["triggered_sell_signals"])

    def test_market_empty_is_hard_exit(self) -> None:
        event = evaluate_holding_event(
            _holding(),
            _bars([10.0 + index * 0.1 for index in range(25)]),
            analysis_date="2026-07-10",
            market_gate={"permission": "empty"},
            sector_state="mainline_top3",
        )
        self.assertEqual(event["holding_state"], "invalid_exit")
        self.assertIn("大盘空仓", event["reason"])

    def test_configured_breakdown_level_is_hard_exit(self) -> None:
        event = evaluate_holding_event(
            _holding(signal_candle_open=9.0, breakdown_level=10.2),
            _bars([10.0] * 25),
            analysis_date="2026-07-10",
            market_gate={"permission": "hold"},
            sector_state="hot",
        )
        self.assertEqual(event["holding_state"], "invalid_exit")
        self.assertIn("breakdown_level_lost", event["triggered_sell_signals"])

    def test_sector_retreat_reduces(self) -> None:
        event = evaluate_holding_event(
            _holding(),
            _bars([10.0 + index * 0.1 for index in range(25)]),
            analysis_date="2026-07-10",
            market_gate={"permission": "hold"},
            sector_state="retreat",
        )
        self.assertEqual(event["holding_state"], "weaken_reduce")
        self.assertIn("板块退潮", event["reason"])

    def test_state_set_is_locked(self) -> None:
        self.assertEqual(
            HOLDING_STATES,
            {
                "strong_hold",
                "normal_pullback",
                "weaken_reduce",
                "invalid_exit",
                "overnight_locked",
                "timed_exit",
            },
        )

    def test_e7_entry_day_is_locked_and_next_day_forces_exit(self) -> None:
        holding = _holding(
            ticker="002185.SZ",
            name="华天科技",
            entry_date="2026-07-10",
            entry_price=20.0,
            cost_basis=20.0,
            entry_signal="E7",
            strategy_id="ma5_v0_2",
            strategy_version="0.2.0",
            stop_loss_1=19.0,
        )
        entry_day = evaluate_holding_event(
            holding,
            _bars([20.0] * 25, end_date="2026-07-10", ticker="002185.SZ"),
            analysis_date="2026-07-10",
        )
        self.assertEqual(entry_day["strategy_id"], "e7_oversold_overnight")
        self.assertEqual(entry_day["holding_state"], "overnight_locked")
        self.assertEqual(entry_day["recommended_action"], "hold")

        exit_day = evaluate_holding_event(
            holding,
            _bars([20.0] * 25, end_date="2026-07-11", ticker="002185.SZ"),
            analysis_date="2026-07-11",
        )
        self.assertEqual(exit_day["holding_state"], "timed_exit")
        self.assertEqual(exit_day["recommended_action"], "sell_all")
        self.assertIn("e7_time_exit", exit_day["triggered_sell_signals"])


class HoldingEventPersistenceTest(unittest.TestCase):
    def test_upsert_is_idempotent_and_history_is_descending(self) -> None:
        collection = FakeCollection()
        first = {"ticker": "603986.SH", "date": "2026-07-09", "holding_state": "strong_hold"}
        second = {"ticker": "603986.SH", "date": "2026-07-10", "holding_state": "weaken_reduce"}
        self.assertEqual(upsert_holding_event(collection, first), "inserted")
        self.assertEqual(upsert_holding_event(collection, second), "inserted")
        self.assertEqual(upsert_holding_event(collection, {**second, "holding_state": "invalid_exit"}), "updated")
        events = list_holding_events(collection, "603986.sh")
        self.assertEqual([item["date"] for item in events], ["2026-07-10", "2026-07-09"])
        self.assertEqual(events[0]["holding_state"], "invalid_exit")

    def test_sector_lookup_prefers_fuller_later_sources(self) -> None:
        lookup = build_sector_states_lookup(
            {
                "top_sectors": [{"sector_name": "半导体", "sector_state": "hot"}],
                "trend_sectors": [{"sector_name": "半导体", "sector_state": "mainline_top3"}],
            }
        )
        self.assertEqual(lookup["半导体"], "hot")


class LegacyHoldingStrategyCompatibilityTest(unittest.TestCase):
    def test_legacy_execution_strategy_is_evaluated_by_ma5_daily_check(self) -> None:
        holding = HoldingRecord(
            ticker="002185.SZ",
            name="华天科技",
            entry_date="2026-07-01",
            entry_price=20.0,
            entry_signal="E2",
            entry_size=0.1,
            strategy_id="ma5_multi_scenario_execution",
            strategy_version="0.3.0",
        ).to_document()

        event = evaluate_holding_event(
            holding,
            _bars([20.0 + index * 0.1 for index in range(25)], ticker="002185.SZ"),
            analysis_date="2026-07-10",
            market_gate={"permission": "hold"},
        )

        self.assertEqual(event["strategy_id"], "ma5_v0_2")
        self.assertEqual(event["strategy_migrated_from"], "ma5_multi_scenario_execution")


class FakeRawMarket:
    def __init__(self, bars: list[dict]) -> None:
        self.bars = bars

    def find_daily_bars(self, *, start_date: str, end_date: str, tickers: list[str] | None = None) -> list[dict]:
        allowed = set(tickers or [])
        return [
            dict(row)
            for row in self.bars
            if (not allowed or row["ticker"] in allowed) and start_date <= row["date"] <= end_date
        ]


class FakeStore:
    def __init__(self, bars: list[dict]) -> None:
        self.collections = {
            "holdings": FakeCollection(),
            "holding_events": FakeCollection(),
            "breakout_events": FakeCollection(),
            "market_part1_cache": FakeCollection(),
            "sector_trends_cache": FakeCollection(),
        }
        self.raw_market = FakeRawMarket(bars)
        self.close_count = 0

    def collection(self, name: str) -> FakeCollection:
        return self.collections[name]

    def close(self) -> None:
        self.close_count += 1


class DailyCheckTest(unittest.TestCase):
    def test_daily_check_writes_event_and_rerun_updates(self) -> None:
        store = FakeStore(_bars([10.0 + index * 0.1 for index in range(25)]))
        upsert_holding(
            store.collection("holdings"),
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
        first = daily_check_holdings(
            store,
            "2026-07-10",
            market_gate={"permission": "hold"},
            sector_states={"半导体": "mainline_top3"},
        )
        second = daily_check_holdings(
            store,
            "2026-07-10",
            market_gate={"permission": "hold"},
            sector_states={"半导体": "mainline_top3"},
        )
        self.assertEqual(first["inserted"], 1)
        self.assertEqual(first["state_counts"], {"strong_hold": 1})
        self.assertEqual(second["updated"], 1)
        self.assertEqual(len(store.collection("holding_events")._docs), 1)

    def test_missing_target_bar_is_reported_not_written(self) -> None:
        store = FakeStore(_bars([10.0] * 10, end_date="2026-07-09"))
        upsert_holding(
            store.collection("holdings"),
            HoldingRecord(
                ticker="603986.SH",
                entry_date="2026-06-20",
                entry_price=10.0,
                entry_signal="manual",
                entry_size=0.2,
            ),
        )
        result = daily_check_holdings(store, "2026-07-10")
        self.assertEqual(result["checked"], 0)
        self.assertEqual(result["failed"], 1)
        self.assertIn("最新日线", result["errors"][0]["error"])


if __name__ == "__main__":
    unittest.main()
