"""Append-only quantity attribution over the journal's existing FIFO matches.

This module never reconstructs FIFO and never writes live executions/positions.
A mode label alone is not a quantity allocation or an independent profit stream.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from copy import deepcopy
from decimal import Decimal
from typing import Any
import uuid

from bson import BSON
from bson.errors import BSONError
from pymongo.errors import DuplicateKeyError

from .definitions import validate_definition_references
from .ledger import digest, money
from .service import TradeJournalError, TradeJournalService, _clean, _number, _time, _evidence_class

COLLECTION = "trade_journal_allocations"
VERSION = "trade-journal-allocations-v1"


class AllocationError(TradeJournalError):
    pass


def mode_key(ref):
    return f'{ref["id"]}@{ref["rule_version"]}'


class TradeAllocationService:
    def __init__(self, store, *, journal=None):
        self.store = store
        self.journal = journal or TradeJournalService(store)
        self.collection = store.collection(COLLECTION)

    def _records(self, episode_id=None):
        query = {"episode_id": episode_id} if episode_id else {}
        rows = [_clean(row) for row in self.collection.find(query, {"_id": 0})]
        for row in rows:
            if digest({key: value for key, value in row.items() if key != "content_hash"}) != row.get("content_hash"):
                raise AllocationError("不可变数量分配校验失败")
        return rows

    def _definition(self, ref):
        if not isinstance(ref, dict) or not isinstance(ref.get("id"), str) or not ref.get("rule_version"):
            raise AllocationError("数量分配必须引用mode_ref的id与确切rule_version")
        if ref["id"].startswith("mode.user."):
            from .mode_cards import ModeCardService, ModeCardError
            try:
                return ModeCardService(self.store).get_definition_snapshot(ref["id"], rule_version=ref["rule_version"])
            except ModeCardError as error:
                raise AllocationError(str(error)) from error
        result = validate_definition_references([ref], expected_category="mode")
        if not result["valid"]:
            raise AllocationError("；".join(item["message"] for item in result["errors"]))
        return result["resolved"][0]

    def _normalize(self, detail, payload):
        rows = payload.get("assignments")
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            raise AllocationError("assignments须为逐成交的数量分配对象列表，不能整轮笼统分份")
        fills = {row["fill_id"]: row for row in detail["fills"]}
        match_map = {(row["sell_fill_id"], row["buy_lot_id"]): row for row in detail["episode"]["matches"]}
        result, by_fill, by_match, seen = [], Counter(), Counter(), set()
        for raw in rows:
            fill_id = raw.get("fill_id")
            if fill_id not in fills:
                raise AllocationError("数量分配引用的fill_id不属于该库存轮")
            fill = fills[fill_id]
            ref = deepcopy(raw.get("mode_ref"))
            definition = self._definition(ref)
            quantity = _number(raw.get("quantity"), "模式股数", minimum=1, integer=True)
            lot = raw.get("buy_lot_id")
            if fill["side"] == "buy":
                if lot and lot != fill_id:
                    raise AllocationError("买入模式股数只能归属于本笔真实买入批次")
                lot = fill_id
            elif lot and (fill_id, lot) not in match_map:
                raise AllocationError("卖出批次必须引用既有FIFO实际匹配，不能改写跨时库存")
            identity = (fill_id, mode_key(ref), lot)
            if identity in seen:
                raise AllocationError("同一成交、模式版本和成本批次重复分配，请合并股数")
            seen.add(identity)
            by_fill[fill_id] += quantity
            if by_fill[fill_id] > fill["quantity"]:
                raise AllocationError("模式分配股数合计超过真实成交股数，不能重复归属同一份收益")
            if fill["side"] == "sell" and lot:
                by_match[(fill_id, lot)] += quantity
                if by_match[(fill_id, lot)] > match_map[(fill_id, lot)]["quantity"]:
                    raise AllocationError("分配股数超过本笔FIFO实际匹配批次，不能跨批次挪用库存")
            result.append({"fill_id": fill_id, "side": fill["side"], "quantity": quantity,
                "mode_ref": {"id": definition["id"], "rule_version": definition["rule_version"]},
                "mode_key": mode_key(ref), "mode_label": definition["label"], "buy_lot_id": lot,
                "definition_snapshot": definition, "note": raw.get("note")})
        return result

    def append_allocations(self, episode_id, payload):
        if not isinstance(payload, dict) or not str(payload.get("reason") or "").strip():
            raise AllocationError("数量分配须保留独立reason；修订保存完整分配表")
        with self.journal.write_guard():
            detail = self.journal.get_episode(episode_id)
            history = self._records(episode_id)
            latest = max(history, key=lambda row: row["revision"]) if history else None
            if latest and (type(payload.get("expected_revision")) is not int or payload["expected_revision"] != latest["revision"]):
                raise AllocationError("数量分配版本已变化，请读取最新版本后追加")
            assignments = self._normalize(detail, payload)
            calculation = self._calculate(detail, assignments)
            now = self.journal._now()
            observed = payload.get("observed_at") or now
            if _time(observed) > _time(now):
                raise AllocationError("数量分配观察时间不能在未来")
            revision = latest["revision"]+1 if latest else 1
            record = {"allocation_id": f'{episode_id}-alloc-v{revision}', "episode_id": episode_id,
                "revision": revision, "previous_revision": latest["revision"] if latest else None,
                "version": VERSION, "assignments": assignments, "labels": deepcopy(payload.get("labels") or []),
                "reason": payload["reason"], "recorded_at": now, "observed_at": _time(observed).isoformat(),
                "evidence_class": _evidence_class(payload.get("evidence_class") or "retrospective_recall"),
                "ledger_version": detail["episode"]["ledger_version"],
                "fill_ids": [row["fill_id"] for row in detail["fills"]],
                "fifo_match_digest": digest(detail["episode"]["matches"]),
                "qualification_note": "股数与FIFO现金切片归属；人工归属不证明当时意图、按计划执行或模式有效"}
            record["content_hash"] = digest(record)
            try:
                if len(BSON.encode(record)) > 15*1024*1024:
                    raise AllocationError("数量分配超过15MiB预算，不截断真实股数记录")
            except (BSONError, TypeError, OverflowError) as error:
                raise AllocationError("数量分配不是有效可持久化结构") from error
            self.journal.renew_write_lease()
            key = {"episode_id": episode_id, "revision": revision}
            try:
                self.collection.update_one(key, {"$setOnInsert": deepcopy(record)}, upsert=True)
            except DuplicateKeyError:
                pass
            saved = _clean(self.collection.find_one(key, {"_id": 0}))
            if saved.get("content_hash") != record["content_hash"]:
                raise AllocationError("数量分配并发写入冲突，旧版本未被覆盖")
            return {"record": saved, "allocation": calculation, "revision": revision}

    def get_allocations(self, episode_id, revision=None):
        rows = self._records(episode_id)
        if revision is not None:
            if type(revision) is not int:
                raise AllocationError("revision须为整数")
            rows = [row for row in rows if row["revision"] == revision]
        if not rows:
            return None
        return max(rows, key=lambda row: row["revision"])

    def history(self, episode_id):
        rows = sorted(self._records(episode_id), key=lambda row: row["revision"])
        return {"episode_id": episode_id, "items": rows, "total": len(rows), "latest_revision": rows[-1]["revision"] if rows else None}

    def calculate(self, episode_id, revision=None):
        detail = self.journal.get_episode(episode_id)
        record = self.get_allocations(episode_id, revision)
        return self.calculate_detail(detail, record)

    def calculate_detail(self, detail, record=None):
        """Reuse an already loaded FIFO detail in whole-ledger reports."""
        episode_id = detail["episode"]["episode_id"]
        result = self._calculate(detail, record["assignments"] if record else [])
        result.update(episode_id=episode_id, revision=record["revision"] if record else None,
            allocation_id=record["allocation_id"] if record else None,
            labels=record.get("labels", []) if record else [])
        if record and record["fifo_match_digest"] != digest(detail["episode"]["matches"]):
            raise AllocationError("底层FIFO匹配已变化，数量分配需明确审查后追加，不能静默重算")
        return result

    def _calculate(self, detail, assignments):
        episode = detail["episode"]
        fills = {row["fill_id"]: row for row in detail["fills"]}
        matches = {(row["sell_fill_id"], row["buy_lot_id"]): row for row in episode["matches"]}
        by_sell = defaultdict(list)
        for key in matches:
            by_sell[key[0]].append(key)
        entry_caps, entry_assigned = Counter(), Counter()
        for row in assignments:
            if row["side"] == "buy":
                entry_caps[(row["fill_id"], row["mode_key"])] += row["quantity"]
                entry_assigned[row["fill_id"]] += row["quantity"]
        total_requested_mode = Counter()
        for row in assignments:
            if row["side"] != "sell":
                continue
            lot = row.get("buy_lot_id")
            options = by_sell[row["fill_id"]]
            if not lot and len(options) == 1:
                lot = options[0][1]
            if lot:
                total_requested_mode[(lot, row["mode_key"])] += row["quantity"]
        used_match, entries, warnings = Counter(), [], []
        for index, raw in enumerate(assignments):
            if raw["side"] != "sell":
                continue
            row = deepcopy(raw)
            row["assignment_index"] = index
            lot = row.get("buy_lot_id")
            options = by_sell[row["fill_id"]]
            if not lot and len(options) == 1:
                lot = options[0][1]
            row.update(buy_lot_id=lot, net_pnl=None, buy_cost=None, sell_proceeds=None,
                buy_fee=None, sell_fee=None, fees=None, return_on_cost=None,
                account_slice_pnl=None, attribution_known=False, reasons=[])
            if not lot:
                row["reasons"].append("卖出涉及多个实际FIFO成本批，未明确buy_lot_id，模式收益保持未知")
                entries.append(row)
                continue
            key = (row["fill_id"], lot)
            match = matches[key]
            used_match[key] += row["quantity"]
            if used_match[key] > match["quantity"]:
                raise AllocationError("同笔FIFO匹配股数被多次归属，超过真实数量")
            cap_key = (lot, row["mode_key"])
            requested_quantity = total_requested_mode[cap_key]
            buy = fills.get(lot)
            cap = entry_caps[cap_key]
            unassigned_entry = (buy["quantity"]-entry_assigned[lot]) if buy else 0
            if buy and unassigned_entry == 0 and requested_quantity > cap:
                raise AllocationError("卖出模式股数超过同一实际FIFO买入批次的模式库存，不能借用后来的买入")
            if row["mode_ref"]["id"] in {"mode.unknown", "mode.mixed"}:
                row["reasons"].append("模式身份未知或混合；数值归属不补出模式身份")
            if not buy or requested_quantity > cap:
                row["reasons"].append("该成本批的买入模式股数未获完整数值证明；不能只凭卖出标签推定收益")
            quantity = Decimal(row["quantity"])
            fraction = quantity / Decimal(match["quantity"])
            row["account_slice_pnl"] = money(Decimal(str(match["realized_pnl"]))*fraction) if match["realized_pnl"] is not None else None
            row["buy_cost"] = money(Decimal(str(match["buy_cost"]))*fraction) if match["buy_cost"] is not None else None
            row["sell_proceeds"] = money(Decimal(str(match["sell_proceeds"]))*fraction) if match["sell_proceeds"] is not None else None
            sale = fills[row["fill_id"]]
            row["buy_fee"] = money(Decimal(str(buy["fees"]))*quantity/Decimal(buy["quantity"])) if buy and buy.get("fees") is not None else None
            row["sell_fee"] = money(Decimal(str(sale["fees"]))*quantity/Decimal(sale["quantity"])) if sale.get("fees") is not None else None
            if row["buy_fee"] is None or row["sell_fee"] is None:
                row["reasons"].append("买入或卖出费用缺失，不补零")
            else:
                row["fees"] = money(Decimal(str(row["buy_fee"]))+Decimal(str(row["sell_fee"])))
            if row["account_slice_pnl"] is None or row["buy_cost"] is None:
                row["reasons"].append("实际FIFO成本或净收益未知")
            row["attribution_known"] = not row["reasons"]
            if row["attribution_known"]:
                row["net_pnl"] = row["account_slice_pnl"]
                row["return_on_cost"] = row["net_pnl"]/row["buy_cost"] if row["buy_cost"] and row["buy_cost"] > 0 else None
            entries.append(row)
        residual = []
        for key, match in matches.items():
            quantity = match["quantity"]-used_match[key]
            if quantity:
                residual.append({**match, "quantity": quantity,
                    "net_pnl": money(Decimal(str(match["realized_pnl"]))*Decimal(quantity)/Decimal(match["quantity"])) if match["realized_pnl"] is not None else None,
                    "reason": "实际FIFO现金切片尚未获得明确模式归属；包含未分配或多成本批未拆分数量"})
        grouped = defaultdict(list)
        for row in entries:
            grouped[row["mode_key"]].append(row)
        mode_results = []
        for key, rows in sorted(grouped.items()):
            known = [row for row in rows if row["attribution_known"]]
            all_known = len(known) == len(rows)
            mode_results.append({"mode_key": key, "mode_ref": rows[0]["mode_ref"], "mode_label": rows[0]["mode_label"],
                "quantity": sum(row["quantity"] for row in rows), "known_quantity": sum(row["quantity"] for row in known),
                "net_pnl": money(sum(Decimal(str(row["net_pnl"])) for row in known)) if all_known else None,
                "known_pnl_component": money(sum(Decimal(str(row["net_pnl"])) for row in known)),
                "buy_cost": money(sum(Decimal(str(row["buy_cost"])) for row in known)) if all_known else None,
                "fees": money(sum(Decimal(str(row["fees"])) for row in known)) if all_known else None,
                "attribution_known": all_known, "assignments": [row["assignment_index"] for row in rows],
                "reasons": sorted({reason for row in rows for reason in row["reasons"]})})
        account_known = all(row["realized_pnl"] is not None for row in matches.values())
        account_net = money(sum(Decimal(str(row["realized_pnl"])) for row in matches.values())) if account_known else None
        split_known = all(row["account_slice_pnl"] is not None for row in entries if row["buy_lot_id"]) and all(row["net_pnl"] is not None for row in residual)
        split_net = money(sum(Decimal(str(row["account_slice_pnl"])) for row in entries if row["buy_lot_id"]) + sum(Decimal(str(row["net_pnl"])) for row in residual)) if split_known else None
        if account_net is not None and split_net is not None and abs(account_net-split_net) > 0.00001:
            raise AllocationError("模式切片与既有FIFO净现金收益不一致，拒绝归属")
        filled_quantities = Counter()
        for row in assignments:
            filled_quantities[row["fill_id"]] += row["quantity"]
        return {"version": VERSION, "episode_id": episode["episode_id"], "mode_results": mode_results,
            "sell_attributions": entries, "unassigned_fifo_slices": residual,
            "fill_quantities": [{"fill_id": fill["fill_id"], "side": fill["side"], "quantity": fill["quantity"],
                "allocated_quantity": filled_quantities[fill["fill_id"]], "unallocated_quantity": fill["quantity"]-filled_quantities[fill["fill_id"]]} for fill in detail["fills"]],
            "reconciliation": {"account_fifo_net_pnl": account_net, "partition_net_pnl": split_net,
                "balanced": account_net is not None and split_net is not None and abs(account_net-split_net) <= 0.00001},
            "scope": "复用真实FIFO匹配逐股切片；未分配和未知归属不重复计入模式收益",
            "warnings": warnings, "qualification": "人工数量归属不授予事前或规则回测资格"}


__all__ = ["TradeAllocationService", "AllocationError", "COLLECTION", "VERSION"]
