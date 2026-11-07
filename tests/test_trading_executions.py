from __future__ import annotations

import unittest

from shilun.trading.executions import ExecutionError, list_executions, record_sell_execution


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
        return all(doc.get(key) == value for key, value in query.items())

    def find_one(self, query, projection=None):
        return next((dict(item) for item in self.docs if self._match(item, query)), None)

    def find(self, query, projection=None):
        return _Cursor(item for item in self.docs if self._match(item, query))

    def update_one(self, query, update, upsert=False):
        for item in self.docs:
            if self._match(item, query):
                item.update(update.get("$set", {}))
                return
        if upsert:
            self.docs.append({**query, **update.get("$setOnInsert", {}), **update.get("$set", {})})


def _holding():
    return {
        "ticker": "603986.SH",
        "name": "兆易创新",
        "status": "active",
        "quantity": 100,
        "entry_price": 663.63,
        "cost_basis": 663.673,
        "entry_size": 1.0,
        "realized_loss_amount": 0,
        "pending_risk_action": {
            "status": "pending",
            "action_type": "risk_exit",
            "remaining_quantity": 100,
        },
    }


class TradingExecutionTests(unittest.TestCase):
    def test_partial_sell_updates_quantity_loss_and_pending_action(self) -> None:
        holdings = _Collection([_holding()])
        executions = _Collection()
        position_events = _Collection()

        execution, updated = record_sell_execution(
            holdings_collection=holdings,
            executions_collection=executions,
            position_event_collection=position_events,
            ticker="603986.sh",
            quantity=40,
            price=612,
            fees=10,
            executed_at="2026-07-13T09:35:00+08:00",
        )

        self.assertEqual(execution["quantity_after"], 60)
        self.assertEqual(updated["quantity"], 60)
        self.assertEqual(updated["status"], "reduced")
        self.assertEqual(updated["pending_risk_action"]["status"], "partially_executed")
        self.assertEqual(updated["realized_loss_amount"], 2076.92)
        self.assertEqual(updated["available_quantity"], 60)
        self.assertEqual(updated["position_revision"], 1)
        self.assertEqual(position_events.docs[0]["changes"]["quantity"], 60)
        self.assertEqual(list_executions(executions, "603986.SH")[0]["execution_id"], execution["execution_id"])

    def test_full_sell_closes_holding_and_completes_pending_action(self) -> None:
        holdings = _Collection([_holding()])
        executions = _Collection()

        _, updated = record_sell_execution(
            holdings_collection=holdings,
            executions_collection=executions,
            ticker="603986.SH",
            quantity=100,
            price=612,
            executed_at="2026-07-13T09:35:00+08:00",
        )

        self.assertEqual(updated["quantity"], 0)
        self.assertEqual(updated["status"], "closed")
        self.assertEqual(updated["exit_price"], 612)
        self.assertEqual(updated["pending_risk_action"]["status"], "completed")

    def test_sell_cannot_exceed_actual_position(self) -> None:
        with self.assertRaisesRegex(ExecutionError, "不能超过当前持仓 100 股"):
            record_sell_execution(
                holdings_collection=_Collection([_holding()]),
                executions_collection=_Collection(),
                ticker="603986.SH",
                quantity=101,
                price=612,
            )

    def test_execution_id_replay_is_idempotent_and_does_not_move_cash_twice(self) -> None:
        holdings = _Collection([_holding()])
        executions = _Collection()
        accounts = _Collection([{
            "profile_id": "default",
            "total_assets": 86_000,
            "available_cash": 78_000,
            "max_loss_per_trade": 6_880,
            "source": "user_configured",
        }])
        inputs = {
            "holdings_collection": holdings,
            "executions_collection": executions,
            "account_collection": accounts,
            "ticker": "603986.SH",
            "quantity": 40,
            "price": 612,
            "fees": 10,
            "execution_id": "stable-fill-1",
            "executed_at": "2026-07-13T09:35:00+08:00",
        }

        first, _ = record_sell_execution(**inputs)
        replay, updated = record_sell_execution(**inputs)

        self.assertEqual(first, replay)
        self.assertEqual(updated["quantity"], 60)
        self.assertEqual(accounts.docs[0]["available_cash"], 102_470)
        self.assertEqual(len(executions.docs), 1)

    def test_unconfigured_cash_does_not_block_sell_fact(self) -> None:
        holdings = _Collection([_holding()])
        accounts = _Collection([{
            "profile_id": "default",
            "total_assets": 86_000,
            "available_cash": None,
            "max_loss_per_trade": 6_880,
            "source": "default",
        }])

        execution, updated = record_sell_execution(
            holdings_collection=holdings,
            executions_collection=_Collection(),
            account_collection=accounts,
            ticker="603986.SH",
            quantity=40,
            price=612,
        )

        self.assertEqual(updated["quantity"], 60)
        self.assertIsNone(execution["account_available_cash_after"])
        self.assertEqual(execution["cash_update_status"], "cash_not_configured")


if __name__ == "__main__":
    unittest.main()
