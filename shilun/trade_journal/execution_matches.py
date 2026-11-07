"""Explicit identity links between archived fills and the live execution owner.

Links do not apply an execution, reconstruct FIFO, change cash, or add another
profit stream. Similar price/quantity is a candidate, never proof of identity.
"""
from copy import deepcopy
from decimal import Decimal, InvalidOperation

from .ledger import digest
from .service import TradeJournalError, TradeJournalService, _clean, _time

COLLECTION = "trade_journal_execution_matches"
VERSION = "journal-execution-matches-v1"
LIVE_FIELDS = {"execution_id", "order_id", "plan_id", "account_alias", "ticker", "side",
               "quantity", "price", "fees", "cash_delta", "executed_at", "source"}
FILL_FIELDS = {"fill_id", "account_alias", "ticker", "date", "time", "time_precision",
               "side", "quantity", "price", "amount", "fees", "net_cash", "source_ref"}


class ExecutionMatchError(TradeJournalError):
    pass


def _decimal(value, field):
    try:
        number = Decimal(str(value))
        if not number.is_finite():
            raise InvalidOperation
        return number
    except (InvalidOperation, TypeError, ValueError):
        raise ExecutionMatchError(f"{field}缺失或不是有限数字") from None


def _money(value):
    return _decimal(value, "金额").quantize(Decimal("0.01"))


class ExecutionMatchService:
    def __init__(self, store, *, journal=None):
        self.store = store
        self.journal = journal or TradeJournalService(store)
        self.collection = store.collection(COLLECTION)

    def _records(self, execution_id=None):
        rows = [_clean(row) for row in self.collection.find(
            {"execution_id": execution_id} if execution_id else {}, {"_id": 0})]
        for row in rows:
            if digest({k: v for k, v in row.items() if k != "content_hash"}) != row.get("content_hash"):
                raise ExecutionMatchError("成交匹配不可变记录校验失败")
        return rows

    def _latest(self):
        result = {}
        for row in self._records():
            key = row["execution_id"]
            if key not in result or row["revision"] > result[key]["revision"]:
                result[key] = row
        return result

    def _live(self, execution_id):
        rows = list(self.store.collection("trade_executions").find({"execution_id": execution_id}, {"_id": 0}))
        if len(rows) != 1:
            raise ExecutionMatchError("生产成交不存在或标识不唯一")
        return {k: v for k, v in _clean(rows[0]).items() if k in LIVE_FIELDS}

    def get_match(self, execution_id):
        history = sorted(self._records(execution_id), key=lambda row: row["revision"])
        latest = history[-1] if history else None
        live = self._live(execution_id)
        state = "unmatched" if latest is None else latest["state"]
        if latest and state == "confirmed" and digest(live) != latest["live_digest"]:
            state = "stale"
        return {"execution_id": execution_id, "revision": latest["revision"] if latest else 0,
                "state": state, "record": latest, "history": history,
                "live_execution": live, "live_digest": digest(live),
                "accounting_scope": "匹配仅关联同一事实；归档FIFO仍每笔计一次，不回写生产账户"}

    def candidates(self, episode_id):
        detail = self.journal.get_episode(episode_id)
        episode, fills = detail["episode"], detail["fills"]
        dates = {row["date"] for row in fills}
        rows = []
        for raw in self.store.collection("trade_executions").find({"ticker": episode["ticker"]}, {"_id": 0}):
            execution_id = raw.get("execution_id")
            if not execution_id:
                continue
            try:
                date = _time(raw.get("executed_at")).date().isoformat()
            except TradeJournalError:
                continue
            if date not in dates:
                continue
            match = self.get_match(execution_id)
            match.pop("history")
            rows.append(match)
        return {"episode_id": episode_id, "account_alias": episode["account_alias"],
                "items": rows, "fills": [{k: v for k, v in row.items() if k in FILL_FIELDS} for row in fills],
                "scope": "同证券及真实成交日期的候选；时间相近或同价同量不自动匹配"}

    def preview(self, episode_id, execution_id, fill_ids):
        detail = self.journal.get_episode(episode_id)
        if not isinstance(fill_ids, list) or not fill_ids or any(not isinstance(v, str) for v in fill_ids) or len(set(fill_ids)) != len(fill_ids):
            raise ExecutionMatchError("匹配须明确选择不重复的完整归档成交fill_ids")
        by_id = {row["fill_id"]: row for row in detail["fills"]}
        if not set(fill_ids) <= set(by_id):
            raise ExecutionMatchError("所选成交不属于该库存轮")
        fills = [{k: v for k, v in by_id[key].items() if k in FILL_FIELDS} for key in sorted(fill_ids)]
        live = self._live(execution_id)
        conflicts, warnings = [], []
        day = _time(live.get("executed_at")).date().isoformat()
        for row in fills:
            if row["ticker"] != live.get("ticker") or row["side"] != live.get("side") or row["date"] != day:
                conflicts.append("证券、方向或交易日不一致")
        if live.get("account_alias") and live["account_alias"] != detail["episode"]["account_alias"]:
            conflicts.append("账户别名不一致")
        quantity = sum(row["quantity"] for row in fills)
        if _decimal(live.get("quantity"), "生产成交股数") != quantity:
            conflicts.append("选中成交股数合计与生产回写股数不同")
        gross = sum((_decimal(row["amount"], "归档实际成交金额") if row.get("amount") is not None
                     else _decimal(row["price"], "归档成交价") * row["quantity"] for row in fills), Decimal(0))
        live_quantity = _decimal(live.get("quantity"), "生产成交股数")
        live_price = _decimal(live.get("price"), "生产成交价")
        if live_quantity <= 0 or live_price <= 0:
            raise ExecutionMatchError("生产成交股数和价格须大于零")
        live_gross = live_price * live_quantity
        gross_basis = "生产存储均价乘股数；尚缺可核对的净收付与费用"
        if live.get("cash_delta") is not None and live.get("fees") is not None:
            cash, live_fee = _decimal(live["cash_delta"], "生产净收付"), _decimal(live["fees"], "生产费用")
            live_gross = -cash-live_fee if live.get("side") == "buy" else cash+live_fee
            gross_basis = "生产实际净收付及费用还原总额；不以四位存储均价重建现金"
            # The existing execution owner stores round(price, 4), but computes
            # cash from the submitted price. Validate that display precision
            # without turning its rounding difference into a cash conflict.
            if (live_gross/live_quantity).quantize(Decimal("0.0001")) != live_price.quantize(Decimal("0.0001")):
                conflicts.append("生产存储价格与其实际净收付、费用不一致")
            elif _money(live_price*live_quantity) != _money(live_gross):
                warnings.append("生产均价按原所有者保留四位；总金额以已知净收付及费用核对，不用均价截位误差替代现金")
        if _money(gross) != _money(live_gross):
            conflicts.append("选中成交总金额与生产回写总金额不同")
        fees = None if any(row.get("fees") is None for row in fills) else sum((_decimal(row["fees"], "归档费用") for row in fills), Decimal(0))
        if fees is None or live.get("fees") is None:
            warnings.append("至少一侧费用未知；确认事实关联不补齐费用或收益资格")
        elif _money(fees) != _money(live["fees"]):
            conflicts.append("归档费用合计与生产回写费用不同，须先核对原记录")
        if any(row.get("net_cash") is None for row in fills) or live.get("cash_delta") is None:
            warnings.append("至少一侧净收付缺失；不能据此认定现金对账完成")
        elif _money(sum((_decimal(row["net_cash"], "归档净收付") for row in fills), Decimal(0))) != _money(live["cash_delta"]):
            conflicts.append("归档净收付合计与生产回写现金变化不同")
        precise_times = [row["time"] if "T" in row["time"] else f'{row["date"]}T{row["time"]}'
                         for row in fills if row.get("time_precision") == "execution_time" and row.get("time")]
        same_time = len(fills) == 1 and len(precise_times) == 1 and _time(precise_times[0]) == _time(live["executed_at"])
        if not same_time:
            warnings.append("两侧时间粒度、具体时刻或分笔粒度不同，需明确确认差异，不能自动证明同一事实")
        for other in self._latest().values():
            if other["execution_id"] != execution_id and other["state"] == "confirmed" and set(other["fill_ids"]) & set(fill_ids):
                conflicts.append("选中归档成交已关联另一份生产成交，须先明确解除旧关联")
        current = self.get_match(execution_id)
        result = {"episode_id": episode_id, "execution_id": execution_id,
                  "account_alias": detail["episode"]["account_alias"], "fill_ids": sorted(fill_ids),
                  "fills": fills, "live_execution": live, "live_digest": digest(live),
                  "fill_digest": digest(fills), "expected_revision": current["revision"],
                  "gross_reconciliation": {"archive_amount":float(gross),"live_amount":float(live_gross),"live_basis":gross_basis},
                  "conflicts": sorted(set(conflicts)), "warnings": warnings,
                  "requires_time_difference_confirmation": not same_time,
                  "can_confirm": not conflicts, "version": VERSION}
        result["preview_digest"] = digest(result)
        return result

    def append_match(self, episode_id, payload):
        if not isinstance(payload, dict) or not isinstance(payload.get("execution_id"), str):
            raise ExecutionMatchError("匹配须提供生产execution_id")
        if not isinstance(payload.get("reason"), str) or not payload["reason"].strip():
            raise ExecutionMatchError("确认或解除匹配须保留独立理由")
        action = payload.get("action")
        if action not in {"confirm", "unlink"}:
            raise ExecutionMatchError("匹配动作只能为confirm或unlink")
        with self.journal.write_guard():
            execution_id = payload["execution_id"]
            current = self.get_match(execution_id)
            if type(payload.get("expected_revision")) is not int or payload["expected_revision"] != current["revision"]:
                raise ExecutionMatchError("匹配版本已变化，请重新读取")
            if action == "confirm":
                preview = self.preview(episode_id, execution_id, payload.get("fill_ids"))
                if payload.get("preview_digest") != preview["preview_digest"]:
                    raise ExecutionMatchError("匹配预览冲突或已过时，请重新预览")
                if not preview["can_confirm"]:
                    raise ExecutionMatchError("；".join(preview["conflicts"]))
                if payload.get("identity_confirmation") is not True or payload.get("account_confirmation") is not True:
                    raise ExecutionMatchError("须明确确认这是同一账户的同一真实成交，不能以相似价量自动关联")
                if preview["requires_time_difference_confirmation"] and payload.get("time_difference_confirmation") is not True:
                    raise ExecutionMatchError("须明确确认已核对两侧时刻或分笔粒度差异")
                record = {k: deepcopy(preview[k]) for k in ("episode_id", "account_alias", "fill_ids", "fills", "live_execution", "live_digest", "fill_digest", "warnings", "gross_reconciliation", "preview_digest")}
                record["state"] = "confirmed"
                record["confirmations"] = {"identity":True,"account":True,
                    "time_difference":payload.get("time_difference_confirmation") is True}
            else:
                if not current["record"] or current["record"]["episode_id"] != episode_id:
                    raise ExecutionMatchError("该库存轮没有可解除的生产成交关联")
                if payload.get("live_digest") != current["live_digest"]:
                    raise ExecutionMatchError("生产成交内容已变化，请重新读取后解除")
                record = {"episode_id": episode_id, "account_alias": current["record"]["account_alias"],
                          "fill_ids": [], "fills": [], "live_execution": current["live_execution"],
                          "live_digest": current["live_digest"], "fill_digest": digest([]), "warnings": [], "state": "unlinked"}
            record.update(execution_id=execution_id, revision=current["revision"] + 1, previous_revision=current["revision"],
                          reason=payload["reason"].strip(), captured_at=self.journal._now(), version=VERSION,
                          evidence_class="user_confirmed_identity", changes_production_account=False)
            record["content_hash"] = digest(record)
            key = {"execution_id": execution_id, "revision": record["revision"]}
            self.journal.renew_write_lease()
            self.collection.update_one(key, {"$setOnInsert": deepcopy(record)}, upsert=True)
            saved = _clean(self.collection.find_one(key, {"_id": 0}))
            if saved != record:
                raise ExecutionMatchError("成交关联保存冲突，原版本未覆盖")
            return {"record": saved, "match": self.get_match(execution_id)}
