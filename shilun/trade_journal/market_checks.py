"""Review frozen individual-stock expectations against stored daily bars.

This owner never fetches data or changes a trade.  A historical bar read today
is a reconstruction, not proof that the same bar was available at the trade.
Preview is read-only. Save archives the exact inputs through the journal owner
and appends an expectation check referring to the original decision revision.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta
from decimal import Decimal
import hashlib
import json
import math

from .ledger import digest
from .service import TZ, TradeJournalError, TradeJournalService, _date, _time


VERSION = "journal-market-check-v2"
PRICE_FIELDS = ("open", "high", "low", "close")
METRICS = {f"{field}_{kind}": field for field in PRICE_FIELDS for kind in ("price", "return_pct")}
PRICE_LABELS = {"open": "开盘价", "high": "全日最高价", "low": "全日最低价", "close": "收盘价"}
OPERATORS = {">": lambda a, b: a > b, ">=": lambda a, b: a >= b,
             "<": lambda a, b: a < b, "<=": lambda a, b: a <= b,
             "==": lambda a, b: a == b}
PAYLOAD_KEYS = {"decision_revision", "as_of", "preview_digest"}


def _finite(value):
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError, OverflowError):
        return None


def _day(value):
    text = str(value or "").replace("-", "")[:8]
    return _date(f"{text[:4]}-{text[4:6]}-{text[6:]}") if len(text) == 8 and text.isdigit() else None


def _json_value(value):
    """Make source snapshots recoverable without leaking Mongo identities."""
    if isinstance(value, dict):
        return {str(k): _json_value(v) for k, v in value.items() if k != "_id"}
    if isinstance(value, (list, tuple)):
        return [_json_value(v) for v in value]
    if isinstance(value, datetime):
        return _time(value).isoformat()
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    return str(value)


def _value(price, metric, baseline):
    if metric.endswith("_price"):
        return price
    if baseline is None or baseline <= 0:
        return None
    # Decimal keeps exact percentage boundaries stable.
    return float((Decimal(str(price)) / Decimal(str(baseline)) - 1) * 100)


def _execution_evaluation(detail, entry, result, as_of):
    """Compare only archived, FIFO-linked sales of the selected buy lot.

    This is separate from whether an OHLC quote met a price condition. A missing
    sale means 'not in the imported records', never 'the user did not sell'.
    """
    out = {"status": "unknown", "reason": "未明确关联唯一买入批次，不能判断这批股票的卖出执行",
        "target_date": result.get("target_date"), "entry_fill_id": entry.get("fill_id") if entry else None,
        "entry_quantity": entry.get("quantity") if entry else None, "evaluated_quantity": 0,
        "target_met_quantity": 0, "fills": [], "data_scope": "imported_fifo_matches",
        "statement_coverage_confirmed": False, "execution_at_market_extreme_verified": False,
        "scope_note": "按已导入交割单的FIFO数量归属比较目标日实际卖出价；未核验是否在最高/最低价时成交，也不判断操作对错"}
    metric = result.get("metric")
    condition = result.get("expectation") if isinstance(result.get("expectation"), dict) else {}
    target = result.get("target_date")
    threshold, operator = _finite(condition.get("target")), condition.get("operator")
    if not entry or not target or metric not in METRICS or threshold is None or operator not in OPERATORS:
        return out
    if as_of < _time(target + "T15:30:00+08:00"):
        out["reason"] = "目标交易日尚未结束，已导入卖出记录可能仍不完整"
        return out
    if target <= entry["date"]:
        out["reason"] = "目标日不晚于该批买入日；不能把当天加仓当作T+1可卖库存"
        return out
    episode = detail["episode"]
    if not episode.get("inventory_valid") or not episode.get("opening_balance_verified"):
        out["reason"] = "库存不连续或期初未核对，无法可靠地归属这批股票的卖出数量"
        return out
    sells = {f["fill_id"]: f for f in detail["fills"] if f.get("side") == "sell"}
    matches = [m for m in episode.get("matches", []) if m.get("buy_lot_id") == entry["fill_id"]]
    basis = (result.get("baseline") or {}).get("value")
    for match in matches:
        sale = sells.get(match.get("sell_fill_id"))
        if not sale or sale.get("date") != target:
            continue
        available_at = _time(sale["executed_at"]) if sale.get("time_precision") == "execution_time" and sale.get("executed_at") else _time(target + "T23:59:59+08:00")
        price = _finite(sale.get("price"))
        if available_at > as_of or price is None or price <= 0:
            out["reason"] = "目标日卖出缺可靠价格或在核验截止时点尚不可确认"
            return out
        value = _value(price, metric, basis)
        if value is None:
            out["reason"] = "实际成交价比较缺明确的原买入价或昨收基准"
            return out
        matched = OPERATORS[operator](value, threshold)
        out["fills"].append({"sell_fill_id": sale["fill_id"], "date": target,
            "executed_at": sale.get("executed_at") if sale.get("time_precision") == "execution_time" else None,
            "time_precision": sale.get("time_precision"), "quantity": match["quantity"], "price": price,
            "comparison_value": value, "condition_met": matched, "source_ref": deepcopy(sale.get("source_ref"))})
        out["evaluated_quantity"] += match["quantity"]
        out["target_met_quantity"] += match["quantity"] if matched else 0
    qty, sold, met = out["entry_quantity"], out["evaluated_quantity"], out["target_met_quantity"]
    if not sold:
        out.update(status="not_recorded", reason="已导入记录中没有这批股票在目标日的卖出；需先确认交割单是否完整，不能据此认定当时未操作")
    elif sold > qty:
        out["reason"] = "匹配卖出数量超过买入批次，需核对交割单"
    elif sold == qty and met == qty:
        out.update(status="supported", reason="该批完整数量在目标日的已归档卖出价均达到同一价格条件；不代表恰在开盘或最高价成交")
    elif sold == qty and not met:
        out.update(status="refuted", reason="该批完整数量在目标日的已归档卖出价均未达到同一价格条件；与行情是否达标分别判断")
    else:
        out.update(status="partial", reason="目标日只卖出部分数量，或各笔卖出价达标情况不同；请逐笔查看")
    return out


class JournalMarketCheckService:
    def __init__(self, store, *, journal=None, now_factory=None):
        self.store = store
        self.journal = journal or TradeJournalService(store, now_factory=now_factory)
        self.now_factory = now_factory or self.journal.now_factory

    def _calendar(self, start, end):
        owner = getattr(self.store, "find_trade_calendar", None)
        rows = owner(start_date=start, end_date=end, exchange="SSE") if callable(owner) else self.store.collection("trade_calendar").find({"exchange": "SSE"}, {"_id": 0})
        grouped = {}
        for row in rows:
            day = _day(row.get("cal_date") or row.get("date"))
            if day and start <= day <= end:
                grouped.setdefault(day, []).append(_json_value(row))
        return grouped

    def _bars(self, ticker, start, end):
        owner = getattr(self.store, "find_daily_bars", None)
        rows = owner(start_date=start, end_date=end, tickers=[ticker]) if callable(owner) else self.store.collection("market_daily_bars").find({"ticker": ticker}, {"_id": 0})
        grouped = {}
        for row in rows:
            day = _day(row.get("date") or row.get("trade_date"))
            if day and start <= day <= end and (row.get("ticker") or row.get("ts_code")) == ticker:
                grouped.setdefault(day, []).append(_json_value(row))
        return grouped

    @staticmethod
    def _open(calendar, day):
        rows = calendar.get(day) or []
        if len(rows) != 1 or str(rows[0].get("is_open")) not in {"0", "1", "False", "True"}:
            return None
        return str(rows[0]["is_open"]) in {"1", "True"}

    @staticmethod
    def _bar_time_error(bar, as_of):
        for key in ("observed_at", "available_at", "fetched_at", "updated_at"):
            if bar.get(key):
                try:
                    if _time(bar[key]) > as_of:
                        return "已存行情的时间晚于as_of，不能用于该时点核验"
                except TradeJournalError:
                    return "已存行情时间无效，保持未知"
        return None

    def preview(self, episode_id, decision_id, payload=None):
        payload = deepcopy(payload or {})
        if set(payload) - PAYLOAD_KEYS:
            raise TradeJournalError("行情核验只能读取原决策版本，不接受临时改写预期")
        now = _time(self.now_factory())
        as_of = _time(payload.get("as_of") or now)
        if as_of > now:
            raise TradeJournalError("核验as_of不能晚于当前时间")
        detail = self.journal.get_episode(episode_id)
        history = self.journal.get_decision_history(decision_id)
        if history["episode_id"] != episode_id:
            raise TradeJournalError("原决策不属于这轮交易")
        revision = payload.get("decision_revision", history["latest_revision"])
        if type(revision) is not int:
            raise TradeJournalError("请明确有效的原决策版本")
        decision = next((r for r in history["items"] if r["revision"] == revision), None)
        if decision is None:
            raise TradeJournalError("回证引用的决策版本不存在")
        ex_ante = bool((decision.get("frozen_plan") or {}).get("ex_ante_verifiable"))
        result = {"version": VERSION, "episode_id": episode_id, "decision_id": decision_id,
            "decision_revision": revision, "status": "unknown", "actual": None, "baseline": None,
            "target_date": None, "sources": [], "reasons": [], "as_of": as_of.isoformat(timespec="seconds"),
            "recorded_at": now.isoformat(timespec="seconds"), "evidence_class": decision.get("evidence_class") or "unknown",
            "ex_ante_verifiable": ex_ante, "plan_was_ex_ante": ex_ante,
            "point_in_time_market_data_verified": False, "market_data_scope": "stored_daily_bar_reconstruction",
            "scope_note": "按当前已存日线复核原版本；不是当时行情快照，也不证明模式有效或可成交",
            "preview_request": {"decision_revision": revision, "as_of": as_of.isoformat(timespec="seconds")}}
        expectation = decision.get("expectation")
        result["expectation"] = deepcopy(expectation)
        associated = [f for f in detail["fills"] if f["fill_id"] in (decision.get("fill_ids") or []) and f.get("side") == "buy"]
        fill_id = expectation.get("baseline_fill_id") if isinstance(expectation, dict) else None
        scope_fill_id = expectation.get("scope_fill_id") if isinstance(expectation, dict) else None
        candidates = ([f for f in detail["fills"] if f["fill_id"] == scope_fill_id and f.get("side") == "buy"] if scope_fill_id else
            [f for f in associated if f["fill_id"] == fill_id] if fill_id else associated)
        entry = candidates[0] if len(candidates) == 1 else None
        scope_error = ("预期所选批次必须是这轮交易中的真实买入成交" if scope_fill_id and entry is None else
            "预期批次与买入价基准指向不同成交，不能跨批次核对" if scope_fill_id and fill_id and scope_fill_id != fill_id else None)
        origin = expectation.get("origin") if isinstance(expectation, dict) else None
        result["evaluation_purpose"] = "post_hoc_reference" if origin == "post_hoc_review" else "recalled_plan" if origin == "recalled_plan" else "recorded_expectation"
        result["interprets_original_expectation"] = origin != "post_hoc_review"
        if origin == "post_hoc_review":
            result.update(ex_ante_verifiable=False, plan_was_ex_ante=False)
        result["expectation_scope"] = {"decision_id": decision_id, "decision_revision": revision,
            "linked_buy_fill_ids": [f["fill_id"] for f in associated], "entry_fill_id": entry["fill_id"] if entry else None,
            "entry_date": entry["date"] if entry else None, "scope_fill_id": scope_fill_id,
            "binding": "expectation_scope" if scope_fill_id else "decision_linked_fills",
            "note": "本次只核对选中操作版本及明确买入批次；后续加仓可以另设预期，不自动沿用或改写首笔预期"}
        def finish(reason=None):
            if reason:
                result["reasons"].append(reason)
            result["market_evaluation"] = {"status": result["status"], "metric": result.get("metric"),
                "price_field": METRICS.get(result.get("metric")), "label": PRICE_LABELS.get(METRICS.get(result.get("metric"))),
                "target_date": result.get("target_date"), "actual": deepcopy(result.get("actual")),
                "observations": deepcopy(result.get("price_observations", [])),
                "reason_code": result.get("reason_code"), "reasons": list(result["reasons"]),
                "evaluation_purpose": result["evaluation_purpose"],
                "interprets_original_expectation": result["interprets_original_expectation"],
                "touch_time": None, "minute_check_status": "not_checked",
                "scope_note": "开盘、全日最高/最低、收盘分别取对应日线字段；全日极值不包含具体触及时刻，也不证明当时可成交"}
            result["execution_evaluation"] = _execution_evaluation(detail, entry, result, as_of)
            # Current wall clock is not part of the CAS; selected as_of is.
            compared = deepcopy({k: v for k, v in result.items() if k not in {"recorded_at", "preview_digest"}})
            if compared.get("actual"):
                compared["actual"].pop("available_at", None)
            if (compared.get("market_evaluation") or {}).get("actual"):
                compared["market_evaluation"]["actual"].pop("available_at", None)
            result["preview_digest"] = digest(compared)
            return result
        if scope_error:
            result["reason_code"] = "invalid_fill_scope"
            return finish(scope_error)
        if not isinstance(expectation, dict) or not expectation.get("metric"):
            result["reason_code"] = "no_expectation" if not expectation or expectation == {"text": "当时没有制定预期"} else "unstructured_expectation"
            return finish("原版本没有结构化预期，需追加版本后核验；不能从文字猜测目标")
        metric, op = expectation.get("metric"), expectation.get("operator")
        threshold = _finite(expectation.get("target"))
        if metric not in METRICS or op not in OPERATORS or threshold is None:
            result["reason_code"] = "incomplete_condition"
            return finish("请明确开盘/全日最高/全日最低/收盘的价格或涨幅、比较符和数值目标")
        result["metric"] = metric
        verification = decision.get("verification_at") or expectation.get("verification_at")
        result["verification_at"] = verification
        if verification and _time(verification) > as_of:
            return finish("原计划验证时点未到")
        if decision.get("recorded_at") and _time(decision["recorded_at"]) > as_of:
            return finish("该决策版本在所选as_of之后才记录，不能倒填当时已知预期")
        anchor = entry["date"] if entry else _day(decision.get("observed_at"))
        result["date_anchor"] = {"date": anchor, "kind": "buy_fill" if entry else "decision_observed_at",
            "fill_id": entry["fill_id"] if entry else None}
        target = expectation.get("target_date")
        if target:
            target = _date(target)
        elif expectation.get("trading_day_offset") is not None:
            if (fill_id and not entry) or (len(associated) > 1 and not entry):
                return finish("多次买入的目标日期不能共用一个锚点，请明确本次baseline_fill_id及其预期")
            offset = expectation["trading_day_offset"]
            if type(offset) is not int or not 1 <= offset <= 20 or not anchor:
                return finish("交易日偏移需为1至20且原版本有观察日期")
            end = (datetime.fromisoformat(anchor) + timedelta(days=60)).date().isoformat()
            calendar = self._calendar(anchor, end)
            cursor, seen, visited = datetime.fromisoformat(anchor), 0, []
            while seen < offset and cursor.date().isoformat() < end:
                cursor += timedelta(days=1)
                day = cursor.date().isoformat()
                is_open = self._open(calendar, day)
                if is_open is None:
                    return finish("交易日历缺失或冲突，不能以工作日推定下一交易日")
                visited += calendar[day]
                seen += int(is_open)
            if seen != offset:
                return finish("交易日历未覆盖目标交易日")
            target = cursor.date().isoformat()
            result["sources"].append({"collection": "trade_calendar", "rows": visited})
        elif verification:
            target = _time(verification).date().isoformat()
        else:
            return finish("原版本缺少目标日期、验证时点或交易日偏移")
        result["target_date"] = target
        settled_at = _time(target + "T15:30:00+08:00")
        result["settled_at"] = settled_at.isoformat(timespec="seconds")
        if as_of < settled_at:
            return finish("目标日尚未收盘定稿（15:30），保持未知")
        if verification and _time(verification).date().isoformat() != target:
            return finish("目标交易日与原版本验证时点不一致")
        earliest = "09:30:00" if METRICS[metric] == "open" else "15:00:00"
        if verification and _time(verification).time() < _time(target + "T" + earliest + "+08:00").time():
            return finish("原计划要求盘中验证，收盘日线不能替代原窗口")
        # Daily close is observed at settlement, not at a later arbitrary deadline.
        if verification and _time(verification) > settled_at:
            return finish("原验证时点晚于日线观察时点；请使用明确的收盘核验窗口")
        start = (datetime.fromisoformat(target) - timedelta(days=45)).date().isoformat()
        calendar = self._calendar(start, target)
        if self._open(calendar, target) is not True:
            return finish("目标日休市、日历缺失或冲突，无法判定")
        result["sources"].append({"collection": "trade_calendar", "rows": calendar[target]})
        ticker = detail["episode"]["ticker"]
        bars = self._bars(ticker, start, target)
        target_rows = bars.get(target) or []
        if len(target_rows) != 1:
            return finish("目标日行情缺失或重复，保持未知")
        bar = target_rows[0]
        price_field = METRICS[metric]
        price = _finite(bar.get(price_field))
        if price is None or price <= 0:
            return finish("目标" + PRICE_LABELS[price_field] + "缺失或无效")
        if str(bar.get("adjust") or "none").lower() not in {"none", "raw", "unadjusted", "不复权"}:
            return finish("目标日线为复权口径，不能直接与真实成交价比较")
        timing_error = self._bar_time_error(bar, as_of)
        if timing_error:
            return finish(timing_error)
        result["sources"].append({"collection": "market_daily_bars", "ticker": ticker, "date": target, "row": bar})
        baseline = None
        if metric.endswith("_return_pct"):
            basis = expectation.get("baseline")
            if basis == "entry_price":
                if entry is None:
                    return finish("买入价基准不唯一或未关联买入成交，请明确baseline_fill_id")
                selected = entry
                if _time(selected["executed_at"]) > as_of or selected["date"] > target:
                    return finish("基准买入尚未发生，不能借用未来成交")
                baseline = _finite(selected.get("price"))
                result["baseline"] = {"kind": basis, "value": baseline, "fill_id": selected["fill_id"]}
            elif basis == "previous_close":
                cursor = datetime.fromisoformat(target)
                previous, visited = None, []
                while cursor.date().isoformat() > start:
                    cursor -= timedelta(days=1)
                    day = cursor.date().isoformat()
                    opened = self._open(calendar, day)
                    if opened is None:
                        return finish("前交易日日历缺失或冲突，不能取最近一根K线代替昨收")
                    visited += calendar[day]
                    if opened:
                        previous = day
                        break
                prior = bars.get(previous) or []
                if len(prior) != 1:
                    return finish("前交易日收盘价缺失或重复")
                timing_error = self._bar_time_error(prior[0], as_of)
                if timing_error:
                    return finish(timing_error)
                baseline = _finite(prior[0].get("close"))
                result["baseline"] = {"kind": basis, "value": baseline, "date": previous}
                result["sources"] += [{"collection": "trade_calendar", "rows": visited},
                    {"collection": "market_daily_bars", "ticker": ticker, "date": previous, "row": prior[0]}]
                quoted_previous = _finite(bar.get("pre_close"))
                if quoted_previous is not None and baseline is not None and abs(quoted_previous - baseline) > 0.000001:
                    return finish("当日昨收字段与前交易日收盘不一致，可能含除权或数据修订；需确认基准")
            else:
                return finish("原涨幅预期未明确entry_price或previous_close基准")
            if baseline is None or baseline <= 0:
                return finish("基准价格无效")
        actual_value = _value(price, metric, baseline)
        result["market_prices"] = {field: _finite(bar.get(field)) for field in PRICE_FIELDS}
        result["price_observations"] = []
        for field in PRICE_FIELDS:
            quoted = _finite(bar.get(field))
            value = _value(quoted, metric, baseline) if quoted is not None and quoted > 0 else None
            result["price_observations"].append({"price_field": field, "label": PRICE_LABELS[field],
                "price": quoted, "comparison_value": value,
                "status": "unknown" if value is None else "supported" if OPERATORS[op](value, threshold) else "refuted"})
        result["actual"] = {"metric": metric, "value": actual_value,
            "observed_at": settled_at.isoformat(timespec="seconds"), "available_at": now.isoformat(timespec="seconds"),
            "price_field": price_field, "price": price, "close": _finite(bar.get("close")),
            "ticker": ticker, "date": target, "aggregate_finalized_at": settled_at.isoformat(timespec="seconds")}
        result["status"] = "supported" if OPERATORS[op](actual_value, threshold) else "refuted"
        result["reasons"].append("仅" + PRICE_LABELS[price_field] + "这一行情指标与原条件比较；实际卖出另列，历史补记仍只属于复盘样本")
        return finish()

    def save(self, episode_id, decision_id, payload=None):
        payload = deepcopy(payload or {})
        if type(payload.get("decision_revision")) is not int or not payload.get("preview_digest") or not payload.get("as_of"):
            raise TradeJournalError("请先预览并携带确切decision_revision、as_of和preview_digest")
        expected = payload.pop("preview_digest")
        with self.journal.write_guard():
            self.journal._invalidate()
            result = self.preview(episode_id, decision_id, payload)
            if result["preview_digest"] != expected:
                raise TradeJournalError("行情核验预览已变化，请重新预览后保存")
            now = _time(self.now_factory()).isoformat(timespec="seconds")
            # We archive today's retrieval, even when as_of is historical.
            snapshot = {"version": VERSION, "retrieved_at": now, "as_of": result["as_of"],
                "decision_id": decision_id, "decision_revision": result["decision_revision"],
                "expectation": result["expectation"], "sources": result["sources"], "actual": result["actual"],
                "status": result["status"], "reasons": result["reasons"],
                "expectation_scope": result["expectation_scope"], "market_evaluation": result["market_evaluation"],
                "execution_evaluation": result["execution_evaluation"],
                "price_observations": result.get("price_observations", []), "market_prices": result.get("market_prices", {}),
                "evaluation_purpose": result["evaluation_purpose"],
                "interprets_original_expectation": result["interprets_original_expectation"],
                "point_in_time_market_data_verified": False}
            content = json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
            sha = hashlib.sha256(content.encode()).hexdigest()
            source_id = "market-check-" + sha
            self.journal.import_batch({"account_alias": self.journal.get_episode(episode_id)["episode"]["account_alias"],
                "sources": [{"source_id": source_id, "sha256": sha, "content": content,
                    "content_encoding": "utf-8", "filename": "market-check.json", "source_type": "stored_market_data_snapshot",
                    "evidence_class": "historical_reconstruction", "metadata": {"provenance_class": "stored_daily_bar_reconstruction",
                        "market_data_verified": False, "retrieved_at": now, "version": VERSION}}], "fills": [], "financial_events": []})
            actual = deepcopy(result["actual"])
            if actual:
                actual.update(source_ref={"source_id": source_id, "record_id": "actual"}, value_path="actual.value", available_at=now)
            check = self.journal.append_expectation_check(decision_id, {
                "decision_revision": result["decision_revision"], "status": result["status"],
                "target_at": result.get("verification_at") or result.get("settled_at"),
                "evaluation_class": "numeric_comparison", "actual": actual,
                "evidence": [{"source_ref": {"source_id": source_id, "record_id": "actual"}, "preview_digest": expected,
                    "market_check_version": VERSION, "market_evaluation": deepcopy(result["market_evaluation"]),
                    "execution_evaluation": deepcopy(result["execution_evaluation"]),
                    "expectation_scope": deepcopy(result["expectation_scope"]),
                    "evaluation_purpose": result["evaluation_purpose"],
                    "interprets_original_expectation": result["interprets_original_expectation"]}],
                "note": "已存日线自动取证；原计划身份不变，行情是当前读取后的归档重建。" + "；".join(result["reasons"])})
            return {**result, "recorded_at": now, "saved": True, "source_id": source_id,
                "check": check["record"], "record": check["record"], "saved_status": check["status"]}
