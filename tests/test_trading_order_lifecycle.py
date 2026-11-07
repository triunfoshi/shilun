from __future__ import annotations

import unittest

from shilun.trading.order_lifecycle import (
    OrderLifecycleError,
    TradeFillInput,
    TradeOrderInput,
    cancel_trade_order,
    create_trade_order,
    expire_trade_orders,
    get_trade_order,
    list_trade_orders,
    record_order_fill,
    settle_t1_positions,
)


class _Collection:
    def __init__(self, docs=None):
        self.docs = [dict(item) for item in (docs or [])]

    @staticmethod
    def _matches(document, query):
        return all(document.get(key) == value for key, value in query.items())

    def find(self, query, projection=None):
        return [dict(item) for item in self.docs if self._matches(item, query)]

    def find_one(self, query, projection=None):
        return next((dict(item) for item in self.docs if self._matches(item, query)), None)

    def update_one(self, query, update, upsert=False):
        for item in self.docs:
            if self._matches(item, query):
                item.update(update.get("$set", {}))
                return
        if upsert:
            self.docs.append({
                **query,
                **update.get("$setOnInsert", {}),
                **update.get("$set", {}),
            })


def _account(cash=78_000):
    return {
        "profile_id": "default",
        "total_assets": 86_000,
        "available_cash": cash,
        "max_loss_per_trade": 6_880,
        "conservative_fee_buffer_rate": 0.002,
        "source": "user_configured",
    }


def _holding(quantity=100, available=100):
    return {
        "ticker": "000001.SZ",
        "name": "平安银行",
        "status": "active",
        "entry_date": "2026-07-14",
        "entry_price": 10.0,
        "cost_basis": 10.0,
        "entry_size": 0.2,
        "quantity": quantity,
        "available_quantity": available,
        "frozen_quantity": 0,
        "position_revision": 1,
        "position_revisions": [],
        "realized_loss_amount": 0,
    }


class OrderHarness:
    def __init__(self, holdings=None, cash=78_000):
        self.orders = _Collection()
        self.executions = _Collection()
        self.holdings = _Collection(holdings)
        self.accounts = _Collection([_account(cash)])
        self.reservations = _Collection()
        self.position_events = _Collection()
        self.order_events = _Collection()

    def create(self, **changes):
        values = {
            "order_id": "order-1",
            "plan_id": "plan-1",
            "ticker": "000001.SZ",
            "side": "buy",
            "quantity": 200,
            "limit_price": 10.0,
            "intent": "entry",
            "scenario_id": "E2",
            "placed_at": "2026-07-15T14:30:00+08:00",
        }
        values.update(changes)
        return create_trade_order(
            orders_collection=self.orders,
            holdings_collection=self.holdings,
            account_collection=self.accounts,
            reservations_collection=self.reservations,
            position_event_collection=self.position_events,
            order_event_collection=self.order_events,
            inputs=TradeOrderInput(**values),
        )

    def fill(self, **changes):
        values = {
            "execution_id": "fill-1",
            "quantity": 100,
            "price": 9.9,
            "fees": 1,
            "executed_at": "2026-07-15T14:35:00+08:00",
        }
        values.update(changes)
        return record_order_fill(
            orders_collection=self.orders,
            executions_collection=self.executions,
            holdings_collection=self.holdings,
            account_collection=self.accounts,
            reservations_collection=self.reservations,
            position_event_collection=self.position_events,
            order_event_collection=self.order_events,
            order_id="order-1",
            inputs=TradeFillInput(**values),
        )

    def cancel(self, **changes):
        return cancel_trade_order(
            orders_collection=self.orders,
            holdings_collection=self.holdings,
            reservations_collection=self.reservations,
            position_event_collection=self.position_events,
            order_event_collection=self.order_events,
            order_id="order-1",
            cancelled_at=changes.get("cancelled_at", "2026-07-15T14:40:00+08:00"),
            reason=changes.get("reason", "user_cancelled"),
        )


class BuyOrderLifecycleTests(unittest.TestCase):
    def test_buy_order_reserves_cash_but_does_not_create_holding(self) -> None:
        harness = OrderHarness()
        order = harness.create()
        self.assertEqual(order["status"], "open")
        self.assertEqual(order["execution_state"], "entry_pending")
        self.assertEqual(order["reserved_cash"], 2_000)
        self.assertEqual(harness.holdings.docs, [])
        self.assertEqual(harness.accounts.docs[0]["available_cash"], 78_000)
        self.assertEqual(harness.reservations.docs[0]["status"], "active")

    def test_partial_buy_keeps_remaining_order_and_creates_t1_locked_holding(self) -> None:
        harness = OrderHarness()
        harness.create()
        result = harness.fill()
        self.assertEqual(result["order"]["status"], "partially_filled")
        self.assertEqual(result["order"]["remaining_quantity"], 100)
        self.assertEqual(result["holding"]["quantity"], 100)
        self.assertEqual(result["holding"]["available_quantity"], 0)
        self.assertEqual(result["holding"]["t1_locked_quantity"], 100)
        self.assertEqual(result["holding"]["strategy_id"], "ma5_v0_2")
        self.assertEqual(result["holding"]["strategy_version"], "0.2.0")
        self.assertEqual(result["account_profile"]["available_cash"], 77_009)
        self.assertEqual(harness.reservations.docs[0]["reserved_cash"], 1_000)

    def test_e7_buy_auto_binds_e7_strategy_through_fill(self) -> None:
        harness = OrderHarness()
        order = harness.create(scenario_id="E7")
        self.assertEqual(order["strategy_id"], "e7_oversold_overnight")
        self.assertEqual(order["strategy_version"], "1.0.0")

        result = harness.fill()
        self.assertEqual(result["holding"]["entry_signal"], "E7")
        self.assertEqual(result["holding"]["strategy_id"], "e7_oversold_overnight")

    def test_e7_buy_rejects_explicit_ma5_mismatch(self) -> None:
        with self.assertRaisesRegex(OrderLifecycleError, "E7 必须绑定"):
            OrderHarness().create(
                scenario_id="E7",
                strategy_id="ma5_v0_2",
                strategy_version="0.2.0",
            )

    def test_replaying_same_execution_is_idempotent(self) -> None:
        harness = OrderHarness()
        harness.create()
        first = harness.fill()
        replay = harness.fill()
        self.assertFalse(first["idempotent_replay"])
        self.assertTrue(replay["idempotent_replay"])
        self.assertEqual(harness.holdings.docs[0]["quantity"], 100)
        self.assertEqual(harness.accounts.docs[0]["available_cash"], 77_009)
        self.assertEqual(len(harness.executions.docs), 1)

    def test_second_fill_updates_weighted_cost_and_consumes_reservation(self) -> None:
        harness = OrderHarness()
        harness.create()
        harness.fill()
        result = harness.fill(
            execution_id="fill-2",
            quantity=100,
            price=9.8,
            fees=1,
            executed_at="2026-07-15T14:36:00+08:00",
        )
        self.assertEqual(result["order"]["status"], "filled")
        self.assertEqual(result["holding"]["quantity"], 200)
        self.assertEqual(result["holding"]["available_quantity"], 0)
        self.assertAlmostEqual(result["holding"]["cost_basis"], 9.86)
        self.assertEqual(harness.reservations.docs[0]["status"], "consumed")
        self.assertEqual(harness.reservations.docs[0]["reserved_cash"], 0)

    def test_cancel_unfilled_buy_releases_cash_without_creating_holding(self) -> None:
        harness = OrderHarness()
        harness.create()
        cancelled = harness.cancel()
        self.assertEqual(cancelled["status"], "cancelled")
        self.assertEqual(cancelled["execution_state"], "armed")
        self.assertEqual(harness.reservations.docs[0]["status"], "released")
        self.assertEqual(harness.accounts.docs[0]["available_cash"], 78_000)
        self.assertEqual(harness.holdings.docs, [])

    def test_buy_order_rejects_unconfigured_or_insufficient_cash(self) -> None:
        missing = OrderHarness()
        missing.accounts.docs[0]["available_cash"] = None
        with self.assertRaisesRegex(OrderLifecycleError, "尚未配置可用现金"):
            missing.create()
        with self.assertRaisesRegex(OrderLifecycleError, "仅有"):
            OrderHarness(cash=1_000).create()

    def test_order_and_order_id_retries_are_stable(self) -> None:
        harness = OrderHarness()
        first = harness.create()
        second = harness.create()
        self.assertEqual(first, second)
        self.assertEqual(len(harness.orders.docs), 1)
        self.assertEqual(len(harness.order_events.docs), 1)
        with self.assertRaisesRegex(OrderLifecycleError, "内容不一致"):
            harness.create(quantity=300)

    def test_add_order_records_a_separate_buy_trade_and_recomputes_position(self) -> None:
        harness = OrderHarness([_holding()])
        order = harness.create(intent="add", quantity=50, limit_price=10.0)
        self.assertEqual(order["intent"], "add")
        result = harness.fill(quantity=50, price=9.9, fees=1)
        self.assertEqual(result["holding"]["quantity"], 150)
        self.assertAlmostEqual(result["holding"]["cost_basis"], 9.9733, places=4)
        self.assertAlmostEqual(result["holding"]["entry_size"], 0.0174, places=4)
        self.assertEqual(len(harness.executions.docs), 1)

    def test_buy_intent_must_match_current_position_state(self) -> None:
        with self.assertRaisesRegex(OrderLifecycleError, "请选择 add"):
            OrderHarness([_holding()]).create(intent="entry", quantity=50)
        with self.assertRaisesRegex(OrderLifecycleError, "没有活动持仓"):
            OrderHarness().create(intent="add", quantity=50)


class SellOrderLifecycleTests(unittest.TestCase):
    def test_sell_order_freezes_available_quantity(self) -> None:
        harness = OrderHarness([_holding()])
        order = harness.create(side="sell", intent="reduce", quantity=40, limit_price=10)
        holding = harness.holdings.docs[0]
        self.assertEqual(order["execution_state"], "reduce_pending")
        self.assertEqual(holding["quantity"], 100)
        self.assertEqual(holding["available_quantity"], 60)
        self.assertEqual(holding["frozen_quantity"], 40)

    def test_partial_sell_preserves_remaining_order_and_cancel_unfreezes_it(self) -> None:
        harness = OrderHarness([_holding()])
        harness.create(side="sell", intent="reduce", quantity=40, limit_price=9.5)
        result = harness.fill(quantity=20, price=10, fees=1)
        self.assertEqual(result["order"]["status"], "partially_filled")
        self.assertEqual(result["order"]["remaining_quantity"], 20)
        self.assertEqual(result["holding"]["quantity"], 80)
        self.assertEqual(result["holding"]["available_quantity"], 60)
        self.assertEqual(result["holding"]["frozen_quantity"], 20)
        self.assertEqual(result["account_profile"]["available_cash"], 78_199)
        harness.cancel()
        holding = harness.holdings.docs[0]
        self.assertEqual(holding["quantity"], 80)
        self.assertEqual(holding["available_quantity"], 80)
        self.assertEqual(holding["frozen_quantity"], 0)

    def test_full_exit_closes_holding_and_moves_cash(self) -> None:
        harness = OrderHarness([_holding()])
        harness.create(side="sell", intent="exit", quantity=100, limit_price=9.5)
        result = harness.fill(quantity=100, price=10, fees=2)
        self.assertEqual(result["order"]["status"], "filled")
        self.assertEqual(result["order"]["execution_state"], "closed")
        self.assertEqual(result["holding"]["status"], "closed")
        self.assertEqual(result["holding"]["quantity"], 0)
        self.assertEqual(result["account_profile"]["available_cash"], 78_998)

    def test_sell_fill_still_updates_position_when_cash_is_unconfigured(self) -> None:
        harness = OrderHarness([_holding()])
        harness.accounts.docs[0]["available_cash"] = None
        harness.create(side="sell", intent="reduce", quantity=40, limit_price=9.5)
        result = harness.fill(quantity=20, price=10, fees=1)
        self.assertEqual(result["holding"]["quantity"], 80)
        self.assertEqual(result["holding"]["frozen_quantity"], 20)
        self.assertIsNone(result["account_profile"]["available_cash"])
        self.assertEqual(result["execution"]["cash_delta"], 199)

    def test_sell_rejects_quantity_above_available_and_bad_fill_price(self) -> None:
        harness = OrderHarness([_holding(quantity=100, available=60)])
        with self.assertRaisesRegex(OrderLifecycleError, "当前可卖 60 股"):
            harness.create(side="sell", intent="reduce", quantity=100, limit_price=9.5)
        harness.create(side="sell", intent="reduce", quantity=60, limit_price=9.5)
        with self.assertRaisesRegex(OrderLifecycleError, "不能低于限价"):
            harness.fill(quantity=20, price=9.4)


class T1SettlementTests(unittest.TestCase):
    def test_same_day_buy_stays_unsellable_and_next_session_unlocks(self) -> None:
        harness = OrderHarness()
        harness.create(quantity=100)
        harness.fill(quantity=100)
        same_day = settle_t1_positions(
            harness.holdings,
            as_of_date="2026-07-15",
            position_event_collection=harness.position_events,
            settled_at="2026-07-15T15:30:00+08:00",
        )
        self.assertEqual(same_day, ())
        self.assertEqual(harness.holdings.docs[0]["available_quantity"], 0)
        next_day = settle_t1_positions(
            harness.holdings,
            as_of_date="2026-07-16",
            position_event_collection=harness.position_events,
            settled_at="2026-07-16T09:00:00+08:00",
        )
        self.assertEqual(len(next_day), 1)
        self.assertEqual(next_day[0]["available_quantity"], 100)
        self.assertEqual(next_day[0]["t1_locked_quantity"], 0)

    def test_order_listing_filters_and_sorts(self) -> None:
        harness = OrderHarness()
        harness.create(order_id="old", placed_at="2026-07-15T14:20:00+08:00")
        harness.create(order_id="new", plan_id="plan-2", ticker="600000.SH")
        self.assertEqual(get_trade_order(harness.orders, "old")["order_id"], "old")
        listed = list_trade_orders(harness.orders, limit=10)
        self.assertEqual([item["order_id"] for item in listed], ["new", "old"])
        self.assertEqual(list_trade_orders(harness.orders, ticker="600000.SH")[0]["order_id"], "new")


class OrderExpiryTests(unittest.TestCase):
    def test_default_1530_expiry_releases_buy_reservation_once(self) -> None:
        harness = OrderHarness()
        harness.create(placed_at="2026-07-15T14:20:00+08:00")

        before = expire_trade_orders(
            orders_collection=harness.orders,
            holdings_collection=harness.holdings,
            reservations_collection=harness.reservations,
            position_event_collection=harness.position_events,
            order_event_collection=harness.order_events,
            as_of="2026-07-15T15:29:59+08:00",
        )
        after = expire_trade_orders(
            orders_collection=harness.orders,
            holdings_collection=harness.holdings,
            reservations_collection=harness.reservations,
            position_event_collection=harness.position_events,
            order_event_collection=harness.order_events,
            as_of="2026-07-15T15:30:00+08:00",
        )
        replay = expire_trade_orders(
            orders_collection=harness.orders,
            holdings_collection=harness.holdings,
            reservations_collection=harness.reservations,
            position_event_collection=harness.position_events,
            order_event_collection=harness.order_events,
            as_of="2026-07-15T15:31:00+08:00",
        )

        self.assertEqual(before["expired_order_ids"], [])
        self.assertEqual(after["expired_order_ids"], ["order-1"])
        self.assertEqual(replay["expired_order_ids"], [])
        self.assertEqual(harness.reservations.docs[0]["status"], "released")
        self.assertEqual(harness.order_events.docs[-1]["event_type"], "order_expired")

    def test_partial_sell_expiry_releases_only_remaining_frozen_quantity(self) -> None:
        harness = OrderHarness([_holding()])
        harness.create(side="sell", intent="reduce", quantity=40, limit_price=9.5)
        harness.fill(quantity=20, price=10, fees=1)

        result = expire_trade_orders(
            orders_collection=harness.orders,
            holdings_collection=harness.holdings,
            reservations_collection=harness.reservations,
            position_event_collection=harness.position_events,
            order_event_collection=harness.order_events,
            as_of="2026-07-15T15:30:00+08:00",
        )

        self.assertEqual(result["expired_order_ids"], ["order-1"])
        self.assertEqual(harness.holdings.docs[0]["quantity"], 80)
        self.assertEqual(harness.holdings.docs[0]["available_quantity"], 80)
        self.assertEqual(harness.holdings.docs[0]["frozen_quantity"], 0)


if __name__ == "__main__":
    unittest.main()
