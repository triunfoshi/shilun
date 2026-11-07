"""Intraday board-sentiment snapshot, adapted from Vibe-Astock.

This is deliberately separate from :mod:`shilun.review.service`: the daily
review reads settled Mongo snapshots, while this endpoint reads the current
Eastmoney push2ex pools and reports a short-lived, read-only market snapshot.
Pool failures are represented as unavailable instead of being converted to
zero, which keeps a network outage from looking like a calm market.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, time, timedelta
from math import isfinite
import threading
import time as monotonic_time
from typing import Any
from zoneinfo import ZoneInfo

import requests


CHINA = ZoneInfo("Asia/Shanghai")
_POOL_URL = "https://push2ex.eastmoney.com/{endpoint}"
_POOL_UT = "7eea3edcaed734bea9cbfc24409ed989"
_TODAY_TTL = 15.0
_PREVIOUS_TTL = 3600.0
SCOPE_LABEL = "东方财富原始板池口径，包含主板、创业板和科创板"
_cache: dict[str, tuple[float, object]] = {}
_cache_lock = threading.Lock()


def _now() -> datetime:
    return datetime.now(CHINA)


def _cached(key: str, ttl: float, build):
    now = monotonic_time.monotonic()
    with _cache_lock:
        hit = _cache.get(key)
        if hit and now - hit[0] < ttl:
            return hit[1]
    value = build()
    # A valid empty pool (for example no跌停) is cacheable. Exceptions and
    # malformed responses are returned as None and intentionally not cached.
    if value is not None:
        with _cache_lock:
            _cache[key] = (now, value)
    return value


def _fetch_pool(endpoint: str, ymd: str) -> list[dict[str, Any]] | None:
    """Fetch one Eastmoney board pool; return None on transport/schema error."""
    params = {
        "ut": _POOL_UT,
        "dpt": "wz.ztzt",
        "Pageindex": 0,
        "pagesize": 10000,
        "sort": {"getTopicDTPool": "fund:asc", "getYesterdayZTPool": "zs:desc"}.get(endpoint, "fbt:asc"),
        "date": ymd,
    }
    try:
        with requests.Session() as session:
            # ECS and the desktop can have different proxy settings. Direct
            # requests avoid a stale system proxy without changing globals.
            session.trust_env = False
            response = session.get(
                _POOL_URL.format(endpoint=endpoint),
                params=params,
                headers={"User-Agent": "Mozilla/5.0", "Referer": "https://quote.eastmoney.com/"},
                timeout=8,
            )
            response.raise_for_status()
            payload = response.json()
        data = payload.get("data") if isinstance(payload, dict) else None
        pool = data.get("pool") if isinstance(data, dict) else None
        if (not isinstance(payload, dict) or payload.get("rc") != 0
                or not isinstance(data, dict) or str(data.get("qdate")) != ymd
                or not isinstance(pool, list) or data.get("tc") != len(pool)):
            return None
        codes = [str(row.get("c", "")) for row in pool if isinstance(row, dict)]
        if len(codes) != len(pool) or len(set(codes)) != len(codes) or any(len(c) != 6 or not c.isdigit() for c in codes):
            return None
        return pool
    except Exception:  # noqa: BLE001 - endpoint must not expose provider details
        return None


def _previous_weekday(day: date) -> date:
    cursor = day - timedelta(days=1)
    while cursor.weekday() >= 5:
        cursor -= timedelta(days=1)
    return cursor


def _settled_date_candidates(day: date) -> list[date]:
    """Return recent weekday candidates, newest first.

    Eastmoney's pool endpoint is not a trading-calendar service.  Walking back
    over weekdays lets a weekend, holiday, or a transient empty response find
    the latest session that actually has a pool snapshot without claiming that
    a missing response means zero涨停.
    """
    cursor = day
    candidates: list[date] = []
    for _ in range(15):
        if cursor.weekday() < 5:
            candidates.append(cursor)
        cursor -= timedelta(days=1)
    return candidates


def _phase(now: datetime) -> tuple[str, str, bool]:
    """Return (key, user-facing label, whether live pools should be queried)."""
    if now.weekday() >= 5:
        return "non_trading", "非交易日", False
    current = now.time()
    if current < time(9, 15):
        return "pre_open", "盘前，尚未取得连续竞价打板池", False
    if current < time(9, 30):
        return "auction", "集合竞价，连续交易情绪稍后更新", False
    if current < time(11, 30) or time(13, 0) <= current < time(15, 0):
        return "continuous", "连续交易", True
    if current < time(13, 0):
        return "lunch", "午间休市，沿用最近一次盘中快照", False
    return "closing", "收盘后，今日盘中快照已结束", False


def _rate(hit: int | None, total: int | None) -> float | None:
    if hit is None or total is None or total <= 0:
        return None
    return round(hit / total, 4)


def _finite(value):
    if isinstance(value, bool) or value is None:
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if isfinite(result) else None


def _board_count(row: dict[str, Any]) -> int:
    try:
        value = int(float(row.get("lbc") or 1))
        return value if value > 0 else 1
    except (TypeError, ValueError):
        return 1


def _load_pools(ymd: str, ttl: float) -> tuple[list[dict[str, Any]] | None, list[dict[str, Any]] | None, list[dict[str, Any]] | None]:
    """Load涨停、炸板、跌停 pools in parallel with the requested cache TTL."""
    endpoints = ("getTopicZTPool", "getTopicZBPool", "getTopicDTPool")

    def load(endpoint: str):
        return _cached(f"{endpoint}:{ymd}", ttl, lambda: _fetch_pool(endpoint, ymd))

    with ThreadPoolExecutor(max_workers=3) as executor:
        return tuple(executor.map(load, endpoints))  # type: ignore[return-value]


def _pool_metrics(
    *,
    snapshot_day: date,
    zt: list[dict[str, Any]],
    zb: list[dict[str, Any]] | None,
    dt: list[dict[str, Any]] | None,
    settled: bool,
    base: dict[str, Any],
    now: datetime,
) -> dict[str, Any]:
    """Build the common metric payload for live and settled snapshots."""
    previous_day = _previous_weekday(snapshot_day)
    previous_ymd = previous_day.strftime("%Y%m%d")
    previous = _cached(
        f"getTopicZTPool:{previous_ymd}", _PREVIOUS_TTL,
        lambda: _fetch_pool("getTopicZTPool", previous_ymd),
    )
    promotion_source = "dated_limit_up_pool"
    if previous is None:
        # The current-day yesterday pool identifies the cohort explicitly.
        # Do not accept a historical request whose response date rolled forward.
        ymd = snapshot_day.strftime("%Y%m%d")
        previous = _cached(f"getYesterdayZTPool:{ymd}", _PREVIOUS_TTL,
                           lambda: _fetch_pool("getYesterdayZTPool", ymd))
        promotion_source = "current_day_yesterday_pool"
    raw_counts = {"zt_count": len(zt), "zb_count": len(zb) if zb is not None else None,
                  "dt_count": len(dt) if dt is not None else None}
    zt_count = len(zt)
    zb_count = len(zb) if zb is not None else None
    dt_count = len(dt) if dt is not None else None
    attempts = zt_count + zb_count if zb_count is not None else None
    today_codes = {str(row.get("c")) for row in zt if row.get("c") is not None}
    promotion_base = len(previous) if previous else None
    promotion_rate = None
    if previous:
        promotion_rate = _rate(
            sum(1 for row in previous if str(row.get("c")) in today_codes),
            promotion_base,
        )
    boards = [_board_count(row) for row in zt]
    return {
        **base,
        "available": True,
        "date": snapshot_day.isoformat(),
        "snapshot_date": snapshot_day.isoformat(),
        "snapshot_mode": "settled" if settled else "live",
        "settled": settled,
        "zt_count": zt_count,
        "dt_count": dt_count,
        "scope": "eastmoney_raw_pool",
        "scope_label": SCOPE_LABEL,
        "raw_pool_counts": raw_counts,
        "down_stocks": [{"code": r["c"], "name": r.get("n") or r["c"],
                         "price": r.get("p") / 1000 if isinstance(r.get("p"), (int, float)) else None,
                         "pct": _finite(r.get("zdp")),
                         "pct_source": "eastmoney_dated_pool" if _finite(r.get("zdp")) is not None else None,
                         "limit_status": "confirmed_limit_down",
                         "verification_source": "东方财富当日跌停池"} for r in dt] if dt is not None else None,
        "zb_count": zb_count,
        "max_boards": max(boards) if boards else 0,
        "lianban_count": sum(board >= 2 for board in boards),
        "seal_rate": _rate(zt_count, attempts),
        "break_rate": _rate(zb_count, attempts),
        "promotion_rate": promotion_rate,
        "promotion_base": promotion_base,
        "promotion_base_date": previous_day.isoformat() if previous and promotion_source == "dated_limit_up_pool" else None,
        "promotion_base_label": ((f"{previous_day.isoformat()} 涨停样本" if promotion_source == "dated_limit_up_pool"
                                  else f"{snapshot_day.isoformat()} 昨日涨停池") if previous else None),
        "promotion_source": promotion_source if previous else None,
        "fetched_at": now.isoformat(timespec="seconds"),
        "data_quality": {
            "涨停池": "ok",
            "炸板池": "ok" if zb is not None else "unavailable",
            "跌停池": "ok" if dt is not None else "unavailable",
            "上一交易日涨停池": "ok" if previous else "unavailable",
        },
        "note": "盘中快照；封板率=涨停／(涨停+炸板)，晋级率=上一交易日涨停中今日再次封住的比例。" if not settled
        else "最近交易日收盘快照；非连续交易时段沿用该快照，不将行情源缺失显示为零涨停。",
    }


def _latest_settled_snapshot(*, now: datetime, base: dict[str, Any]) -> dict[str, Any]:
    """Resolve the most recent valid completed session for non-live periods."""
    # After the close, today's session is complete.  Before the close (and on
    # weekends/holidays), start from the prior weekday and walk backwards.
    start = now.date() if now.weekday() < 5 and now.time() >= time(15, 0) else _previous_weekday(now.date())
    for snapshot_day in _settled_date_candidates(start):
        ymd = snapshot_day.strftime("%Y%m%d")
        zt, zb, dt = _load_pools(ymd, _PREVIOUS_TTL)
        if zt is None:
            continue
        # A completely empty weekday response is more likely a holiday or an
        # upstream gap than a meaningful settled market state.  Keep walking.
        if not any(pool for pool in (zt, zb, dt) if pool is not None):
            continue
        settled_base = {
            **base,
            "phase": f"{base['phase']}；沿用最近交易日收盘状态",
            "phase_key": f"{base['phase_key']}_settled",
            "requested_date": now.date().isoformat(),
            "cache_ttl_seconds": int(_PREVIOUS_TTL),
        }
        return _pool_metrics(
            snapshot_day=snapshot_day,
            zt=zt,
            zb=zb,
            dt=dt,
            settled=True,
            base=settled_base,
            now=now,
        )
    return {
        **base,
        "snapshot_mode": "settled",
        "requested_date": now.date().isoformat(),
        "reason": "最近交易日收盘打板池尚未取得；数据失败不按零涨停处理",
        "data_quality": {"涨停池": "unavailable", "炸板池": "unknown", "跌停池": "unknown"},
    }


def reconcile_settled_downside(payload, store):
    """Use the exact dated, validated P2 close for settled all-market downside."""
    if payload.get('snapshot_mode') != 'settled' or not payload.get('available'):
        return payload
    from shilun.review.service import REVIEW_COLLECTION, REVIEW_VERSION
    doc = store.collection(REVIEW_COLLECTION).find_one(
        {'analysis_date': payload['snapshot_date'], 'engine_version': REVIEW_VERSION},
        {'_id': 0, 'sentiment.downside_risk': 1, 'previous_trade_date': 1}) or {}
    risk = doc.get('sentiment', {}).get('downside_risk', {})
    count = risk.get('limit_down_count')
    if not risk.get('count_ready') or type(count) is not int or count < 0 or len(risk.get('stocks', [])) != count:
        return {**payload, 'downside_scope': '东方财富原始跌停池；全市场收盘核验待补'}
    # The validated roster proves the limit-down identity, but does not contain
    # returns.  Keep those facts distinct rather than erasing every raw % as
    # the previous adapter did.  Read only this roster on its exact session.
    day = payload['snapshot_date']
    prior = doc.get('previous_trade_date')
    dates = [day] + ([prior] if isinstance(prior, str) and prior < day else [])
    tickers = [r['ticker'] for r in risk['stocks']]
    bars, repeated = {}, set()
    quote_read_failed = False
    if tickers:
        from pymongo.errors import PyMongoError
        try:
            for bar in store.collection('market_daily_bars').find(
                {'date': {'$in': dates}, 'ticker': {'$in': tickers}},
                {'_id': 0, 'date': 1, 'ticker': 1, 'close': 1, 'pre_close': 1},
                batch_size=1000,
            ):
                key = (bar.get('ticker'), bar.get('date'))
                if key in bars:
                    repeated.add(key)
                bars[key] = bar
        except PyMongoError:
            # The roster has already been verified.  A later quote read failure
            # must not silently shrink the all-market count back to the raw pool.
            bars = {}
            quote_read_failed = True
    raw = {str(r.get('code')): r for r in payload.get('down_stocks', []) or []}
    rows = []
    for item in risk['stocks']:
        ticker = item['ticker']
        code = ticker.split('.')[0]
        bar = bars.get((ticker, day), {}) if (ticker, day) not in repeated else {}
        close, reference = _finite(bar.get('close')), _finite(bar.get('pre_close'))
        reference_source = 'daily_pre_close'
        if reference is None or reference <= 0:
            previous = bars.get((ticker, prior), {}) if (ticker, prior) not in repeated else {}
            reference = _finite(previous.get('close'))
            reference_source = 'previous_session_close'
        valid = close is not None and close > 0 and reference is not None and reference > 0
        observed_pct = round((close / reference - 1) * 100, 6) if valid else _finite(raw.get(code, {}).get('pct'))
        rows.append({
            'code': code, 'ticker': ticker, 'name': item.get('name') or ticker,
            'date': day, 'price': close if close is not None and close > 0 else None,
            'pct': observed_pct,
            'pct_source': reference_source if valid else 'eastmoney_dated_pool' if observed_pct is not None else None,
            'pct_missing_reason': None if observed_pct is not None else
                '同日日线读取暂不可用，原始跌停池也未返回涨跌幅' if quote_read_failed else
                '同日日线重复，暂不采用日线涨跌幅' if (ticker, day) in repeated else
                '同日日线或有效前收参考价缺失，原始跌停池也未返回涨跌幅',
            'limit_status': 'confirmed_limit_down',
            'verification_source': '全市场收盘价与跌停价核验',
        })
    raw_count = payload.get('raw_pool_counts', {}).get('dt_count')
    return {**payload, 'dt_count': count, 'downside_source': 'daily_close_and_price_limit',
        'downside_scope': '全市场收盘核验（含 ST），与下方短线复盘同口径',
        'down_stocks': rows,
        'note': payload.get('note', '') + f' 跌停按同日全市场收盘价与跌停价核验（含 ST）：{count} 家；东方财富原始池 {raw_count if raw_count is not None else "未取得"} 家。'}


def snapshot(*, force: bool = False) -> dict[str, Any]:
    """Return Vibe-Astock-like live board sentiment without writing Mongo."""
    if force:
        with _cache_lock:
            _cache.clear()
    now = _now()
    today = now.date()
    phase_key, phase_label, queryable = _phase(now)
    base = {
        "available": False,
        "date": today.isoformat(),
        "as_of": now.isoformat(timespec="seconds"),
        "phase": phase_label,
        "phase_key": phase_key,
        "settled": phase_key == "closing",
        "source": "eastmoney_push2ex",
        "source_label": "东方财富实时涨停／炸板／跌停池",
        "scope_label": SCOPE_LABEL,
        "cache_ttl_seconds": int(_TODAY_TTL),
    }
    if not queryable:
        return _latest_settled_snapshot(now=now, base=base)

    ymd = today.strftime("%Y%m%d")
    # The three pools are independent; fetching together keeps the card from
    # waiting three serial network round trips. Cache still limits refreshes to
    # one request per pool every 15 seconds.
    zt, zb, dt = _load_pools(ymd, _TODAY_TTL)
    if zt is None:
        return {**base, "reason": "涨停池取数失败，暂不把失败显示为零涨停",
                "data_quality": {"涨停池": "unavailable", "炸板池": "unknown", "跌停池": "unknown"}}
    if not zt:
        return {**base, "reason": "今日尚未返回涨停池；可能尚未开盘或行情源暂未形成有效快照",
                "data_quality": {"涨停池": "empty", "炸板池": "unknown", "跌停池": "unknown"}}

    return _pool_metrics(snapshot_day=today, zt=zt, zb=zb, dt=dt, settled=False, base=base, now=now)


__all__ = ["snapshot"]
