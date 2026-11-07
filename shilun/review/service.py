"""Materialize one facts-first daily review snapshot from MongoDB.

The API reads the materialized document.  Expensive full-market scans happen in
the post-close job, not when the browser opens, which keeps the cloud web process
small and predictable.
"""

from __future__ import annotations

from collections import Counter, defaultdict
import re
from datetime import datetime
from statistics import mean, median
from typing import Any, Iterable
from zoneinfo import ZoneInfo

from shilun.review.contracts import ExpectationState, MarketPosture
from shilun.review.engine import (
    assign_exclusive_role,
    build_expectation_band,
    build_ladder,
    build_position_chart,
    classify_board_nature,
    classify_board_type,
    classify_expectation,
    classify_market_posture,
    classify_sector_cycle,
    detect_520,
    detect_red_k_net_inflow,
    detect_turnover_new_faces,
    detect_weekly_breakout,
    evaluate_tape_strength,
)
from shilun.review.dated_pools import COLLECTION as PREVIOUS_POOL_COLLECTION, sync_dated_evidence, sync_review_history, sync_heat_summaries, sync_review_board_history
from shilun.review.emotion_history import five_day_heat, load_heat_summaries
from shilun.review.theme_evidence import price_limit_hit
from shilun.review.topic_perspectives import load_perspectives, VERSION as PERSPECTIVE_VERSION
from shilun.review.theme_evidence import front_support as _front_support, theme_evidence as _theme_evidence, downside_risk as _downside_risk, limit_up_resonance as _limit_up_resonance
from shilun.review.quality import assess_review_data_quality
from shilun.review.sources import at_limit, load_futures_history, load_review_sessions, reconstruct_ladder
from shilun.review.index_liquidity import breadth_distribution, market_rows, volume_facts, THRESHOLD_RULE


REVIEW_COLLECTION = "daily_review_snapshots"
REVIEW_VERSION = "daily_review_v1.9_eastmoney_taxonomy"
REVIEW_SOCKET_TIMEOUT_MS = 60_000
REVIEW_BATCH_SIZE = 1000
MARKET_TIMEZONE = ZoneInfo("Asia/Shanghai")
CORE_INDICES = (
    ("000001.SH", "上证指数"),
    ("000300.SH", "沪深300"),
    ("399001.SZ", "深证成指"),
    ("399006.SZ", "创业板指"),
)
BOARD_TYPE_LABELS = {
    "one_word": "一字板",
    "shrinking_volume": "缩量板",
    "explosive_volume": "爆量板",
    "broken_messy": "烂板",
    "normal_board": "普通板",
}


def _number(value: Any, default: float | None = None) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _date_text(value: Any) -> str:
    text = str(value or "").strip()
    digits = "".join(character for character in text if character.isdigit())
    if len(digits) >= 8:
        return f"{digits[:4]}-{digits[4:6]}-{digits[6:8]}"
    return text[:10]


def _compact_date(value: Any) -> str:
    return _date_text(value).replace("-", "")


def _days_between(later: Any, earlier: Any) -> int | None:
    try:
        return (datetime.fromisoformat(_date_text(later)) - datetime.fromisoformat(_date_text(earlier))).days
    except (TypeError, ValueError):
        return None


def _ticker(value: Any) -> str:
    text = str(value or "").strip().upper()
    if not text:
        return ""
    if "." in text:
        return text
    if len(text) != 6 or not text.isdigit():
        return text
    if text.startswith(("4", "8", "9")):
        return f"{text}.BJ"
    if text.startswith(("5", "6")):
        return f"{text}.SH"
    return f"{text}.SZ"


def _amount_yi(value: Any) -> float:
    # Tushare daily.amount uses thousand CNY; 1 yi CNY = 100,000 thousand CNY.
    return round((_number(value, 0.0) or 0.0) / 100_000.0, 4)


def _market_cap_yi(value: Any) -> float:
    # Tushare daily_basic.total_mv uses 10,000 CNY; 1 yi CNY = 10,000 * 10,000 CNY.
    return round((_number(value, 0.0) or 0.0) / 10_000.0, 4)


def _moneyflow_yi(value: Any) -> float:
    # Tushare moneyflow amounts use 10,000 CNY.
    return round((_number(value, 0.0) or 0.0) / 10_000.0, 4)


def _pct(current: Any, previous: Any) -> float | None:
    current_value = _number(current)
    previous_value = _number(previous)
    if current_value is None or previous_value in (None, 0):
        return None
    return current_value / previous_value - 1.0


def _close_position(bar: dict[str, Any]) -> float | None:
    high = _number(bar.get("high"))
    low = _number(bar.get("low"))
    close = _number(bar.get("close"))
    if None in (high, low, close) or high <= low:
        return None
    return (close - low) / (high - low)


def _upper_shadow_ratio(bar: dict[str, Any]) -> float | None:
    high = _number(bar.get("high"))
    low = _number(bar.get("low"))
    close = _number(bar.get("close"))
    open_price = _number(bar.get("open"))
    if None in (high, low, close, open_price) or high <= low:
        return None
    return (high - max(close, open_price)) / (high - low)


def _solid_red_k(bar: dict[str, Any]) -> bool:
    high = _number(bar.get("high"))
    low = _number(bar.get("low"))
    close = _number(bar.get("close"))
    open_price = _number(bar.get("open"))
    if None in (high, low, close, open_price) or high <= low or close <= open_price:
        return False
    return (close - open_price) / (high - low) >= 0.45 and (close - low) / (high - low) >= 0.65


from shilun.review.market_mood import _market_mood_state
from shilun.review.suspensions import load_suspensions
from shilun.review.event_theme_taxonomy import VERSION as EVENT_SCHEMA, topic_code
from shilun.review.vibe_shortline import clean_reason, tags_of


def _event_theme_location(analysis_date):
    return {'status': 'event_theme_views', 'date': analysis_date, 'topic_schema': EVENT_SCHEMA,
            'themes': [], 'active_themes': [],
            'data_note': '题材事件树、轮动及当日证据统一读取同日涨停原因事件标签；旧DC题材快照保留为历史归档。'}


def _event_topics(rows, reason_doc, day):
    reasons = reason_doc.get('reasons', {}) if reason_doc.get('date') == day else {}
    for row in rows:
        labels = list(dict.fromkeys(tags_of(clean_reason(reasons.get(str(row.get('ticker', ''))[:6], '')))))
        row.update(topics=labels, topic_ids=[topic_code(label) for label in labels],
                   topic_date=day, topic_source='同日涨停原因事件标签（Vibe-Astock）', topic_schema=EVENT_SCHEMA)


def _label_values(value: Any) -> set[str]:
    if value is None:
        return set()
    values = value if isinstance(value, (list, tuple, set)) else str(value).replace("，", ",").replace("、", ",").replace("/", ",").split(",")
    return {str(item).strip() for item in values if str(item).strip()}


from shilun.review.theme_scope import topic_name as _theme_name

def _theme_labels(row: dict[str, Any]) -> tuple[set[str], set[str]]:
    """Return (Eastmoney concept attribution, limit-pool topic/industry labels)."""
    eastmoney = set()
    for key in ("eastmoney_concepts", "eastmoney_concept", "em_concepts", "em_concept"):
        eastmoney.update(_label_values(row.get(key)))
    pool = set()
    for key in ("theme_name", "concept_name", "topic", "theme", "industry", "sector_name"):
        pool.update(_label_values(row.get(key)))
    return eastmoney, pool


def _median_value(values: list[float]) -> float | None:
    return round(float(median(values)), 6) if values else None


def _aggregate_expectation_states(items: Iterable[dict[str, Any]]) -> dict[str, Any]:
    states = [str(item.get("state") or "") for item in items if item.get("state")]
    counts = Counter(states)
    if counts[ExpectationState.ABOVE.value] >= 2 and not counts[ExpectationState.BELOW.value]:
        state = ExpectationState.ABOVE
        action = "超预期拿，直到预期一致或逻辑反转"
    elif counts[ExpectationState.BELOW.value] >= 2:
        state = ExpectationState.BELOW
        action = "低于预期走，取消继续扩散的假设"
    elif states and not counts[ExpectationState.INSUFFICIENT.value]:
        state = ExpectationState.IN_LINE
        action = "符合历史标签区间，按原计划管理"
    else:
        state = ExpectationState.INSUFFICIENT
        action = "样本不足，只记录事实"
    labels = {
        ExpectationState.ABOVE: "超预期",
        ExpectationState.BELOW: "低于预期",
        ExpectationState.IN_LINE: "符合预期",
        ExpectationState.INSUFFICIENT: "样本不足",
    }
    return {"state": state.value, "label": labels[state], "action": action, "checks": list(items)}


class DailyReviewService:
    def __init__(self, store: Any) -> None:
        self.store = store

    def latest_date(self, requested_date: str | None = None) -> str:
        query: dict[str, Any] = {}
        if requested_date:
            query["trade_date"] = {"$lte": _compact_date(requested_date)}
        document = self.store.collection("daily_basic").find_one(
            query,
            {"_id": 0, "trade_date": 1},
            sort=[("trade_date", -1)],
        )
        if document:
            return _date_text(document.get("trade_date"))
        fallback_query: dict[str, Any] = {}
        if requested_date:
            fallback_query["date"] = {"$lte": _date_text(requested_date)}
        fallback = self.store.collection("market_daily_bars").find_one(
            fallback_query,
            {"_id": 0, "date": 1},
            sort=[("date", -1)],
        )
        if not fallback:
            raise RuntimeError("market_daily_bars 没有可用于复盘的收盘数据")
        return _date_text(fallback.get("date"))

    def get(self, requested_date: str | None = None, *, allow_build: bool = False) -> dict[str, Any]:
        analysis_date = self.latest_date(requested_date)
        cached = self.store.collection(REVIEW_COLLECTION).find_one(
            {"analysis_date": analysis_date, "engine_version": REVIEW_VERSION},
            {"_id": 0},
        )
        if cached:
            # Keep the snapshot's audited facts but refresh the cache-only CFFEX
            # window on every read.  This lets the UI's incremental gap sync
            # immediately appear without rebuilding the whole review snapshot.
            payload = dict(cached)
            dates = load_review_sessions(self.store, analysis_date)
            payload["market"] = {**payload.get("market", {}),
                                 "futures": load_futures_history(self.store, analysis_date, dates)}
            sentiment = payload.get("sentiment", {})
            from shilun.review.market_mood import load_market_moods
            shared_mood = load_market_moods(self.store, [analysis_date]).get(analysis_date)
            if shared_mood:
                sentiment = {**sentiment, 'market_mood': shared_mood}
                payload['sentiment'] = sentiment
            payload['sentiment'] = {**sentiment, 'theme_evidence': _event_theme_location(analysis_date)}
            from shilun.review.event_downside import attach_event_groups
            payload['sentiment']['downside_risk'] = attach_event_groups(
                self.store, sentiment.get('downside_risk', {}), analysis_date, dates)
            # Transform only the returned view; the immutable old snapshot and
            # its historical DC evidence remain untouched in the archive.
            from copy import deepcopy
            payload['expectations'] = deepcopy(payload.get('expectations', {}))
            rows = payload['expectations'].get('observations', [])
            previous = dates[-2] if len(dates) >= 2 else None
            if previous:
                from shilun.review.board_data import REASON_COLLECTION
                reason_doc = self.store.collection(REASON_COLLECTION).find_one({'date': previous}, {'_id': 0}) or {}
                _event_topics(rows, reason_doc, previous)
            from shilun.review.status_refresh import refresh_taxonomy_status
            from shilun.jobs.pipeline_state import TaskLedger
            payload['pipeline_status'] = {'analysis_date': analysis_date,
                                          'tasks': TaskLedger(self.store).snapshot(analysis_date)}
            return refresh_taxonomy_status(self.store, payload)
        if not allow_build:
            return {
                "status": "not_materialized",
                "analysis_date": analysis_date,
                "engine_version": REVIEW_VERSION,
                "message": "该交易日尚未生成复盘快照，请点击“生成所选日复盘”。",
            }
        return self.build_and_save(analysis_date)

    def build_and_save(self, requested_date: str | None = None, *, sync_sources: bool = False) -> dict[str, Any]:
        analysis_date = self.latest_date(requested_date)
        source_sync = []
        if sync_sources:
            source_sync = sync_review_history(self.store, analysis_date)
            sessions = load_review_sessions(self.store, analysis_date)
            from shilun.review.board_data import BoardResearchData
            source_sync.extend(sync_review_board_history(self.store, BoardResearchData(self.store).sessions(analysis_date, 30)))
            from shilun.review.board_data import sync_reasons
            from shilun.review.board_auction import sync_first_board_auction
            # Expectations use yesterday's labels to evaluate today's return.
            for reason_date in sessions[-2:]:
                source_sync.append(sync_reasons(self.store, reason_date))
                source_sync.append(sync_first_board_auction(self.store, reason_date))
            source_sync.extend(sync_dated_evidence(self.store, sessions))
            source_sync.extend(sync_heat_summaries(self.store, sessions))
            from shilun.review.suspensions import sync_suspensions
            try:
                sync_suspensions(self.store, sessions[max(0, len(sessions)-60)], analysis_date)
                source_sync.append({'date': analysis_date, 'dataset': 'suspensions', 'status': 'ok'})
            except Exception as error:
                source_sync.append({'date': analysis_date, 'dataset': 'suspensions', 'status': 'unavailable', 'error_type': type(error).__name__})
            from shilun.review.board_price_quality import sync_board_price_gaps
            try:
                source_sync.append(sync_board_price_gaps(self.store, analysis_date))
            except Exception as error:
                source_sync.append({'date': analysis_date, 'dataset': 'board_cohort_prices',
                                    'status': 'unavailable', 'error_type': type(error).__name__})
        payload = self.build(analysis_date)
        payload["source_sync"] = source_sync
        for item in source_sync:
            if item["status"] == "unavailable":
                payload["freshness"]["alerts"].append(f"{item['date']} {item.get('dataset', '涨停池')} 上游补数不可用；采用已核验定稿样本或明确保留缺口。")
        from shilun.review.topic_details import split_topic_details, COLLECTION as TOPIC_DETAILS
        split_topic_details(self.store, payload)
        self.store.collection(REVIEW_COLLECTION).update_one(
            {"analysis_date": analysis_date, "engine_version": REVIEW_VERSION},
            {"$set": payload},
            upsert=True,
        )
        # Keep recent generations for in-flight readers and concurrent rebuilds.
        from datetime import timedelta, timezone
        self.store.collection(TOPIC_DETAILS).delete_many({'analysis_date': analysis_date,
            'created_at': {'$lt': datetime.now(timezone.utc) - timedelta(days=1)}})
        from shilun.review.market_mood import MOOD_COLLECTION
        self.store.collection(MOOD_COLLECTION).replace_one({'date': analysis_date},
            {'date': analysis_date, 'mood': payload['sentiment']['market_mood'], 'engine_version': REVIEW_VERSION}, upsert=True)
        return payload

    def build(self, analysis_date: str) -> dict[str, Any]:
        available_dates = self._available_stock_dates(analysis_date)
        dates = available_dates[-121:]
        if not dates:
            raise RuntimeError(f"{analysis_date} 没有日线数据")
        analysis_date = dates[-1]
        sessions = load_review_sessions(self.store, analysis_date)
        previous_date = sessions[-2] if len(sessions) >= 2 else ""
        premium_dates = sorted(set(dates + sessions[-22:]))
        basic = self._stock_basic(analysis_date, industry_only=True)
        stock_tickers = set(basic)
        bars_by_ticker, bars_by_date = self._load_bars(dates, stock_tickers)
        current_bars = bars_by_date.get(analysis_date, {})
        previous_bars = bars_by_date.get(previous_date, {})
        if not current_bars:
            raise RuntimeError(f"{analysis_date} 没有个股日线数据")

        daily_basic = self._daily_basic(analysis_date)
        moneyflow = self._moneyflow(analysis_date)
        moneyflow_by_date = self._moneyflow_history(dates[-7:])
        limit_rows_by_date, limit_source_date = self._limit_rows(premium_dates, basic)
        current_limits = limit_rows_by_date.get(analysis_date, [])
        previous_limits = limit_rows_by_date.get(previous_date, [])
        price_limits = self._price_limits(analysis_date)
        price_limits_by_date = self._price_limits_history(sessions[-21:])
        settled_pools = {}
        for document in self.store.collection(PREVIOUS_POOL_COLLECTION).find(
            {"analysis_date": {"$in": sessions[-22:]}}, {"_id": 0}):
            day, prior = document.get("analysis_date"), document.get("previous_date")
            if day not in sessions or sessions.index(day) == 0 or sessions[sessions.index(day)-1] != prior:
                continue
            rows = document.get("rows") or []
            # A local dated cache must agree with the target day's daily close.
            observed = [r for r in rows if at_limit(bars_by_date.get(day, {}).get(r["ticker"], {}).get("close"), r.get("close")) is True]
            if rows and len(observed) / len(rows) < .8:
                continue
            settled_pools[day] = {**document, "verified_rows": observed}
            if prior not in limit_rows_by_date:
                limit_rows_by_date[prior] = [{**r, "trade_date": prior, "source": document.get("source"),
                    "sector_name": r.get("industry") or basic.get(r["ticker"], {}).get("sector") or "未分类"}
                    for r in rows]
        for state in self.store.collection("sync_state").find(
            {"dataset": {"$in": ["eastmoney_limit_up_pool", "akshare_limit_up_pool"]}, "scope": {"$in": [_compact_date(d) for d in sessions]},
             "status": "empty_confirmed"}, {"_id": 0, "scope": 1}):
            limit_rows_by_date.setdefault(_date_text(state["scope"]), [])
        for rows in limit_rows_by_date.values():
            for row in rows:
                meta = basic.get(row['ticker'], {})
                row.update(sector_name=meta.get('sector') or '分类待补齐', sector_code=meta.get('industry_code'),
                           taxonomy_version=meta.get('taxonomy_version'), membership_date=analysis_date)
        current_limits = limit_rows_by_date.get(analysis_date, [])
        previous_limits = limit_rows_by_date.get(previous_date, [])

        indices = self._indices(dates, analysis_date)
        breadth = self._breadth(current_bars, previous_bars)
        amount_series = self._amount_series(sessions[-61:], bars_by_date)
        market_amount = amount_series[-1]["amount_yi"] if amount_series else 0.0
        previous_amount = amount_series[-2]["amount_yi"] if len(amount_series) >= 2 else None
        ma20_amount = mean(item["amount_yi"] for item in amount_series[-20:]) if amount_series else None
        chart_volume = volume_facts(sessions[-61:], bars_by_date)
        breadth["distribution"] = breadth_distribution(current_bars, previous_bars, price_limits)
        breadth["markets"] = market_rows(current_bars, previous_bars,
            sessions=sessions, bars_by_date=bars_by_date)
        breadth["expansion_rule"] = THRESHOLD_RULE
        posture = classify_market_posture(
            indices,
            market_amount_yi=market_amount,
            previous_amount_yi=previous_amount,
            amount_ma20_yi=ma20_amount,
            up_ratio=breadth.get("up_ratio"),
        )

        sentiment = self._sentiment(
            analysis_date=analysis_date,
            previous_date=previous_date,
            current_bars=current_bars,
            previous_bars=previous_bars,
            current_limits=current_limits,
            previous_limits=previous_limits,
            price_limits=price_limits,
            bars_by_ticker=bars_by_ticker,
            indices=indices,
            basic=basic,
            market_amount_yi=market_amount,
            amount_ma20_yi=ma20_amount,
            up_ratio=breadth.get("up_ratio"),
            limit_rows_by_date=limit_rows_by_date,
            bars_by_date=bars_by_date,
            price_limits_by_date=price_limits_by_date,
            moneyflow=moneyflow,
            moneyflow_by_date=moneyflow_by_date,
            sessions=sessions,
            settled_pool=settled_pools.get(analysis_date),
        )
        ladder = build_ladder(current_limits, sector_by_ticker={ticker: item["sector"] for ticker, item in basic.items()})
        if current_limits:
            ladder.update({"status": "ok", "source": "eastmoney_limit_up_pool", "source_label": "东方财富涨停池",
                           "source_date": analysis_date, "note": "按涨停池的连板数分组。"})
        else:
            empty_pool = self.store.collection("sync_state").find_one(
                {"dataset": {"$in": ["eastmoney_limit_up_pool", "akshare_limit_up_pool"]}, "scope": _compact_date(analysis_date), "status": "empty_confirmed"}, {"_id": 0})
            if empty_pool:
                ladder.update({"status": "empty", "source": "eastmoney_limit_up_pool", "source_label": "东方财富涨停池",
                               "source_date": analysis_date, "note": "接口已核验所选交易日，涨停池为空。"})
            else:
                ladder = self._reconstruct_ladder(dates, bars_by_date, price_limits, basic)
        sectors = self._sectors(
            current_bars=current_bars,
            previous_bars=previous_bars,
            moneyflow=moneyflow,
            daily_basic=daily_basic,
            basic=basic,
            current_limits=current_limits,
            previous_limits=previous_limits,
            current_pool_ready=analysis_date in limit_rows_by_date,
            previous_pool_ready=previous_date in limit_rows_by_date,
        )
        sector_lookup = {item["sector_code"]: item for item in sectors}
        expectations = self._expectations(
            dates=premium_dates,
            analysis_date=analysis_date,
            previous_date=previous_date,
            bars_by_ticker=bars_by_ticker,
            limit_rows_by_date=limit_rows_by_date,
            current_limits=current_limits,
        )
        genealogy = self._leader_genealogy(
            limit_rows_by_date=limit_rows_by_date,
            analysis_date=analysis_date,
            source_date=limit_source_date,
        )

        previous_active_tickers = {
            ticker
            for date in dates[-21:-1]
            for ticker, bar in bars_by_date.get(date, {}).items()
            if _amount_yi(bar.get("amount")) >= 20.0
        }
        turnover_new_faces = detect_turnover_new_faces(
            [
                {
                    **bar,
                    "ticker": ticker,
                    "name": basic.get(ticker, {}).get("name") or ticker,
                    "sector_name": basic.get(ticker, {}).get("sector") or "未分类",
                }
                for ticker, bar in current_bars.items()
            ],
            previous_active_tickers,
        )[:50]

        tactics = self._tactics(
            posture=posture,
            indices=indices,
            current_bars=current_bars,
            previous_bars=previous_bars,
            bars_by_ticker=bars_by_ticker,
            basic=basic,
            daily_basic=daily_basic,
            moneyflow=moneyflow,
            ladder=ladder,
            sectors=sector_lookup,
            current_limits=current_limits,
        )
        futures = load_futures_history(self.store, analysis_date, load_review_sessions(self.store, analysis_date)[-7:])
        action = self._action_summary(posture, expectations, tactics)
        futures_date = futures.get("source_date")
        generated_date = datetime.now(MARKET_TIMEZONE).date().isoformat()
        as_of_lag = _days_between(generated_date, analysis_date)
        limit_lag = _days_between(analysis_date, limit_source_date) if limit_source_date else None
        futures_lag = _days_between(analysis_date, futures_date) if futures_date else None
        freshness_alerts = []
        if as_of_lag is not None and as_of_lag > 3:
            freshness_alerts.append(f"个股日线停留在 {analysis_date}，距当前日期 {as_of_lag} 天。")
        if limit_lag is not None and limit_lag > 0:
            freshness_alerts.append(f"连板增强数据停留在 {limit_source_date}，不用于冒充 {analysis_date} 当日梯队。")
        if futures_lag is not None and futures_lag > 0:
            freshness_alerts.append(f"股指期货机构持仓停留在 {futures_date}。")
        freshness = {
            "daily_bars": analysis_date,
            "limit_up_pool": limit_source_date or None,
            "limit_up_pool_current": limit_source_date == analysis_date,
            "cffex_positions": futures_date,
            "as_of_lag_days": as_of_lag,
            "limit_up_pool_lag_days": limit_lag,
            "cffex_lag_days": futures_lag,
            "alerts": freshness_alerts,
        }
        current_tickers = set(current_bars)
        data_quality = assess_review_data_quality(
            analysis_date=analysis_date,
            generated_date=generated_date,
            stock_master_count=len(basic),
            current_bar_count=len(current_bars),
            previous_bar_count=len(previous_bars),
            daily_basic_count=len(current_tickers.intersection(daily_basic)),
            moneyflow_count=len(current_tickers.intersection(moneyflow)),
            price_limit_count=len(current_tickers.intersection(price_limits)),
            limit_pool_date=limit_source_date or None,
            futures_date=futures_date,
        )
        generated_at = datetime.now(MARKET_TIMEZONE).isoformat(timespec="seconds")
        classification_counts = Counter((basic.get(t) or {}).get('classification_status', 'stock_unmapped') for t in current_tickers)
        mapping_ready = classification_counts.get('ready', 0)
        data_quality.setdefault('checks', []).append({'id': 'unified_taxonomy',
            'status': 'pass' if mapping_ready == len(current_tickers) else 'warn',
            'label': '东方财富统一分类', 'observed': mapping_ready,
            'coverage': mapping_ready / len(current_tickers) if current_tickers else 0,
            'impact': f'唯一三级行业覆盖 {mapping_ready}/{len(current_tickers)}；分类缺口单独保留。'})
        if mapping_ready < len(current_tickers):
            freshness.setdefault('alerts', []).append(f'东方财富行业映射缺口 {len(current_tickers)-mapping_ready} 只；未使用旧来源补齐。')
            if data_quality.get('status') == 'ready':
                data_quality.update(status='degraded', label='部分模块降级')
        payload = {
            "status": "ok",
            "analysis_date": analysis_date,
            "previous_trade_date": previous_date,
            "engine_version": REVIEW_VERSION,
            "taxonomy_version": "eastmoney-taxonomy-v1",
            "taxonomy_coverage": dict(classification_counts),
            "cycle_diagnostics": {
                'date': analysis_date,
                'counts': dict(Counter(r['cycle'].get('diagnostic_status', 'matched') for r in sectors)),
                'unresolved': [{'sector_code': r['sector_code'], 'sector_name': r['sector_name'], **r['cycle']}
                               for r in sectors if r['cycle'].get('diagnostic_status') != 'matched'],
            },
            "generated_at": generated_at,
            "doctrine": {
                "arbitrage": "利用短期价值与市场预期之间的错位，在逻辑成立且价格尚未充分定价时进入，在预期一致时兑现，在逻辑反转时认错。",
                "expectation": "按标签统计过去N日表现形成低/平/高预期，再用情绪周期与盘面两类预期差验证今日行为。",
                "execution": "上升走趋势，弱势走抱团，量能决定进攻市值大小。",
            },
            "action": action,
            "market": {
                "posture": posture,
                "indices": indices,
                "volume": chart_volume,
                "breadth": breadth,
                "futures": futures,
            },
            "sentiment": {**sentiment, "ladder": ladder, "leader_genealogy": genealogy},
            "expectations": expectations,
            "turnover_new_faces": turnover_new_faces,
            "tactics": tactics,
            "freshness": freshness,
            "data_quality": data_quality,

        }
        return payload

    @staticmethod
    def _leader_genealogy(
        *,
        limit_rows_by_date: dict[str, list[dict[str, Any]]],
        analysis_date: str,
        source_date: str,
    ) -> dict[str, Any]:
        histories: dict[str, dict[str, Any]] = {}
        for date in sorted(limit_rows_by_date):
            for row in limit_rows_by_date[date]:
                level = int(_number(row.get("consecutive_count"), 1.0) or 1)
                if level < 2:
                    continue
                ticker = _ticker(row.get("ticker"))
                item = histories.setdefault(
                    ticker,
                    {
                        "ticker": ticker,
                        "name": row.get("name") or ticker,
                        "sector_name": row.get("sector_name") or "未分类",
                        "path": [],
                        "max_board": 0,
                    },
                )
                item["path"].append({"date": date, "board_level": level})
                item["max_board"] = max(int(item["max_board"]), level)
                item["latest_date"] = date
                item["latest_board"] = level
        leaders = sorted(
            histories.values(),
            key=lambda item: (int(item.get("latest_board") or 0), int(item.get("max_board") or 0), str(item.get("latest_date") or "")),
            reverse=True,
        )[:30]
        for item in leaders:
            item["status"] = "current" if item.get("latest_date") == analysis_date else "historical_reference"
        by_sector: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for item in leaders:
            by_sector[str(item.get("sector_name") or "未分类")].append(item)
        return {
            "source_date": source_date or None,
            "current": source_date == analysis_date,
            "leaders": leaders,
            "sector_lineages": [
                {
                    "sector_name": sector,
                    "highest_board": max(int(item.get("max_board") or 0) for item in items),
                    "leaders": items[:6],
                }
                for sector, items in sorted(
                    by_sector.items(),
                    key=lambda pair: max(int(item.get("max_board") or 0) for item in pair[1]),
                    reverse=True,
                )
            ],
            "note": "龙头谱系只沿用有连板增强数据的日期；数据缺口时保留历史参考并明确标注，不外推为当日高标。",
        }

    def _available_stock_dates(self, analysis_date: str) -> list[str]:
        """Return sessions with a real cross-section, excluding index-only dates.

        A partially synced database can contain benchmark bars for dates where
        thousands of stock bars are absent.  Treating those as the previous
        session corrupts breadth, relative strength, and premium calculations.
        """

        pipeline = [
            {"$match": {"date": {"$lte": _date_text(analysis_date)}}},
            {"$group": {"_id": "$date", "count": {"$sum": 1}}},
            {"$match": {"count": {"$gte": 500}}},
            {"$sort": {"_id": -1}},
            {"$limit": 121},
        ]
        rows = self.store.collection("market_daily_bars").aggregate(pipeline, allowDiskUse=True)
        return sorted(_date_text(row.get("_id")) for row in rows if row.get("_id"))

    def _stock_basic(self, analysis_date: str, *, industry_only=False) -> dict[str, dict[str, Any]]:
        result: dict[str, dict[str, Any]] = {}
        cursor = self.store.collection("stock_basic").find(
            {}, {"_id": 0, "ts_code": 1, "ticker": 1, "name": 1, "industry": 1, "market": 1}
        )
        from shilun.market.taxonomy import classify_stocks
        for row in classify_stocks(self.store, analysis_date, list(cursor), industry_only=industry_only):
            ticker = _ticker(row.get("ts_code") or row.get("ticker"))
            if ticker:
                result[ticker] = {
                    **row,
                    "ticker": ticker,
                    "name": str(row.get("name") or ticker),
                    "sector": str(row.get("industry") or "未分类"),
                    "market": str(row.get("market") or ""),
                }
        return result

    def _load_bars(
        self,
        dates: list[str],
        stock_tickers: set[str],
    ) -> tuple[dict[str, list[dict[str, Any]]], dict[str, dict[str, dict[str, Any]]]]:
        by_ticker: dict[str, list[dict[str, Any]]] = defaultdict(list)
        by_date: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
        projection = {"_id": 0, "date": 1, "ticker": 1, "open": 1, "high": 1, "low": 1, "close": 1, "pre_close": 1, "amount": 1, "volume": 1}
        # Keep remote batches small: the default getMore may transfer 16 MiB at
        # once over the ECS tunnel. Sort the retained per-stock history locally
        # so the database can stream from the existing date index immediately.
        cursor = self.store.collection("market_daily_bars").find(
            {"date": {"$in": dates}}, projection, batch_size=REVIEW_BATCH_SIZE,
        )
        for row in cursor:
            ticker = _ticker(row.get("ticker"))
            if ticker not in stock_tickers:
                continue
            date = _date_text(row.get("date"))
            item = {**row, "ticker": ticker, "date": date}
            by_ticker[ticker].append(item)
            by_date[date][ticker] = item
        return (
            {ticker: sorted(by_ticker[ticker], key=lambda row: row["date"]) for ticker in sorted(by_ticker)},
            {date: dict(sorted(by_date[date].items())) for date in sorted(by_date)},
        )

    def _daily_basic(self, analysis_date: str) -> dict[str, dict[str, Any]]:
        rows = self.store.collection("daily_basic").find(
            {"trade_date": _compact_date(analysis_date)},
            {"_id": 0, "ts_code": 1, "total_mv": 1, "circ_mv": 1, "turnover_rate": 1, "volume_ratio": 1},
        )
        return {_ticker(row.get("ts_code")): dict(row) for row in rows if _ticker(row.get("ts_code"))}

    def _moneyflow(self, analysis_date: str) -> dict[str, dict[str, Any]]:
        rows = self.store.collection("moneyflow").find(
            {"trade_date": _compact_date(analysis_date)},
            {"_id": 0, "ts_code": 1, "net_mf_amount": 1, "buy_lg_amount": 1, "buy_elg_amount": 1, "sell_lg_amount": 1, "sell_elg_amount": 1},
        )
        return {_ticker(row.get("ts_code")): dict(row) for row in rows if _ticker(row.get("ts_code"))}

    def _moneyflow_history(self, dates: list[str]) -> dict[str, dict[str, dict[str, Any]]]:
        compact = list(dict.fromkeys([*dates, *[_compact_date(date) for date in dates]]))
        rows = self.store.collection("moneyflow").find(
            {"trade_date": {"$in": compact}},
            {"_id": 0, "ts_code": 1, "trade_date": 1, "net_mf_amount": 1},
        )
        result: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
        for row in rows:
            ticker = _ticker(row.get("ts_code"))
            date = _date_text(row.get("trade_date"))
            if ticker and date:
                result[date][ticker] = dict(row)
        return {date: dict(values) for date, values in result.items()}

    def _limit_rows(
        self,
        dates: list[str],
        basic: dict[str, dict[str, Any]],
    ) -> tuple[dict[str, list[dict[str, Any]]], str]:
        compact = list(dict.fromkeys([*dates, *[_compact_date(date) for date in dates]]))
        rows = self.store.collection("limit_up_pool").find({"trade_date": {"$in": compact}}, {"_id": 0})
        result: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for raw in rows:
            ticker = _ticker(raw.get("ticker") or raw.get("ts_code"))
            date = _date_text(raw.get("trade_date"))
            item = {
                **raw,
                "ticker": ticker,
                "trade_date": date,
                "name": raw.get("name") or basic.get(ticker, {}).get("name") or ticker,
                "sector_name": raw.get("industry") or basic.get(ticker, {}).get("sector") or "未分类",
            }
            result[date].append(item)
        # Research and review share dated pools. Older stored feedback alone
        # cannot cover the entire baseline; reuse complete U lists first.
        from shilun.review.board_data import BoardResearchData
        missing_dates = [day for day in dates if day not in result]
        archived = BoardResearchData(self.store).pools(missing_dates, settled=True) if missing_dates else {}
        for day, document in archived.items():
            result[day] = [{**r, 'trade_date': day, 'consecutive_count': r.get('boards'),
                            'broken_count': r.get('break_count'), 'last_seal_time': r.get('last_seal_time'),
                            'first_seal_time': r.get('seal_time'), 'sector_name': r.get('industry') or '未分类',
                            'source': document.get('source')} for r in document['stocks']]
        source_date = max(result, default="")
        return dict(result), source_date

    def _reconstruct_ladder(self, dates, bars_by_date, current_limits, basic):
        target = dates[-1]
        dates = load_review_sessions(self.store, target, dates[0]) or [target]
        current_prices = {ticker: row.get("up_limit", row.get("up")) for ticker, row in current_limits.items()}
        candidates = [ticker for ticker, bar in bars_by_date.get(target, {}).items()
                      if at_limit(bar.get("close"), current_prices.get(ticker)) is True]
        by_date = {target: current_prices}
        if candidates:
            # Read individual evidence even on dates lacking a full-market scan.
            bars_by_date = {date: dict(rows) for date, rows in bars_by_date.items()}
            bars = self.store.collection("market_daily_bars").find(
                {"date": {"$in": dates[:-1]}, "ticker": {"$in": candidates}},
                {"_id": 0, "ticker": 1, "date": 1, "close": 1})
            for bar in bars:
                bars_by_date.setdefault(_date_text(bar["date"]), {})[_ticker(bar["ticker"])] = bar
            rows = self.store.collection("daily_price_limits").find(
                {"trade_date": {"$in": list(dict.fromkeys([*dates[:-1], *[_compact_date(d) for d in dates[:-1]]]))},
                 "$or": [{"ticker": {"$in": candidates}}, {"ts_code": {"$in": candidates}}]},
                {"_id": 0, "ticker": 1, "ts_code": 1, "trade_date": 1, "up_limit": 1, "up": 1})
            for row in rows:
                by_date.setdefault(_date_text(row.get("trade_date")), {})[_ticker(row.get("ticker") or row.get("ts_code"))] = row.get("up_limit", row.get("up"))
        return reconstruct_ladder(dates, bars_by_date, by_date, basic)

    def _price_limits(self, analysis_date: str) -> dict[str, dict[str, Any]]:
        rows = self.store.collection("daily_price_limits").find(
            {"trade_date": {"$in": [analysis_date, _compact_date(analysis_date)]}},
            {"_id": 0},
        )
        return {_ticker(row.get("ticker") or row.get("ts_code")): dict(row) for row in rows if _ticker(row.get("ticker") or row.get("ts_code"))}

    def _price_limits_history(self, dates: list[str]) -> dict[str, dict[str, dict[str, Any]]]:
        compact = list(dict.fromkeys([*dates, *[_compact_date(date) for date in dates]]))
        rows = self.store.collection("daily_price_limits").find(
            {"trade_date": {"$in": compact}},
            {"_id": 0},
        )
        result: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
        for row in rows:
            date = _date_text(row.get("trade_date"))
            ticker = _ticker(row.get("ticker") or row.get("ts_code"))
            if date and ticker:
                result[date][ticker] = dict(row)
        return {date: dict(rows) for date, rows in result.items()}

    @staticmethod
    def _pattern_evidence(history, analysis_date, ticker, name):
        from shilun.market.part1 import (_prepare_index_frame, _build_support_pressure,
                                        _market_trend_summary, _build_pattern_forecast)
        import pandas as pd
        from shilun.common.db import _normalize_mongo_value
        frame = _prepare_index_frame(pd.DataFrame(history), analysis_date)
        if len(frame) < 20 or str(history[-1].get('date'))[:10] != analysis_date:
            return {'status': 'missing', 'note': '对应日期或至少20日日线未齐，形态暂不推演。'}
        levels = _build_support_pressure(frame)
        forecast = _build_pattern_forecast(frame, levels, _market_trend_summary(frame.iloc[-1]))
        return _normalize_mongo_value({'status': 'ready', 'analysis_date': analysis_date,
            'benchmark_ticker': ticker, 'benchmark_name': name, 'pattern_forecast': forecast,
            'chart_data': {'wave_series': history[-80:]}, 'history_count': len(history),
            'source': 'PART1 shared pattern engine; dated index bars'})

    def _indices(self, dates: list[str], analysis_date: str) -> list[dict[str, Any]]:
        tickers = [ticker for ticker, _ in CORE_INDICES]
        # Keep enough history for the index-review structure summary.  The
        # previous six-session window was sufficient for MA5 only, but made a
        # cached review unable to explain MA20/50/120 or key levels.
        start_date = dates[0]
        rows = self.store.collection("market_daily_bars").find(
            {"ticker": {"$in": tickers}, "date": {"$gte": start_date, "$lte": analysis_date}},
            {"_id": 0},
        ).sort([("ticker", 1), ("date", 1)])
        grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in rows:
            grouped[_ticker(row.get("ticker"))].append(dict(row))
        result = []
        for ticker, name in CORE_INDICES:
            history = grouped.get(ticker, [])
            if not history:
                result.append({"ticker": ticker, "name": name, "status": "missing"})
                continue
            current = history[-1]
            def rolling_mean(period: int) -> float | None:
                values = [_number(item.get("close")) for item in history[-period:]]
                values = [float(value) for value in values if value is not None]
                return round(sum(values) / len(values), 4) if values else None

            ma5 = rolling_mean(5)
            ma10 = rolling_mean(10)
            ma20 = rolling_mean(20)
            ma50 = rolling_mean(50)
            ma120 = rolling_mean(120)
            high = _number(current.get("high"))
            low = _number(current.get("low"))
            previous_close = _number(history[-2].get("close")) if len(history) >= 2 else None
            close = _number(current.get("close"))
            amplitude = (high - low) / previous_close if None not in (high, low, previous_close) and previous_close else None
            pct_chg = _pct(close, previous_close)
            index_vs_ma20 = _pct(close, ma20)
            recent = history[-20:]
            recent_highs = [_number(item.get("high")) for item in recent]
            recent_lows = [_number(item.get("low")) for item in recent]
            recent_highs = [float(value) for value in recent_highs if value is not None]
            recent_lows = [float(value) for value in recent_lows if value is not None]
            level_candidates = [
                (ma5, "MA5"),
                (ma10, "MA10"),
                (ma20, "MA20"),
                (min(recent_lows) if recent_lows else None, "近20日低点"),
                (max(recent_highs) if recent_highs else None, "近20日高点"),
            ]
            support_candidates = sorted(
                [(value, label) for value, label in level_candidates if value is not None and close is not None and value < close],
                key=lambda item: item[0],
                reverse=True,
            )
            pressure_candidates = sorted(
                [(value, label) for value, label in level_candidates if value is not None and close is not None and value > close],
                key=lambda item: item[0],
            )
            support_1 = support_candidates[0] if support_candidates else (None, None)
            support_2 = support_candidates[1] if len(support_candidates) > 1 else (None, None)
            pressure_1 = pressure_candidates[0] if pressure_candidates else (None, None)
            if close is not None and ma5 is not None and ma10 is not None and ma20 is not None:
                if close > ma5 > ma10 > ma20:
                    trend_stage = "上涨趋势"
                elif close < ma20 and ma5 < ma10:
                    trend_stage = "下跌阶段"
                elif close < ma5:
                    trend_stage = "调整阶段"
                else:
                    trend_stage = "震荡阶段"
            else:
                trend_stage = "结构待确认"
            coverage = {
                "ma5": round(min(len(history), 5) / 5, 2),
                "ma10": round(min(len(history), 10) / 10, 2),
                "ma20": round(min(len(history), 20) / 20, 2),
                "ma50": round(min(len(history), 50) / 50, 2),
                "ma120": round(min(len(history), 120) / 120, 2),
            }
            result.append(
                {
                    "ticker": ticker,
                    "name": name,
                    "date": _date_text(current.get("date")),
                    "open": _number(current.get("open")),
                    "high": high,
                    "low": low,
                    "close": close,
                    "amplitude": round(amplitude, 6) if amplitude is not None else None,
                    "pct_chg": round(pct_chg, 6) if pct_chg is not None else None,
                    "ma5": ma5,
                    "ma10": ma10,
                    "ma20": ma20,
                    "ma50": ma50,
                    "ma120": ma120,
                    "index_vs_ma20": round(index_vs_ma20, 6) if index_vs_ma20 is not None else None,
                    "history_count": len(history),
                    "ma_coverage": coverage,
                    "trend_stage": trend_stage,
                    "support_1": round(support_1[0], 4) if support_1[0] is not None else None,
                    "support_1_source": support_1[1],
                    "support_2": round(support_2[0], 4) if support_2[0] is not None else None,
                    "support_2_source": support_2[1],
                    "pressure_1": round(pressure_1[0], 4) if pressure_1[0] is not None else None,
                    "pressure_1_source": pressure_1[1],
                    "above_ma5": bool(close is not None and ma5 is not None and close >= ma5),
                    "solid_red_k": _solid_red_k(current),
                    "pattern_evidence": self._pattern_evidence(history, analysis_date, ticker, name),
                }
            )
        return result

    @staticmethod
    def _breadth(current: dict[str, dict[str, Any]], previous: dict[str, dict[str, Any]]) -> dict[str, Any]:
        up = down = flat = 0
        for ticker, bar in current.items():
            change = _pct(bar.get("close"), (previous.get(ticker) or {}).get("close"))
            if change is None:
                continue
            if change > 0.0001:
                up += 1
            elif change < -0.0001:
                down += 1
            else:
                flat += 1
        total = up + down + flat
        return {"up_count": up, "down_count": down, "flat_count": flat, "total": total, "up_ratio": round(up / total, 4) if total else None}

    @staticmethod
    def _amount_series(dates: list[str], bars_by_date: dict[str, dict[str, dict[str, Any]]]) -> list[dict[str, Any]]:
        return [
            {"date": date, "amount_yi": round(sum(_amount_yi(bar.get("amount")) for bar in bars_by_date.get(date, {}).values()), 2)}
            for date in dates
            if bars_by_date.get(date)
        ]

    def _sentiment(
        self,
        *,
        analysis_date: str,
        previous_date: str,
        current_bars: dict[str, dict[str, Any]],
        previous_bars: dict[str, dict[str, Any]],
        current_limits: list[dict[str, Any]],
        previous_limits: list[dict[str, Any]],
        price_limits: dict[str, dict[str, Any]],
        bars_by_ticker: dict[str, list[dict[str, Any]]],
        indices: list[dict[str, Any]],
        basic: dict[str, dict[str, Any]],
        market_amount_yi: float | None,
        amount_ma20_yi: float | None,
        up_ratio: float | None,
        limit_rows_by_date: dict[str, list[dict[str, Any]]],
        bars_by_date: dict[str, dict[str, dict[str, Any]]],
        price_limits_by_date: dict[str, dict[str, dict[str, Any]]],
        moneyflow: dict[str, dict[str, Any]],
        moneyflow_by_date: dict[str, dict[str, dict[str, Any]]] | None = None,
        sessions: list[str] | None = None,
        settled_pool: dict[str, Any] | None = None,
        mood_only: bool = False,
    ) -> dict[str, Any]:
        down_states = {t: price_limit_hit(r.get("close"), price_limits.get(t, {})) for t, r in current_bars.items()}
        down_ready = bool(down_states) and all(v is not None for v in down_states.values())
        limit_down_tickers = {t for t, hit in down_states.items() if hit is True}
        up_states = {t: at_limit(r.get("close"), price_limits.get(t, {}).get("up_limit")) for t, r in current_bars.items()}
        pool_ready = analysis_date in limit_rows_by_date
        current_tickers = {_ticker(r.get("ticker")) for r in current_limits} if pool_ready else {t for t, hit in up_states.items() if hit is True}
        current_count = len(current_tickers) if pool_ready or up_states and all(v is not None for v in up_states.values()) else None
        prior_ready = previous_date in limit_rows_by_date
        prior_count = len(previous_limits) if prior_ready else None
        cohort = {_ticker(r.get("ticker")) for r in previous_limits}
        observed = []
        if settled_pool is not None:
            observed = settled_pool.get("verified_rows") or []
            premiums = [r["return_1d"] for r in observed]
            sample_count = len(settled_pool.get("rows") or [])
            relimit = sum(at_limit(r.get("close"), r.get("up_limit")) is True for r in observed)
            relimit_ready = all(at_limit(r.get("close"), r.get("up_limit")) is not None for r in observed)
            source = settled_pool.get("source")
        else:
            for ticker in sorted(cohort):
                bar = current_bars.get(ticker, {})
                value = _pct(bar.get("close"), _number(bar.get("pre_close")) or previous_bars.get(ticker, {}).get("close"))
                if value is not None:
                    observed.append({"ticker": ticker, "return_1d": value})
            premiums = [r["return_1d"] for r in observed]
            sample_count = len(cohort)
            relimit = sum(r["ticker"] in current_tickers for r in observed)
            relimit_ready = current_count is not None
            source = "dated_pool_and_daily_close"
        coverage = len(observed) / sample_count if sample_count else None
        usable = bool(premiums) and coverage >= .8
        feedback = {
            "status": "ok" if usable and coverage == 1 else "partial" if usable else "pending",
            "sample_size": len(premiums), "cohort_size": sample_count, "coverage": coverage,
            "mean": round(mean(premiums), 6) if usable else None,
            "median": _median_value(premiums) if usable else None,
            "positive_rate": sum(v > 0 for v in premiums) / len(premiums) if usable else None,
            "relimit_rate": relimit / len(premiums) if usable and relimit_ready else None,
            "source": source, "cohort_date": previous_date, "return_date": analysis_date,
            "note": f"{previous_date} 收盘涨停固定样本 → {analysis_date} 收盘涨跌幅中位数。有效 {len(premiums)}/{sample_count}；等权，不含费用与实际成交假设。",
            "rows": observed,
        }
        downside = _downside_risk(analysis_date=analysis_date, limit_down_tickers=limit_down_tickers,
            current_bars=current_bars, previous_bars=previous_bars, bars_by_ticker=bars_by_ticker,
            bars_by_date=bars_by_date, price_limits_by_date=price_limits_by_date,
            basic=basic, indices=indices, sessions=sessions, count_ready=down_ready,
            suspensions=load_suspensions(self.store, sessions or []))
        if not mood_only:
            from shilun.review.event_downside import attach_event_groups
            downside = attach_event_groups(self.store, downside, analysis_date, sessions or [])
        boards = [int(_number(r.get("consecutive_count"), 1) or 1) for r in current_limits]
        mood = _market_mood_state(current_count=current_count or 0, previous_count=prior_count or 0,
            max_board=max(boards, default=0), lianban_count=sum(v >= 2 for v in boards), up_ratio=up_ratio,
            market_amount_yi=market_amount_yi, amount_ma20_yi=amount_ma20_yi, feedback_median=feedback["median"],
            relimit_rate=feedback["relimit_rate"], limit_down_count=len(limit_down_tickers),
            data_ready=current_count is not None and prior_ready and usable and down_ready,
            downside_penalty_override=downside["penalty"])
        missing_inputs = []
        if not current_bars: missing_inputs.append('当日日线')
        if current_count is None: missing_inputs.append('当日涨停池或涨停价')
        if not prior_ready: missing_inputs.append('上一交易日涨停池')
        if not usable: missing_inputs.append('昨日涨停反馈有效覆盖不足80%')
        if not down_ready: missing_inputs.append('当日跌停价或无涨跌停限制标记')
        mood['missing_inputs'] = missing_inputs
        mood['input_coverage'] = {'date': analysis_date, 'previous_date': previous_date,
            'daily_bars': len(current_bars), 'price_limits': sum(v is not None for v in down_states.values()),
            'missing_price_limit_tickers': sorted(t for t, v in down_states.items() if v is None),
            'feedback_valid': len(observed), 'feedback_total': sample_count,
            'current_pool_ready': pool_ready, 'previous_pool_ready': prior_ready}
        if mood['code'] == 'pending' and missing_inputs:
            mood['reason'] = '待补齐：' + '、'.join(missing_inputs)
        if mood_only:
            return {"market_mood": mood, "downside_risk": downside, "yesterday_limit_up_feedback": feedback}
        summaries = load_heat_summaries(self.store, sessions or [])
        from shilun.review.strong_stock_data import load_snapshot
        core_snapshot = load_snapshot(self.store, analysis_date)
        certified_cores = {r['ticker']: r for r in core_snapshot.get('rows', []) if r['status'] == 'qualified'}
        return {
            "analysis_date": analysis_date, "limit_up_count": current_count, "limit_down_count": downside["limit_down_count"],
            "previous_limit_up_count": prior_count, "yesterday_limit_up_feedback": feedback,
            "limit_down_wave": {"active": len(limit_down_tickers) >= 10 if down_ready else None,
                "counts": downside["position_counts"], "stocks": downside["stocks"], "warning": downside["label"]},
            "limit_up_wave": _limit_up_resonance(analysis_date=analysis_date, previous_date=previous_date,
                current_count=current_count, previous_count=prior_count, limit_rows_by_date=limit_rows_by_date,
                indices=indices, sessions=sessions, summaries=summaries),
            "heat_history": five_day_heat(analysis_date, sessions or sorted(bars_by_date), limit_rows_by_date, bars_by_ticker, summaries),
            "market_mood": mood,
            "theme_evidence": _event_theme_location(analysis_date),
            "downside_risk": downside,
            "evidence_coverage": {"price_limits": sum(v is not None for v in down_states.values()), "daily_bars": len(current_bars)},
        }

    def _sectors(
        self,
        *,
        current_bars: dict[str, dict[str, Any]],
        previous_bars: dict[str, dict[str, Any]],
        moneyflow: dict[str, dict[str, Any]],
        daily_basic: dict[str, dict[str, Any]],
        basic: dict[str, dict[str, Any]],
        current_limits: list[dict[str, Any]],
        previous_limits: list[dict[str, Any]],
        current_pool_ready: bool = True,
        previous_pool_ready: bool = True,
    ) -> list[dict[str, Any]]:
        limit_by_sector: dict[str, list[dict[str, Any]]] = defaultdict(list)
        prior_first = Counter()
        for row in current_limits:
            limit_by_sector[str(row.get("sector_code") or "")].append(row)
        for row in previous_limits:
            if int(_number(row.get("consecutive_count"), 1.0) or 1) == 1:
                prior_first[str(row.get("sector_code") or "")] += 1

        grouped: dict[str, dict[str, Any]] = defaultdict(
            lambda: {"returns": [], "amount_yi": 0.0, "net_flow_yi": 0.0, "market_cap_yi": 0.0, "up": 0, "count": 0, "flow_count": 0, "stocks": []}
        )
        for ticker, bar in current_bars.items():
            meta = basic.get(ticker) or {}
            sector = str(meta.get("industry_code") or "")
            if not sector:
                continue
            change = _pct(bar.get("close"), _number(bar.get("pre_close")) or (previous_bars.get(ticker) or {}).get("close"))
            data = grouped[sector]
            data["name"] = meta.get("sector")
            data["taxonomy_date"] = meta.get("taxonomy_date")
            data["count"] += 1
            data["up"] += int(change is not None and change > 0)
            if change is not None:
                data["returns"].append(change)
            amount = _amount_yi(bar.get("amount"))
            flow = _moneyflow_yi((moneyflow.get(ticker) or {}).get("net_mf_amount"))
            market_cap = _market_cap_yi((daily_basic.get(ticker) or {}).get("total_mv"))
            data["amount_yi"] += amount
            data["net_flow_yi"] += flow
            data["flow_count"] += _number((moneyflow.get(ticker) or {}).get("net_mf_amount")) is not None
            data["market_cap_yi"] += market_cap
            data["stocks"].append({"ticker": ticker, "name": meta.get("name") or ticker, "amount_yi": amount, "net_flow_yi": flow, "market_cap_yi": market_cap, "pct_chg": change})

        amount_values = sorted(float(item["amount_yi"]) for item in grouped.values())
        amount_q75 = amount_values[int((len(amount_values) - 1) * 0.75)] if amount_values else 0.0
        result = []
        for sector, data in grouped.items():
            limit_rows = limit_by_sector.get(sector, [])
            board_count = len(limit_rows)
            first_count = sum(int(_number(row.get("consecutive_count"), 1.0) or 1) == 1 for row in limit_rows)
            max_board = max((int(_number(row.get("consecutive_count"), 1.0) or 1) for row in limit_rows), default=0)
            broken_count = sum(int(_number(row.get("broken_count"), 0.0) or 0) > 0 for row in limit_rows)
            up_ratio = data["up"] / len(data["returns"]) if len(data["returns"]) >= .95 * data["count"] else None
            cycle = classify_sector_cycle(
                first_board_count=first_count,
                max_board=max_board,
                board_count=board_count,
                broken_count=broken_count,
                up_ratio=up_ratio,
                net_flow=data["net_flow_yi"] if data["flow_count"] >= .95 * data["count"] else None,
                prior_first_board_count=prior_first.get(sector, 0),
            )
            from shilun.review.cycle_diagnostics import require_cycle_inputs
            missing_inputs = ([] if current_pool_ready else ['current_limit_pool']) + ([] if previous_pool_ready else ['previous_limit_pool'])
            if any(_number(r.get('broken_count')) is None for r in limit_rows):
                missing_inputs.append('broken_count')
            cycle = require_cycle_inputs(cycle, missing_inputs)
            cycle.update(sector_code=sector, taxonomy_date=data['taxonomy_date'])
            emotion_dominant = max_board >= 2 or (board_count >= 3 and board_count / max(1, data["count"]) >= 0.04)
            trend_dominant = data["amount_yi"] >= amount_q75 and data["market_cap_yi"] / max(1, data["count"]) >= 100
            if emotion_dominant and trend_dominant:
                style = "mixed"
                style_label = "情绪与趋势混合"
            elif emotion_dominant:
                style = "emotion"
                style_label = "情绪型"
            elif trend_dominant:
                style = "trend"
                style_label = "趋势型"
            else:
                style = "unconfirmed"
                style_label = "风格待确认"
            stocks = sorted(data["stocks"], key=lambda item: (float(item["net_flow_yi"]), float(item["amount_yi"])), reverse=True)[:5]
            result.append(
                {
                    "sector_name": data["name"],
                    "sector_code": sector,
                    "taxonomy_date": data["taxonomy_date"],
                    "style": style,
                    "style_label": style_label,
                    "cycle": cycle,
                    "return_1d": round(mean(data["returns"]), 6) if data["returns"] else None,
                    "up_ratio": round(up_ratio, 4) if up_ratio is not None else None,
                    "amount_yi": round(data["amount_yi"], 2),
                    "net_flow_yi": round(data["net_flow_yi"], 2),
                    "market_cap_yi": round(data["market_cap_yi"], 2),
                    "stock_count": data["count"],
                    "limit_up_count": board_count,
                    "first_board_count": first_count,
                    "highest_board": max_board,
                    "broken_board_count": broken_count,
                    "front_rows": stocks,
                    "intraday_cashflow_status": "unavailable",
                    "intraday_cashflow_note": "当前数据源只有日级个股资金流；未接入板块分钟净流入前不模拟附图中的日内曲线。",
                }
            )
        result.sort(key=lambda item: (float(item["net_flow_yi"]), float(item["amount_yi"])), reverse=True)
        return result

    def _expectations(
        self,
        *,
        dates: list[str],
        analysis_date: str,
        previous_date: str,
        bars_by_ticker: dict[str, list[dict[str, Any]]],
        limit_rows_by_date: dict[str, list[dict[str, Any]]],
        current_limits: list[dict[str, Any]],
    ) -> dict[str, Any]:
        date_index = {date: index for index, date in enumerate(dates)}
        history_by_ticker = {
            ticker: {str(bar.get("date")): bar for bar in history}
            for ticker, history in bars_by_ticker.items()
        }
        grouped: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
        daily_feedback: list[dict[str, Any]] = []
        observation_rows = []
        historical_days: list[dict[str, Any]] = []

        for date in dates[:-1]:
            next_index = date_index.get(date, -1) + 1
            if next_index <= 0 or next_index >= len(dates):
                continue
            next_date = dates[next_index]
            rows = limit_rows_by_date.get(date, [])
            if not rows:
                continue
            day_close_premiums = []
            relimit_count = 0
            next_limit_tickers = {_ticker(row.get("ticker")) for row in limit_rows_by_date.get(next_date, [])}
            for row in rows:
                ticker = _ticker(row.get("ticker"))
                day_bar = (history_by_ticker.get(ticker) or {}).get(date)
                next_bar = (history_by_ticker.get(ticker) or {}).get(next_date)
                if not day_bar or not next_bar:
                    continue
                history = bars_by_ticker.get(ticker, [])
                position = next((i for i, bar in enumerate(history) if str(bar.get("date")) == date), -1)
                prior_amounts = [_number(item.get("amount")) for item in history[max(0, position - 5):position]] if position >= 0 else []
                prior_amounts = [float(value) for value in prior_amounts if value is not None and value > 0]
                current_amount = _number(day_bar.get("amount"))
                amount_ratio = current_amount / mean(prior_amounts) if current_amount is not None and prior_amounts else None
                board_type = classify_board_type(row, day_bar, amount_ratio_5=amount_ratio)
                base = _number(day_bar.get("close"))
                open_premium = _pct(next_bar.get("open"), base)
                close_premium = _pct(next_bar.get("close"), base)
                high_premium = _pct(next_bar.get("high"), base)
                if close_premium is None:
                    continue
                grouped[board_type]["open"].append(open_premium if open_premium is not None else close_premium)
                grouped[board_type]["close"].append(close_premium)
                grouped[board_type]["high"].append(high_premium if high_premium is not None else close_premium)
                day_close_premiums.append(close_premium)
                relimit_count += int(ticker in next_limit_tickers)
                daily_feedback.append({"date": next_date, "board_type": board_type, "ticker": ticker, "close_premium": close_premium})
            if day_close_premiums:
                historical_days.append(
                    {
                        "date": next_date,
                        "median_premium": median(day_close_premiums),
                        "positive_rate": sum(value > 0 for value in day_close_premiums) / len(day_close_premiums),
                        "relimit_rate": relimit_count / len(day_close_premiums),
                    }
                )

        baselines = {}
        for board_type in BOARD_TYPE_LABELS:
            metrics = grouped.get(board_type, {})
            baselines[board_type] = {
                "label": BOARD_TYPE_LABELS[board_type],
                "open": build_expectation_band(metrics.get("open", [])),
                "close": build_expectation_band(metrics.get("close", [])),
                "high": build_expectation_band(metrics.get("high", [])),
            }

        current_tickers = {_ticker(row.get("ticker")) for row in current_limits}
        from shilun.review.board_data import BoardResearchData, REASON_COLLECTION
        from shilun.review.board_effects import seal_time
        from shilun.market.taxonomy import classify_stocks
        classifications = {r['ticker']: r for r in classify_stocks(self.store, previous_date,
                           limit_rows_by_date.get(previous_date, []))}
        reason_doc = self.store.collection(REASON_COLLECTION).find_one({'date': previous_date}, {'_id': 0}) or {}
        reasons = reason_doc.get('reasons', {}) if reason_doc.get('date') == previous_date else {}
        today_pool = {r['ticker']: r for r in BoardResearchData(self.store).pools([analysis_date], 'broken').get(analysis_date, {}).get('stocks', [])}
        today_pool.update({r['ticker']: {'break_count': r.get('broken_count'), 'last_seal_time': r.get('last_seal_time')} for r in current_limits})
        previous_rows = limit_rows_by_date.get(previous_date, [])
        for row in previous_rows:
            ticker = _ticker(row.get("ticker"))
            history = history_by_ticker.get(ticker) or {}
            day_bar = history.get(previous_date)
            current_bar = history.get(analysis_date)
            if not day_bar or not current_bar:
                continue
            ordered_history = bars_by_ticker.get(ticker, [])
            position = next((i for i, bar in enumerate(ordered_history) if str(bar.get("date")) == previous_date), -1)
            prior_amounts = [_number(item.get("amount")) for item in ordered_history[max(0, position - 5):position]] if position >= 0 else []
            prior_amounts = [float(value) for value in prior_amounts if value is not None and value > 0]
            current_amount = _number(day_bar.get("amount"))
            ratio = current_amount / mean(prior_amounts) if current_amount is not None and prior_amounts else None
            board_type = classify_board_type(row, day_bar, amount_ratio_5=ratio)
            observed = _pct(current_bar.get("close"), day_bar.get("close"))
            expectation = classify_expectation(observed, baselines[board_type]["close"])
            observation_rows.append(
                {
                    "ticker": ticker,
                    "name": row.get("name") or ticker,
                    "sector_name": row.get("sector_name") or "未分类",
                    "industry_name": classifications.get(ticker, {}).get("industry"),
                    "industry_code": classifications.get(ticker, {}).get("industry_code"),
                    "industry_membership_date": previous_date,
                    "topics": list(dict.fromkeys(tags_of(clean_reason(reasons.get(ticker[:6], ''))))),
                    "topic_ids": [topic_code(label) for label in dict.fromkeys(tags_of(clean_reason(reasons.get(ticker[:6], ''))))],
                    "limit_reason": reasons.get(ticker.split(".")[0], ""),
                    "limit_reason_source": reason_doc.get("source"),
                    "topic_date": previous_date,
                    "topic_source": "同日涨停原因事件标签（Vibe-Astock）",
                    "topic_schema": EVENT_SCHEMA,
                    "board_type": board_type,
                    "board_type_label": BOARD_TYPE_LABELS[board_type],
                    "consecutive_count": int(_number(row.get("consecutive_count"), 1.0) or 1),
                    "open_premium": _pct(current_bar.get("open"), day_bar.get("close")),
                    "close_premium": observed,
                    "high_premium": _pct(current_bar.get("high"), day_bar.get("close")),
                    "low_premium": _pct(current_bar.get("low"), day_bar.get("close")),
                    "relimit": ticker in current_tickers,
                    "today_broken_count": today_pool.get(ticker, {}).get('break_count'),
                    "today_last_seal_time": seal_time(today_pool.get(ticker, {}).get('last_seal_time')) if ticker in current_tickers else None,
                    "expectation": expectation,
                }
            )

        median_band = build_expectation_band(item["median_premium"] for item in historical_days)
        positive_band = build_expectation_band(item["positive_rate"] for item in historical_days)
        relimit_band = build_expectation_band(item["relimit_rate"] for item in historical_days)
        current_close = [item["close_premium"] for item in observation_rows if item.get("close_premium") is not None]
        current_positive = sum(value > 0 for value in current_close) / len(current_close) if current_close else None
        current_relimit = sum(bool(item.get("relimit")) for item in observation_rows) / len(observation_rows) if observation_rows else None
        emotion_checks = [
            {"metric": "昨日涨停中位溢价", **classify_expectation(median(current_close) if current_close else None, median_band)},
            {"metric": "昨日涨停翻红率", **classify_expectation(current_positive, positive_band)},
            {"metric": "昨日涨停再板率", **classify_expectation(current_relimit, relimit_band)},
        ]
        emotion_gap = _aggregate_expectation_states(emotion_checks)
        emotion_gap.update({"type": "emotion_cycle", "type_label": "情绪周期预期差"})

        tape_states = [item["expectation"] for item in observation_rows]
        tape_gap = _aggregate_expectation_states(tape_states)
        tape_gap.update({"type": "tape", "type_label": "盘面预期差"})
        return {
            "lookback_sessions": min(20, len(historical_days)),
            "method": "按涨停板标签拆分过去N日次日表现，Q25/中位数/Q75分别作为低/平/高预期。",
            "board_type_baselines": baselines,
            "observations": sorted(observation_rows, key=lambda item: (int(item["consecutive_count"]), float(item.get("close_premium") or -99)), reverse=True),
            "emotion_cycle_gap": emotion_gap,
            "tape_gap": tape_gap,
            "consensus_definition": "超预期行为已扩散到价格、成交和参与者，价值错位被充分定价；这时即使股票仍强，套利空间也已关闭。",
            "logic_reversal_definition": "最初进入所依赖的事实或因果链被破坏；不与普通低于预期混为一谈。",
        }

    def _tactics(
        self,
        *,
        posture: dict[str, Any],
        indices: list[dict[str, Any]],
        current_bars: dict[str, dict[str, Any]],
        previous_bars: dict[str, dict[str, Any]],
        bars_by_ticker: dict[str, list[dict[str, Any]]],
        basic: dict[str, dict[str, Any]],
        daily_basic: dict[str, dict[str, Any]],
        moneyflow: dict[str, dict[str, Any]],
        ladder: dict[str, Any],
        sectors: dict[str, dict[str, Any]],
        current_limits: list[dict[str, Any]],
    ) -> dict[str, Any]:
        index_return_values = [_number(item.get("pct_chg")) for item in indices]
        index_return_values = [float(value) for value in index_return_values if value is not None]
        index_return = mean(index_return_values) if index_return_values else 0.0
        current_limit_map = {_ticker(row.get("ticker")): row for row in current_limits}
        sector_highest = {item["sector_name"]: int(item.get("highest_board") or 0) for item in ladder.get("sector_ladders") or []}
        results = {"red_k_net_inflow": [], "structure_repair_520": [], "weekly_breakout": []}

        for ticker, bar in current_bars.items():
            meta = basic.get(ticker) or {}
            sector_name = str(meta.get("sector") or "未分类")
            sector = sectors.get(meta.get("industry_code")) or {}
            from shilun.review.cycle_diagnostics import context_cycle
            cycle = context_cycle(meta, sector)
            history = bars_by_ticker.get(ticker, [])
            flow = _number((moneyflow.get(ticker) or {}).get("net_mf_amount"), 0.0) or 0.0
            market_cap = _market_cap_yi((daily_basic.get(ticker) or {}).get("total_mv"))
            amount = _amount_yi(bar.get("amount"))
            weekly = detect_weekly_breakout(history)
            repair = detect_520(history)
            red_k = detect_red_k_net_inflow(
                bar,
                net_inflow=flow,
                market_red_k_confirmed=bool(posture.get("market_red_k_confirmed")),
            )
            if not any((weekly, repair, red_k)):
                continue
            limit_row = current_limit_map.get(ticker) or {}
            consecutive = int(_number(limit_row.get("consecutive_count"), 0.0) or 0)
            sector_front_tickers = {
                str(item.get("ticker") or "")
                for item in (sector.get("front_rows") or [])[:3]
            }
            independently_front = bool(limit_row) or ticker in sector_front_tickers
            role = assign_exclusive_role(
                consecutive_count=consecutive,
                sector_highest_board=sector_highest.get(sector_name, 0),
                market_cap_yi=market_cap,
                amount_yi=amount,
                trend_core=bool((weekly or repair) and independently_front),
                front_row=independently_front,
            )
            stock_return = _pct(bar.get("close"), _number(bar.get("pre_close")) or (previous_bars.get(ticker) or {}).get("close"))
            close_position = _close_position(bar)
            upper_shadow = _upper_shadow_ratio(bar)
            sector_resonance = (_number(sector.get("up_ratio"), 0.0) or 0.0) >= 0.55 and (_number(sector.get("net_flow_yi"), 0.0) or 0.0) > 0
            relative_strength = stock_return is not None and stock_return > index_return
            tape = evaluate_tape_strength(
                proactive_flow=flow > 0,
                absorption=bool(close_position is not None and close_position >= 0.65 and (upper_shadow is None or upper_shadow < 0.3)),
                relative_strength=relative_strength,
                linkage=sector_resonance,
                close_quality=bool(close_position is not None and close_position >= 0.7),
            )
            board_nature = classify_board_nature(
                posture=str(posture.get("code") or ""),
                sector_resonance=sector_resonance,
                relative_strength=relative_strength,
                opens_space=bool(consecutive >= 2 or weekly),
            )
            common = {
                "ticker": ticker,
                "name": meta.get("name") or ticker,
                "sector_name": sector_name,
                "sector_code": meta.get("industry_code"),
                "cycle": cycle,
                "role": role,
                "board_nature": board_nature,
                "tape_strength": tape,
                "amount_yi": amount,
                "market_cap_yi": market_cap,
                "net_inflow_yi": _moneyflow_yi(flow),
                "pct_chg": stock_return,
                "cap_fit": "fit" if posture.get("cap_code") == "large_cap_open" or market_cap < 300 else "avoid_large_cap",
            }
            for key, signal in (("red_k_net_inflow", red_k), ("structure_repair_520", repair), ("weekly_breakout", weekly)):
                if not signal:
                    continue
                if key == "red_k_net_inflow" and not (
                    bool(role.get("front_row"))
                    and sector_resonance
                    and tape.get("state") in {"normal", "strong"}
                    and common["cap_fit"] == "fit"
                ):
                    continue
                item = {
                    **common,
                    "signal": signal,
                    "position_chart": build_position_chart(
                        market=str(posture.get("label") or ""),
                        sector_cycle=cycle["label"],
                        role=str(role.get("label") or "后排"),
                        price_shape=str(signal.get("price_shape") or ""),
                    ),
                }
                results[key].append(item)

        results["red_k_net_inflow"].sort(key=lambda item: (float(item.get("net_inflow_yi") or 0.0), float(item.get("amount_yi") or 0.0)), reverse=True)
        results["structure_repair_520"].sort(
            key=lambda item: (
                not bool((item.get("role") or {}).get("front_row")),
                float((item.get("signal") or {}).get("gap_to_ma20") or 99.0),
                -float(item.get("amount_yi") or 0.0),
            )
        )
        results["weekly_breakout"].sort(
            key=lambda item: (
                bool((item.get("role") or {}).get("front_row")),
                float(item.get("amount_yi") or 0.0),
                float((item.get("signal") or {}).get("breakout_pct") or 0.0),
            ),
            reverse=True,
        )
        definitions = {
            "red_k_net_inflow": {"label": "红K净流入", "buy_point": "大盘实心红K确认当日，按净流入降序并结合量能、板块共振和辨识度选择前排。"},
            "structure_repair_520": {"label": "结构修复520", "buy_point": "下跌后稳住，5日线出现拐点并即将上穿20日线时；5日线斜率重新转负为卖点。"},
            "weekly_breakout": {"label": "周K突破套利", "buy_point": "日K收盘首次突破过去100个交易日（约20周）最高价。"},
        }
        return {
            key: {**definitions[key], "count": len(results[key]), "items": results[key][:30]}
            for key in definitions
        }

    @staticmethod
    def _action_summary(posture: dict[str, Any], expectations: dict[str, Any], tactics: dict[str, Any]) -> dict[str, Any]:
        emotion_gap = expectations.get("emotion_cycle_gap") or {}
        tape_gap = expectations.get("tape_gap") or {}
        priorities = [posture.get("doctrine"), posture.get("cap_scope")]
        if emotion_gap.get("action"):
            priorities.append(f"情绪周期：{emotion_gap['action']}")
        if tape_gap.get("action"):
            priorities.append(f"盘面：{tape_gap['action']}")
        counts = {key: int(value.get("count") or 0) for key, value in tactics.items()}
        return {
            "headline": f"{posture.get('label')} · {posture.get('position')}",
            "priorities": [item for item in priorities if item],
            "tactic_counts": counts,
        }

    def _latest_cache(self, collection: str, analysis_date: str) -> dict[str, Any] | None:
        return self.store.collection(collection).find_one(
            {"analysis_date": {"$lte": _date_text(analysis_date)}},
            {"_id": 0},
            sort=[("analysis_date", -1)],
        )


__all__ = ["DailyReviewService", "REVIEW_COLLECTION", "REVIEW_VERSION"]
