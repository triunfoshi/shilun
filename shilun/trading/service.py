"""从 Mongo 事实快照组装 Trading Cockpit，并驱动 Decision Timeline。"""

from __future__ import annotations

from dataclasses import asdict, replace
from datetime import date, datetime, timedelta
from enum import Enum
from typing import Any
from zoneinfo import ZoneInfo

from shilun.market.holdings import get_holding, list_holdings
from shilun.market.minute_bars_store import COLLECTION_NAME as MINUTE_COLLECTION
from shilun.trading.playbooks.ma5_trend import (
    Ma5IntradayFacts,
    Ma5TrendPlaybook,
    Ma5WatchPlan,
)
from shilun.trading.stop_plans import StopPlanError, build_ma5_stop_plan
from shilun.trading.contracts import (
    ACTION_DEFINITIONS,
    ActionIntent,
    ActionType,
    TradingAction,
    canonical_action_type,
    execution_state_view,
)
from shilun.trading.risk import (
    COLLECTION_NAME as RISK_PROFILE_COLLECTION,
    DynamicRiskInput,
    RiskConfigurationError,
    calculate_dynamic_target,
    get_account_risk_profile,
)
from shilun.trading.replay import ReplayError, replay_ma5_session
from shilun.trading.source_adapters import build_p0_source_snapshots
from shilun.trading.timeline import (
    COLLECTION_NAME as TIMELINE_COLLECTION,
    decision_payload,
    list_decision_events,
    persist_decision_if_changed,
)


MARKET_TIMEZONE = ZoneInfo("Asia/Shanghai")
REALTIME_COLLECTION = "trading_realtime_quotes"
TREND_COLLECTION = "intraday_trend_snapshots"
WATCH_PLAN_COLLECTION = "trading_watch_plans"
SSE_TICKER = "000001.SH"
CHINEXT_TICKER = "399006.SZ"


class TradingDecisionError(ValueError):
    """现有落库事实不足以安全执行 Playbook。"""


def _jsonable(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        return value.isoformat(timespec="seconds")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value


def _positive(*values: Any) -> float | None:
    for value in values:
        try:
            number = float(value)
        except (TypeError, ValueError):
            continue
        if number > 0:
            return number
    return None


def _market_state(quote: dict[str, Any] | None) -> str:
    """KlineShare 的 change_percent 是小数比例；P0 只做方向性硬规则。"""

    try:
        change_percent = float((quote or {}).get("pct_chg"))
    except (TypeError, ValueError):
        return "unknown"
    if change_percent >= 0.005:
        return "修复"
    if change_percent <= -0.005:
        return "走弱"
    return "震荡"


def _latest_document(collection: Any, query: dict[str, Any], sort_field: str,
                     projection: dict[str, int] | None = None) -> dict[str, Any] | None:
    documents = list(collection.find(query, projection or {"_id": 0}).sort(sort_field, -1).limit(1))
    return documents[0] if documents else None


class TradingDecisionService:
    """读取已落库事实；只有 refresh 方法允许写入决策事件。"""

    def __init__(self, store: Any, *, now_factory: Any | None = None) -> None:
        self.store = store
        self.now_factory = now_factory or (lambda: datetime.now(tz=MARKET_TIMEZONE))
        self.playbook = Ma5TrendPlaybook()

    def _holding(self, ticker: str) -> dict[str, Any]:
        holding = get_holding(self.store.collection("holdings"), ticker)
        if holding is None or holding.get("status") not in {"active", "reduced"}:
            raise TradingDecisionError(f"{ticker} 没有 active/reduced 持仓")
        holding = dict(holding)
        from shilun.strategies import (
            E7_OVERSOLD_OVERNIGHT_STRATEGY_ID,
            scenario_code,
        )

        strategy_id = str(holding.get("strategy_id") or "").strip().lower()
        if scenario_code(holding.get("entry_signal")) == "E7":
            if strategy_id != E7_OVERSOLD_OVERNIGHT_STRATEGY_ID:
                holding["_trading_strategy_resolution"] = "repaired_from_e7_entry_signal"
            strategy_id = E7_OVERSOLD_OVERNIGHT_STRATEGY_ID
        if not strategy_id:
            entry_signal = str(holding.get("entry_signal") or "").strip().lower()
            plan_strategy = str((holding.get("current_plan") or {}).get("strategy_id") or "").strip().lower()
            if "ma5" in entry_signal or plan_strategy in {"ma5_v0_2", "ma5_trend_v0_2"}:
                strategy_id = "ma5_v0_2"
                holding["_trading_strategy_resolution"] = "legacy_ma5_evidence"
        if strategy_id not in {
            "ma5_v0_2",
            "ma5_trend_v0_2",
            E7_OVERSOLD_OVERNIGHT_STRATEGY_ID,
        }:
            raise TradingDecisionError(f"{ticker} 当前策略 {strategy_id or 'unknown'} 尚未接入 Trading Playbook")
        if not holding.get("sector_name") or not holding.get("name"):
            basic = self.store.collection("stock_basic").find_one(
                {"ts_code": ticker},
                {"_id": 0, "name": 1, "industry": 1},
            ) or {}
            holding["name"] = holding.get("name") or basic.get("name")
            holding["sector_name"] = holding.get("sector_name") or basic.get("industry")
        holding["_trading_strategy_id"] = strategy_id
        return holding

    @staticmethod
    def _is_e7_holding(holding: dict[str, Any]) -> bool:
        from shilun.strategies import E7_OVERSOLD_OVERNIGHT_STRATEGY_ID

        return str(holding.get("_trading_strategy_id") or holding.get("strategy_id") or "").lower() == (
            E7_OVERSOLD_OVERNIGHT_STRATEGY_ID
        )

    @staticmethod
    def _active_pending_risk_action(holding: dict[str, Any]) -> dict[str, Any] | None:
        pending = dict(holding.get("pending_risk_action") or {})
        return pending if pending.get("status") in {"pending", "partially_executed"} else None

    @classmethod
    def _overlay_pending_execution(
        cls,
        decision: dict[str, Any],
        holding: dict[str, Any],
    ) -> dict[str, Any]:
        pending = cls._active_pending_risk_action(holding)
        if not pending:
            return decision
        try:
            remaining = int(holding.get("quantity") or pending.get("remaining_quantity") or 0)
        except (TypeError, ValueError):
            remaining = 0
        available = cls._available_quantity(holding, remaining)
        target_after_available_sell = max(0, remaining - available)
        payload = dict(decision)
        payload["decision_state"] = "pending_execution"
        payload["pending_execution"] = _jsonable(pending)
        payload["action"] = {
            "action_type": "risk_exit",
            "label": "风险退出已确认，等待成交回写",
            "target_quantity": target_after_available_sell,
            "change_quantity": -available,
            "execution_zone": None,
        }
        payload["action_intent"] = "defensive"
        payload["reasons"] = [
            "此前硬止损已确认；在成交回写前风险动作跨交易日保持有效",
            *list(payload.get("reasons") or []),
        ]
        return payload

    def _latch_risk_exit(self, result: Any, context: dict[str, Any]) -> dict[str, Any] | None:
        action = result.action
        if action is None or action.action_type != "risk_exit":
            return self._active_pending_risk_action(context["holding"])
        holding = context["holding"]
        existing = self._active_pending_risk_action(holding)
        if existing:
            return existing
        quantity = self._current_quantity(holding) or 0
        available = self._available_quantity(holding, quantity)
        pending = {
            "status": "pending",
            "action_type": "risk_exit",
            "label": "硬止损确认，等待实际卖出回写",
            "triggered_at": result.computed_at.isoformat(timespec="seconds"),
            "trigger_price": context.get("stock", {}).get("current_price"),
            "trigger_scenario": result.current_scenario.scenario_id if result.current_scenario else "hard_stop",
            "target_quantity": 0,
            "remaining_quantity": quantity,
            "available_quantity": available,
            "playbook_id": self.playbook.playbook_id,
            "playbook_version": self.playbook.version,
        }
        self.store.collection("holdings").update_one(
            {"ticker": result.ticker},
            {"$set": {"pending_risk_action": pending, "updated_at": result.computed_at.isoformat(timespec="seconds")}},
        )
        holding["pending_risk_action"] = pending
        return pending

    def _latch_daily_exit(self, result: Any, context: dict[str, Any]) -> dict[str, Any] | None:
        """把日检 sell_all 接入盘中执行闭环；只在显式 refresh 时推进。"""

        holding = context["holding"]
        existing = self._active_pending_risk_action(holding)
        if existing:
            return existing
        event = _latest_document(
            self.store.collection("holding_events"),
            {"ticker": result.ticker, "recommended_action": "sell_all"},
            "date",
        )
        if not event:
            return None
        quantity = self._current_quantity(holding) or 0
        available = self._available_quantity(holding, quantity)
        triggered_at = event.get("updated_at") or f"{event.get('date')}T15:00:00+08:00"
        pending = {
            "status": "pending",
            "action_type": "risk_exit",
            "label": "日检已确认全部退出，等待实际卖出回写",
            "triggered_at": triggered_at,
            "trigger_price": event.get("current_price"),
            "trigger_scenario": "daily_invalid_exit",
            "target_quantity": 0,
            "remaining_quantity": quantity,
            "available_quantity": available,
            "playbook_id": self.playbook.playbook_id,
            "playbook_version": self.playbook.version,
            "source": "holding_daily_check",
            "source_event_date": event.get("date"),
            "source_reason": event.get("reason"),
        }
        self.store.collection("holdings").update_one(
            {"ticker": result.ticker},
            {"$set": {"pending_risk_action": pending, "updated_at": result.computed_at.isoformat(timespec="seconds")}},
        )
        holding["pending_risk_action"] = pending
        return pending

    def _sector_reference(self, sector_name: str | None, trade_date: str) -> dict[str, Any]:
        name = str(sector_name or "").strip()
        if not name:
            return {"name": None, "state": "unknown", "source": "unconfigured"}
        date_text = f"{trade_date[:4]}-{trade_date[4:6]}-{trade_date[6:]}"
        cached = _latest_document(
            self.store.collection("sector_trends_cache"),
            {"analysis_date": {"$lte": date_text}},
            "analysis_date",
            projection={"_id": 0, "analysis_date": 1, **{
                f"payload.{group}.{field}": 1
                for group in ("top_sectors", "trend_sectors", "all_sectors")
                for field in ("sector_name", "sector_state_label", "stage_label", "trend_label",
                              "sector_state", "stage", "metrics.return_5d", "metrics.relative_return_5d")
            }},
        )
        payload = (cached or {}).get("payload") or {}
        matched: dict[str, Any] | None = None
        for key in ("top_sectors", "trend_sectors", "all_sectors"):
            matched = next(
                (dict(item) for item in payload.get(key) or [] if str(item.get("sector_name") or "") == name),
                None,
            )
            if matched:
                break
        if not matched:
            return {
                "name": name,
                "state": "unknown",
                "source": "sector_trends_cache_missing",
                "analysis_date": (cached or {}).get("analysis_date"),
            }
        metrics = matched.get("metrics") or {}
        state = matched.get("sector_state_label") or matched.get("stage_label") or matched.get("trend_label") or "参考可用"
        return {
            "name": name,
            "state": state,
            "state_code": matched.get("sector_state") or matched.get("stage"),
            "stage_label": matched.get("stage_label"),
            "trend_label": matched.get("trend_label"),
            "return_5d": metrics.get("return_5d"),
            "relative_return_5d": metrics.get("relative_return_5d"),
            "analysis_date": (cached or {}).get("analysis_date"),
            "source": "sector_trends_cache",
            "drives_action": False,
        }

    def _quote(self, ticker: str) -> dict[str, Any] | None:
        return self.store.collection(REALTIME_COLLECTION).find_one(
            {"ticker": ticker},
            {"_id": 0},
        )

    def _minute_document(self, ticker: str, trade_date: str) -> dict[str, Any] | None:
        collection = self.store.collection(MINUTE_COLLECTION)
        exact = collection.find_one(
            {"ticker": ticker, "trade_date": trade_date, "interval": "5"},
            {"_id": 0},
        )
        return exact or _latest_document(collection, {"ticker": ticker, "interval": "5"}, "trade_date")

    def _trend_document(self, ticker: str, trade_date: str) -> dict[str, Any] | None:
        return self.store.collection(TREND_COLLECTION).find_one(
            {"ticker": ticker, "trade_date": trade_date},
            {"_id": 0},
        )

    def _daily_bars(self, ticker: str, trade_date: str) -> list[dict[str, Any]]:
        end = datetime.strptime(trade_date, "%Y%m%d") - timedelta(days=1)
        start = end - timedelta(days=180)
        raw_market = getattr(self.store, "raw_market", None)
        finder = getattr(raw_market, "find_daily_bars", None)
        if callable(finder):
            rows = finder(
                start_date=start.strftime("%Y-%m-%d"),
                end_date=end.strftime("%Y-%m-%d"),
                tickers=[ticker],
            )
        else:
            rows = list(self.store.collection("market_daily_bars").find(
                {
                    "ticker": ticker,
                    "date": {"$gte": start.strftime("%Y-%m-%d"), "$lte": end.strftime("%Y-%m-%d")},
                },
                {"_id": 0},
            ))
        return sorted(rows, key=lambda item: str(item.get("date") or item.get("trade_date") or ""))

    @staticmethod
    def _trade_date(quote: dict[str, Any], now: datetime) -> str:
        quote_at = quote.get("quote_at") or quote.get("update_at")
        try:
            parsed = datetime.fromisoformat(str(quote_at).replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=MARKET_TIMEZONE)
            return parsed.astimezone(MARKET_TIMEZONE).strftime("%Y%m%d")
        except (TypeError, ValueError):
            return now.astimezone(MARKET_TIMEZONE).strftime("%Y%m%d")

    def _derive_watch_plan(
        self,
        ticker: str,
        holding: dict[str, Any],
        quote: dict[str, Any],
        trade_date: str,
    ) -> dict[str, Any]:
        rows = self._daily_bars(ticker, trade_date)
        closes = [_positive(row.get("close")) for row in rows]
        closes = [value for value in closes if value is not None]
        highs = [_positive(row.get("high")) for row in rows[-20:]]
        highs = [value for value in highs if value is not None]
        if len(closes) < 5:
            raise TradingDecisionError(f"{ticker} 缺少开盘前至少 5 根有效日线，无法冻结 MA5")

        current_plan = dict(holding.get("current_plan") or {})
        frozen_ma5 = sum(closes[-5:]) / 5
        configured_stop = _positive(
            holding.get("stop_loss_1"),
            current_plan.get("stop_loss_1"),
        )
        stop_plan = dict(holding.get("stop_loss_plan") or current_plan.get("stop_loss_plan") or {})
        stop_origin = holding.get("stop_loss_mode") or ("legacy" if configured_stop else "strategy_generated")
        if configured_stop is None:
            reference_price = _positive(
                holding.get("cost_basis"),
                holding.get("entry_price"),
                quote.get("prev_close"),
                closes[-1],
            )
            try:
                stop_plan = build_ma5_stop_plan(
                    rows,
                    reference_price=float(reference_price),
                    as_of=rows[-1].get("date") or rows[-1].get("trade_date"),
                )
                configured_stop = float(stop_plan["recommended_stop"])
            except (StopPlanError, TypeError, ValueError) as error:
                raise TradingDecisionError(f"{ticker} 无法生成 MA5 策略止损：{error}") from error
        hard_stop = configured_stop
        previous_high = max(highs) if highs else _positive(quote.get("prev_close"))
        previous_close = _positive(quote.get("prev_close"), closes[-1])
        open_price = _positive(quote.get("open"), previous_close)
        try:
            position_revision = int(
                holding.get("position_revision") or holding.get("revision") or 0
            )
        except (TypeError, ValueError):
            position_revision = 0
        return {
            "plan_id": (
                f"{trade_date}:{ticker}:{self.playbook.playbook_id}:{self.playbook.version}"
            ),
            "ticker": ticker,
            "trade_date": trade_date,
            "playbook_id": self.playbook.playbook_id,
            "playbook_version": self.playbook.version,
            "plan_revision": 1,
            "position_revision": max(0, position_revision),
            "previous_close": round(float(previous_close), 4),
            "open_price": round(float(open_price), 4),
            "frozen_ma5": round(float(frozen_ma5), 4),
            "hard_stop": round(float(hard_stop), 4),
            "hard_stop_origin": stop_origin,
            "stop_plan": _jsonable(stop_plan),
            "previous_high": round(float(previous_high), 4),
            "derived_from_daily_bar_count": len(rows),
        }

    @staticmethod
    def _current_quantity(holding: dict[str, Any]) -> int | None:
        try:
            quantity = int(holding.get("quantity"))
        except (TypeError, ValueError):
            return None
        return quantity if quantity > 0 else None

    @staticmethod
    def _available_quantity(holding: dict[str, Any], current_quantity: int) -> int:
        raw = holding.get("available_quantity")
        if raw is None:
            return max(0, current_quantity)
        try:
            available = int(raw)
        except (TypeError, ValueError):
            return 0
        return max(0, min(current_quantity, available))

    def _watch_plan(
        self,
        ticker: str,
        holding: dict[str, Any],
        quote: dict[str, Any],
        trade_date: str,
        *,
        persist: bool,
        frozen_at: datetime,
    ) -> dict[str, Any]:
        collection = self.store.collection(WATCH_PLAN_COLLECTION)
        key = {
            "ticker": ticker,
            "trade_date": trade_date,
            "playbook_id": self.playbook.playbook_id,
            "playbook_version": self.playbook.version,
        }
        existing = collection.find_one(key, {"_id": 0})
        if existing:
            try:
                current_revision = max(
                    0,
                    int(holding.get("position_revision") or holding.get("revision") or 0),
                )
            except (TypeError, ValueError):
                current_revision = 0
            if int(existing.get("position_revision") or 0) != current_revision and persist:
                collection.update_one(
                    key,
                    {"$set": {"position_revision": current_revision}},
                )
            return {**existing, "position_revision": current_revision}
        plan = self._derive_watch_plan(ticker, holding, quote, trade_date)
        if not persist:
            return plan
        plan["frozen_at"] = frozen_at.isoformat(timespec="seconds")
        collection.update_one(key, {"$setOnInsert": plan}, upsert=True)
        return collection.find_one(key, {"_id": 0}) or plan

    def evaluate(
        self,
        ticker: str,
        *,
        now: datetime | None = None,
        persist_plan: bool = False,
    ) -> tuple[Any, dict[str, Any]]:
        normalized = ticker.strip().upper()
        computed_at = now or self.now_factory()
        if computed_at.tzinfo is None or computed_at.utcoffset() is None:
            raise TradingDecisionError("now 必须包含时区")
        holding = self._holding(normalized)
        if self._is_e7_holding(holding):
            raise TradingDecisionError(
                f"{normalized} 是 E7 隔夜持仓，必须使用全场景执行引擎。"
            )
        stock_quote = self._quote(normalized)
        if not stock_quote:
            raise TradingDecisionError(f"{normalized} 缺少 trading_realtime_quotes 快照")
        trade_date = self._trade_date(stock_quote, computed_at)
        minute_doc = self._minute_document(normalized, trade_date)
        bars = list((minute_doc or {}).get("bars") or [])
        current_price = _positive(stock_quote.get("current"), bars[-1].get("close") if bars else None)
        if current_price is None:
            raise TradingDecisionError(f"{normalized} 缺少有效当前价")

        sse_quote = self._quote(SSE_TICKER)
        chinext_quote = self._quote(CHINEXT_TICKER)
        plan_payload = self._watch_plan(
            normalized,
            holding,
            stock_quote,
            trade_date,
            persist=persist_plan,
            frozen_at=computed_at,
        )
        current_quantity = self._current_quantity(holding)
        plan = Ma5WatchPlan(
            ticker=plan_payload["ticker"],
            previous_close=plan_payload["previous_close"],
            open_price=plan_payload["open_price"],
            frozen_ma5=plan_payload["frozen_ma5"],
            hard_stop=plan_payload["hard_stop"],
            previous_high=plan_payload["previous_high"],
            # Playbook 契约要求整数；真实数量缺失时仅给纯规则判断，输出层会移除伪数量。
            current_quantity=current_quantity or 0,
        )
        valid_bars = [bar for bar in bars if _positive(bar.get("close")) is not None]
        volume_sum = sum(float(bar.get("volume") or 0) for bar in valid_bars)
        amount_sum = sum(float(bar.get("amount") or 0) for bar in valid_bars)
        vwap = amount_sum / volume_sum if amount_sum > 0 and volume_sum > 0 else None
        vwap_source = "closed_5m_amount_volume"
        trend_document = self._trend_document(normalized, trade_date)
        trend_average = _positive((trend_document or {}).get("avg_price"))
        if trend_average is not None:
            try:
                source_at = datetime.fromisoformat(
                    str((trend_document or {}).get("source_at") or "").replace("Z", "+00:00")
                )
                if source_at.tzinfo is None:
                    source_at = source_at.replace(tzinfo=MARKET_TIMEZONE)
                age_seconds = (computed_at - source_at.astimezone(MARKET_TIMEZONE)).total_seconds()
            except (TypeError, ValueError):
                age_seconds = float("inf")
            if 0 <= age_seconds <= 60:
                vwap = trend_average
                vwap_source = "klineshare_trend_avg_price"
        volume_ratio = _positive(stock_quote.get("volume_ratio"))
        if volume_ratio is None and len(valid_bars) >= 2:
            previous_volumes = [float(bar.get("volume") or 0) for bar in valid_bars[:-1]]
            baseline = sum(previous_volumes) / len(previous_volumes) if previous_volumes else 0
            latest_volume = float(valid_bars[-1].get("volume") or 0)
            volume_ratio = latest_volume / baseline if baseline > 0 else None

        sources = build_p0_source_snapshots(
            stock_quote=stock_quote,
            stock_5m_bars=valid_bars,
            sse_quote=sse_quote,
            chinext_quote=chinext_quote,
            sector_quote=None,
        )
        facts = Ma5IntradayFacts(
            computed_at=computed_at,
            closed_5m_prices=tuple(float(bar["close"]) for bar in valid_bars),
            current_price=float(current_price),
            vwap=vwap,
            volume_ratio=volume_ratio,
            sse_state=_market_state(sse_quote),
            chinext_state=_market_state(chinext_quote),
            sector_state="unknown",
        )
        result = self.playbook.evaluate(plan, facts, sources)
        result, risk_context = self._apply_risk_sizing(
            result,
            holding=holding,
            current_quantity=current_quantity,
            current_price=float(current_price),
            effective_stop=plan.hard_stop,
        )
        context = {
            "holding": holding,
            "trade_date": trade_date,
            "watch_plan": plan_payload,
            "stock": {
                "name": holding.get("name"),
                "current_price": current_price,
                "pct_chg": stock_quote.get("pct_chg"),
                "open": stock_quote.get("open"),
                "prev_close": stock_quote.get("prev_close"),
                "vwap": vwap,
                "vwap_source": vwap_source,
                "intraday_average_source_at": (trend_document or {}).get("source_at"),
                "volume_ratio": volume_ratio,
                "closed_5m_count": len(valid_bars),
                "latest_closed_5m_at": valid_bars[-1].get("datetime") if valid_bars else None,
            },
            "risk": risk_context,
            "market": {
                "sse": {"ticker": SSE_TICKER, "state": facts.sse_state, "pct_chg": (sse_quote or {}).get("pct_chg")},
                "chinext": {"ticker": CHINEXT_TICKER, "state": facts.chinext_state, "pct_chg": (chinext_quote or {}).get("pct_chg")},
                "sector": self._sector_reference(holding.get("sector_name"), trade_date),
                "stock": {
                    "ticker": normalized,
                    "name": holding.get("name"),
                    "state": result.current_scenario.title if result.current_scenario else "数据暂停",
                    "current_price": current_price,
                    "pct_chg": stock_quote.get("pct_chg"),
                    "vwap": vwap,
                },
            },
        }
        return result, context

    def _apply_risk_sizing(
        self,
        result: Any,
        *,
        holding: dict[str, Any],
        current_quantity: int | None,
        current_price: float,
        effective_stop: float,
    ) -> tuple[Any, dict[str, Any]]:
        profile = get_account_risk_profile(self.store.collection(RISK_PROFILE_COLLECTION))
        profile_payload = profile.to_document()
        if current_quantity is None:
            action = result.action
            if action is not None:
                action = replace(action, target_quantity=None, change_quantity=None)
                result = replace(result, action=action)
            return result, {
                "status": "quantity_not_configured",
                "executable": False,
                "profile": profile_payload,
                "reason": "持仓未配置真实股数，禁止生成动态数量",
            }

        available_quantity = self._available_quantity(holding, current_quantity)
        minimum_target = current_quantity - available_quantity
        cost_basis = holding.get("cost_basis") or holding.get("entry_price")
        realized_loss = holding.get("realized_loss_amount") or 0
        try:
            inputs = DynamicRiskInput(
                current_quantity=current_quantity,
                cost_basis=float(cost_basis),
                current_price=current_price,
                effective_stop=effective_stop,
                realized_loss_amount=float(realized_loss),
            )
            sizing = calculate_dynamic_target(inputs, profile)
        except (RiskConfigurationError, TypeError, ValueError) as error:
            # 硬止损只依赖真实数量，可继续给出全部退出；其他动作不得伪造动态数量。
            action = result.action
            if action is not None and action.action_type == "risk_exit":
                action = replace(
                    action,
                    target_quantity=minimum_target,
                    change_quantity=-available_quantity,
                )
            elif action is not None:
                action = replace(action, target_quantity=None, change_quantity=None)
            return replace(result, action=action), {
                "status": "risk_input_invalid",
                "executable": bool(action and action.action_type == "risk_exit"),
                "profile": profile_payload,
                "reason": str(error),
            }

        action = result.action
        if action is not None and action.action_type == "risk_exit":
            action = replace(
                action,
                target_quantity=minimum_target,
                change_quantity=-available_quantity,
            )
        elif action is not None and sizing.target_quantity < current_quantity:
            executable_target = max(minimum_target, sizing.target_quantity)
            action_type = "risk_exit" if executable_target == 0 else "risk_reduce"
            action = TradingAction(
                action_type=action_type,
                label=(
                    "风险预算要求全部退出"
                    if executable_target == 0
                    else f"风险预算要求降至 {executable_target} 股"
                ),
                target_quantity=executable_target,
                change_quantity=executable_target - current_quantity,
            )
            result = replace(
                result,
                action_intent=ActionIntent.DEFENSIVE,
                reasons=(*result.reasons, *sizing.explanation),
            )
        elif action is not None:
            action = replace(action, target_quantity=current_quantity, change_quantity=0)
        return replace(result, action=action), {
            "status": sizing.status,
            "executable": action is not None,
            "profile": profile_payload,
            "inputs": asdict(inputs),
            "calculation": sizing.to_dict(),
        }

    def _latest_e7_scenario_event(self, ticker: str) -> dict[str, Any] | None:
        cursor = self.store.collection(TIMELINE_COLLECTION).find(
            {"ticker": ticker},
            {"_id": 0},
        ).sort("computed_at", -1).limit(80)
        for raw in cursor:
            event = dict(raw)
            decision = dict(event.get("decision") or {})
            scenarios = list(decision.get("candidate_scenarios") or [])
            current = dict(decision.get("current_scenario") or {})
            if str(current.get("scenario_id") or "").upper() == "E7" or any(
                str((item or {}).get("scenario_id") or "").upper() == "E7"
                for item in scenarios
                if isinstance(item, dict)
            ):
                return event
        return None

    def _latest_full_scenario_document(
        self,
        collection_name: str,
        ticker: str,
        *,
        sort_field: str,
    ) -> dict[str, Any] | None:
        cursor = self.store.collection(collection_name).find(
            {"ticker": ticker},
            {"_id": 0},
        ).sort(sort_field, -1).limit(30)
        for raw in cursor:
            document = dict(raw)
            if document.get("playbook_id") in {"execution_scenarios_v0_3", "full_scenario_execution"}:
                return document
            if collection_name == WATCH_PLAN_COLLECTION and isinstance(document.get("plan"), dict):
                return document
            if collection_name == "execution_scenario_fact_snapshots" and isinstance(document.get("facts"), dict):
                return document
        return None

    def _e7_risk_context(
        self,
        holding: dict[str, Any],
        *,
        current_price: float,
        effective_stop: float,
        action: dict[str, Any],
    ) -> dict[str, Any]:
        profile = get_account_risk_profile(self.store.collection(RISK_PROFILE_COLLECTION))
        profile_payload = profile.to_document()
        quantity = self._current_quantity(holding)
        if quantity is None:
            return {
                "status": "quantity_not_configured",
                "executable": False,
                "profile": profile_payload,
                "reason": "持仓未配置真实股数，禁止生成 E7 退出数量",
            }
        try:
            inputs = DynamicRiskInput(
                current_quantity=quantity,
                cost_basis=float(holding.get("cost_basis") or holding.get("entry_price")),
                current_price=float(current_price),
                effective_stop=float(effective_stop),
                realized_loss_amount=float(holding.get("realized_loss_amount") or 0),
            )
            sizing = calculate_dynamic_target(inputs, profile)
            calculation = sizing.to_dict()
            calculation.update({
                "target_quantity": action.get("target_quantity"),
                "change_quantity": action.get("change_quantity"),
                "effective_stop": round(float(effective_stop), 4),
            })
            return {
                "status": "e7_exit_plan",
                "executable": action.get("action_type") in {"prepare_reduce", "risk_exit"},
                "profile": profile_payload,
                "inputs": asdict(inputs),
                "calculation": calculation,
            }
        except (RiskConfigurationError, TypeError, ValueError) as error:
            return {
                "status": "risk_input_invalid",
                "executable": action.get("action_type") == "risk_exit",
                "profile": profile_payload,
                "reason": str(error),
            }

    def _e7_cockpit(self, ticker: str, holding: dict[str, Any]) -> dict[str, Any]:
        event = self._latest_e7_scenario_event(ticker)
        if event is None:
            raise TradingDecisionError(
                f"{ticker} 尚无 E7 全场景执行快照；请点击“同步行情并重算”。"
            )
        decision = dict(event.get("decision") or {})
        action = dict(decision.get("action") or {})
        current_scenario = dict(decision.get("current_scenario") or {})
        computed_at = str(event.get("computed_at") or "")
        plan_doc = self._latest_full_scenario_document(
            WATCH_PLAN_COLLECTION,
            ticker,
            sort_field="updated_at",
        ) or {}
        fact_doc = self._latest_full_scenario_document(
            "execution_scenario_fact_snapshots",
            ticker,
            sort_field="computed_at",
        ) or {}
        raw_plan = dict(plan_doc.get("plan") or {})
        raw_facts = dict(fact_doc.get("facts") or {})
        required_fresh = bool(fact_doc.get("required_data_fresh"))
        failed_sources = list(fact_doc.get("freshness_failed_sources") or [])
        freshness_state = "normal" if required_fresh else "missing"
        decision.update({
            "computed_at": computed_at,
            "reasons": list(event.get("reasons") or []),
            "data_freshness": {
                "state": freshness_state,
                "can_generate_action": required_fresh,
                "sources": [],
                "reasons": failed_sources,
            },
        })
        execution_state = str(decision.get("execution_state") or "positioned")
        try:
            state_view = execution_state_view(execution_state)
        except (ValueError, TypeError):
            state_view = execution_state_view("positioned")
        try:
            action_definition = ACTION_DEFINITIONS[canonical_action_type(action.get("action_type"))]
        except (ValueError, TypeError, KeyError):
            action_definition = ACTION_DEFINITIONS[ActionType.NO_ACTION]
        raw_presentation = dict(decision.get("presentation") or {})
        decision["presentation"] = {
            "state_label": state_view.label,
            "headline": raw_presentation.get("headline") or action.get("label") or state_view.instruction,
            "instruction": state_view.instruction,
            "reason": raw_presentation.get("reason") or (decision["reasons"][0] if decision["reasons"] else "E7 执行规则已完成本轮评估"),
            "next_condition": raw_presentation.get("next_condition") or ((decision.get("switch_conditions") or ["等待下一根闭合 5 分钟 K"])[0]),
            "urgency": action_definition.urgency,
            "executable": bool(action_definition.executable and required_fresh),
            "target_quantity": action.get("target_quantity"),
            "change_quantity": action.get("change_quantity"),
            "deadline": raw_presentation.get("deadline") or "10:00",
            "data_as_of": computed_at,
        }
        quote = self._quote(ticker) or {}
        minute_doc = self._minute_document(ticker, str(plan_doc.get("trade_date") or "")) or {}
        minute_bars = list(minute_doc.get("bars") or [])
        current_price = _positive(
            quote.get("current"),
            minute_bars[-1].get("close") if minute_bars else None,
            holding.get("entry_price"),
        ) or 0.0
        hard_stop = _positive(holding.get("stop_loss_1"), raw_plan.get("hard_stop")) or 0.01
        watch_plan = {
            "plan_id": plan_doc.get("plan_id") or event.get("plan_id"),
            "trade_date": plan_doc.get("trade_date"),
            "playbook_id": plan_doc.get("playbook_id") or "execution_scenarios_v0_3",
            "playbook_version": plan_doc.get("playbook_version"),
            "previous_close": raw_plan.get("previous_close"),
            "open_price": raw_plan.get("open_price"),
            "frozen_ma5": raw_plan.get("ma5"),
            "hard_stop": hard_stop,
            "hard_stop_origin": "strategy",
            "stop_plan": holding.get("stop_loss_plan") or {},
            "previous_high": raw_plan.get("pressure"),
            "exit_window": "09:30–10:00",
        }
        risk = self._e7_risk_context(
            holding,
            current_price=current_price,
            effective_stop=hard_stop,
            action=action,
        )
        trade_date = str(plan_doc.get("trade_date") or "")
        context = {
            "holding": holding,
            "trade_date": trade_date,
            "watch_plan": watch_plan,
            "stock": {
                "name": holding.get("name"),
                "current_price": current_price,
                "pct_chg": quote.get("pct_chg"),
                "open": quote.get("open"),
                "prev_close": quote.get("prev_close"),
                "closed_5m_count": len(minute_bars),
                "latest_closed_5m_at": minute_bars[-1].get("datetime") if minute_bars else None,
            },
            "risk": risk,
            "market": {
                "sse": {"ticker": SSE_TICKER, "state": _market_state(self._quote(SSE_TICKER)), "pct_chg": (self._quote(SSE_TICKER) or {}).get("pct_chg")},
                "chinext": {"ticker": CHINEXT_TICKER, "state": _market_state(self._quote(CHINEXT_TICKER)), "pct_chg": (self._quote(CHINEXT_TICKER) or {}).get("pct_chg")},
                "sector": self._sector_reference(holding.get("sector_name"), trade_date),
                "stock": {
                    "ticker": ticker,
                    "name": holding.get("name"),
                    "state": current_scenario.get("title") or "E7 隔夜退出",
                    "current_price": current_price,
                    "pct_chg": quote.get("pct_chg"),
                },
            },
        }
        return {
            "status": "ok",
            "playbook": {
                "id": plan_doc.get("playbook_id") or "execution_scenarios_v0_3",
                "version": plan_doc.get("playbook_version"),
            },
            "decision": self._overlay_pending_execution(decision, holding),
            **_jsonable(context),
            "latest_timeline_event": event,
            "execution_facts": raw_facts,
        }

    def cockpit(self, ticker: str, *, now: datetime | None = None) -> dict[str, Any]:
        """只读计算，不写 Timeline，也不冻结新的 watch plan。"""

        normalized = ticker.strip().upper()
        holding = self._holding(normalized)
        if self._is_e7_holding(holding):
            return self._e7_cockpit(normalized, holding)
        result, context = self.evaluate(ticker, now=now, persist_plan=False)
        plan_id = context["watch_plan"].get("plan_id") or (
            f"{context['trade_date']}:{result.ticker}:{self.playbook.playbook_id}:{self.playbook.version}"
        )
        events = list_decision_events(
            self.store.collection(TIMELINE_COLLECTION),
            result.ticker,
            limit=1,
            trade_date=context["trade_date"],
            plan_id=plan_id,
        )
        decision = self._overlay_pending_execution(decision_payload(result), context["holding"])
        return {
            "status": "ok",
            "playbook": {"id": self.playbook.playbook_id, "version": self.playbook.version},
            "decision": decision,
            **_jsonable(context),
            "latest_timeline_event": events[0] if events else None,
        }

    def refresh(self, ticker: str, *, now: datetime | None = None) -> dict[str, Any]:
        """显式计算并在语义变化时追加 Timeline 事件。"""

        normalized = ticker.strip().upper()
        holding = self._holding(normalized)
        if self._is_e7_holding(holding):
            from shilun.trading.execution_production import FullScenarioProductionService

            scenario_result = FullScenarioProductionService(self.store).refresh_all(
                now=now,
                tickers=[normalized],
            )
            cockpit = self._e7_cockpit(normalized, holding)
            decision = dict(cockpit.get("decision") or {})
            action = dict(decision.get("action") or {})
            pending = self._active_pending_risk_action(holding)
            if action.get("action_type") == "risk_exit" and pending is None:
                quantity = self._current_quantity(holding) or 0
                available = self._available_quantity(holding, quantity)
                pending = {
                    "status": "pending",
                    "action_type": "risk_exit",
                    "label": "E7 次日退出已确认，等待实际卖出回写",
                    "triggered_at": decision.get("computed_at"),
                    "trigger_price": (cockpit.get("stock") or {}).get("current_price"),
                    "trigger_scenario": "E7",
                    "target_quantity": 0,
                    "remaining_quantity": quantity,
                    "available_quantity": available,
                    "playbook_id": (cockpit.get("playbook") or {}).get("id"),
                    "playbook_version": (cockpit.get("playbook") or {}).get("version"),
                    "source": "e7_full_scenario",
                }
                self.store.collection("holdings").update_one(
                    {"ticker": normalized},
                    {"$set": {"pending_risk_action": pending, "updated_at": decision.get("computed_at")}},
                )
                holding["pending_risk_action"] = pending
                decision = self._overlay_pending_execution(decision, holding)
            return {
                "status": "ok",
                "ticker": normalized,
                "event_created": bool(scenario_result.get("decision_count")),
                "event": cockpit.get("latest_timeline_event"),
                "pending_execution": _jsonable(pending),
                "decision": decision,
                "scenario_refresh": scenario_result,
            }

        result, context = self.evaluate(ticker, now=now, persist_plan=True)
        pending_before = self._active_pending_risk_action(context["holding"])
        if not pending_before:
            pending_before = self._latch_daily_exit(result, context)
        if pending_before and (result.action is None or result.action.action_type != "risk_exit"):
            created, event = False, None
        else:
            plan_id = context["watch_plan"].get("plan_id") or (
                f"{context['trade_date']}:{result.ticker}:{self.playbook.playbook_id}:{self.playbook.version}"
            )
            created, event = persist_decision_if_changed(
                self.store.collection(TIMELINE_COLLECTION),
                result,
                playbook_id=self.playbook.playbook_id,
                playbook_version=self.playbook.version,
                facts_snapshot={
                    "trade_date": context["trade_date"],
                    "watch_plan": context["watch_plan"],
                    "stock": context["stock"],
                    "market": context["market"],
                    "risk": context["risk"],
                },
                plan_id=plan_id,
                trade_date=context["trade_date"],
                plan_revision=int(context["watch_plan"].get("plan_revision") or 1),
                position_revision=int(context["watch_plan"].get("position_revision") or 0),
            )
        pending = self._latch_risk_exit(result, context)
        decision = self._overlay_pending_execution(decision_payload(result), context["holding"])
        return {
            "status": "ok",
            "ticker": result.ticker,
            "event_created": created,
            "event": event,
            "pending_execution": _jsonable(pending),
            "decision": decision,
        }

    def dashboard(self, *, now: datetime | None = None) -> dict[str, Any]:
        holdings = list_holdings(self.store.collection("holdings"))
        items: list[dict[str, Any]] = []
        for holding in holdings:
            ticker = str(holding.get("ticker") or "").upper()
            try:
                cockpit = self.cockpit(ticker, now=now)
                decision = cockpit["decision"]
                items.append({
                    "ticker": ticker,
                    "name": holding.get("name"),
                    "status": "ok",
                    "action": decision.get("action"),
                    "current_scenario": decision.get("current_scenario"),
                    "data_freshness": decision.get("data_freshness"),
                    "risk": cockpit.get("risk"),
                })
            except TradingDecisionError as error:
                items.append({"ticker": ticker, "name": holding.get("name"), "status": "unavailable", "reason": str(error)})
        return {"status": "ok", "count": len(items), "holdings": items}

    def refresh_all(self, *, now: datetime | None = None) -> dict[str, Any]:
        holdings = list_holdings(self.store.collection("holdings"))
        results: list[dict[str, Any]] = []
        for holding in holdings:
            ticker = str(holding.get("ticker") or "").upper()
            try:
                results.append(self.refresh(ticker, now=now))
            except TradingDecisionError as error:
                results.append({"status": "unavailable", "ticker": ticker, "reason": str(error), "event_created": False})
        return {
            "status": "ok" if all(item["status"] == "ok" for item in results) else "partial",
            "count": len(results),
            "event_created_count": sum(bool(item.get("event_created")) for item in results),
            "results": results,
        }

    def timeline(self, ticker: str, *, limit: int = 100) -> list[dict[str, Any]]:
        return list_decision_events(
            self.store.collection(TIMELINE_COLLECTION),
            ticker.strip().upper(),
            limit=limit,
        )

    def scenarios(self, ticker: str, *, now: datetime | None = None) -> dict[str, Any]:
        normalized = ticker.strip().upper()
        holding = self._holding(normalized)
        if self._is_e7_holding(holding):
            cockpit = self._e7_cockpit(normalized, holding)
            decision = dict(cockpit.get("decision") or {})
            return {
                "status": "ok",
                "ticker": normalized,
                "current_scenario": decision.get("current_scenario"),
                "candidate_scenarios": decision.get("candidate_scenarios") or [],
                "switch_conditions": decision.get("switch_conditions") or [],
                "watch_plan": cockpit.get("watch_plan") or {},
            }
        result, context = self.evaluate(ticker, now=now, persist_plan=False)
        return {
            "status": "ok",
            "ticker": result.ticker,
            "current_scenario": _jsonable(asdict(result.current_scenario)) if result.current_scenario else None,
            "candidate_scenarios": _jsonable([asdict(item) for item in result.candidate_scenarios]),
            "switch_conditions": list(result.switch_conditions),
            "watch_plan": _jsonable(context["watch_plan"]),
        }

    def replay(self, ticker: str, *, trade_date: str) -> dict[str, Any]:
        """只读回放一个已落库交易日，不写 Watch Plan 或 Timeline。"""

        normalized = ticker.strip().upper()
        if len(trade_date) != 8 or not trade_date.isdigit():
            raise TradingDecisionError("trade_date 必须使用 YYYYMMDD")
        holding = self._holding(normalized)
        if self._is_e7_holding(holding):
            raise TradingDecisionError("E7 回放必须使用全场景回放事实，不能套用 MA5 回放器。")
        collection = self.store.collection(MINUTE_COLLECTION)

        def document(target: str) -> dict[str, Any] | None:
            return collection.find_one(
                {"ticker": target, "trade_date": trade_date, "interval": "5"},
                {"_id": 0},
            )

        stock_doc = document(normalized)
        sse_doc = document(SSE_TICKER)
        chinext_doc = document(CHINEXT_TICKER)
        stock_bars = list((stock_doc or {}).get("bars") or [])
        if not stock_bars:
            raise TradingDecisionError(f"{normalized} 在 {trade_date} 缺少 5 分钟 K")
        rows = self._daily_bars(normalized, trade_date)
        if not rows:
            raise TradingDecisionError(f"{normalized} 在 {trade_date} 前缺少日线")
        previous_close = _positive(rows[-1].get("close"))
        quote = {
            "prev_close": previous_close,
            "open": _positive(stock_bars[0].get("open"), stock_bars[0].get("close")),
        }
        plan_payload = self._derive_watch_plan(normalized, holding, quote, trade_date)
        plan = Ma5WatchPlan(
            ticker=normalized,
            previous_close=plan_payload["previous_close"],
            open_price=plan_payload["open_price"],
            frozen_ma5=plan_payload["frozen_ma5"],
            hard_stop=plan_payload["hard_stop"],
            previous_high=plan_payload["previous_high"],
            current_quantity=self._current_quantity(holding) or 0,
        )
        try:
            result = replay_ma5_session(
                plan=plan,
                stock_bars=stock_bars,
                sse_bars=list((sse_doc or {}).get("bars") or []),
                chinext_bars=list((chinext_doc or {}).get("bars") or []),
                playbook=self.playbook,
            )
        except ReplayError as error:
            raise TradingDecisionError(str(error)) from error
        return {
            **result,
            "trade_date": trade_date,
            "watch_plan": _jsonable(plan_payload),
            "sources": {
                "stock": (stock_doc or {}).get("source"),
                "sse": (sse_doc or {}).get("source"),
                "chinext": (chinext_doc or {}).get("source"),
            },
        }
