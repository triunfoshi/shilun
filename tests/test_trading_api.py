from __future__ import annotations

import unittest
from unittest.mock import patch

from fastapi import HTTPException

from shilun.api import (
    AccountRiskProfileHttpRequest,
    TradeFillHttpRequest,
    TradeOrderHttpRequest,
    create_app,
    create_trading_order,
    create_trading_order_fill,
    get_trading_data_latency,
    get_trading_decision_timeline,
    get_trading_risk_profile,
    get_trading_replay_gate,
    get_trading_workbench,
    update_trading_risk_profile,
)


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
        return iter(self.docs)


class _Collection:
    def __init__(self, docs):
        self.docs = docs

    def find(self, query, projection=None):
        return _Cursor([
            item for item in self.docs
            if all(item.get(key) in value['$in'] if isinstance(value, dict) and '$in' in value
                   else item.get(key) == value for key, value in query.items())
        ])

    def find_one(self, query, projection=None):
        return next(iter(self.find(query, projection)), None)

    def update_one(self, query, update, upsert=False):
        for item in self.docs:
            if all(item.get(key) == value for key, value in query.items()):
                item.update(update.get("$set", {}))
                return
        if upsert:
            self.docs.append({**query, **update.get("$set", {}), **update.get("$setOnInsert", {})})


class _Store:
    def __init__(self):
        self.closed = False
        self.events = _Collection([
            {"ticker": "603986.SH", "computed_at": "2026-07-13T10:15:00+08:00", "current": {"action_type": "hold"}},
        ])
        self.collections = {
            "trading_decision_events": self.events,
            "account_risk_profiles": _Collection([]),
            "trading_quote_latency_samples": _Collection([]),
        }

    def collection(self, name):
        return self.collections.setdefault(name, _Collection([]))

    def close(self):
        self.closed = True


class TradingApiTests(unittest.TestCase):
    def test_job4_routes_are_registered(self) -> None:
        paths = {route.path for route in create_app().routes}
        self.assertTrue({
            "/api/v1/trading/dashboard",
            "/api/v1/trading/workbench",
            "/api/v1/trading/replay-gate",
            "/api/v1/trading/holdings/{ticker}/cockpit",
            "/api/v1/trading/holdings/{ticker}/scenarios",
            "/api/v1/trading/holdings/{ticker}/replay",
            "/api/v1/trading/holdings/{ticker}/decision-timeline",
            "/api/v1/trading/holdings/{ticker}/refresh",
            "/api/v1/trading/refresh",
            "/api/v1/trading/risk-profile",
            "/api/v1/trading/data-latency",
            "/api/v1/trading/readiness",
            "/api/v1/trading/holdings/{ticker}/executions",
            "/api/v1/trading/executions",
            "/api/v1/trading/orders",
            "/api/v1/trading/orders/{order_id}",
            "/api/v1/trading/orders/{order_id}/fills",
            "/api/v1/trading/orders/{order_id}/cancel",
            "/api/v1/trading/positions/t1-settlement",
        }.issubset(paths))

    def test_replay_gate_api_closes_when_historical_context_is_missing(self) -> None:
        store = _Store()
        store.collection("minute_bars_daily").docs.extend([
            {"ticker": "000001.SH", "trade_date": "20260710", "interval": "5m"},
            {"ticker": "399006.SZ", "trade_date": "20260710", "interval": "5m"},
            {"ticker": "000001.SZ", "trade_date": "20260710", "interval": "5m"},
        ])
        with patch("shilun.api._open_holdings_store", return_value=store):
            payload = get_trading_replay_gate(min_sessions=20)
        self.assertFalse(payload["ready_for_launch"])
        self.assertEqual(payload["inventory"]["paired_stock_session_count"], 1)
        self.assertIn("缺少 execution_market_facts 历史板块事实", payload["blockers"])
        self.assertTrue(store.closed)

    def test_timeline_api_reads_events_and_closes_store(self) -> None:
        store = _Store()
        with patch("shilun.api._open_holdings_store", return_value=store):
            payload = get_trading_decision_timeline("603986.sh", limit=10)
        self.assertEqual(payload["ticker"], "603986.SH")
        self.assertEqual(payload["count"], 1)
        self.assertTrue(store.closed)

    def test_workbench_api_returns_the_backend_event_timeline_and_closes_store(self) -> None:
        store = _Store()
        store.events.docs[0].update({
            "event_id": "timeline-1",
            "previous": {"action_label": "继续持有"},
            "current": {
                "canonical_action_type": "risk_exit",
                "action_label": "立即清仓",
                "position_state": "positioned",
                "execution_state": "exit_pending",
            },
            "reasons": ["跌破硬止损"],
        })
        store.collection("holdings").docs.append({
            "ticker": "603986.SH", "status": "active", "quantity": 100,
        })
        with patch("shilun.api._open_holdings_store", return_value=store):
            payload = get_trading_workbench(limit=10)
        self.assertEqual(payload["timeline"][0]["event_id"], "timeline-1")
        self.assertEqual(payload["queues"]["risk"][0]["action_name_cn"], "立即清仓")
        self.assertTrue(store.closed)

    def test_timeline_limit_is_bounded_before_opening_store(self) -> None:
        with patch("shilun.api._open_holdings_store") as opener:
            with self.assertRaises(HTTPException) as context:
                get_trading_decision_timeline("603986.SH", limit=501)
        self.assertEqual(context.exception.status_code, 400)
        opener.assert_not_called()

    def test_risk_profile_defaults_then_persists_explicit_configuration(self) -> None:
        store = _Store()
        with patch("shilun.api._open_holdings_store", return_value=store):
            default = get_trading_risk_profile()
            updated = update_trading_risk_profile(AccountRiskProfileHttpRequest(
                total_assets=86_000,
                available_cash=30_000,
                max_loss_per_trade=6_880,
                max_cash_per_trade=20_000,
                commission_rate=0.0003,
                min_commission=5,
                stamp_duty_rate=0.0005,
                slippage_bps=5,
            ))
        self.assertFalse(default["configured"])
        self.assertTrue(updated["configured"])
        self.assertTrue(updated["profile"]["cash_configured"])
        self.assertEqual(updated["profile"]["available_cash"], 30_000)
        self.assertEqual(updated["profile"]["max_cash_per_trade"], 20_000)
        self.assertEqual(updated["profile"]["max_loss_per_trade"], 6_880)
        self.assertEqual(updated["profile"]["source"], "user_configured")

    def test_order_api_only_creates_holding_after_real_buy_fill(self) -> None:
        store = _Store()
        store.collection("account_risk_profiles").docs.append({
            "profile_id": "default",
            "total_assets": 86_000,
            "available_cash": 78_000,
            "max_loss_per_trade": 6_880,
            "conservative_fee_buffer_rate": 0.002,
            "source": "user_configured",
        })
        with patch("shilun.api._open_holdings_store", return_value=store):
            created = create_trading_order(TradeOrderHttpRequest(
                order_id="api-order",
                plan_id="api-plan",
                ticker="000001.sz",
                side="buy",
                intent="entry",
                quantity=100,
                limit_price=10,
                placed_at="2026-07-15T14:30:00+08:00",
            ))
        self.assertEqual(created["order"]["status"], "open")
        self.assertEqual(store.collection("holdings").docs, [])

        with patch("shilun.api._open_holdings_store", return_value=store):
            filled = create_trading_order_fill(
                "api-order",
                TradeFillHttpRequest(
                    execution_id="api-fill",
                    quantity=100,
                    price=9.9,
                    fees=1,
                    executed_at="2026-07-15T14:35:00+08:00",
                ),
            )
        self.assertEqual(filled["order"]["status"], "filled")
        self.assertEqual(filled["holding"]["quantity"], 100)
        self.assertEqual(filled["holding"]["available_quantity"], 0)
        self.assertEqual(filled["account_profile"]["available_cash"], 77_009)

    def test_latency_api_reports_calibration_state(self) -> None:
        store = _Store()
        store.collection("trading_quote_latency_samples").docs.append({
            "ticker": "603986.SH",
            "observed_at": "2026-07-13T10:00:00+08:00",
            "quote_age_seconds": 2,
            "request_latency_ms": 100,
            "over_threshold": False,
        })
        with patch("shilun.api._open_holdings_store", return_value=store):
            payload = get_trading_data_latency("603986.SH", limit=100)
        self.assertEqual(payload["threshold_seconds"], 45)
        self.assertFalse(payload["summary"]["calibration_ready"])


if __name__ == "__main__":
    unittest.main()
