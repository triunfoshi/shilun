"""Read-only, first-lot FIFO comparison with a next-session close scenario.

This is an explicitly requested historical replay, not a strategy backtest or a
claim that a closing-price order could have filled. It never changes fills,
positions, cash, plans or the journal's accounting algorithm.
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
import math
import re

from .ledger import digest, money
from .service import TradeJournalError, TradeJournalService, _number, _time


VERSION = "journal-first-lot-next-close-v1"
LIMITATIONS = [
    "本次提交规则用于历史回放，不证明原交易前已有该规则，也不进入事前预测成绩。",
    "固定首笔真实买入及完整数量，仅替换退出；不混入后续加仓，也不代表整个账户收益。",
    "收盘价扣显式费用和滑点后仅为情景参考；日线不能证明订单排队、收盘撮合或冲击成本。",
    "收益率分母沿用这批实际FIFO含费买入成本；差额是复查线索，不自动证明执行错误。",
]


def _day(value):
    text = str(value or "")[:10]
    if len(text) == 8 and text.isdigit():
        text = f"{text[:4]}-{text[4:6]}-{text[6:]}"
    try:
        return date.fromisoformat(text).isoformat()
    except ValueError:
        return None


def _finite(value, *, positive=False):
    try:
        result = _number(value, "行情值")
        return result if not positive or result > 0 else None
    except TradeJournalError:
        return None


def _fill_available_at(row):
    # A broker statement event clock is not a verified matching timestamp.
    if row.get("time_precision") == "execution_time" and row.get("executed_at"):
        return _time(row["executed_at"])
    return _time(row["date"] + "T23:59:59+08:00")


class JournalRuleComparisonService:
    def __init__(self, store, *, journal=None, now_factory=None):
        self.store = store
        self.journal = journal or TradeJournalService(store, now_factory=now_factory)
        self.now_factory = now_factory or (lambda: _time(self.journal._now()))

    def _calendar(self, start, end):
        owner = getattr(self.store, "find_trade_calendar", None)
        rows = owner(start_date=start, end_date=end, exchange="SSE") if callable(owner) else self.store.collection("trade_calendar").find({"exchange": "SSE"}, {"_id": 0})
        result = {}
        duplicates = set()
        for row in rows:
            day = _day(row.get("cal_date") or row.get("date"))
            if day and start <= day <= end:
                if day in result:
                    duplicates.add(day)
                result[day] = row
        return result, duplicates

    def _next_day(self, entry_day):
        start = date.fromisoformat(entry_day)
        calendar, duplicates = self._calendar(entry_day, (start + timedelta(days=31)).isoformat())
        if entry_day not in calendar or entry_day in duplicates or str(calendar[entry_day].get("is_open")) != "1":
            return None, "入场日交易日历缺失、冲突或并非开市日", None
        for offset in range(1, 32):
            day = (start + timedelta(days=offset)).isoformat()
            row = calendar.get(day)
            if not row or day in duplicates or str(row.get("is_open")) not in {"0", "1"}:
                return None, "交易日历不连续或冲突，不能用工作日猜测下一交易日", None
            if str(row["is_open"]) == "1":
                snapshot = [{"exchange": "SSE", "date": key, "is_open": int(calendar[key]["is_open"])}
                    for key in sorted(calendar) if entry_day <= key <= day]
                return day, None, {"collection": "trade_calendar", "exchange": "SSE",
                    "snapshot": snapshot, "content_hash": digest(snapshot), "immutable_archive": False}
        return None, "已存交易日历未覆盖下一交易日", None

    def _bar(self, ticker, day):
        owner = getattr(self.store, "find_daily_bars", None)
        rows = owner(start_date=day, end_date=day, tickers=[ticker]) if callable(owner) else self.store.collection("market_daily_bars").find({"ticker": ticker}, {"_id": 0})
        rows = [row for row in rows if _day(row.get("date") or row.get("trade_date")) == day and str(row.get("ticker") or row.get("ts_code")) == ticker]
        return rows[0] if len(rows) == 1 else None

    def _limits(self, ticker, day):
        owner = getattr(self.store, "find_price_limits", None)
        rows = owner(trade_date=day, tickers=[ticker]) if callable(owner) else self.store.collection("daily_price_limits").find({"ticker": ticker}, {"_id": 0})
        rows = [row for row in rows if _day(row.get("trade_date") or row.get("date")) == day and str(row.get("ticker") or row.get("ts_code")) == ticker]
        return rows[0] if len(rows) == 1 else None

    def preview(self, episode_id, payload):
        if not isinstance(payload, dict) or not isinstance(payload.get("rule"), dict):
            raise TradeJournalError("请明确提交规则对象rule")
        requested = payload["rule"]
        if requested.get("kind") != "next_trading_day_close":
            raise TradeJournalError("当前仅支持next_trading_day_close规则")
        for field in ("sell_fee_bps", "slippage_bps"):
            if field in requested:
                value = _number(requested[field], field, minimum=0)
                if value >= 10000:
                    raise TradeJournalError(f"{field}必须小于10000基点")
        rule = {"kind": "next_trading_day_close", "version": VERSION,
            "sell_fee_bps": requested.get("sell_fee_bps"), "slippage_bps": requested.get("slippage_bps")}
        now = _time(self.now_factory())
        result = {"version": VERSION, "episode_id": episode_id, "status": "unknown",
            "evidence_class": "historical_replay", "ex_ante_verifiable": False,
            "strategy_validated": False, "generated_at": now.isoformat(timespec="seconds"),
            "rule": rule, "entry": None, "actual": None, "reference": None,
            "actual_return": None, "reference_return": None, "difference_pp": None,
            "reasons": [], "sources": [], "limitations": list(LIMITATIONS), "write_effects": False}
        detail = self.journal.get_episode(episode_id)
        ep, fills = detail["episode"], detail["fills"]
        ticker = ep["ticker"]
        if not re.fullmatch(r"(?:(?:00|30)\d{4}\.SZ|(?:60|68)\d{4}\.SH|(?:[48]\d{5}|92\d{4})\.BJ)", ticker):
            result["reasons"].append("本轮仅支持A股普通股票；ETF、债券及其他证券不使用该规则")
        if ep.get("opening_quantity") or not ep.get("inventory_valid"):
            result["reasons"].append("存在期初库存或库存不连续，无法固定同一真实买入批次")
        if not ep.get("opening_balance_verified"):
            result["reasons"].append("首笔前后库存缺少券商余额核对，无法证明完整期初状态")
        buys = [row for row in fills if row.get("side") == "buy"]
        if not buys:
            result["reasons"].append("没有可识别的首笔真实买入")
            return result
        first = buys[0]
        if payload.get("entry_fill_id") not in (None, first["fill_id"]):
            raise TradeJournalError("本轮对照必须使用首笔真实买入，不能事后另选有利入场")
        quantity = first["quantity"]
        if "quantity" in payload and _number(payload["quantity"], "对照股数", minimum=1, integer=True) != quantity:
            raise TradeJournalError("本轮必须使用首笔买入完整数量，暂不支持数量切片")
        result["entry"] = {"fill_id": first["fill_id"], "ticker": ticker, "date": first["date"],
            "quantity": quantity, "price": first["price"], "source_ref": first.get("source_ref")}
        if first.get("fees") is None or first.get("net_cash") is None:
            result["reasons"].append("首笔真实买入费用或净现金流缺失，收益分母未知")
        if first.get("quantity_before") not in (None, 0):
            result["reasons"].append("首笔买入前并非空仓，无法确定独立首批库存")
        if _fill_available_at(first) > now:
            result["reasons"].append("首笔成交尚未在核验时点发生或缺少当日可用时间")
        matches = [row for row in ep.get("matches", []) if row.get("buy_lot_id") == first["fill_id"]]
        sells = {row["fill_id"]: row for row in fills if row.get("side") == "sell"}
        actual_reasons = []
        if sum(row["quantity"] for row in matches) != quantity:
            actual_reasons.append("首笔买入批次尚未全部卖出，实际同量收益未完成")
        elif any(row.get("buy_cost") is None or row.get("sell_proceeds") is None or row.get("realized_pnl") is None for row in matches):
            actual_reasons.append("首批FIFO匹配的费用或成本未知")
        elif any(row.get("sell_fill_id") not in sells or sells[row["sell_fill_id"]]["date"] <= first["date"] for row in matches):
            actual_reasons.append("首批实际卖出缺少跨交易日可卖依据，不能认定为可执行A股对照")
        elif any(_fill_available_at(sells[row["sell_fill_id"]]) > now for row in matches):
            actual_reasons.append("首批卖出尚未在核验时点发生，不能读取未来实际收益")
        else:
            cost = sum(Decimal(str(row["buy_cost"])) for row in matches)
            pnl = sum(Decimal(str(row["realized_pnl"])) for row in matches)
            if cost > 0:
                result["actual"] = {"quantity": quantity, "buy_cost": money(cost),
                    "net_pnl": money(pnl), "return_pct": money(pnl / cost * 100),
                    "matches": matches, "cost_basis": "existing_fifo_actual_fee_inclusive"}
                result["actual_return"] = result["actual"]["return_pct"]
            else:
                actual_reasons.append("首批实际含费成本必须大于零")
        target, calendar_reason, calendar_source = self._next_day(first["date"])
        if calendar_reason:
            result["reasons"].append(calendar_reason)
        else:
            result["sources"].append(calendar_source)
            result["reference"] = {"quantity": quantity, "exit_date": target, "close": None,
                "net_pnl": None, "return_pct": None, "estimated_sell_fee": None,
                "slippage_cost": None, "execution_verified": False}
            if _time(f"{target}T15:30:00+08:00") > now:
                result["reasons"].append("目标交易日尚未收盘定稿，不能读取未来收盘价")
            else:
                self._reference(result, ticker, first, target, detail)
        result["reasons"].extend(actual_reasons)
        if not result["reasons"] and result["actual"] and result["reference"] and result["reference"]["return_pct"] is not None:
            result["status"] = "compared"
            result["reference_return"] = result["reference"]["return_pct"]
            result["difference_pp"] = round(result["actual_return"] - result["reference_return"], 8)
        return result

    def _reference(self, result, ticker, first, target, detail):
        ref, rule, reasons = result["reference"], result["rule"], result["reasons"]
        if rule["sell_fee_bps"] is None or rule["slippage_bps"] is None:
            reasons.append("须显式填写参考卖出总费用率和滑点基点；缺失不能当零")
        last_actual = max((row["date"] for row in detail["fills"] if row.get("side") == "sell"), default=target)
        for event in detail.get("security_financial_events", []):
            event_day = _day(event.get("settlement_date") or event.get("date"))
            if event_day and first["date"] <= event_day <= max(target, last_actual):
                reasons.append("对照期间存在分红、税款或公司行为，本版不跨此类事件推算")
                break
        bar = self._bar(ticker, target)
        if not bar:
            reasons.append("目标交易日日线缺失或不唯一，不能以其他日期价格替代")
            return
        snapshot = {key: bar.get(key) for key in ("ticker", "date", "trade_date", "close", "vol", "volume", "high", "low", "adjust") if key in bar}
        snapshot = {key: value if isinstance(value, (str, int, bool, type(None))) or isinstance(value, float) and math.isfinite(value) else str(value)
            for key, value in snapshot.items()}
        result["sources"].append({"collection": "market_daily_bars", "ticker": ticker, "date": target,
            "snapshot": snapshot, "content_hash": digest(snapshot), "immutable_archive": False})
        close = _finite(bar.get("close"), positive=True)
        ref["close"] = close
        volume = _finite(bar.get("vol", bar.get("volume")), positive=True)
        if close is None or volume is None:
            reasons.append("日线收盘价或成交量无效，停牌与缺行情不假设可成交")
        if str(bar.get("adjust") or "none").lower() not in {"none", "raw", "unadjusted", "不复权"}:
            reasons.append("日线价格为复权或调整口径，不能直接与真实成交成本对照")
        limits = self._limits(ticker, target)
        lower = _finite((limits or {}).get("down_limit"), positive=True)
        upper = _finite((limits or {}).get("up_limit"), positive=True)
        if lower is None or upper is None or lower >= upper:
            reasons.append("缺有效当日涨跌停价，无法核验收盘退出限制")
        elif close is not None:
            limit_snapshot = {"ticker": ticker, "trade_date": target, "down_limit": lower, "up_limit": upper}
            result["sources"].append({"collection": "daily_price_limits", "snapshot": limit_snapshot,
                "content_hash": digest(limit_snapshot), "immutable_archive": False})
            if close <= lower or close > upper:
                reasons.append("目标日收盘处于跌停或价格超出限制，不能假定卖出成交")
            elif rule["slippage_bps"] is not None and close * (1 - float(rule["slippage_bps"]) / 10000) < lower:
                reasons.append("滑点假设导致参考卖价跌破当日跌停价，不能使用该成交假设")
        if reasons or first.get("net_cash") is None:
            return
        cost = -Decimal(str(first["net_cash"]))
        quantity = Decimal(str(first["quantity"]))
        gross = Decimal(str(close)) * quantity
        slip = gross * Decimal(str(rule["slippage_bps"])) / 10000
        proceeds_before_fee = gross - slip
        fee = proceeds_before_fee * Decimal(str(rule["sell_fee_bps"])) / 10000
        pnl = proceeds_before_fee - fee - cost
        if cost <= 0:
            reasons.append("真实含费买入成本无效")
            return
        ref.update(net_pnl=money(pnl), return_pct=money(pnl / cost * 100),
            estimated_sell_fee=money(fee), slippage_cost=money(slip), buy_cost=money(cost),
            fee_scope="显式用户假设的卖出总费用率，须含全部适用卖出费用；非券商真实回执")
