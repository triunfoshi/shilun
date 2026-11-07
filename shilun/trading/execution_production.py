"""Job 12I：把盘中真实事实接入 FullScenarioPlaybook 并持久化可回放事件。

本桥接器不请求行情网络，也不补造缺失事实。它只消费盘中同步任务已经落库
的报价、闭合 5 分钟 K、双指数、板块样本、涨跌停和上一交易日日线；缺失
任一硬事实时写入 ``data_blocked`` 评估，供工作台解释“未触发”而非“未计算”。
"""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from datetime import date, datetime, timedelta
from enum import Enum
from hashlib import sha256
import json
from statistics import median
from typing import Any, Iterable
from zoneinfo import ZoneInfo

from shilun.trading.contracts import ACTION_DEFINITIONS, ActionType, PositionState, ScenarioState
from shilun.trading.key_levels import KeyLevelError, build_entry_price_ceiling, build_frozen_key_levels
from shilun.trading.market_facts import (
    CHINEXT_TICKER,
    SECTOR_FACT_COLLECTION,
    SSE_TICKER,
    collect_execution_monitor_pool,
    matching_benchmark,
)
from shilun.trading.playbooks.execution_scenarios import (
    ClosedFiveMinuteBar,
    E7Phase,
    E7ThreeDayFacts,
    FullScenarioPlaybook,
    IntradayMarketState,
    MarketPermission,
    ScenarioDecision,
    ScenarioExecutionPlan,
    ScenarioMarketFacts,
    SectorExecutionState,
)
from shilun.trading.risk import get_account_risk_profile
from shilun.trading.sizing import ScenarioSizingContext, list_active_cash_reservations


MARKET_TIMEZONE = ZoneInfo("Asia/Shanghai")
PLAN_COLLECTION = "trading_watch_plans"
FACT_COLLECTION = "execution_scenario_fact_snapshots"
E7_EVALUATION_COLLECTION = "execution_e7_evaluations"
SECTOR_SNAPSHOT_COLLECTION = "execution_market_fact_snapshots"
DECISION_COLLECTION = "trading_decision_events"
MINUTE_COLLECTION = "minute_bars_daily"
QUOTE_COLLECTION = "trading_realtime_quotes"
TREND_COLLECTION = "intraday_trend_snapshots"
PRICE_LIMIT_COLLECTION = "execution_price_limit_facts"
BENCHMARKS = {SSE_TICKER, CHINEXT_TICKER}


def _number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _date_text(value: Any) -> str:
    text = str(value or "")[:10]
    if len(text) >= 8 and text[:8].isdigit():
        text = f"{text[:4]}-{text[4:6]}-{text[6:8]}"
    return text


def _aware(value: Any, *, fallback: datetime | None = None) -> datetime | None:
    if value in (None, ""):
        return fallback
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return fallback
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=MARKET_TIMEZONE)
    return parsed.astimezone(MARKET_TIMEZONE)


def _jsonable(value: Any) -> Any:
    if is_dataclass(value):
        return _jsonable(asdict(value))
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (tuple, list, set, frozenset)):
        return [_jsonable(item) for item in value]
    return value


def _digest(value: Any) -> str:
    raw = json.dumps(_jsonable(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return sha256(raw.encode("utf-8")).hexdigest()


def _scenario_plan_from_payload(payload: dict[str, Any]) -> ScenarioExecutionPlan:
    """还原当日首次落库的冻结计划，后续盘中轮询不再重算关键位。"""

    sellable = payload.get("sellable_quantity")
    return ScenarioExecutionPlan(
        ticker=str(payload.get("ticker") or "").upper(),
        position_state=PositionState(str(payload.get("position_state") or "flat")),
        previous_close=float(payload["previous_close"]),
        open_price=float(payload["open_price"]),
        atr=float(payload["atr"]),
        support=float(payload["support"]),
        ma5=float(payload["ma5"]),
        ma8=float(payload["ma8"]),
        pressure=float(payload["pressure"]),
        first_target=float(payload["first_target"]),
        soft_risk=float(payload["soft_risk"]),
        hard_stop=float(payload["hard_stop"]),
        max_acceptable_price=float(payload["max_acceptable_price"]),
        entry_zone=tuple(float(value) for value in payload["entry_zone"]),
        current_quantity=int(payload.get("current_quantity") or 0),
        sellable_quantity=None if sellable is None else int(sellable),
        lot_size=int(payload.get("lot_size") or 100),
        missed_locked=bool(payload.get("missed_locked")),
    )


def _documents(collection: Any, query: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    try:
        return [dict(item) for item in collection.find(query or {}, {"_id": 0})]
    except Exception:  # noqa: BLE001 - 旧测试桩或尚未创建集合时显式返回空事实
        return []


def _latest(collection: Any, query: dict[str, Any] | None, field: str) -> dict[str, Any] | None:
    docs = _documents(collection, query)
    docs.sort(key=lambda item: str(item.get(field) or ""), reverse=True)
    return docs[0] if docs else None


def _weekday_after(value: date) -> date:
    candidate = value + timedelta(days=1)
    while candidate.weekday() >= 5:
        candidate += timedelta(days=1)
    return candidate


def _trade_day_after(store: Any, value: date) -> date:
    try:
        docs = _documents(store.collection("trade_calendar"), {"is_open": 1})
        dates = sorted(
            parsed for item in docs
            if (text := _date_text(item.get("cal_date")))
            and (parsed := date.fromisoformat(text)) > value
        )
        if dates:
            return dates[0]
    except (TypeError, ValueError):
        pass
    return _weekday_after(value)


def _daily_return_strength(rows: list[dict[str, Any]]) -> float:
    if len(rows) < 16:
        return 0.0
    closes = [_number(item.get("close")) for item in rows]
    highs = [_number(item.get("high")) for item in rows]
    lows = [_number(item.get("low")) for item in rows]
    if any(value is None for value in (*closes[-15:], *highs[-14:], *lows[-14:])):
        return 0.0
    previous_close = float(closes[-2])
    daily_return = float(closes[-1]) / previous_close - 1.0
    true_ranges = []
    for index in range(len(rows) - 14, len(rows)):
        prior = float(closes[index - 1])
        true_ranges.append(max(
            float(highs[index]) - float(lows[index]),
            abs(float(highs[index]) - prior),
            abs(float(lows[index]) - prior),
        ))
    atr_pct = (sum(true_ranges) / len(true_ranges)) / previous_close
    return round(max(0.0, -daily_return / atr_pct), 6) if atr_pct > 0 else 0.0


def _closed_bars(raw_bars: Iterable[dict[str, Any]], computed_at: datetime) -> tuple[ClosedFiveMinuteBar, ...]:
    result: list[ClosedFiveMinuteBar] = []
    for raw in raw_bars:
        closed_at = _aware(raw.get("datetime") or raw.get("closed_at"))
        open_price = _number(raw.get("open"))
        high = _number(raw.get("high"))
        low = _number(raw.get("low"))
        close = _number(raw.get("close"))
        if None in {closed_at, open_price, high, low, close} or closed_at > computed_at:
            continue
        try:
            result.append(ClosedFiveMinuteBar(
                closed_at=closed_at,
                open=float(open_price),
                high=float(high),
                low=float(low),
                close=float(close),
                volume_ratio=(
                    float(raw["volume_ratio"])
                    if raw.get("volume_ratio") not in (None, "") else None
                ),
            ))
        except (TypeError, ValueError):
            continue
    return tuple(sorted(result, key=lambda item: item.closed_at))


def _vwap(raw_bars: list[dict[str, Any]], bars: tuple[ClosedFiveMinuteBar, ...]) -> float:
    if not bars:
        raise ValueError("缺少闭合 5 分钟 K")
    volume_sum = sum(max(0.0, float(item.get("volume") or 0)) for item in raw_bars)
    amount_sum = sum(max(0.0, float(item.get("amount") or 0)) for item in raw_bars)
    raw = amount_sum / volume_sum if volume_sum > 0 and amount_sum > 0 else 0.0
    low = min(item.low for item in bars)
    high = max(item.high for item in bars)
    if low * 0.5 <= raw <= high * 2:
        return raw
    weighted = [
        (bar.close, max(0.0, float(item.get("volume") or 0)))
        for bar, item in zip(bars, raw_bars[-len(bars):])
    ]
    weight = sum(item[1] for item in weighted)
    return sum(price * volume for price, volume in weighted) / weight if weight else median(item.close for item in bars)


def _quote_return(quote: dict[str, Any]) -> float | None:
    current = _number(quote.get("current"))
    previous = _number(quote.get("prev_close") or quote.get("pre_close"))
    if current and previous:
        return current / previous - 1.0
    try:
        return float(quote.get("pct_chg"))
    except (TypeError, ValueError):
        return None


def _intraday_state(quotes: dict[str, dict[str, Any]]) -> IntradayMarketState:
    sse = _quote_return(quotes.get(SSE_TICKER) or {})
    growth = _quote_return(quotes.get(CHINEXT_TICKER) or {})
    if sse is None or growth is None:
        return IntradayMarketState.RANGE
    if sse <= -0.01 and growth <= -0.01:
        return IntradayMarketState.RISK_OFF
    if sse >= 0 and growth >= 0:
        return IntradayMarketState.REPAIR
    if abs(sse - growth) >= 0.01:
        return IntradayMarketState.DIVERGENCE
    return IntradayMarketState.RANGE


def _index_stability(bars: tuple[ClosedFiveMinuteBar, ...]) -> tuple[bool, bool]:
    recent = bars[-3:]
    if len(recent) < 3:
        return False, False
    no_new_low = recent[1].low >= recent[0].low and recent[2].low >= recent[0].low
    accelerating = recent[2].low < recent[1].low < recent[0].low
    return no_new_low, accelerating


class FullScenarioProductionService:
    """构造、评估并持久化一轮全场景生产快照。"""

    def __init__(self, store: Any, *, playbook: FullScenarioPlaybook | None = None) -> None:
        self.store = store
        self.playbook = playbook or FullScenarioPlaybook()

    def refresh_all(
        self,
        *,
        now: datetime | None = None,
        tickers: Iterable[str] | None = None,
    ) -> dict[str, Any]:
        computed_at = now or datetime.now(tz=MARKET_TIMEZONE)
        if computed_at.tzinfo is None or computed_at.utcoffset() is None:
            raise ValueError("now 必须包含时区")
        computed_at = computed_at.astimezone(MARKET_TIMEZONE)
        trade_date = computed_at.strftime("%Y%m%d")
        pool = collect_execution_monitor_pool(self.store, trade_date)
        scenario_scope = set(pool.scenario_tickers)
        requested_tickers = {str(ticker).upper() for ticker in (tickers or ()) if str(ticker).strip()}
        candidates = [
            item for item in pool.items
            if item.ticker in scenario_scope and item.ticker not in BENCHMARKS
            and (not requested_tickers or item.ticker in requested_tickers)
        ]
        holdings = {
            str(item.get("ticker") or "").upper(): item
            for item in _documents(self.store.collection("holdings"))
            if item.get("status") in {"active", "reduced"} and int(item.get("quantity") or 0) > 0
        }
        quotes = {
            str(item.get("ticker") or "").upper(): item
            for item in _documents(self.store.collection(QUOTE_COLLECTION))
        }
        minute_docs = {
            str(item.get("ticker") or "").upper(): item
            for item in _documents(self.store.collection(MINUTE_COLLECTION), {
                "trade_date": trade_date, "interval": "5",
            })
        }
        sector_facts = {
            str(item.get("sector_name") or ""): item
            for item in _documents(self.store.collection(SECTOR_FACT_COLLECTION), {"trade_date": trade_date})
        }
        price_limits = {
            str(item.get("ticker") or "").upper(): item
            for item in _documents(self.store.collection(PRICE_LIMIT_COLLECTION), {"trade_date": trade_date})
        }
        latest_market = _latest(self.store.collection("market_part1_cache"), {}, "cached_at") or {}
        market_payload = latest_market.get("payload") or {}
        market_source_at = _aware(latest_market.get("cached_at") or latest_market.get("computed_at"))
        permission_text = str(
            (market_payload.get("market_gate") or {}).get("state")
            or market_payload.get("market_permission")
            or "empty"
        ).lower()
        try:
            market_permission = MarketPermission(permission_text)
        except ValueError:
            market_permission = MarketPermission.EMPTY

        tickers = [item.ticker for item in candidates] + [SSE_TICKER, CHINEXT_TICKER]
        daily_by_ticker = self._daily_history(tickers, computed_at)
        names = self._stock_names()
        profile = get_account_risk_profile(self.store.collection("account_risk_profiles"))
        reservations = list_active_cash_reservations(self.store.collection("execution_cash_reservations"))

        prepared: list[tuple[Any, ScenarioExecutionPlan, ScenarioMarketFacts, ScenarioDecision, str]] = []
        blocked: list[str] = []
        for item in candidates:
            try:
                plan, plan_id = self._build_plan(
                    item=item,
                    holding=holdings.get(item.ticker),
                    quote=quotes.get(item.ticker) or {},
                    daily=daily_by_ticker.get(item.ticker) or [],
                    trade_date=trade_date,
                )
                facts = self._build_facts(
                    item=item,
                    plan=plan,
                    holding=holdings.get(item.ticker),
                    quote=quotes.get(item.ticker) or {},
                    quotes=quotes,
                    minute_docs=minute_docs,
                    sector_facts=sector_facts,
                    price_limit=price_limits.get(item.ticker) or {},
                    daily_by_ticker=daily_by_ticker,
                    market_permission=market_permission,
                    market_source_at=market_source_at,
                    computed_at=computed_at,
                    trade_date=trade_date,
                    pool=pool,
                )
                decision = self.playbook.evaluate(plan, facts)
                prepared.append((item, plan, facts, decision, plan_id))
            except (KeyLevelError, ValueError, TypeError) as error:
                blocked.append(item.ticker)
                self._persist_blocked_e7(
                    ticker=item.ticker,
                    name=names.get(item.ticker),
                    trade_date=trade_date,
                    computed_at=computed_at,
                    reason=str(error),
                )

        ready_tickers = [
            item.ticker for item, _plan, _facts, decision, _plan_id in prepared
            if decision.execution_state.value == "entry_ready"
        ]
        main_ticker = ready_tickers[0] if ready_tickers else None
        decision_count = 0
        for item, plan, facts, preliminary, plan_id in prepared:
            context = ScenarioSizingContext(
                profile=profile,
                plan_id=plan_id,
                is_main_opportunity=(
                    plan.position_state is PositionState.POSITIONED or item.ticker == main_ticker
                ),
                active_reservations=reservations,
            )
            decision = self.playbook.evaluate(plan, facts, context)
            self._persist_plan(item, plan, plan_id, trade_date, computed_at)
            self._persist_facts(item, facts, plan_id, trade_date, computed_at)
            self._persist_e7(item.ticker, names.get(item.ticker), decision, facts, trade_date, computed_at)
            if self._persist_decision(
                ticker=item.ticker,
                name=names.get(item.ticker),
                plan=plan,
                decision=decision,
                plan_id=plan_id,
            ):
                decision_count += 1

        return {
            "status": "success" if not blocked else "partial",
            "trade_date": trade_date,
            "candidate_count": len(candidates),
            "evaluated_count": len(prepared),
            "decision_count": decision_count,
            "e7_evaluation_count": len(prepared) + len(blocked),
            "failed_tickers": blocked,
            "primary_opportunity_ticker": main_ticker,
        }

    def _daily_history(self, tickers: list[str], computed_at: datetime) -> dict[str, list[dict[str, Any]]]:
        end_date = (computed_at.date() - timedelta(days=1)).isoformat()
        start_date = (computed_at.date() - timedelta(days=150)).isoformat()
        rows = self.store.raw_market.find_daily_bars(
            start_date=start_date,
            end_date=end_date,
            tickers=list(dict.fromkeys(tickers)),
        )
        grouped: dict[str, list[dict[str, Any]]] = {}
        for raw in rows:
            ticker = str(raw.get("ticker") or raw.get("ts_code") or "").upper()
            if not ticker:
                continue
            grouped.setdefault(ticker, []).append({**raw, "date": _date_text(raw.get("date") or raw.get("trade_date"))})
        for values in grouped.values():
            values.sort(key=lambda item: item["date"])
        return grouped

    def _stock_names(self) -> dict[str, str]:
        try:
            return {
                str(item.get("ts_code") or item.get("ticker") or "").upper(): str(item.get("name") or "")
                for item in self.store.raw_market.find_stock_basic()
            }
        except Exception:  # noqa: BLE001
            return {}

    def _build_plan(
        self,
        *,
        item: Any,
        holding: dict[str, Any] | None,
        quote: dict[str, Any],
        daily: list[dict[str, Any]],
        trade_date: str,
    ) -> tuple[ScenarioExecutionPlan, str]:
        current = _number(quote.get("current"))
        if current is None:
            raise ValueError("缺少有效实时价")
        frozen = _latest(
            self.store.collection(PLAN_COLLECTION),
            {
                "ticker": item.ticker,
                "trade_date": trade_date,
                "playbook_id": self.playbook.playbook_id,
                "playbook_version": self.playbook.version,
            },
            "frozen_at",
        )
        if frozen and isinstance(frozen.get("plan"), dict):
            try:
                return _scenario_plan_from_payload(frozen["plan"]), str(frozen["plan_id"])
            except (KeyError, TypeError, ValueError):
                # 旧数据不完整时安全重建，新计划会以首次有效值冻结。
                pass
        levels = build_frozen_key_levels(daily, reference_price=current)
        support = levels.support_level if levels.support_level and levels.support_level < current else current - levels.atr14
        pressure = levels.pressure_level if levels.pressure_level and levels.pressure_level > current else current + 2.2 * levels.atr14
        support = max(0.01, float(support))
        pressure = max(current + 0.01, float(pressure))
        positioned = bool(holding)
        quantity = int((holding or {}).get("quantity") or 0)
        available = (
            int((holding or {}).get("available_quantity"))
            if (holding or {}).get("available_quantity") is not None else quantity
        )
        suggested_holding_stop = max(
            value for value in (
                levels.frozen_ma8 if levels.frozen_ma8 < current else 0.0,
                support - levels.atr14,
            ) if value > 0
        )
        confirmed_stop = _number((holding or {}).get("stop_loss_1"))
        hard_stop = confirmed_stop if positioned and confirmed_stop else (
            suggested_holding_stop if positioned else support - 0.15 * levels.atr14
        )
        hard_stop = max(0.01, min(float(hard_stop), current - 0.01))
        target = _number((holding or {}).get("target_price")) if positioned else None
        first_target = target if target and target > hard_stop else pressure
        zone_low = max(hard_stop + 0.01, support - 0.25 * levels.atr14)
        zone_high = min(first_target - 0.01, max(zone_low, support + 0.25 * levels.atr14))
        if zone_high < zone_low:
            zone_high = zone_low
        if positioned:
            max_price = max(current, pressure)
        else:
            ceiling = build_entry_price_ceiling(
                target_1=first_target,
                hard_stop=hard_stop,
                trigger_price=max(zone_low, min(current, first_target - 0.01)),
                entry_zone=(zone_low, zone_high),
                atr_pct14=levels.atr_pct14,
            )
            max_price = ceiling.max_entry_price
        plan = ScenarioExecutionPlan(
            ticker=item.ticker,
            position_state=PositionState.POSITIONED if positioned else PositionState.FLAT,
            previous_close=_number(quote.get("prev_close") or quote.get("pre_close")) or levels.prev_close,
            open_price=_number(quote.get("open") or quote.get("today_open")) or current,
            atr=levels.atr14,
            support=round(support, 4),
            ma5=levels.frozen_ma5,
            ma8=levels.frozen_ma8,
            pressure=round(pressure, 4),
            first_target=round(first_target, 4),
            soft_risk=round(support, 4),
            hard_stop=round(hard_stop, 4),
            max_acceptable_price=round(max_price, 4),
            entry_zone=(round(zone_low, 4), round(zone_high, 4)),
            current_quantity=quantity,
            sellable_quantity=available,
        )
        plan_id = _digest({
            "ticker": item.ticker,
            "trade_date": trade_date,
            "playbook": self.playbook.playbook_id,
            "version": self.playbook.version,
            "levels_as_of": levels.as_of,
        })
        return plan, plan_id

    def _build_facts(
        self,
        *,
        item: Any,
        plan: ScenarioExecutionPlan,
        holding: dict[str, Any] | None,
        quote: dict[str, Any],
        quotes: dict[str, dict[str, Any]],
        minute_docs: dict[str, dict[str, Any]],
        sector_facts: dict[str, dict[str, Any]],
        price_limit: dict[str, Any],
        daily_by_ticker: dict[str, list[dict[str, Any]]],
        market_permission: MarketPermission,
        market_source_at: datetime | None,
        computed_at: datetime,
        trade_date: str,
        pool: Any,
    ) -> ScenarioMarketFacts:
        raw_bars = list((minute_docs.get(item.ticker) or {}).get("bars") or [])
        bars = _closed_bars(raw_bars, computed_at)
        if not bars:
            raise ValueError("缺少当日已闭合 5 分钟 K")
        current = _number(quote.get("current"))
        if current is None:
            raise ValueError("缺少有效实时价")
        sector_candidates = [sector_facts.get(name) for name in item.sectors if sector_facts.get(name)]
        sector_fact = next((fact for fact in sector_candidates if fact.get("state") == "weak_resonance"), None)
        sector_fact = sector_fact or next((fact for fact in sector_candidates if fact.get("state") == "recovery_resonance"), None)
        sector_fact = sector_fact or (sector_candidates[0] if sector_candidates else None)
        try:
            sector_state = SectorExecutionState(str((sector_fact or {}).get("state") or "unavailable"))
        except ValueError:
            sector_state = SectorExecutionState.UNAVAILABLE
        quote_at = _aware(quote.get("quote_at") or quote.get("fetched_at"))
        quote_age = (computed_at - quote_at).total_seconds() if quote_at else 999999.0
        if quote_age < 0:
            quote_age = 999999.0
        latest_bar_age = max(0.0, (computed_at - bars[-1].closed_at).total_seconds())
        index_quote_times = {
            ticker: _aware(
                (quotes.get(ticker) or {}).get("quote_at")
                or (quotes.get(ticker) or {}).get("fetched_at")
            )
            for ticker in BENCHMARKS
        }
        index_freshness = {
            ticker: bool(
                stamp and 0 <= (computed_at - stamp).total_seconds() <= 45
            )
            for ticker, stamp in index_quote_times.items()
        }
        sector_at = _aware((sector_fact or {}).get("computed_at"))
        price_limit_at = _aware(
            price_limit.get("computed_at") or price_limit.get("fetched_at")
        )
        # 保留每个来源槽位，即使多个来源同秒到达也不去重；
        # 物化任务需要用数量区分“来源齐全”与“只有一个时间”。
        source_timestamps = tuple(
            value for value in (
                quote_at,
                bars[-1].closed_at,
                index_quote_times.get(SSE_TICKER),
                index_quote_times.get(CHINEXT_TICKER),
                sector_at,
                market_source_at,
                price_limit_at,
            )
            if value is not None
        )
        freshness_checks = {
            "stock_quote": quote_age <= 45,
            "stock_5m": latest_bar_age <= 7 * 60,
            "index_sse_quote": index_freshness.get(SSE_TICKER, False),
            "index_chinext_quote": index_freshness.get(CHINEXT_TICKER, False),
            "sector_fact": bool(
                sector_at
                and 0 <= (computed_at - sector_at).total_seconds() <= 2 * 60
            ),
            "sector_coverage": sector_state is not SectorExecutionState.UNAVAILABLE,
            "source_time_order": all(value <= computed_at for value in source_timestamps),
        }
        failed_sources = tuple(
            source for source, passed in freshness_checks.items() if not passed
        )
        required_fresh = not failed_sources
        benchmark_return = _quote_return(quotes.get(matching_benchmark(item.ticker)) or {})
        stock_return = _quote_return(quote)
        e7 = self._build_e7(
            item=item,
            holding=holding,
            bars=bars,
            minute_docs=minute_docs,
            daily_by_ticker=daily_by_ticker,
            sector_fact=sector_fact,
            pool=pool,
            computed_at=computed_at,
            trade_date=trade_date,
            price_limit=price_limit,
        )
        return ScenarioMarketFacts(
            computed_at=computed_at,
            current_price=current,
            day_low=min(item.low for item in bars),
            vwap=_vwap(raw_bars, bars),
            closed_bars=bars,
            volume_ratio=bars[-1].volume_ratio,
            relative_strength=(stock_return - benchmark_return) if stock_return is not None and benchmark_return is not None else None,
            market_permission=market_permission,
            intraday_market_state=_intraday_state(quotes),
            sector_state=sector_state,
            required_data_fresh=required_fresh,
            freshness_failed_sources=failed_sources,
            quote_age_seconds=round(quote_age, 3),
            price_limit_locked=bool(price_limit.get("down_limit_sealed")),
            suspended=bool(quote.get("suspended")),
            e7=e7,
            source_timestamps=source_timestamps,
        )

    def _build_e7(
        self,
        *,
        item: Any,
        holding: dict[str, Any] | None,
        bars: tuple[ClosedFiveMinuteBar, ...],
        minute_docs: dict[str, dict[str, Any]],
        daily_by_ticker: dict[str, list[dict[str, Any]]],
        sector_fact: dict[str, Any] | None,
        pool: Any,
        computed_at: datetime,
        trade_date: str,
        price_limit: dict[str, Any],
    ) -> E7ThreeDayFacts | None:
        stock_daily = daily_by_ticker.get(item.ticker) or []
        market_daily = daily_by_ticker.get(SSE_TICKER) or []
        if len(stock_daily) < 21 or len(market_daily) < 21:
            return None
        holding_scenario = str(
            (holding or {}).get("entry_signal")
            or (holding or {}).get("entry_scenario")
            or (holding or {}).get("scenario_id")
            or ""
        ).upper()
        holding_entry_date = _date_text((holding or {}).get("entry_date"))
        if holding and holding_scenario.startswith("E7") and holding_entry_date:
            stabilization_date = date.fromisoformat(holding_entry_date)
            previous_days = [
                date.fromisoformat(_date_text(row.get("date")))
                for row in stock_daily
                if _date_text(row.get("date")) and date.fromisoformat(_date_text(row.get("date"))) < stabilization_date
            ]
            if previous_days and stabilization_date < computed_at.date():
                return E7ThreeDayFacts(
                    phase=E7Phase.NEXT_DAY_EXIT,
                    t_minus_2_date=previous_days[-1],
                    t_minus_1_date=stabilization_date,
                    exit_date=computed_at.date(),
                    limit_down_locked=bool(price_limit.get("down_limit_sealed")),
                    stock_bars_after_1400=tuple(item for item in bars if item.closed_at.hour >= 14),
                )
        t_minus_2 = date.fromisoformat(_date_text(stock_daily[-1].get("date")))
        t_minus_1 = computed_at.date()
        exit_date = _trade_day_after(self.store, t_minus_1)
        if not t_minus_2 < t_minus_1 < exit_date:
            return None
        sector_name = str((sector_fact or {}).get("sector_name") or "")
        sample_tickers = [row.get("ticker") for row in (pool.sector_samples.get(sector_name) or []) if row.get("ticker")]
        sector_strengths = [
            _daily_return_strength(daily_by_ticker.get(ticker) or []) for ticker in sample_tickers
            if len(daily_by_ticker.get(ticker) or []) >= 16
        ]
        sector_strength = median(sector_strengths) if sector_strengths else 0.0
        snapshots = _documents(self.store.collection(SECTOR_SNAPSHOT_COLLECTION), {
            "trade_date": trade_date,
            "sector_name": sector_name,
        })
        snapshots.sort(key=lambda snapshot: str(snapshot.get("computed_at") or ""))
        history = tuple(
            float(value) for snapshot in snapshots
            if (value := snapshot.get("up_ratio")) is not None
        )[-6:]
        new_low_checks: list[bool] = []
        for ticker in sample_tickers:
            daily = daily_by_ticker.get(ticker) or []
            sample_bars = _closed_bars((minute_docs.get(ticker) or {}).get("bars") or [], computed_at)
            previous_low = _number((daily[-1] if daily else {}).get("low"))
            if previous_low and sample_bars:
                new_low_checks.append(min(item.low for item in sample_bars) < previous_low)
        new_low_ratio = (
            sum(new_low_checks) / len(new_low_checks)
            if len(new_low_checks) >= 8 and len(new_low_checks) / max(1, len(sample_tickers)) >= 0.6
            else 1.0
        )
        sse_bars = _closed_bars((minute_docs.get(SSE_TICKER) or {}).get("bars") or [], computed_at)
        growth_bars = _closed_bars((minute_docs.get(CHINEXT_TICKER) or {}).get("bars") or [], computed_at)
        sse_stable, sse_accelerating = _index_stability(sse_bars)
        growth_stable, growth_accelerating = _index_stability(growth_bars)
        after_1400 = tuple(item for item in bars if item.closed_at.time().replace(tzinfo=None).hour >= 14)
        another_e7 = any(
            str(
                holding_doc.get("entry_signal")
                or holding_doc.get("entry_scenario")
                or holding_doc.get("scenario_id")
                or ""
            ).upper().startswith("E7")
            and str(holding_doc.get("ticker") or "").upper() != item.ticker
            for holding_doc in _documents(self.store.collection("holdings"))
            if holding_doc.get("status") in {"active", "reduced"}
        )
        return E7ThreeDayFacts(
            phase=E7Phase.STABILIZATION_ENTRY,
            t_minus_2_date=t_minus_2,
            t_minus_1_date=t_minus_1,
            exit_date=exit_date,
            market_oversold_strength=_daily_return_strength(market_daily),
            sector_oversold_strength=round(float(sector_strength), 6),
            stock_oversold_strength=_daily_return_strength(stock_daily),
            t_minus_2_low=float(_number(stock_daily[-1].get("low")) or 0.0),
            t_minus_1_low=min(item.low for item in bars),
            sector_up_ratio_history=history,
            sector_new_low_ratio=round(new_low_ratio, 6),
            stock_bars_after_1400=after_1400,
            sse_three_bars_no_new_low=sse_stable,
            chinext_three_bars_no_new_low=growth_stable,
            sse_accelerating_breakdown=sse_accelerating,
            chinext_accelerating_breakdown=growth_accelerating,
            limit_down_locked=bool(price_limit.get("down_limit_sealed")),
            suspended=False,
            another_e7_position_open=another_e7,
        )

    def _persist_plan(self, item: Any, plan: ScenarioExecutionPlan, plan_id: str, trade_date: str, computed_at: datetime) -> None:
        document = {
            "plan_id": plan_id,
            "ticker": item.ticker,
            "trade_date": trade_date,
            "sector_name": item.sectors[0] if item.sectors else None,
            "sources": list(item.sources),
            "roles": list(item.roles),
            "playbook_id": self.playbook.playbook_id,
            "playbook_version": self.playbook.version,
            "plan": _jsonable(plan),
            "frozen_at": computed_at.isoformat(timespec="seconds"),
            "updated_at": computed_at.isoformat(timespec="seconds"),
        }
        self.store.collection(PLAN_COLLECTION).update_one(
            {"ticker": item.ticker, "trade_date": trade_date, "playbook_id": self.playbook.playbook_id, "playbook_version": self.playbook.version},
            {
                "$setOnInsert": document,
                "$set": {"last_observed_at": computed_at.isoformat(timespec="seconds")},
            },
            upsert=True,
        )

    def _persist_facts(self, item: Any, facts: ScenarioMarketFacts, plan_id: str, trade_date: str, computed_at: datetime) -> None:
        fact_id = _digest({"ticker": item.ticker, "computed_at": computed_at.isoformat(timespec="seconds"), "plan_id": plan_id})
        document = {
            "fact_id": fact_id,
            "ticker": item.ticker,
            "trade_date": trade_date,
            "plan_id": plan_id,
            "playbook_id": self.playbook.playbook_id,
            "playbook_version": self.playbook.version,
            "facts": _jsonable(facts),
            "required_data_fresh": facts.required_data_fresh,
            "freshness_failed_sources": list(facts.freshness_failed_sources),
            "computed_at": computed_at.isoformat(timespec="seconds"),
        }
        self.store.collection(FACT_COLLECTION).update_one({"fact_id": fact_id}, {"$set": document}, upsert=True)

    def _persist_e7(self, ticker: str, name: str | None, decision: ScenarioDecision, facts: ScenarioMarketFacts, trade_date: str, computed_at: datetime) -> None:
        scenario = decision.scenario("E7")
        failed = [trace.reason for trace in scenario.traces if str(trace.state.value) != "satisfied"]
        status = (
            "triggered" if scenario.state is ScenarioState.TRIGGERED
            else "data_blocked" if facts.e7 is None or not facts.required_data_fresh
            else "not_triggered"
        )
        reason = failed[0] if failed else "E7 三日事实全部满足"
        next_condition = failed[1] if len(failed) > 1 else "等待下一组闭合事实"
        if facts.e7 is None:
            reason = "缺少严格有序的 E7 T-2/T-1/T 三日事实"
            next_condition = "补齐个股、大盘与板块的已完成日线后重算"
        elif not facts.required_data_fresh:
            failed_text = "、".join(facts.freshness_failed_sources) or "必要事实"
            reason = f"本轮 {failed_text} 未通过，不能据此判定 E7 未触发"
            next_condition = f"补齐 {failed_text} 后重新评估"
        document = {
            "ticker": ticker,
            "name": name,
            "trade_date": trade_date,
            "status": status,
            "scenario_state": scenario.state.value,
            "reason": reason,
            "next_condition": next_condition,
            "traces": _jsonable(scenario.traces),
            "freshness_failed_sources": list(facts.freshness_failed_sources),
            "computed_at": computed_at.isoformat(timespec="seconds"),
        }
        self.store.collection(E7_EVALUATION_COLLECTION).update_one(
            {"trade_date": trade_date, "ticker": ticker}, {"$set": document}, upsert=True,
        )

    def _persist_blocked_e7(self, *, ticker: str, name: str | None, trade_date: str, computed_at: datetime, reason: str) -> None:
        document = {
            "ticker": ticker,
            "name": name,
            "trade_date": trade_date,
            "status": "data_blocked",
            "scenario_state": "far",
            "reason": reason,
            "next_condition": "补齐有效报价、已闭合 5 分钟 K 与至少 21 根已完成日线后重算",
            "traces": [],
            "computed_at": computed_at.isoformat(timespec="seconds"),
        }
        self.store.collection(E7_EVALUATION_COLLECTION).update_one(
            {"trade_date": trade_date, "ticker": ticker}, {"$set": document}, upsert=True,
        )

    def _persist_decision(self, *, ticker: str, name: str | None, plan: ScenarioExecutionPlan, decision: ScenarioDecision, plan_id: str) -> bool:
        primary = decision.primary_scenario
        current = {
            "canonical_action_type": decision.action.action_type,
            "action_label": decision.action.label,
            "position_state": plan.position_state.value,
            "execution_state": decision.execution_state.value,
            "scenario_id": primary.scenario_id if primary else None,
            "scenario_title": primary.title if primary else None,
            "scenario_state": primary.state.value if primary else None,
        }
        fingerprint = _digest({
            "current": current,
            "target_quantity": decision.action.target_quantity,
            "change_quantity": decision.action.change_quantity,
            "deadline": decision.deadline,
        })
        # trading_decision_events 同时承载基础时间线与全场景执行事件。若只按
        # ticker 取最后一条，基础时间线每分钟写入的事件会遮住上一条场景
        # 决策，导致相同 N/H/R 结论被再次落库。场景去重必须限定到稳定的
        # plan_id 命名空间。
        previous = _latest(
            self.store.collection(DECISION_COLLECTION),
            {"ticker": ticker, "plan_id": plan_id},
            "computed_at",
        )
        if previous and previous.get("fingerprint") == fingerprint:
            return False
        unsatisfied = [
            trace.reason for trace in (primary.traces if primary else ())
            if trace.state.value != "satisfied"
        ]
        action_type = ActionType(decision.action.action_type)
        headline = decision.action.label
        if decision.action.change_quantity:
            headline = f"{headline} {abs(int(decision.action.change_quantity))} 股"
        presentation = {
            "headline": headline,
            "reason": decision.reasons[0] if decision.reasons else "全场景规则已完成本轮评估",
            "next_condition": unsatisfied[0] if unsatisfied else "等待下一根闭合 5 分钟 K",
            "target_quantity": decision.action.target_quantity,
            "change_quantity": decision.action.change_quantity,
            "data_as_of": decision.computed_at.isoformat(timespec="seconds"),
            "urgency": ACTION_DEFINITIONS[action_type].urgency,
            "executable": ACTION_DEFINITIONS[action_type].executable,
        }
        event_id = _digest({"ticker": ticker, "computed_at": decision.computed_at, "fingerprint": fingerprint})
        document = {
            "event_id": event_id,
            "ticker": ticker,
            "name": name,
            "plan_id": plan_id,
            "computed_at": decision.computed_at.isoformat(timespec="seconds"),
            "fingerprint": fingerprint,
            "previous": (previous or {}).get("current"),
            "current": current,
            "reasons": list(decision.reasons),
            "decision": {
                "position_state": plan.position_state.value,
                "execution_state": decision.execution_state.value,
                "action": _jsonable(decision.action),
                "current_scenario": _jsonable(primary),
                "switch_conditions": unsatisfied[:4],
                "presentation": presentation,
                "candidate_scenarios": _jsonable(decision.candidate_scenarios),
                "suppressed_scenarios": _jsonable(decision.suppressed_scenarios),
                "sizing": _jsonable(decision.sizing),
            },
        }
        self.store.collection(DECISION_COLLECTION).update_one({"event_id": event_id}, {"$set": document}, upsert=True)
        return True
