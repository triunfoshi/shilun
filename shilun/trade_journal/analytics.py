"""Descriptive complete-denominator journal reports and read-only risk captures.

There is no strategy discovery, external AI, broker write or invented historical
NAV here. Closed-account results and attributed mode slices use different units.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from copy import deepcopy
from decimal import Decimal, InvalidOperation
from statistics import median
import json
import uuid

from bson import BSON
from bson.errors import BSONError
from pymongo.errors import DuplicateKeyError

from .allocations import TradeAllocationService, AllocationError
from .ledger import digest, money
from .service import TradeJournalError, TradeJournalService, _clean, _date, _number, _ticker, _time, _page

VERSION = "trade-journal-analytics-v1"
PORTFOLIO_COLLECTION = "trade_journal_portfolio_snapshots"
DIMENSIONS = ("mode", "month", "environment", "role", "execution_difference", "security_type")


def _stats(rows, *, sensitivity=True):
    known = [row for row in rows if row.get("net_pnl") is not None]
    values = [float(row["net_pnl"]) for row in known]
    gains = sum(value for value in values if value > 0)
    losses = -sum(value for value in values if value < 0)
    costs = [row.get("buy_cost") for row in known]
    cost_complete = bool(known) and all(value is not None and value > 0 for value in costs)
    total_cost = sum(costs) if cost_complete else None
    result = {"n": len(known), "episode_n": len({row["episode_id"] for row in known}),
        "net_pnl": round(sum(values), 8) if values else None,
        "median_pnl": round(median(values), 8) if values else None,
        "win_n": sum(value > 0 for value in values), "loss_n": sum(value < 0 for value in values),
        "flat_n": sum(value == 0 for value in values),
        "win_rate": sum(value > 0 for value in values)/len(values) if values else None,
        "profit_factor": gains/losses if values and losses else None,
        "profit_factor_status": "known" if values and losses else "no_loss_denominator" if values else "no_samples",
        "buy_cost": total_cost,
        "return_on_cost": sum(values)/total_cost if total_cost else None,
        "return_denominator": "既有FIFO实际含费买入成本；不是账户权益、预算或虚构净值",
        "tail": {"worst_pnl": min(values) if values else None, "best_pnl": max(values) if values else None,
            "worst_return": min((row["net_pnl"]/row["buy_cost"] for row in known if row.get("buy_cost") and row["buy_cost"] > 0), default=None),
            "worst_members": [row["member_id"] for row in known if row["net_pnl"] == min(values)] if values else []}}
    if sensitivity:
        largest = max(known, key=lambda row: abs(row["net_pnl"]), default=None)
        largest_gain = max((row for row in known if row["net_pnl"] > 0), key=lambda row: row["net_pnl"], default=None)
        result["largest_trade_sensitivity"] = {
            "drop_largest_absolute": {"removed_member": largest["member_id"], "removed_pnl": largest["net_pnl"],
                "remaining": _stats([row for row in known if row is not largest], sensitivity=False)} if largest else None,
            "drop_largest_gain": {"removed_member": largest_gain["member_id"], "removed_pnl": largest_gain["net_pnl"],
                "remaining": _stats([row for row in known if row is not largest_gain], sensitivity=False)} if largest_gain else None}
    return result


def _label(value):
    if value in (None, "", [], {}):
        return None
    if isinstance(value, dict):
        return str(value.get("id") or value.get("label") or value.get("state") or json.dumps(value, sort_keys=True, ensure_ascii=False))
    return str(value)


def _decision_dimension(decisions, field):
    values = sorted({text for row in decisions if (text := _label(row.get(field)))})
    return values[0] if len(values) == 1 else "mixed" if values else "unknown", values


class JournalAnalyticsService:
    def __init__(self, store, *, journal=None, allocations=None):
        self.store = store
        self.journal = journal or TradeJournalService(store)
        self.allocations = allocations or TradeAllocationService(store, journal=self.journal)

    def _security_type(self, episode, financial):
        if any(row["kind"] == "stock_credit" and row["financial_event_id"] in episode["financial_event_ids"] for row in financial):
            return "ipo", "真实新股入账与配售现金事件"
        code = episode["code"]
        if code.startswith(("159", "510", "512", "513", "515", "516", "518", "588", "560", "561", "562", "563", "589")):
            return "etf", "ETF代码家族代理，非产品基本信息核验"
        if code.startswith(("00", "30", "60", "68", "4", "8", "92")):
            return "secondary_equity", "A股普通证券代码家族；IPO库存轮另列"
        return "unknown", "尚缺明确证券类型来源"

    def report(self, query="", start_date=None, end_date=None, group_by=None):
        if isinstance(group_by, str):
            group_by = [item.strip() for item in group_by.split(",") if item.strip()]
        dimensions = list(dict.fromkeys(group_by if group_by is not None else DIMENSIONS))
        if any(dimension not in DIMENSIONS for dimension in dimensions):
            raise TradeJournalError("未知分析维度；仅支持mode/month/environment/role/execution_difference/security_type")
        ledger = self.journal._ledger()
        financial = self.journal._facts()[1]
        decisions = self.journal._all("decisions")
        fills_by_episode = defaultdict(list)
        for fill in ledger["fills"]:
            fills_by_episode[fill["episode_id"]].append(fill)
        allocation_records = {}
        if "mode" in dimensions:
            for row in self.allocations._records():
                key = row["episode_id"]
                if key not in allocation_records or row["revision"] > allocation_records[key]["revision"]:
                    allocation_records[key] = row
        members, excluded, grouped_members, grouped_excluded = [], [], defaultdict(list), defaultdict(list)
        for episode in ledger["episodes"]:
            if query and query.casefold() not in f'{episode["ticker"]} {episode["name"]} {" ".join(episode["names"])}'.casefold():
                continue
            basis_date = episode["end_date"] or episode["start_date"]
            if start_date and basis_date < _date(start_date) or end_date and basis_date > _date(end_date):
                continue
            latest = self.journal._latest_decisions(episode["episode_id"], decisions)
            environment, environment_raw = _decision_dimension(latest, "environment")
            role, role_raw = _decision_dimension(latest, "role")
            execution, execution_raw = _decision_dimension(latest, "execution_difference")
            security_type, type_basis = self._security_type(episode, financial)
            base = {"member_id": episode["episode_id"], "episode_id": episode["episode_id"],
                "ticker": episode["ticker"], "name": episode["name"], "status": episode["status"],
                "start_date": episode["start_date"], "end_date": episode["end_date"], "month": basis_date[:7],
                "environment": environment, "role": role, "execution_difference": execution,
                "security_type": security_type, "security_type_basis": type_basis,
                "decision_labels": {"environment": environment_raw, "role": role_raw, "execution_difference": execution_raw,
                    "modes": sorted({str(row.get("mode_id") or row.get("mode")) for row in latest if row.get("mode_id") or row.get("mode")})},
                "decision_evidence_classes": sorted({row["evidence_class"] for row in latest}),
                "net_pnl": episode["closed_pnl"], "buy_cost": episode["buy_cost"], "fees": episode["fees"],
                "mode": "mode.unallocated", "mode_attribution_known": False, "unit": "closed_inventory_episode"}
            reasons = []
            if episode["status"] != "closed":
                reasons.append("库存轮未闭合；不进入清仓胜率和已实现全轮收益")
            if episode["closed_pnl"] is None and episode["status"] == "closed":
                reasons.append("完整清仓收益未知；期初成本、费用或库存资料不齐")
            if not episode["inventory_valid"]:
                reasons.append("库存未通过一致性核对")
            if reasons:
                row = {**base, "net_pnl": None, "exclusion_reasons": reasons}
                excluded.append(row)
                excluded_modes = []
                allocation_record = allocation_records.get(episode["episode_id"])
                if "mode" in dimensions and allocation_record:
                    excluded_modes = sorted({item["mode_key"] for item in allocation_record["assignments"]})
                for mode in excluded_modes or [row["mode"]]:
                    grouped_row = {**row, "mode": mode}
                    key = tuple(grouped_row[dimension] for dimension in dimensions)
                    grouped_excluded[key].append(grouped_row)
                continue
            members.append(base)
            parts = []
            if "mode" in dimensions:
                try:
                    allocation = self.allocations.calculate_detail({"episode": episode, "fills": fills_by_episode[episode["episode_id"]]}, allocation_records.get(episode["episode_id"]))
                except AllocationError as error:
                    allocation = {"sell_attributions": [], "revision": None}
                    base["allocation_review_error"] = str(error)
                attributed = defaultdict(list)
                for row in allocation["sell_attributions"]:
                    if row["attribution_known"]:
                        attributed[row["mode_key"]].append(row)
                for key, rows in attributed.items():
                    parts.append({**base, "member_id": base["member_id"] + "#" + key, "mode": key,
                        "mode_ref": rows[0]["mode_ref"], "mode_label": rows[0]["mode_label"],
                        "mode_attribution_known": True, "unit": "quantity_attributed_episode_part",
                        "net_pnl": money(sum(Decimal(str(row["net_pnl"])) for row in rows)),
                        "buy_cost": money(sum(Decimal(str(row["buy_cost"])) for row in rows)),
                        "fees": money(sum(Decimal(str(row["fees"])) for row in rows)),
                        "quantity": sum(row["quantity"] for row in rows), "allocation_revision": allocation["revision"]})
                residual_cost = episode["buy_cost"]-sum(row["buy_cost"] for row in parts)
                residual_pnl = episode["closed_pnl"]-sum(row["net_pnl"] for row in parts)
                if not parts or abs(residual_cost) > 0.000001 or abs(residual_pnl) > 0.000001:
                    parts.append({**base, "member_id": base["member_id"] + "#unallocated",
                        "net_pnl": round(residual_pnl, 8), "buy_cost": round(residual_cost, 8),
                        "fees": round(episode["fees"]-sum(row["fees"] for row in parts), 8),
                        "unit": "unattributed_episode_part", "mode_net_pnl": None,
                        "attribution_note": "保留真实账户现金结果，但没有把未知或非数值标签当成独立模式收益"})
            else:
                parts = [base]
            for row in parts:
                key = tuple(row[dimension] for dimension in dimensions)
                grouped_members[key].append(row)
        groups = []
        for key in sorted(set(grouped_members) | set(grouped_excluded)):
            rows, omissions = grouped_members[key], grouped_excluded[key]
            values = dict(zip(dimensions, key))
            groups.append({"group_id": "group-"+digest(values)[:24], "dimensions": values,
                "statistics": _stats(rows), "members": rows, "excluded": omissions,
                "member_n": len(rows), "excluded_n": len(omissions),
                "unit": "quantity_attributed_or_unattributed_episode_part" if "mode" in dimensions else "closed_inventory_episode"})
        return {"version": VERSION, "filters": {"query": query, "start_date": start_date, "end_date": end_date},
            "group_by": dimensions, "overall": _stats(members), "groups": groups, "members": members, "excluded": excluded,
            "denominator": {"selected_episode_n": len(members)+len(excluded), "closed_known_n": len(members),
                "excluded_n": len(excluded), "open_n": sum(row["status"] == "open" for row in excluded),
                "closed_unknown_n": sum(row["status"] == "closed" for row in excluded),
                "source_scope": "该账户所有已提交真实库存轮；按清仓月份分组，未闭轮用起始月展示排除",
                "group_unit": "mode维度含数量切片，n可能超过完整库存轮数；overall始终每真实轮一次。排除轮若有多模式数值分配可出现在多个组的排除名单，不能合计其n"},
            "limitations": ["全样本保留IPO、ETF与普通二级市场分组；分红和延期税未强行分配进买卖PnL。",
                "模式只来自确切版本逐股分配，决策原始标签单列；未知模式不补默认MA5、人工计划或entry_pattern。",
                "市场环境、角色与执行差异只读真实决策；未填写或冲突保留unknown/mixed，不事后从K线猜测。",
                "这些是描述性分组，样本小或分组多不能据此选择最优规则；没有多重检验、样本外或可成交验证。",
                "没有逐日完整账户权益，最大回撤、资金收益率和历史组合风险保持未知。"],
            "historical_portfolio": {"equity_curve": None, "max_drawdown": None, "status": "unknown_missing_equity_history"}}

    def group(self, group_id, **filters):
        report = self.report(**filters)
        result = next((row for row in report["groups"] if row["group_id"] == group_id), None)
        if result is None:
            raise TradeJournalError("分析分组不存在或筛选范围已变化")
        return {**result, "version": VERSION, "filters": report["filters"], "group_by": report["group_by"],
            "limitations": report["limitations"]}


class PortfolioRiskService:
    """Read configured owners and freeze a current context, never execute it."""
    def __init__(self, store, *, journal=None):
        self.store = store
        self.journal = journal or TradeJournalService(store)
        self.collection = store.collection(PORTFOLIO_COLLECTION)

    @staticmethod
    def _positive(value):
        try:
            number = Decimal(str(value))
            return float(number) if number.is_finite() and number > 0 else None
        except Exception:
            return None

    def context(self, profile_id="default", scenario=None):
        from shilun.trading.risk import AccountRiskProfile, DynamicRiskInput, RiskConfigurationError, calculate_dynamic_target
        from shilun.trading.sizing import (EntrySizingInput, TradingUnitRule, calculate_entry_sizing,
            list_active_cash_reservations, CASH_RESERVATION_COLLECTION)
        now = self.journal._now()
        raw_profile = _clean(self.store.collection("account_risk_profiles").find_one({"profile_id": profile_id}, {"_id": 0}))
        profile, gaps = None, []
        if raw_profile:
            required = {"total_assets", "max_loss_per_trade"}
            if required <= raw_profile.keys():
                try:
                    allowed = AccountRiskProfile.__dataclass_fields__
                    for field in ("total_assets", "available_cash", "max_loss_per_trade", "max_cash_per_trade", "commission_rate", "min_commission", "stamp_duty_rate", "slippage_bps", "conservative_fee_buffer_rate"):
                        if field in raw_profile:
                            _number(raw_profile[field], "已有账户配置"+field, nullable=True)
                    profile = AccountRiskProfile(**{key: value for key, value in raw_profile.items() if key in allowed})
                except (RiskConfigurationError, TypeError, ValueError, InvalidOperation) as error:
                    gaps.append("已有账户配置无效："+str(error))
            else:
                gaps.append("已有风险配置缺总资产或单笔上限，不套默认金额")
        else:
            gaps.append("没有已保存账户风险配置，不套默认资产/亏损金额")
        rows = [_clean(row) for row in self.store.collection("holdings").find({}, {"_id": 0}) if row.get("status") in {"active", "reduced"}]
        holdings, known_value = [], Decimal(0)
        value_complete = True
        for row in rows:
            quote = _clean(self.store.collection("trading_realtime_quotes").find_one({"ticker": row["ticker"]}, {"_id": 0}))
            price = self._positive(quote.get("current"))
            quote_at = quote.get("quote_at") or quote.get("update_at") or quote.get("fetched_at")
            price_status = "owner_snapshot_unassessed_freshness"
            if price is None or not quote_at:
                price_status, price = "missing_price_or_time", None
            else:
                try:
                    if _time(quote_at) > _time(now):
                        price_status, price = "future_quote_rejected", None
                except TradeJournalError:
                    price_status, price = "invalid_quote_time", None
            try:
                quantity = _number(row.get("quantity"), "所有者持仓数量", nullable=True, minimum=0, integer=True)
            except TradeJournalError:
                quantity = None
            try:
                available_quantity = _number(row.get("available_quantity"), "所有者可卖数量", nullable=True, minimum=0, integer=True)
            except TradeJournalError:
                available_quantity = None
            if quantity is not None and available_quantity is not None and available_quantity > quantity:
                available_quantity = None
            value = quantity*price if quantity is not None and price is not None else None
            if value is None:
                value_complete = False
            else:
                known_value += Decimal(str(value))
            cost = self._positive(row.get("cost_basis"))
            stop = self._positive(row.get("stop_loss_1"))
            risk_result, risk_gaps = None, []
            if profile and quantity and cost and price and stop:
                try:
                    risk_result = calculate_dynamic_target(DynamicRiskInput(current_quantity=quantity, cost_basis=cost,
                        current_price=price, effective_stop=stop, realized_loss_amount=row.get("realized_loss_amount") or 0), profile).to_dict()
                except (RiskConfigurationError, TypeError, ValueError, InvalidOperation) as error:
                    risk_gaps.append(str(error))
            else:
                risk_gaps.append("风险预算、实际股数、成本、报价或有效止损缺失，不新设止损阈值")
            holdings.append({"ticker": row["ticker"], "name": row.get("name"), "quantity": quantity,
                "available_quantity": available_quantity, "cost_basis": cost,
                "holding_updated_at": row.get("updated_at"), "position_revision": row.get("position_revision"),
                "sector_name": row.get("sector_name") or "unknown", "strategy_id": row.get("strategy_id"),
                "strategy_version": row.get("strategy_version"), "mode": "unknown",
                "current_price": price, "quote_at": quote_at, "quote_source": quote.get("source"),
                "quote_status": price_status, "market_value": value,
                "weight_on_configured_assets": value/profile.total_assets if value is not None and profile else None,
                "effective_stop": stop, "dynamic_risk": risk_result, "risk_gaps": risk_gaps})
        projected, sizing = None, None
        if scenario is not None:
            if not isinstance(scenario, dict):
                raise TradeJournalError("组合情景须为用户提议的对象")
            projected = self._project(holdings, scenario, profile)
            if scenario.get("entry_sizing"):
                request = deepcopy(scenario["entry_sizing"])
                if not profile:
                    gaps.append("无有效账户配置，不能调用新仓数量计算")
                elif not request.get("unit_rule"):
                    gaps.append("缺证券交易单位来源，不猜新仓买入单位")
                else:
                    try:
                        request["unit_rule"] = TradingUnitRule(**request["unit_rule"])
                        request["active_reservations"] = list_active_cash_reservations(self.store.collection(CASH_RESERVATION_COLLECTION))
                        sizing = calculate_entry_sizing(EntrySizingInput(**request), profile).to_dict()
                    except (RiskConfigurationError, TypeError, ValueError, InvalidOperation) as error:
                        gaps.append("既有sizing无法计算："+str(error))
        sector_values = defaultdict(list)
        for row in holdings:
            sector_values[row["sector_name"]].append(row)
        known_risk = [row["dynamic_risk"]["full_hold_worst_loss"] for row in holdings if row["dynamic_risk"] is not None]
        return {"version": VERSION, "captured_at": now, "scope": "当前所有者集合的只读事实快照，不是历史账户重建或下单建议",
            "profile_id": profile_id, "profile": profile.to_document() if profile else None,
            "profile_source": raw_profile.get("source"), "profile_updated_at": raw_profile.get("updated_at"),
            "holdings": holdings, "holding_n": len(holdings),
            "market_value": money(known_value) if value_complete else None, "known_market_value_component": money(known_value),
            "available_cash": profile.available_cash if profile else None,
            "configured_total_assets": profile.total_assets if profile else None,
            "current_verified_net_asset_value": None,
            "configured_risk_aggregation": {"known_full_hold_worst_loss_component": round(sum(known_risk), 2),
                "full_hold_worst_loss": round(sum(known_risk), 2) if len(known_risk) == len(holdings) and holdings else None,
                "known_holding_n": len(known_risk), "total_holding_n": len(holdings),
                "scope": "仅汇总既有单票stop风险计算，未建立组合VaR、相关性/跳空模型或新增组合限额"},
            "sector_exposure": [{"sector_name": name, "tickers": [row["ticker"] for row in group],
                "market_value": sum(row["market_value"] for row in group) if all(row["market_value"] is not None for row in group) else None} for name, group in sorted(sector_values.items())],
            "scenario": projected, "sizing_preview": sizing, "gaps": gaps,
            "owners": {"holdings": "holdings", "quotes": "trading_realtime_quotes", "profile": "account_risk_profiles",
                "risk_calculator": "shilun.trading.risk.calculate_dynamic_target", "sizing_calculator": "shilun.trading.sizing.calculate_entry_sizing"},
            "historical": {"equity_curve": None, "max_drawdown": None, "status": "unknown_missing_point_in_time_equity"},
            "limitations": ["profile.total_assets是已保存的配置口径，不冒充当前券商核验净资产。",
                "报价保留原始时点，未新增新鲜度秒数或70%/5%等阈值；不能自动执行。",
                "当前持仓和账户不能回填为10月23日风华/九阳等交易当时的组合状态。",
                "情景只保存已知事实、用户提议与上限，不为缺失费用、可卖数量或先后成交假设补零。"]}

    def _project(self, holdings, scenario, profile):
        legs = scenario.get("legs") or []
        if not isinstance(legs, list) or any(not isinstance(row, dict) for row in legs):
            raise TradeJournalError("情景legs须为明确先后顺序的逐腿提议")
        known = {row["ticker"]: deepcopy(row) for row in holdings}
        states, cash, cash_known = [], Decimal(0), True
        for raw in legs:
            ticker, side = _ticker(raw.get("ticker")), raw.get("side")
            if side not in {"buy", "sell"}:
                raise TradeJournalError("情景买卖方向须明确buy/sell")
            quantity = _number(raw.get("quantity"), "情景股数", minimum=1, integer=True)
            price = _number(raw.get("price"), "用户情景价格", minimum=0.00000001)
            fees = _number(raw.get("fees"), "情景费用", minimum=0, nullable=True)
            before = known.get(ticker, {"ticker": ticker, "quantity": None, "available_quantity": None})
            after_quantity = before["quantity"] + (quantity if side == "buy" else -quantity) if before["quantity"] is not None else None
            if after_quantity is not None and after_quantity < 0:
                raise TradeJournalError("情景卖出股数超过已知当前持仓；不能假设未来买入已完成")
            leg_cash = ((-quantity*price if side == "buy" else quantity*price)-fees) if fees is not None else None
            if leg_cash is None:
                cash_known = False
            else:
                cash += Decimal(str(leg_cash))
            sellable = before.get("available_quantity")
            flags = []
            if side == "sell" and (sellable is None or quantity > sellable):
                flags.append("可卖数量缺失或不足；提议不代表可成交")
            states.append({"ticker": ticker, "side": side, "quantity": quantity, "user_price": price,
                "fees": fees, "before_quantity": before["quantity"], "after_quantity": after_quantity,
                "cash_delta": leg_cash, "flags": flags, "provenance": "user_proposed_scenario"})
            known[ticker] = {**before, "quantity": after_quantity,
                "available_quantity": max(0, sellable-quantity) if side == "sell" and type(sellable) is int else sellable}
        return {"action": scenario.get("action"), "legs": states, "user_limits": deepcopy(scenario.get("user_limits") or {}),
            "reason": scenario.get("reason"), "reference_tickers": deepcopy(scenario.get("reference_tickers") or []),
            "cash_delta": money(cash) if cash_known else None, "known_cash_delta_component": money(cash),
            "after_available_cash": money(Decimal(str(profile.available_cash))+cash) if cash_known and profile and profile.available_cash is not None else None,
            "executed": False, "note": "假设逐腿按用户价格成交的事实投影；无成交承诺，不冻结或扣减生产现金"}

    def capture_snapshot(self, payload=None):
        payload = payload or {}
        if not isinstance(payload, dict):
            raise TradeJournalError("组合快照须为对象")
        with self.journal.write_guard():
            context = self.context(profile_id=payload.get("profile_id") or "default", scenario=payload.get("scenario"))
            now = self.journal._now()
            record = {"snapshot_id": str(payload.get("snapshot_id") or "portfolio-"+uuid.uuid4().hex),
                "version": VERSION, "kind": "current_portfolio_context", "captured_at": now, "recorded_at": now,
                "content": context, "content_digest": digest(context), "reason": payload.get("reason"),
                "requested_historical_at": payload.get("historical_at"),
                "temporal_note": "captured_at是本次服务器真实时点；requested_historical_at不能把当前账户当成历史事实"}
            record["content_hash"] = digest(record)
            try:
                if len(BSON.encode(record)) > 15*1024*1024:
                    raise TradeJournalError("组合快照超过15MiB预算，不截断事实")
            except (BSONError, TypeError, OverflowError) as error:
                raise TradeJournalError("组合快照不是可持久化结构") from error
            self.journal.renew_write_lease()
            try:
                self.collection.update_one({"snapshot_id": record["snapshot_id"]}, {"$setOnInsert": deepcopy(record)}, upsert=True)
            except DuplicateKeyError:
                pass
            saved = self.get_snapshot(record["snapshot_id"], include_content=True)
            if saved["content_hash"] != record["content_hash"]:
                raise TradeJournalError("组合快照标识冲突，原快照不可覆盖")
            return {"record": saved, "snapshot_ref": self.reference(saved["snapshot_id"])}

    def reference(self, snapshot_id):
        record = self.get_snapshot(snapshot_id)
        return {"snapshot_id": record["snapshot_id"], "content_hash": record["content_hash"],
            "content_digest": record["content_digest"], "captured_at": record["captured_at"],
            "collection": PORTFOLIO_COLLECTION, "kind": "current_portfolio_context"}

    def resolve_reference(self, ref, *, before_at=None):
        if not isinstance(ref, dict) or not ref.get("snapshot_id") or not ref.get("content_hash"):
            raise TradeJournalError("组合快照引用须有snapshot_id与确切content_hash")
        record = self.get_snapshot(ref["snapshot_id"], include_content=True)
        if record["content_hash"] != ref["content_hash"] or ref.get("content_digest", record["content_digest"]) != record["content_digest"]:
            raise TradeJournalError("组合快照引用与不可变原件不一致")
        if before_at is not None and _time(record["captured_at"]) > _time(before_at):
            raise TradeJournalError("组合快照晚于计划冻结时点，不能作为事前风险证据")
        return record

    def get_snapshot(self, snapshot_id, *, include_content=False):
        projection = {"_id": 0, **({} if include_content else {"content": 0})}
        row = self.collection.find_one({"snapshot_id": snapshot_id}, projection)
        if row is None:
            raise TradeJournalError("组合快照不存在")
        record = _clean(row)
        if include_content and (digest(record["content"]) != record["content_digest"] or digest({key: value for key, value in record.items() if key != "content_hash"}) != record["content_hash"]):
            raise TradeJournalError("组合快照完整性校验失败")
        return record

    def list_snapshots(self, limit=100, offset=0):
        rows = [_clean(row) for row in self.collection.find({}, {"_id": 0, "content": 0})]
        rows.sort(key=lambda row: row["captured_at"], reverse=True)
        return _page(rows, limit, offset)


__all__ = ["JournalAnalyticsService", "PortfolioRiskService", "VERSION", "PORTFOLIO_COLLECTION"]
