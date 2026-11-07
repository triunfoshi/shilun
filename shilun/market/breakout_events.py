"""突破事件（`breakout_events`）集合的数据层。

本模块负责封装 `breakout_events` 集合的写入 / 查询 / 状态迁移，
把「昨日检测出突破 → 记录基线 → 逐日回填 T+1..T+5 → 结算 breakout_quality」
的整条追踪链，用一张表打通。

设计口径参照 v0.2 战法第八/九章：突破日先固化基线（`breakout_close` /
`previous_high_20` / `box_upper` / `breakout_volume`），此后每天把新一根日线
追加到 `post_bars`，同时增量更新聚合字段 `post_breakout_drawdown_5d` /
`post_breakout_shrink_ratio` / `next_day_hold_flag` / `previous_high_hold_ratio`。
`compute_breakout_quality_score()`（Job 5）与 `build_ma_features()`
的 `post_breakout_shrink_ratio`（Job 6）都改成从这里读，替换掉旧的占位。

状态机：
    pending    刚检测出突破，还没有 T+1 数据（同日写入即为此状态）
    tracking   已有 1..4 根 post_bar，继续等剩余交易日
    settled    已有 5 根 post_bar，聚合字段稳定，可供评分使用
    expired    突破日超过 30 个自然日仍未 settle，视为数据缺失，不再追踪

只有 status ∈ {pending, tracking} 的事件会被 Job 4 每日回填任务重新扫描；
settled / expired 事件冻结不动。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Iterable

BREAKOUT_ENGINE_VERSION = "breakout_v1"

COLLECTION_NAME = "breakout_events"

TRACK_DAYS = 5

VALID_STATUSES = {"pending", "tracking", "settled", "expired"}

# ── 突破判定阈值 ──────────────────────────────────────────────────
# 与 `shilun.market.ma5_features._breakout_flag` 保持同一套口径，
# 便于日后拟合时集中调整。
BREAKOUT_MIN_DISTANCE = 0.005          # close / ma5 - 1 >= 0.5%
BREAKOUT_MIN_REAL_BODY_RATIO = 0.35    # 阳线实体健康
BREAKOUT_MIN_CLOSE_POSITION = 0.55     # 收在当日中枢以上
BREAKOUT_MIN_VOLUME_RATIO = 1.2        # 突破日量比 >= 1.2（v0.2 第 8 章）
BREAKOUT_MIN_HISTORY_BARS = 25         # 需要足够历史算 previous_high_20


def _f(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number


@dataclass
class BreakoutBaseline:
    """突破日基线字段。由 Job 3 检测函数构造后传入 `upsert_breakout_event`。"""

    ticker: str
    breakout_date: str
    breakout_close: float
    breakout_ma5: float
    breakout_volume: float
    breakout_volume_ratio: float
    previous_high_20: float
    previous_high_60: float
    box_upper: float
    close_position: float
    real_body_ratio: float
    industry: str | None = None

    def to_document(self) -> dict[str, Any]:
        now = datetime.now().isoformat(timespec="seconds")
        return {
            "ticker": self.ticker,
            "breakout_date": self.breakout_date,
            "engine_version": BREAKOUT_ENGINE_VERSION,
            "industry": self.industry,
            "breakout_close": round(_f(self.breakout_close), 4),
            "breakout_ma5": round(_f(self.breakout_ma5), 4),
            "breakout_volume": round(_f(self.breakout_volume), 2),
            "breakout_volume_ratio": round(_f(self.breakout_volume_ratio), 4),
            "previous_high_20": round(_f(self.previous_high_20), 4),
            "previous_high_60": round(_f(self.previous_high_60), 4),
            "box_upper": round(_f(self.box_upper), 4),
            "close_position": round(_f(self.close_position), 4),
            "real_body_ratio": round(_f(self.real_body_ratio), 4),
            "status": "pending",
            "tracked_days": 0,
            "post_bars": [],
            "created_at": now,
            "updated_at": now,
        }


@dataclass
class PostBreakoutBar:
    """回填 T+n 时传入的字段。由 Job 4 从 daily bar 构造。"""

    date: str
    n: int
    close: float
    low: float
    high: float
    volume: float

    def to_document(self, *, breakout_volume: float, box_upper: float, previous_high_20: float) -> dict[str, Any]:
        volume_shrink_ratio = _f(self.volume) / breakout_volume if breakout_volume else 0.0
        hold_ratio = _f(self.low) / previous_high_20 - 1.0 if previous_high_20 else 0.0
        return {
            "date": self.date,
            "n": int(self.n),
            "close": round(_f(self.close), 4),
            "low": round(_f(self.low), 4),
            "high": round(_f(self.high), 4),
            "volume": round(_f(self.volume), 2),
            "volume_shrink_ratio": round(volume_shrink_ratio, 4),
            "hold_ratio": round(hold_ratio, 4),
            "fell_back_into_box": bool(box_upper and _f(self.close) < box_upper),
        }


def upsert_breakout_event(collection: Any, baseline: BreakoutBaseline) -> str:
    """插入或更新一个突破基线。已存在同 (ticker, breakout_date) 不覆盖 post_bars。

    返回 "inserted" / "existing" 便于 Job 3 汇总日志。
    """
    doc = baseline.to_document()
    key = {"ticker": doc["ticker"], "breakout_date": doc["breakout_date"]}
    existing = collection.find_one(key, {"_id": 0, "status": 1, "tracked_days": 1})
    if existing:
        # 已存在（可能是之前失败重跑）：仅更新基线字段，post_bars/status 不动，
        # 避免把已经回填过的追踪数据清空。
        set_fields = {k: v for k, v in doc.items() if k not in {"status", "tracked_days", "post_bars", "created_at"}}
        set_fields["updated_at"] = datetime.now().isoformat(timespec="seconds")
        collection.update_one(key, {"$set": set_fields}, upsert=False)
        return "existing"
    collection.update_one(key, {"$set": doc}, upsert=True)
    return "inserted"


def append_post_bar(collection: Any, ticker: str, breakout_date: str, bar: PostBreakoutBar) -> bool:
    """把一根 T+n 追加到 post_bars，并同步聚合字段与 status。

    返回 True 表示写入成功，False 表示事件不存在或 T+n 已存在。
    """
    doc = collection.find_one({"ticker": ticker, "breakout_date": breakout_date}, {"_id": 0})
    if not doc:
        return False
    if doc.get("status") in {"settled", "expired"}:
        return False
    existing_bars = list(doc.get("post_bars") or [])
    if any(int(b.get("n", 0)) == bar.n for b in existing_bars):
        return False

    bar_doc = bar.to_document(
        breakout_volume=_f(doc.get("breakout_volume")),
        box_upper=_f(doc.get("box_upper")),
        previous_high_20=_f(doc.get("previous_high_20")),
    )
    existing_bars.append(bar_doc)
    existing_bars.sort(key=lambda x: int(x.get("n", 0)))

    aggregates = _recompute_aggregates(doc, existing_bars)
    tracked_days = len(existing_bars)
    status = "settled" if tracked_days >= TRACK_DAYS else "tracking"

    update_payload = {
        "post_bars": existing_bars,
        "tracked_days": tracked_days,
        "status": status,
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        **aggregates,
    }
    collection.update_one(
        {"ticker": ticker, "breakout_date": breakout_date},
        {"$set": update_payload},
    )
    return True


def _recompute_aggregates(baseline_doc: dict[str, Any], post_bars: list[dict[str, Any]]) -> dict[str, Any]:
    """基于当前所有 post_bars 重新计算聚合字段。post_bars 需按 n 升序。"""
    breakout_close = _f(baseline_doc.get("breakout_close"))
    breakout_volume = _f(baseline_doc.get("breakout_volume"))
    previous_high_20 = _f(baseline_doc.get("previous_high_20"))
    box_upper = _f(baseline_doc.get("box_upper"))

    lows = [_f(b.get("low")) for b in post_bars if _f(b.get("low")) > 0]
    volumes = [_f(b.get("volume")) for b in post_bars if _f(b.get("volume")) > 0]
    closes = [_f(b.get("close")) for b in post_bars if _f(b.get("close")) > 0]

    post_breakout_drawdown_5d = 0.0
    if lows and breakout_close:
        post_breakout_drawdown_5d = max(0.0, 1.0 - min(lows) / breakout_close)

    post_breakout_shrink_ratio = 0.0
    if volumes and breakout_volume:
        post_breakout_shrink_ratio = (sum(volumes) / len(volumes)) / breakout_volume

    next_day_bar = next((b for b in post_bars if int(b.get("n", 0)) == 1), None)
    next_day_hold_flag = None
    if next_day_bar is not None and breakout_close:
        next_day_hold_flag = _f(next_day_bar.get("close")) >= breakout_close * 0.98

    previous_high_hold_ratio = 0.0
    if lows and previous_high_20:
        previous_high_hold_ratio = min(lows) / previous_high_20 - 1.0

    fall_back_into_box_flag = False
    if closes and box_upper:
        fall_back_into_box_flag = any(c < box_upper for c in closes)

    breakout_quality = classify_breakout_quality(
        previous_high_hold_ratio=previous_high_hold_ratio,
        next_day_hold_flag=next_day_hold_flag,
        post_breakout_shrink_ratio=post_breakout_shrink_ratio,
        fall_back_into_box_flag=fall_back_into_box_flag,
        tracked_days=len(post_bars),
    )

    return {
        "post_breakout_drawdown_5d": round(post_breakout_drawdown_5d, 4),
        "post_breakout_shrink_ratio": round(post_breakout_shrink_ratio, 4),
        "next_day_hold_flag": next_day_hold_flag,
        "previous_high_hold_ratio": round(previous_high_hold_ratio, 4),
        "fall_back_into_box_flag": fall_back_into_box_flag,
        "breakout_quality": breakout_quality,
    }


def classify_breakout_quality(
    *,
    previous_high_hold_ratio: float,
    next_day_hold_flag: bool | None,
    post_breakout_shrink_ratio: float,
    fall_back_into_box_flag: bool,
    tracked_days: int,
) -> str:
    """把聚合字段折成 valid / pending_confirmation / suspicious / failed 四挡。

    - failed：明显跌破前高 (< -2%) 或跌回箱体
    - suspicious：轻微跌破 (-2%~-1%) 或次日不守
    - pending_confirmation：追踪天数还没到 5 天且没有明确坏信号
    - valid：前高守住 + 次日守住 + 缩量健康
    """
    if fall_back_into_box_flag or previous_high_hold_ratio < -0.02:
        return "failed"
    if next_day_hold_flag is False or previous_high_hold_ratio < -0.01:
        return "suspicious"
    if tracked_days < TRACK_DAYS:
        return "pending_confirmation"
    if previous_high_hold_ratio >= 0 and post_breakout_shrink_ratio and post_breakout_shrink_ratio < 1.0:
        return "valid"
    return "pending_confirmation"


def get_breakout_event(collection: Any, ticker: str, breakout_date: str) -> dict[str, Any] | None:
    return collection.find_one(
        {"ticker": ticker, "breakout_date": breakout_date},
        {"_id": 0},
    )


def get_latest_breakout_event(collection: Any, ticker: str, *, on_or_before: str | None = None) -> dict[str, Any] | None:
    """按 breakout_date 倒序取给定 ticker 的最近一条突破事件，供特征提取时读用。"""
    query: dict[str, Any] = {"ticker": ticker}
    if on_or_before:
        query["breakout_date"] = {"$lte": on_or_before}
    cursor = collection.find(query, {"_id": 0}).sort("breakout_date", -1).limit(1)
    for doc in cursor:
        return doc
    return None


def build_latest_events_lookup(
    collection: Any,
    tickers: list[str] | None = None,
    *,
    on_or_before: str | None = None,
    lookback_days: int = 30,
) -> dict[str, dict[str, Any]]:
    """Job 6 用：一次 Mongo query 拉出多只票各自最近一条突破事件，返回 {ticker: event}。

    - `tickers=None` 表示不限票，取所有；否则只查这个集合内的票。
    - `on_or_before` 限制事件的 `breakout_date <= 该日`，用于历史回放。
    - `lookback_days` 限制向前扫多少个自然日，避免拉出很老的事件（默认 30 天，
      与 Job 2 的 `max_age_days` 一致）。

    当同一票有多条事件时，取 `breakout_date` 最大的那条。
    """
    query: dict[str, Any] = {}
    if on_or_before:
        date_filter: dict[str, Any] = {"$lte": on_or_before}
        try:
            oldest = (
                datetime.strptime(on_or_before, "%Y-%m-%d")
                - timedelta(days=max(1, int(lookback_days)))
            ).strftime("%Y-%m-%d")
            date_filter["$gte"] = oldest
        except ValueError:
            pass
        query["breakout_date"] = date_filter
    if tickers:
        query["ticker"] = {"$in": [str(t) for t in tickers]}

    lookup: dict[str, dict[str, Any]] = {}
    for doc in collection.find(query, {"_id": 0}).sort("breakout_date", -1):
        ticker = str(doc.get("ticker") or "")
        if not ticker or ticker in lookup:
            continue
        lookup[ticker] = doc
    return lookup


def find_events_needing_backfill(collection: Any, *, up_to_date: str, max_age_days: int = 30) -> list[dict[str, Any]]:
    """Job 4 用：拿到所有 status ∈ {pending, tracking}、且 breakout_date 在合理窗口内的事件。"""
    cursor = collection.find(
        {"status": {"$in": ["pending", "tracking"]}, "breakout_date": {"$lte": up_to_date}},
        {"_id": 0},
    ).sort("breakout_date", 1)
    events = list(cursor)
    filtered: list[dict[str, Any]] = []
    for event in events:
        breakout_date = str(event.get("breakout_date") or "")
        if not breakout_date:
            continue
        # 简单窗口过滤：突破日距 up_to_date 超过 max_age_days 自然日就 expire
        try:
            delta_days = (datetime.strptime(up_to_date, "%Y-%m-%d") - datetime.strptime(breakout_date, "%Y-%m-%d")).days
        except ValueError:
            continue
        if delta_days > max_age_days:
            collection.update_one(
                {"ticker": event["ticker"], "breakout_date": breakout_date},
                {"$set": {"status": "expired", "updated_at": datetime.now().isoformat(timespec="seconds")}},
            )
            continue
        filtered.append(event)
    return filtered


def bulk_insert_baselines(collection: Any, baselines: Iterable[BreakoutBaseline]) -> dict[str, int]:
    """Job 3 主入口：批量落库当日检测出的突破。返回 {inserted, existing}。"""
    counts = {"inserted": 0, "existing": 0}
    for baseline in baselines:
        result = upsert_breakout_event(collection, baseline)
        counts[result] = counts.get(result, 0) + 1
    return counts


# ── Job 3：突破日检测 ────────────────────────────────────────────


def _previous_high(closes_highs: list[float], window: int) -> float:
    """给定按日期升序的 high 序列，返回最近 `window` 根的最高价（不含当日）。"""
    prior = closes_highs[-window - 1:-1] if len(closes_highs) > 1 else closes_highs[-window:]
    prior = [value for value in prior if value and value > 0]
    return max(prior) if prior else 0.0


def _is_breakout_bar(latest: dict, prev: dict) -> bool:
    """与 `ma5_features._breakout_flag` 保持一致的四条约束 + 量能约束。

    这里额外加上 `breakout_volume_ratio >= BREAKOUT_MIN_VOLUME_RATIO`，
    因为落库进 `breakout_events` 是"要长期追踪的事件"，宁缺毋滥；
    per-bar 特征提取（`ma5_features._breakout_flag`）不加量能硬门槛，
    是为了让 `compute_ma5_breakout_score()` 里的 volume 子项能拿到梯度。
    """
    close = float(latest.get("close") or 0)
    ma5 = float(latest.get("ma5") or 0)
    prev_close = float(prev.get("close") or 0)
    prev_ma5 = float(prev.get("ma5") or 0)
    if not (close and ma5 and prev_ma5):
        return False
    if prev_close <= prev_ma5:
        return False
    if close <= ma5 * (1.0 + BREAKOUT_MIN_DISTANCE):
        return False
    if float(latest.get("real_body_ratio") or 0) <= BREAKOUT_MIN_REAL_BODY_RATIO:
        return False
    if float(latest.get("close_position") or 0.5) <= BREAKOUT_MIN_CLOSE_POSITION:
        return False
    if float(latest.get("volume_ratio_20") or 0) < BREAKOUT_MIN_VOLUME_RATIO:
        return False
    return True


def detect_daily_breakouts(stock_frame: Any, analysis_date: str) -> list[BreakoutBaseline]:
    """从预处理过的全市场 DataFrame 里，找出 `analysis_date` 当天新触发的突破。

    `stock_frame` 期待含列：`ticker, date, close, ma5, volume, volume_ratio_20,
    close_position, real_body_ratio, high, industry`。这是 `sector._prepare_stock_frame`
    的标准输出，所以 Job 8 的 API 可以直接复用同一份数据。

    返回 `BreakoutBaseline` 列表，调用方决定是否落库（便于测试）。
    """
    import pandas as pd  # 局部导入，让此模块在没有 pandas 的环境也能被 import

    if stock_frame is None or getattr(stock_frame, "empty", True):
        return []

    frame = stock_frame.copy()
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
    target_ts = pd.Timestamp(analysis_date)
    frame = frame.loc[frame["date"] <= target_ts].sort_values(["ticker", "date"])
    if frame.empty:
        return []

    baselines: list[BreakoutBaseline] = []
    for ticker, ticker_bars in frame.groupby("ticker", sort=False):
        if len(ticker_bars) < BREAKOUT_MIN_HISTORY_BARS:
            continue
        # 只处理"分析日就是当只票的最新一根"这种情况，否则算作历史日，跳过
        latest_row = ticker_bars.iloc[-1]
        if pd.Timestamp(latest_row["date"]) != target_ts:
            continue
        prev_row = ticker_bars.iloc[-2]
        latest = latest_row.to_dict()
        prev = prev_row.to_dict()
        if not _is_breakout_bar(latest, prev):
            continue

        recent = ticker_bars.tail(65)
        highs = [float(x) if x is not None else 0.0 for x in recent["high"].tolist()]
        previous_high_20 = _previous_high(highs, 20)
        previous_high_60 = _previous_high(highs, 60)
        breakout_date_str = target_ts.strftime("%Y-%m-%d")

        baselines.append(
            BreakoutBaseline(
                ticker=str(ticker),
                breakout_date=breakout_date_str,
                breakout_close=float(latest.get("close") or 0),
                breakout_ma5=float(latest.get("ma5") or 0),
                breakout_volume=float(latest.get("volume") or 0),
                breakout_volume_ratio=float(latest.get("volume_ratio_20") or 0),
                previous_high_20=previous_high_20,
                previous_high_60=previous_high_60,
                box_upper=previous_high_20,  # MVP：以 20 日前高近似箱体上沿
                close_position=float(latest.get("close_position") or 0),
                real_body_ratio=float(latest.get("real_body_ratio") or 0),
                industry=str(latest.get("industry") or "") or None,
            )
        )
    return baselines


def backfill_post_bars(
    store: Any,
    *,
    up_to_date: str,
    max_age_days: int = 30,
) -> dict[str, Any]:
    """Job 4 主入口：把 pending/tracking 事件的 T+1..T+5 从日线补齐。

    幂等：`append_post_bar` 会拒绝重复 n；同一天多次跑不会重复写入。
    超龄（> max_age_days 自然日）的事件由 `find_events_needing_backfill`
    自动置 `expired` 并跳过。

    步骤：
        1. 拿出所有需回填事件（顺带把超龄置 expired）
        2. 按事件涉及的 ticker 一次性批量拉 Mongo 日线（`start_date=最早 breakout_date`）
        3. 对每个事件，取 breakout_date 之后升序的日线，依次填 T+1..T+5（跳过已存在的 n）

    返回 {scanned, appended, settled, expired_after_scan}。
    """
    collection = store.collection(COLLECTION_NAME)
    events = find_events_needing_backfill(collection, up_to_date=up_to_date, max_age_days=max_age_days)
    if not events:
        return {
            "up_to_date": up_to_date,
            "scanned": 0,
            "appended": 0,
            "settled": 0,
        }

    tickers = sorted({str(event["ticker"]) for event in events})
    earliest_breakout = min(str(event["breakout_date"]) for event in events)
    bars = store.raw_market.find_daily_bars(
        start_date=earliest_breakout,
        end_date=up_to_date,
        tickers=tickers,
    )

    # 按 ticker → 按日期升序的 dict，便于 O(1) 定位 breakout_date 之后的交易日
    bars_by_ticker: dict[str, list[dict]] = {}
    for bar in bars:
        ticker = str(bar.get("ticker") or "")
        date_str = str(bar.get("date") or "")
        if not ticker or not date_str:
            continue
        bars_by_ticker.setdefault(ticker, []).append(bar)
    for ticker_bars in bars_by_ticker.values():
        ticker_bars.sort(key=lambda b: str(b.get("date") or ""))

    appended_total = 0
    settled_total = 0
    for event in events:
        ticker = str(event["ticker"])
        breakout_date = str(event["breakout_date"])
        ticker_bars = bars_by_ticker.get(ticker) or []
        # breakout_date 之后（严格大于）的所有交易日，升序
        post_dates = [b for b in ticker_bars if str(b.get("date") or "") > breakout_date]
        if not post_dates:
            continue
        existing_ns = {int(b.get("n", 0)) for b in (event.get("post_bars") or [])}
        for n, bar in enumerate(post_dates[:TRACK_DAYS], start=1):
            if n in existing_ns:
                continue
            post_bar = PostBreakoutBar(
                date=str(bar.get("date") or ""),
                n=n,
                close=float(bar.get("close") or 0),
                low=float(bar.get("low") or 0),
                high=float(bar.get("high") or 0),
                volume=float(bar.get("volume") or bar.get("vol") or 0),
            )
            if append_post_bar(collection, ticker, breakout_date, post_bar):
                appended_total += 1
        # 重新读一次判断是否已 settled
        refreshed = get_breakout_event(collection, ticker, breakout_date)
        if refreshed and refreshed.get("status") == "settled":
            settled_total += 1

    return {
        "up_to_date": up_to_date,
        "scanned": len(events),
        "appended": appended_total,
        "settled": settled_total,
    }


def precompute_breakout_events(
    store: Any,
    analysis_date: str,
    *,
    lookback_days: int = 90,
    exclude_st: bool = True,
) -> dict[str, Any]:
    """上层入口：从 Mongo 读全市场日线 → 预处理 → 检测 → 落库。

    Job 8 的 API 端点直接调这个函数即可。
    返回 {inserted, existing, detected, analysis_date, lookback_days}。
    """
    import pandas as pd
    from datetime import datetime, timedelta

    from shilun.market.sector import _prepare_stock_frame

    target_dt = datetime.strptime(analysis_date, "%Y-%m-%d")
    start_date = (target_dt - timedelta(days=max(90, int(lookback_days)))).strftime("%Y-%m-%d")

    market_records = store.raw_market.find_daily_bars(start_date=start_date, end_date=analysis_date)
    from shilun.market.taxonomy import classify_stocks
    stock_basic = pd.DataFrame(classify_stocks(store, analysis_date, store.raw_market.find_stock_basic()))
    market_bars = pd.DataFrame(market_records)
    if market_bars.empty:
        return {
            "analysis_date": analysis_date,
            "detected": 0,
            "inserted": 0,
            "existing": 0,
            "message": "No market bars available.",
        }
    stock_frame = _prepare_stock_frame(
        market_bars,
        stock_basic=stock_basic,
        daily_basic=None,
        moneyflow=None,
        exclude_st=exclude_st,
    )
    baselines = detect_daily_breakouts(stock_frame, analysis_date)
    if not baselines:
        return {
            "analysis_date": analysis_date,
            "detected": 0,
            "inserted": 0,
            "existing": 0,
            "message": "No breakout detected on this date.",
        }
    collection = store.collection(COLLECTION_NAME)
    counts = bulk_insert_baselines(collection, baselines)
    return {
        "analysis_date": analysis_date,
        "detected": len(baselines),
        "inserted": counts.get("inserted", 0),
        "existing": counts.get("existing", 0),
        "lookback_days": lookback_days,
    }
