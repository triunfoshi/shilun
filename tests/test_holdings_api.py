"""阶段 7 Job 2：持仓 REST API 的边界测试。"""

from __future__ import annotations

import unittest
from unittest.mock import patch

from fastapi import HTTPException

from shilun.api import (
    HoldingCloseHttpRequest,
    HoldingCreateHttpRequest,
    HoldingPatchHttpRequest,
    app,
    close_holding_endpoint,
    create_holding,
    delete_holding_endpoint,
    get_holding_detail,
    get_holdings,
    get_strategy_catalog,
    apply_holding_stop_plan,
    preview_holding_stop_plan,
    preview_strategy_stop_plan,
    refresh_holding_derived,
    _holding_stop_strategy_context,
    update_holding,
    _strategy_daily_bars,
)
from shilun.common.config import AppConfig
from tests.test_holdings import FakeCollection


class FakeStore:
    def __init__(self) -> None:
        self.holdings = FakeCollection()
        self.position_events = FakeCollection()
        self.quotes = FakeCollection()
        self.risk_profiles = FakeCollection()
        self.risk_profiles.update_one(
            {"profile_id": "default"},
            {"$set": {"profile_id": "default", "total_assets": 100_000.0}},
            upsert=True,
        )
        self.close_count = 0
        self.raw_market = self

    def find_daily_bars(self, *, start_date, end_date, tickers):
        closes = [90, 92, 94, 96, 98, 100, 102, 104, 106, 108]
        lows = [89, 91, 93, 95, 97, 99, 96, 101, 103, 105]
        return [
            {"ticker": tickers[0], "date": f"2026-06-{index:02d}", "close": close, "low": low}
            for index, (close, low) in enumerate(zip(closes, lows), start=1)
            if start_date <= f"2026-06-{index:02d}" <= end_date
        ]

    def collection(self, name: str) -> FakeCollection:
        if name == "holdings":
            return self.holdings
        if name == "trading_realtime_quotes":
            return self.quotes
        if name == "holding_position_events":
            return self.position_events
        if name == "account_risk_profiles":
            return self.risk_profiles
        raise AssertionError(f"unexpected collection: {name}")

    def close(self) -> None:
        self.close_count += 1


def _patch_store(store: FakeStore):
    return patch("shilun.common.db.MongoSnapshotStore", return_value=store)


def _patch_config():
    return patch(
        "shilun.api.load_config",
        return_value=AppConfig(mongo_uri="mongodb://fake", mongo_db="fake"),
    )


def _create_payload(**overrides) -> HoldingCreateHttpRequest:
    values = {
        "ticker": " 603986.sh ",
        "name": "兆易创新",
        "sector_name": "半导体",
        "entry_date": "2026-07-10",
        "entry_price": 518.0,
        "entry_signal": "ma5_pullback_confirm",
        "entry_size": 0.3,
        "target_price": 620.0,
        "stop_loss_1": 507.64,
        "invalid_conditions": ["close < signal_candle_open"],
    }
    values.update(overrides)
    return HoldingCreateHttpRequest(**values)


class HoldingsApiTest(unittest.TestCase):
    def setUp(self) -> None:
        self.store = FakeStore()
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
        expected = {
            ("/api/v1/holdings", "POST"),
            ("/api/v1/holdings", "GET"),
            ("/api/v1/holdings/{ticker}", "GET"),
            ("/api/v1/holdings/{ticker}", "PATCH"),
            ("/api/v1/holdings/{ticker}", "DELETE"),
            ("/api/v1/holdings/{ticker}/close", "POST"),
            ("/api/v1/holdings/{ticker}/stop-plan", "GET"),
            ("/api/v1/holdings/{ticker}/stop-plan/apply", "POST"),
            ("/api/v1/holdings/{ticker}/refresh-derived", "POST"),
            ("/api/v1/strategies/{strategy_id}/stop-plan", "GET"),
        }
        self.assertTrue(expected.issubset(routes))

    def test_create_normalizes_ticker_and_returns_record(self) -> None:
        result = create_holding(_create_payload())
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["action"], "inserted")
        self.assertEqual(result["holding"]["ticker"], "603986.SH")
        self.assertEqual(result["holding"]["entry_date"], "2026-07-10")
        self.assertEqual(result["holding"]["target_price"], 620.0)
        self.assertEqual(result["holding"]["strategy_id"], "ma5_v0_2")
        self.assertEqual(result["holding"]["strategy_version"], "0.2.0")
        self.assertEqual(result["holding"]["initial_plan"]["entry_price"], 518.0)
        self.assertEqual(self.store.close_count, 1)

    def test_create_derives_entry_size_from_quantity_cost_and_total_assets(self) -> None:
        result = create_holding(_create_payload(
            quantity=200,
            cost_basis=36.0,
            entry_size=0.99,
        ))

        self.assertAlmostEqual(result["holding"]["entry_size"], 0.072, places=6)

    def test_strategy_catalog_is_available_without_mongo(self) -> None:
        result = get_strategy_catalog()
        ids = {item["strategy_id"] for item in result["strategies"]}
        self.assertEqual(ids, {"ma5_v0_2", "manual", "e7_oversold_overnight"})

    def test_e7_create_auto_binds_overnight_strategy_and_removes_price_target(self) -> None:
        result = create_holding(_create_payload(
            ticker="002185.SZ",
            name="华天科技",
            entry_signal="E7",
            strategy_id=None,
            target_price=620.0,
            stop_loss_1=19.2,
        ))

        holding = result["holding"]
        self.assertEqual(holding["strategy_id"], "e7_oversold_overnight")
        self.assertEqual(holding["strategy_version"], "1.0.0")
        self.assertIsNone(holding["target_price"])
        self.assertEqual(holding["current_plan"]["exit_deadline"], "次一交易日 10:00")

    def test_e7_stop_context_repairs_legacy_ma5_binding_and_uses_cost(self) -> None:
        self.store.quotes.update_one(
            {"ticker": "002185.SZ"},
            {"$set": {"current": 20.8}},
            upsert=True,
        )
        strategy_id, strategy_version, reference = _holding_stop_strategy_context(
            self.store,
            {
                "ticker": "002185.SZ",
                "entry_signal": "E7",
                "strategy_id": "ma5_v0_2",
                "strategy_version": "0.2.0",
                "entry_price": 21.17,
                "cost_basis": 21.17,
            },
        )

        self.assertEqual(strategy_id, "e7_oversold_overnight")
        self.assertEqual(strategy_version, "1.0.0")
        self.assertEqual(reference, 21.17)

    def test_actual_entry_rebinds_strategy_plan(self) -> None:
        result = create_holding(_create_payload(
            entry_price=520.0,
            proposed_plan={"entry_price": 500.0, "target_price": 600.0, "stop_loss_1": 480.0},
            target_price=None,
            stop_loss_1=None,
        ))
        plan = result["holding"]["initial_plan"]
        self.assertEqual(plan["planned_entry_price"], 500.0)
        self.assertEqual(plan["entry_price"], 520.0)
        self.assertEqual(plan["reward_risk_ratio"], 2.0)

    def test_ma5_create_generates_and_confirms_stop_when_missing(self) -> None:
        result = create_holding(_create_payload(entry_price=110.0, stop_loss_1=None, proposed_plan={}))

        holding = result["holding"]
        self.assertEqual(holding["stop_loss_mode"], "strategy")
        self.assertEqual(holding["stop_loss_1"], 101.0)
        self.assertEqual(holding["stop_loss_2"], 96.0)
        self.assertEqual(holding["stop_loss_plan"]["status"], "confirmed")

    def test_ma5_create_uses_latest_quote_as_stop_reference(self) -> None:
        self.store.quotes.update_one(
            {"ticker": "603986.SH"},
            {"$set": {"current": 115.0}},
            upsert=True,
        )

        result = create_holding(_create_payload(entry_price=110.0, stop_loss_1=None, proposed_plan={}))

        self.assertEqual(result["holding"]["stop_loss_plan"]["reference_price"], 115.0)

    def test_create_returns_latest_market_price_and_auto_levels(self) -> None:
        self.store.quotes.update_one(
            {"ticker": "603986.SH"},
            {"$set": {"current": 115.0, "quote_at": "2026-06-10T15:00:00+08:00", "source": "test_quote"}},
            upsert=True,
        )

        result = create_holding(_create_payload(
            entry_price=110.0,
            target_price=None,
            stop_loss_1=None,
            proposed_plan={},
        ))

        holding = result["holding"]
        self.assertEqual(holding["current_price"], 115.0)
        self.assertEqual(holding["current_price_kind"], "realtime")
        self.assertGreater(holding["target_price"], 0)
        self.assertTrue(holding["current_plan"]["target_source"])
        self.assertEqual(holding["stop_loss_1"], 101.0)

    def test_refresh_derived_migrates_legacy_strategy_and_replaces_manual_values(self) -> None:
        create_holding(_create_payload(entry_price=110.0, target_price=620.0, stop_loss_1=80.0))
        self.store.holdings.update_one(
            {"ticker": "603986.SH"},
            {"$set": {
                "strategy_id": "ma5_multi_scenario_execution",
                "strategy_version": "0.3.0",
                "manual_current_price": 999.0,
                "target_price": 620.0,
                "stop_loss_1": 80.0,
            }},
        )
        self.store.quotes.update_one(
            {"ticker": "603986.SH"},
            {"$set": {"current": 115.0, "quote_at": "2026-06-10T15:00:00+08:00"}},
            upsert=True,
        )

        result = refresh_holding_derived("603986.SH", overwrite=True)

        holding = result["holding"]
        self.assertEqual(result["strategy_migrated_from"], "ma5_multi_scenario_execution")
        self.assertEqual(holding["strategy_id"], "ma5_v0_2")
        self.assertIsNone(holding["manual_current_price"])
        self.assertEqual(holding["current_price"], 115.0)
        self.assertGreater(holding["target_price"], 0)
        self.assertTrue(holding["current_plan"]["target_source"])
        self.assertEqual(holding["stop_loss_1"], 101.0)
        self.assertEqual(holding["stop_loss_mode"], "strategy")

        plan_revision_count = len(holding["plan_revisions"])
        position_revision_count = len(holding["position_revisions"])
        repeated = refresh_holding_derived("603986.SH", overwrite=True)["holding"]
        self.assertEqual(len(repeated["plan_revisions"]), plan_revision_count)
        self.assertEqual(len(repeated["position_revisions"]), position_revision_count)

    def test_strategy_daily_bars_include_synced_as_of_date(self) -> None:
        class RawMarket:
            def find_daily_bars(self, *, start_date, end_date, tickers):
                self.query = {"start_date": start_date, "end_date": end_date, "tickers": tickers}
                return [{"ticker": tickers[0], "date": "2026-07-15", "close": 39.0, "low": 34.05}]

        raw_market = RawMarket()
        rows, completed_through = _strategy_daily_bars(
            type("Store", (), {"raw_market": raw_market})(),
            "603538.SH",
            as_of="2026-07-15",
        )

        self.assertEqual(raw_market.query["end_date"], "2026-07-15")
        self.assertEqual(rows[-1]["date"], "2026-07-15")
        self.assertEqual(completed_through, "2026-07-15")

    def test_stop_plan_preview_is_read_only_then_apply_is_audited(self) -> None:
        create_holding(_create_payload(entry_price=110.0, stop_loss_1=80.0))
        direct = preview_strategy_stop_plan("ma5_v0_2", "603986.SH", 110.0, "2026-07-10")
        preview = preview_holding_stop_plan("603986.SH")
        self.assertEqual(direct["stop_plan"]["recommended_stop"], 101.0)
        self.assertEqual(preview["holding_stop"]["effective_stop"], 80.0)
        self.assertEqual(get_holding_detail("603986.SH")["holding"]["stop_loss_1"], 80.0)

        applied = apply_holding_stop_plan("603986.SH")
        self.assertEqual(applied["holding"]["stop_loss_1"], 101.0)
        self.assertEqual(applied["holding"]["stop_loss_mode"], "strategy")
        self.assertEqual(len(applied["holding"]["plan_revisions"]), 1)

    def test_strategy_stop_apply_never_lowers_existing_protection(self) -> None:
        create_holding(_create_payload(entry_price=110.0, stop_loss_1=105.0))

        applied = apply_holding_stop_plan("603986.SH")

        self.assertEqual(applied["action"], "tighter_existing_preserved")
        self.assertEqual(applied["effective_stop"], 105.0)
        self.assertEqual(applied["holding"]["stop_loss_1"], 105.0)
        self.assertEqual(applied["stop_plan"]["recommended_stop"], 101.0)

    def test_duplicate_active_holding_returns_400(self) -> None:
        create_holding(_create_payload())
        with self.assertRaises(HTTPException) as ctx:
            create_holding(_create_payload(entry_price=520.0))
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn("已存在", ctx.exception.detail)

    def test_list_detail_and_status_validation(self) -> None:
        create_holding(_create_payload())
        self.store.quotes.update_one(
            {"ticker": "603986.SH"},
            {"$set": {"current": 521.25, "quote_at": "2026-07-16T15:00:00+08:00"}},
            upsert=True,
        )
        listed = get_holdings()
        self.assertEqual(listed["count"], 1)
        self.assertEqual(listed["holdings"][0]["current_price"], 521.25)
        detail = get_holding_detail("603986.sh")
        self.assertEqual(detail["holding"]["name"], "兆易创新")

        with self.assertRaises(HTTPException) as ctx:
            get_holdings("unknown")
        self.assertEqual(ctx.exception.status_code, 400)

    def test_missing_detail_returns_404(self) -> None:
        with self.assertRaises(HTTPException) as ctx:
            get_holding_detail("000001.SZ")
        self.assertEqual(ctx.exception.status_code, 404)

    def test_patch_updates_risk_level_and_realized_size(self) -> None:
        create_holding(_create_payload())
        result = update_holding(
            "603986.SH",
            HoldingPatchHttpRequest(
                stop_loss_1=510.0,
                realized_size=0.1,
                quantity=1_000,
                cost_basis=520.0,
                entry_price=519.0,
                manual_current_price=525.0,
            ),
        )
        self.assertEqual(result["action"], "updated")
        self.assertEqual(result["holding"]["stop_loss_1"], 510.0)
        self.assertEqual(result["holding"]["status"], "reduced")
        self.assertEqual(result["holding"]["quantity"], 1_000)
        self.assertEqual(result["holding"]["entry_price"], 519.0)
        self.assertEqual(result["holding"]["manual_current_price"], 525.0)
        self.assertEqual(len(result["holding"]["position_revisions"]), 1)

    def test_empty_patch_returns_400(self) -> None:
        create_holding(_create_payload())
        with self.assertRaises(HTTPException) as ctx:
            update_holding("603986.SH", HoldingPatchHttpRequest())
        self.assertEqual(ctx.exception.status_code, 400)

    def test_explicit_close_is_idempotent_and_computes_pnl(self) -> None:
        create_holding(_create_payload(entry_price=500.0))
        first = close_holding_endpoint(
            "603986.SH",
            HoldingCloseHttpRequest(
                exit_date="2026-07-10",
                exit_price=600.0,
                reason="target_reached",
            ),
        )
        second = close_holding_endpoint(
            "603986.SH",
            HoldingCloseHttpRequest(exit_price=999.0),
        )
        self.assertEqual(first["holding"]["realized_pnl"], 0.2)
        self.assertEqual(second["holding"]["exit_price"], 600.0)

    def test_delete_defaults_to_close_and_force_physically_deletes(self) -> None:
        create_holding(_create_payload())
        closed = delete_holding_endpoint("603986.SH")
        self.assertEqual(closed["action"], "closed")
        self.assertEqual(get_holdings("all")["holdings"][0]["status"], "closed")

        deleted = delete_holding_endpoint("603986.SH", force=True)
        self.assertEqual(deleted, {"status": "ok", "action": "deleted", "ticker": "603986.SH"})
        with self.assertRaises(HTTPException) as ctx:
            get_holding_detail("603986.SH")
        self.assertEqual(ctx.exception.status_code, 404)

    def test_invalid_date_returns_400_before_touching_mongo(self) -> None:
        with self.assertRaises(HTTPException) as ctx:
            create_holding(_create_payload(entry_date="20260710"))
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn("YYYY-MM-DD", ctx.exception.detail)


class HoldingsApiConfigurationTest(unittest.TestCase):
    def test_missing_mongo_returns_400(self) -> None:
        with patch(
            "shilun.api.load_config",
            return_value=AppConfig(mongo_uri="", mongo_db=""),
        ):
            with self.assertRaises(HTTPException) as ctx:
                get_holdings()
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn("Mongo 未配置", ctx.exception.detail)


if __name__ == "__main__":
    unittest.main()
