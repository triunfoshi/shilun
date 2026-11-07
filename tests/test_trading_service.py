from __future__ import annotations

import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

from shilun.trading.service import TradingDecisionService, _market_state


TZ = ZoneInfo("Asia/Shanghai")
NOW = datetime(2026, 7, 13, 10, 17, tzinfo=TZ)


class _Cursor:
    def __init__(self, docs):
        self.docs = list(docs)

    def sort(self, key, direction=1):
        self.docs.sort(key=lambda item: item.get(key) or "", reverse=direction < 0)
        return self

    def limit(self, limit):
        self.docs = self.docs[:limit]
        return self

    def __iter__(self):
        return iter([dict(item) for item in self.docs])


class _Collection:
    def __init__(self, docs=None):
        self.docs = list(docs or [])

    @staticmethod
    def _match(doc, query):
        for key, expected in query.items():
            actual = doc.get(key)
            if isinstance(expected, dict):
                if "$in" in expected and actual not in expected["$in"]:
                    return False
                if "$gte" in expected and actual < expected["$gte"]:
                    return False
                if "$lte" in expected and actual > expected["$lte"]:
                    return False
            elif actual != expected:
                return False
        return True

    def find_one(self, query, projection=None, sort=None):
        docs = [item for item in self.docs if self._match(item, query)]
        if sort:
            key, direction = sort[0]
            docs.sort(key=lambda item: item.get(key) or "", reverse=direction < 0)
        return dict(docs[0]) if docs else None

    def find(self, query, projection=None):
        return _Cursor([item for item in self.docs if self._match(item, query)])

    def update_one(self, query, update, upsert=False):
        for item in self.docs:
            if self._match(item, query):
                item.update(update.get("$set", {}))
                return
        if upsert:
            self.docs.append({**query, **update.get("$set", {}), **update.get("$setOnInsert", {})})


class _RawMarket:
    def __init__(self):
        self.rows = [
            {"ticker": "603986.SH", "date": date, "close": close, "low": low, "high": 110}
            for date, close, low in zip(
                [f"2026-07-{day:02d}" for day in range(1, 11)],
                [90, 92, 94, 96, 98, 100, 101, 100, 99, 100],
                [89, 91, 93, 95, 97, 99, 96, 99, 98, 99],
            )
        ]

    def find_daily_bars(self, *, start_date, end_date, tickers):
        return [row for row in self.rows if row["ticker"] in tickers and start_date <= row["date"] <= end_date]


class _Store:
    def __init__(self):
        quote_at = "2026-07-13T10:17:00+08:00"
        self.collections = {
            "holdings": _Collection([{
                "ticker": "603986.SH",
                "name": "兆易创新",
                "status": "active",
                "entry_date": "2026-07-10",
                "strategy_id": "ma5_v0_2",
                "stop_loss_1": 95,
            }]),
            "trading_realtime_quotes": _Collection([
                {"ticker": "603986.SH", "current": 101, "open": 100, "prev_close": 100, "pct_chg": 0.01, "quote_at": quote_at},
                {"ticker": "000001.SH", "current": 4000, "pct_chg": 0.006, "quote_at": quote_at},
                {"ticker": "399006.SZ", "current": 3000, "pct_chg": -0.001, "quote_at": quote_at},
            ]),
            "minute_bars_daily": _Collection([{
                "ticker": "603986.SH",
                "trade_date": "20260713",
                "interval": "5",
                "bars": [
                    {"datetime": "2026-07-13T10:05:00+08:00", "close": 100, "volume": 100, "amount": 10000},
                    {"datetime": "2026-07-13T10:10:00+08:00", "close": 101, "volume": 100, "amount": 10100},
                ],
            }]),
            "trading_watch_plans": _Collection(),
            "trading_decision_events": _Collection(),
        }
        self.raw_market = _RawMarket()

    def collection(self, name):
        return self.collections.setdefault(name, _Collection())


class TradingDecisionServiceTests(unittest.TestCase):
    def test_market_state_uses_klineshare_fractional_change_percent(self) -> None:
        self.assertEqual(_market_state({"pct_chg": 0.006}), "修复")
        self.assertEqual(_market_state({"pct_chg": -0.006}), "走弱")
        self.assertEqual(_market_state({"pct_chg": 0.001}), "震荡")

    def test_cockpit_get_is_read_only_and_returns_action_reason_evidence_data(self) -> None:
        store = _Store()
        service = TradingDecisionService(store, now_factory=lambda: NOW)

        payload = service.cockpit("603986.SH", now=NOW)

        self.assertEqual(payload["decision"]["action"]["action_type"], "hold")
        self.assertEqual(payload["decision"]["data_freshness"]["state"], "normal")
        self.assertEqual(payload["risk"]["status"], "quantity_not_configured")
        self.assertIsNone(payload["decision"]["action"]["target_quantity"])
        self.assertEqual(store.collection("trading_watch_plans").docs, [])
        self.assertEqual(store.collection("trading_decision_events").docs, [])

    def test_fresh_klineshare_trend_average_replaces_closed_bar_vwap_proxy(self) -> None:
        store = _Store()
        store.collection("intraday_trend_snapshots").docs.append({
            "ticker": "603986.SH",
            "trade_date": "20260713",
            "avg_price": 99.8,
            "source_at": "2026-07-13T10:16:30+08:00",
        })

        payload = TradingDecisionService(store, now_factory=lambda: NOW).cockpit("603986.SH", now=NOW)

        self.assertEqual(payload["stock"]["vwap"], 99.8)
        self.assertEqual(payload["stock"]["vwap_source"], "klineshare_trend_avg_price")

    def test_refresh_freezes_watch_plan_and_only_writes_semantic_changes(self) -> None:
        store = _Store()
        service = TradingDecisionService(store, now_factory=lambda: NOW)

        first = service.refresh("603986.SH", now=NOW)
        second = service.refresh("603986.SH", now=NOW)

        self.assertTrue(first["event_created"])
        self.assertFalse(second["event_created"])
        self.assertEqual(len(store.collection("trading_watch_plans").docs), 1)
        self.assertEqual(len(store.collection("trading_decision_events").docs), 1)

        bars = store.collection("minute_bars_daily").docs[0]["bars"]
        bars[-2]["close"] = 99
        bars[-1]["close"] = 99
        changed = service.refresh("603986.SH", now=NOW)
        self.assertTrue(changed["event_created"])
        self.assertEqual(changed["decision"]["action"]["action_type"], "prepare_reduce")
        self.assertEqual(changed["event"]["previous"]["action_type"], "hold")

    def test_position_revision_updates_frozen_plan_namespace_next_cycle(self) -> None:
        store = _Store()
        holding = store.collection("holdings").docs[0]
        holding.update({"quantity": 100, "cost_basis": 100, "position_revision": 1})
        service = TradingDecisionService(store, now_factory=lambda: NOW)

        first = service.refresh("603986.SH", now=NOW)
        holding.update({"quantity": 200, "position_revision": 2})
        second = service.refresh("603986.SH", now=NOW)

        self.assertTrue(first["event_created"])
        self.assertTrue(second["event_created"])
        self.assertEqual(second["event"]["position_revision"], 2)
        self.assertEqual(store.collection("trading_watch_plans").docs[0]["position_revision"], 2)

    def test_stale_index_quote_stops_active_action(self) -> None:
        store = _Store()
        index = store.collection("trading_realtime_quotes").docs[1]
        index["quote_at"] = "2026-07-13T10:00:00+08:00"

        result, _ = TradingDecisionService(store, now_factory=lambda: NOW).evaluate("603986.SH", now=NOW)

        self.assertIsNone(result.action)
        self.assertEqual(result.data_freshness.state.value, "delayed")

    def test_legacy_holding_requires_explicit_ma5_evidence(self) -> None:
        store = _Store()
        holding = store.collection("holdings").docs[0]
        holding.pop("strategy_id")
        holding["entry_signal"] = "ma5_pullback_confirm"
        payload = TradingDecisionService(store, now_factory=lambda: NOW).cockpit("603986.SH", now=NOW)
        self.assertEqual(payload["holding"]["_trading_strategy_resolution"], "legacy_ma5_evidence")

        holding["entry_signal"] = "manual"
        with self.assertRaisesRegex(ValueError, "unknown"):
            TradingDecisionService(store, now_factory=lambda: NOW).cockpit("603986.SH", now=NOW)

    def test_confirmed_break_uses_absolute_risk_budget_for_dynamic_quantity(self) -> None:
        store = _Store()
        holding = store.collection("holdings").docs[0]
        holding.update({"quantity": 1_000, "cost_basis": 100, "stop_loss_1": 90})
        bars = store.collection("minute_bars_daily").docs[0]["bars"]
        bars[-2]["close"] = 99
        bars[-1]["close"] = 99

        payload = TradingDecisionService(store, now_factory=lambda: NOW).cockpit("603986.SH", now=NOW)

        action = payload["decision"]["action"]
        self.assertEqual(action["action_type"], "risk_reduce")
        self.assertEqual(action["target_quantity"], 600)
        self.assertEqual(action["change_quantity"], -400)
        self.assertEqual(payload["risk"]["calculation"]["max_loss_per_trade"], 6_880)

    def test_available_quantity_caps_reduction_within_same_calculation_cycle(self) -> None:
        store = _Store()
        holding = store.collection("holdings").docs[0]
        holding.update({
            "quantity": 1_000,
            "available_quantity": 200,
            "cost_basis": 100,
            "stop_loss_1": 90,
        })
        bars = store.collection("minute_bars_daily").docs[0]["bars"]
        bars[-2]["close"] = 99
        bars[-1]["close"] = 99

        payload = TradingDecisionService(store, now_factory=lambda: NOW).cockpit("603986.SH", now=NOW)

        action = payload["decision"]["action"]
        self.assertEqual(action["target_quantity"], 800)
        self.assertEqual(action["change_quantity"], -200)

    def test_missing_legacy_stop_uses_strategy_stop_plan_instead_of_ma5_discount(self) -> None:
        store = _Store()
        store.collection("holdings").docs[0].pop("stop_loss_1")

        payload = TradingDecisionService(store, now_factory=lambda: NOW).cockpit("603986.SH", now=NOW)

        self.assertEqual(payload["watch_plan"]["hard_stop_origin"], "strategy_generated")
        self.assertEqual(payload["watch_plan"]["hard_stop"], 98.5)
        self.assertEqual(payload["watch_plan"]["stop_plan"]["recommended_source"], "ma8")

    def test_confirmed_hard_stop_remains_pending_until_execution_writeback(self) -> None:
        store = _Store()
        holding = store.collection("holdings").docs[0]
        holding.update({"quantity": 100, "cost_basis": 100, "stop_loss_1": 95})
        quote = store.collection("trading_realtime_quotes").docs[0]
        quote["current"] = 94
        bars = store.collection("minute_bars_daily").docs[0]["bars"]
        bars[-2]["close"] = 94
        bars[-1]["close"] = 94

        triggered = TradingDecisionService(store, now_factory=lambda: NOW).refresh("603986.SH", now=NOW)

        self.assertEqual(triggered["decision"]["action"]["action_type"], "risk_exit")
        self.assertEqual(triggered["decision"]["decision_state"], "pending_execution")
        self.assertEqual(holding["pending_risk_action"]["status"], "pending")

        quote["current"] = 101
        bars[-2]["close"] = 100
        bars[-1]["close"] = 101
        recovered = TradingDecisionService(store, now_factory=lambda: NOW).cockpit("603986.SH", now=NOW)

        self.assertEqual(recovered["decision"]["action"]["action_type"], "risk_exit")
        self.assertEqual(recovered["decision"]["action"]["target_quantity"], 0)
        self.assertEqual(recovered["decision"]["decision_state"], "pending_execution")
        self.assertIn("成交回写前", recovered["decision"]["reasons"][0])

    def test_daily_sell_all_is_latched_when_user_explicitly_refreshes_decision(self) -> None:
        store = _Store()
        holding = store.collection("holdings").docs[0]
        holding.update({"quantity": 200, "cost_basis": 100, "stop_loss_1": 95})
        store.collection("holding_events").docs.append({
            "ticker": "603986.SH",
            "date": "2026-07-10",
            "recommended_action": "sell_all",
            "holding_state": "invalid_exit",
            "current_price": 99,
            "reason": "触发致命退出条件：浮盈回撤到成本线。",
            "updated_at": "2026-07-12T10:00:00+08:00",
        })

        payload = TradingDecisionService(store, now_factory=lambda: NOW).refresh("603986.SH", now=NOW)

        self.assertEqual(payload["decision"]["decision_state"], "pending_execution")
        self.assertEqual(payload["decision"]["action"]["target_quantity"], 0)
        self.assertEqual(payload["decision"]["action"]["change_quantity"], -200)
        self.assertEqual(holding["pending_risk_action"]["source"], "holding_daily_check")

    def test_cockpit_enriches_sector_and_stock_reference_without_driving_action(self) -> None:
        store = _Store()
        holding = store.collection("holdings").docs[0]
        holding.update({"quantity": 200, "cost_basis": 100, "sector_name": "半导体"})
        store.collection("sector_trends_cache").docs.append({
            "analysis_date": "2026-07-10",
            "payload": {
                "trend_sectors": [{
                    "sector_name": "半导体",
                    "stage": "repair",
                    "stage_label": "修复",
                    "trend_label": "资金趋势",
                    "metrics": {"return_5d": 0.12, "relative_return_5d": 0.08},
                }],
            },
        })

        payload = TradingDecisionService(store, now_factory=lambda: NOW).cockpit("603986.SH", now=NOW)

        self.assertEqual(payload["market"]["sector"]["state"], "修复")
        self.assertEqual(payload["market"]["sector"]["analysis_date"], "2026-07-10")
        self.assertFalse(payload["market"]["sector"]["drives_action"])
        self.assertEqual(payload["market"]["stock"]["current_price"], 101)
        self.assertEqual(payload["market"]["stock"]["name"], "兆易创新")


if __name__ == "__main__":
    unittest.main()
