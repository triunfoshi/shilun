"""持仓（`holdings`）数据层的单元测试。

覆盖 CRUD + 状态迁移 + 幂等 + 输入校验。用 in-memory FakeCollection 桩，
避免依赖真实 Mongo。
"""

from __future__ import annotations

import unittest

from shilun.market.holdings import (
    HOLDING_STATUSES,
    HoldingRecord,
    HoldingsError,
    close_holding,
    delete_holding,
    get_holding,
    list_holdings,
    patch_holding,
    upsert_holding,
)


class FakeCollection:
    """最小可用的 in-memory Mongo collection 桩。"""

    def __init__(self) -> None:
        self._docs: list[dict] = []

    @staticmethod
    def _match(doc: dict, query: dict) -> bool:
        for key, expect in query.items():
            actual = doc.get(key)
            if isinstance(expect, dict):
                for op, op_val in expect.items():
                    if op == "$in" and actual not in op_val:
                        return False
                    if op == "$lte" and not (actual is not None and actual <= op_val):
                        return False
                    if op == "$gte" and not (actual is not None and actual >= op_val):
                        return False
            else:
                if actual != expect:
                    return False
        return True

    def find_one(self, query: dict, projection: dict | None = None) -> dict | None:
        for doc in self._docs:
            if self._match(doc, query):
                return dict(doc)
        return None

    def update_one(self, query: dict, update: dict, upsert: bool = False) -> None:
        for doc in self._docs:
            if self._match(doc, query):
                doc.update(update.get("$set", {}))
                return
        if upsert:
            new_doc = dict(query)
            new_doc.update(update.get("$set", {}))
            self._docs.append(new_doc)

    def delete_one(self, query: dict) -> "FakeCollection._DeleteResult":
        for i, doc in enumerate(self._docs):
            if self._match(doc, query):
                del self._docs[i]
                return self._DeleteResult(1)
        return self._DeleteResult(0)

    class _DeleteResult:
        def __init__(self, deleted_count: int) -> None:
            self.deleted_count = deleted_count

    class _Cursor:
        def __init__(self, docs: list[dict]) -> None:
            self._docs = docs

        def sort(self, key: str, direction: int = 1) -> "FakeCollection._Cursor":
            self._docs = sorted(self._docs, key=lambda d: d.get(key) or "", reverse=direction < 0)
            return self

        def __iter__(self):
            return iter([dict(d) for d in self._docs])

    def find(self, query: dict, projection: dict | None = None) -> "FakeCollection._Cursor":
        matched = [doc for doc in self._docs if self._match(doc, query)]
        return self._Cursor(matched)


def _rec(
    *,
    ticker: str = "603986.SH",
    entry_price: float = 518.00,
    entry_size: float = 0.3,
    entry_date: str = "2026-06-15",
    entry_signal: str = "ma5_pullback_confirm",
    **overrides,
) -> HoldingRecord:
    return HoldingRecord(
        ticker=ticker,
        entry_date=entry_date,
        entry_price=entry_price,
        entry_signal=entry_signal,
        entry_size=entry_size,
        **overrides,
    )


class UpsertHoldingTest(unittest.TestCase):
    def test_first_insert_returns_inserted(self) -> None:
        col = FakeCollection()
        result = upsert_holding(col, _rec())
        self.assertEqual(result, "inserted")
        doc = get_holding(col, "603986.SH")
        self.assertIsNotNone(doc)
        self.assertEqual(doc["status"], "active")
        self.assertEqual(doc["entry_size"], 0.3)
        self.assertEqual(doc["realized_size"], 0.0)

    def test_duplicate_active_holding_is_rejected(self) -> None:
        col = FakeCollection()
        upsert_holding(col, _rec())
        with self.assertRaises(HoldingsError) as ctx:
            upsert_holding(col, _rec(entry_price=520.0))
        self.assertIn("已存在", str(ctx.exception))

    def test_reopen_after_close_is_allowed(self) -> None:
        col = FakeCollection()
        upsert_holding(col, _rec())
        close_holding(col, "603986.SH", exit_price=600.0)
        # 重新开仓
        result = upsert_holding(col, _rec(entry_price=550.0, entry_date="2026-07-01"))
        self.assertEqual(result, "reopened")
        doc = get_holding(col, "603986.SH")
        self.assertEqual(doc["status"], "active")
        self.assertEqual(doc["entry_price"], 550.0)

    def test_invalid_status_rejected(self) -> None:
        col = FakeCollection()
        rec = _rec()
        rec.status = "bogus"
        with self.assertRaises(HoldingsError):
            upsert_holding(col, rec)

    def test_full_field_roundtrip(self) -> None:
        col = FakeCollection()
        upsert_holding(col, _rec(
            name="兆易创新",
            sector_name="半导体",
            target_price=620.0,
            stop_loss_1=507.64,
            stop_loss_2=495.0,
            breakdown_level=485.0,
            add_point=555.0,
            signal_candle_open=518.0,
            invalid_conditions=["close < signal_candle_open", "MA20 * 0.99"],
            note="半导体主线",
        ))
        doc = get_holding(col, "603986.SH")
        self.assertEqual(doc["name"], "兆易创新")
        self.assertEqual(doc["target_price"], 620.0)
        self.assertEqual(doc["signal_candle_open"], 518.0)
        self.assertEqual(len(doc["invalid_conditions"]), 2)


class ListHoldingsTest(unittest.TestCase):
    def test_defaults_to_active_plus_reduced(self) -> None:
        col = FakeCollection()
        upsert_holding(col, _rec(ticker="A"))
        upsert_holding(col, _rec(ticker="B", entry_date="2026-05-01"))
        close_holding(col, "B", exit_price=600.0)
        result = list_holdings(col)
        tickers = [d["ticker"] for d in result]
        self.assertEqual(tickers, ["A"], "默认只返回 active/reduced，closed 不算")

    def test_explicit_closed_filter(self) -> None:
        col = FakeCollection()
        upsert_holding(col, _rec(ticker="A"))
        upsert_holding(col, _rec(ticker="B"))
        close_holding(col, "B", exit_price=600.0)
        result = list_holdings(col, statuses=["closed"])
        self.assertEqual([d["ticker"] for d in result], ["B"])

    def test_sorted_by_entry_date_desc(self) -> None:
        col = FakeCollection()
        upsert_holding(col, _rec(ticker="OLDER", entry_date="2026-04-01"))
        upsert_holding(col, _rec(ticker="NEWER", entry_date="2026-06-01"))
        result = list_holdings(col)
        self.assertEqual([d["ticker"] for d in result], ["NEWER", "OLDER"])


class PatchHoldingTest(unittest.TestCase):
    def test_patch_stop_loss(self) -> None:
        col = FakeCollection()
        upsert_holding(col, _rec(stop_loss_1=500.0))
        updated = patch_holding(col, "603986.SH", {"stop_loss_1": 510.0})
        self.assertEqual(updated["stop_loss_1"], 510.0)

    def test_patch_status_directly_is_forbidden(self) -> None:
        col = FakeCollection()
        upsert_holding(col, _rec())
        with self.assertRaises(HoldingsError):
            patch_holding(col, "603986.SH", {"status": "closed"})

    def test_patch_entry_price_forbidden(self) -> None:
        col = FakeCollection()
        upsert_holding(col, _rec())
        with self.assertRaises(HoldingsError):
            patch_holding(col, "603986.SH", {"entry_price": 999.0})

    def test_realized_size_partial_reduces_status_to_reduced(self) -> None:
        col = FakeCollection()
        upsert_holding(col, _rec(entry_size=0.3))
        updated = patch_holding(col, "603986.SH", {"realized_size": 0.1})
        self.assertEqual(updated["status"], "reduced")
        self.assertEqual(updated["realized_size"], 0.1)

    def test_realized_size_reaches_full_auto_closes(self) -> None:
        col = FakeCollection()
        upsert_holding(col, _rec(entry_size=0.3))
        updated = patch_holding(col, "603986.SH", {"realized_size": 0.3})
        self.assertEqual(updated["status"], "closed")

    def test_realized_size_negative_rejected(self) -> None:
        col = FakeCollection()
        upsert_holding(col, _rec(entry_size=0.3))
        with self.assertRaises(HoldingsError):
            patch_holding(col, "603986.SH", {"realized_size": -0.05})

    def test_realized_size_overflow_rejected(self) -> None:
        col = FakeCollection()
        upsert_holding(col, _rec(entry_size=0.3))
        with self.assertRaises(HoldingsError):
            patch_holding(col, "603986.SH", {"realized_size": 0.5})

    def test_patch_on_closed_holding_rejected(self) -> None:
        col = FakeCollection()
        upsert_holding(col, _rec())
        close_holding(col, "603986.SH", exit_price=600.0)
        with self.assertRaises(HoldingsError):
            patch_holding(col, "603986.SH", {"stop_loss_1": 500.0})

    def test_empty_updates_rejected(self) -> None:
        col = FakeCollection()
        upsert_holding(col, _rec())
        with self.assertRaises(HoldingsError):
            patch_holding(col, "603986.SH", {})


class CloseHoldingTest(unittest.TestCase):
    def test_close_computes_realized_pnl(self) -> None:
        col = FakeCollection()
        upsert_holding(col, _rec(entry_price=500.0))
        closed = close_holding(col, "603986.SH", exit_price=600.0, exit_date="2026-07-10")
        self.assertEqual(closed["status"], "closed")
        self.assertEqual(closed["exit_price"], 600.0)
        self.assertEqual(closed["exit_date"], "2026-07-10")
        self.assertAlmostEqual(closed["realized_pnl"], 0.2, places=4)

    def test_close_without_exit_price_still_works(self) -> None:
        """允许仅关闭状态、不填 exit_price（例如手动清理误录入的持仓）。"""
        col = FakeCollection()
        upsert_holding(col, _rec())
        closed = close_holding(col, "603986.SH", reason="录入错误")
        self.assertEqual(closed["status"], "closed")
        self.assertIsNone(closed.get("exit_price"))
        self.assertEqual(closed["close_reason"], "录入错误")

    def test_close_is_idempotent(self) -> None:
        col = FakeCollection()
        upsert_holding(col, _rec())
        first = close_holding(col, "603986.SH", exit_price=600.0)
        second = close_holding(col, "603986.SH", exit_price=999.0)
        # 再次 close 不覆盖，直接返回原状态
        self.assertEqual(second["exit_price"], first["exit_price"])

    def test_close_nonexistent_raises(self) -> None:
        col = FakeCollection()
        with self.assertRaises(HoldingsError):
            close_holding(col, "NOTHING")


class DeleteHoldingTest(unittest.TestCase):
    def test_delete_removes_record(self) -> None:
        col = FakeCollection()
        upsert_holding(col, _rec())
        self.assertTrue(delete_holding(col, "603986.SH"))
        self.assertIsNone(get_holding(col, "603986.SH"))

    def test_delete_nonexistent_returns_false(self) -> None:
        col = FakeCollection()
        self.assertFalse(delete_holding(col, "NOTHING"))


class StatusConstantsTest(unittest.TestCase):
    def test_status_set_is_locked(self) -> None:
        self.assertEqual(HOLDING_STATUSES, {"active", "reduced", "closed"})


if __name__ == "__main__":
    unittest.main()
