from __future__ import annotations

from typing import Any

import pandas as pd

_SIGNAL_PRIORITY = {
    "breakout_confirm": 0,
    "pullback_to_ma5": 1,
    "gentle_rise": 2,
    "watch": 3,
}

_SIGNAL_LABELS = {
    "breakout_confirm": "突破确认",
    "pullback_to_ma5": "回调突破",
    "gentle_rise": "缩量上涨",
    "watch": "仅关注",
}


def _f(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _calc_rsi(closes: list[float], period: int = 14) -> float:
    if len(closes) < period + 1:
        return 50.0
    gains, losses = [], []
    for i in range(1, len(closes)):
        delta = closes[i] - closes[i - 1]
        if delta > 0:
            gains.append(delta)
            losses.append(0.0)
        else:
            gains.append(0.0)
            losses.append(-delta)
    avg_gain = sum(gains[-period:]) / period
    avg_loss = sum(losses[-period:]) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return round(100 - 100 / (1 + rs), 2)


def _ma20_slope_up(bars: list[dict[str, Any]]) -> bool:
    """MA20[today] > MA20[5日前]，确认主升段。"""
    if len(bars) < 6:
        return False
    return _f(bars[-1].get("ma20")) > _f(bars[-6].get("ma20"))


def detect_ma5_signal(bars: list[dict[str, Any]]) -> dict[str, Any]:
    """
    输入：按日期升序的近 20 根日线，每个 dict 含 close/ma5/ma10/ma20/volume/volume_ma5。
    输出：signal / signal_label / ma_aligned / ma20_slope_up / rsi / return_20d /
          entry_quality / risk_flags / reason。
    """
    _empty = {
        "signal": "watch", "signal_label": "仅关注", "ma_aligned": False,
        "ma20_slope_up": False, "rsi": 50.0, "return_20d": None,
        "entry_quality": 0, "risk_flags": [], "reason": "数据不足",
    }
    if len(bars) < 3:
        return _empty

    latest = bars[-1]
    prev = bars[-2]
    prev2 = bars[-3]

    close = _f(latest.get("close"))
    ma5 = _f(latest.get("ma5"))
    ma10 = _f(latest.get("ma10"))
    ma20 = _f(latest.get("ma20"))
    volume = _f(latest.get("volume"))
    volume_ma5 = _f(latest.get("volume_ma5")) or 1.0

    prev_close = _f(prev.get("close"))
    prev_ma5 = _f(prev.get("ma5"))
    prev_volume = _f(prev.get("volume"))

    prev2_close = _f(prev2.get("close"))
    prev2_ma5 = _f(prev2.get("ma5"))

    ma_aligned = ma5 > ma10 > ma20 and ma5 > 0
    slope_up = _ma20_slope_up(bars)

    closes = [_f(b.get("close")) for b in bars]
    rsi = _calc_rsi(closes)

    return_20d: float | None = None
    if len(bars) >= 20:
        old = _f(bars[-20].get("close"))
        if old > 0:
            return_20d = (close - old) / old
    elif len(bars) >= 2:
        old = _f(bars[0].get("close"))
        if old > 0:
            return_20d = (close - old) / old

    # 风险标记
    risk_flags: list[str] = []
    if volume > volume_ma5 * 1.2 and close < prev_close:
        risk_flags.append("量价背离")
    if rsi > 75 and return_20d is not None and return_20d > 0.35:
        risk_flags.append("高位过热")
    prev_pct = (prev_close - prev2_close) / (prev2_close or 1)
    if prev_pct >= 0.095 and close < prev_close:
        risk_flags.append("涨停炸板")

    base = {
        "ma_aligned": ma_aligned,
        "ma20_slope_up": slope_up,
        "rsi": rsi,
        "return_20d": return_20d,
        "risk_flags": risk_flags,
    }

    if not ma_aligned:
        return {**base, "signal": "watch", "signal_label": "仅关注",
                "entry_quality": 0, "reason": "均线未多头排列，暂不入场"}

    # 主升段检查
    if not slope_up:
        return {**base, "signal": "watch", "signal_label": "仅关注",
                "entry_quality": 5,
                "reason": f"多头排列但 MA20 未向上（非主升段），不入场。MA5 {ma5:.2f} > MA10 {ma10:.2f} > MA20 {ma20:.2f}。"}

    def _apply_rsi_penalty(q: int) -> int:
        if rsi > 75:
            q -= 15
        elif rsi > 70:
            q -= 8
        return max(0, min(100, q))

    # ── 形态 1：突破确认 ─────────────────────────────────────────────
    # 昨日首次突破 MA5（prev_close > prev_ma5, prev2_close <= prev2_ma5）
    # 涨幅 ≥ 3%，量能 ≥ 1.5 倍均量，今日不回破（close ≥ ma5 × 0.995）
    prev_broke_above = prev_close > prev_ma5 and prev2_close <= prev2_ma5
    prev_pct_chg = (prev_close - prev2_close) / (prev2_close or 1)
    prev_vol_ratio = prev_volume / volume_ma5 if volume_ma5 else 0
    today_holds = close >= ma5 * 0.995
    vol_ok = prev_vol_ratio >= 1.5
    big_candle = prev_pct_chg >= 0.03

    if prev_broke_above and today_holds and vol_ok and big_candle:
        q = 60 + 15  # base + 主升段
        q += min(10, int((prev_vol_ratio - 1.5) * 5))
        q = _apply_rsi_penalty(q)
        return {**base, "signal": "breakout_confirm",
                "signal_label": _SIGNAL_LABELS["breakout_confirm"],
                "entry_quality": q,
                "reason": (
                    f"昨日大阳突破 MA5（涨幅{prev_pct_chg*100:.1f}%，量比{prev_vol_ratio:.1f}x），"
                    f"今日收盘 {close:.2f} 持稳，主升段确认。"
                )}

    # ── 形态 2：回调突破 ─────────────────────────────────────────────
    # 近 8 根内曾高于 MA5×1.05（有阶段峰值），
    # 随后回踩至 MA5±2%，昨日缩量，今日收盘站上 MA5
    had_peak = any(
        _f(b.get("close")) >= _f(b.get("ma5")) * 1.05
        for b in bars[-8:]
        if _f(b.get("ma5")) > 0
    )
    prev2_near_ma5 = abs(prev2_close - prev2_ma5) / (prev2_ma5 or 1) <= 0.02
    near_ma5_today = abs(close - ma5) / (ma5 or 1) <= 0.02
    prev_shrink = prev_volume < volume_ma5 * 0.85
    today_above_ma5 = close > ma5

    if had_peak and (prev2_near_ma5 or near_ma5_today) and prev_shrink and today_above_ma5:
        q = 55 + 15  # base + 主升段
        shrink_ratio = prev_volume / volume_ma5 if volume_ma5 else 1
        q += max(0, int((0.85 - shrink_ratio) * 40))
        q = _apply_rsi_penalty(q)
        return {**base, "signal": "pullback_to_ma5",
                "signal_label": _SIGNAL_LABELS["pullback_to_ma5"],
                "entry_quality": q,
                "reason": (
                    f"回踩至 MA5（{ma5:.2f}）附近缩量，今日收盘 {close:.2f} 站上，"
                    f"真正买点：次日收盘确认。"
                )}

    # ── 形态 3：缩量上涨 ─────────────────────────────────────────────
    if len(bars) >= 3:
        last3 = bars[-3:]
        closes3 = [_f(b.get("close")) for b in last3]
        vols3 = [_f(b.get("volume")) for b in last3]
        rising = closes3[0] < closes3[1] < closes3[2]
        shrinking = vols3[0] > vols3[1] > vols3[2]
        converging = (ma5 - ma20) / (ma20 or 1) < 0.03
        if rising and shrinking and converging:
            q = _apply_rsi_penalty(40 + 15)
            return {**base, "signal": "gentle_rise",
                    "signal_label": _SIGNAL_LABELS["gentle_rise"],
                    "entry_quality": q,
                    "reason": (
                        f"连续 3 日缩量上涨，均线收拢（MA5 {ma5:.2f} / MA20 {ma20:.2f}），"
                        f"等待放量突破信号。"
                    )}

    # watch（多头排列 + 主升段，暂无形态）
    return {**base, "signal": "watch", "signal_label": "仅关注",
            "entry_quality": 20,
            "reason": (
                f"主升段多头排列（MA5 {ma5:.2f} > MA10 {ma10:.2f} > MA20 {ma20:.2f}），"
                f"暂无明确入场形态，持续关注。"
            )}


def build_candidates(
    top_sectors: list[dict[str, Any]],
    stock_frame: pd.DataFrame,
    analysis_date: str,
) -> list[dict[str, Any]]:
    """
    从 top_sectors 的 leader_candidates + zhongjun_candidates 中筛出 MA5 趋势战法候选。
    stock_frame 已含 ma5/ma10/ma20（由 sector._prepare_stock_frame() 计算）。
    """
    if stock_frame.empty:
        return []

    frame = stock_frame.copy()
    if "volume_ma5" not in frame.columns:
        frame["volume_ma5"] = (
            frame.groupby("ticker", group_keys=False)["volume"]
            .transform(lambda s: s.rolling(5, min_periods=1).mean())
        )

    seen: set[str] = set()
    raw_candidates: list[dict[str, Any]] = []
    for sector_rank, sector in enumerate(top_sectors):
        for pool_key in ("leader_candidates", "zhongjun_candidates"):
            for item in sector.get(pool_key) or []:
                ticker = item.get("ticker")
                if ticker and ticker not in seen:
                    seen.add(ticker)
                    raw_candidates.append({
                        **item,
                        "_sector_name": sector.get("sector_name", ""),
                        "_sector_rank": sector_rank,
                    })

    if not raw_candidates:
        return []

    all_tickers = [c["ticker"] for c in raw_candidates]
    ticker_frame = frame[frame["ticker"].isin(all_tickers)].sort_values(["ticker", "date"])

    results: list[dict[str, Any]] = []
    for candidate in raw_candidates:
        ticker = candidate["ticker"]
        rows = ticker_frame[ticker_frame["ticker"] == ticker].tail(20)
        if rows.empty:
            continue

        cols = ["date", "close", "ma5", "ma10", "ma20", "volume", "volume_ma5"]
        bars = rows[cols].to_dict("records")
        sig = detect_ma5_signal(bars)

        latest = bars[-1]
        ma5_val = _f(latest.get("ma5"))
        close_val = _f(latest.get("close"))

        results.append({
            "ticker": ticker,
            "name": candidate.get("name", ticker),
            "sector_name": candidate.get("sector_name") or candidate.get("_sector_name", ""),
            "sector_rank": candidate.get("_sector_rank", 99),
            "role_label": candidate.get("role_label", ""),
            "close": round(close_val, 2),
            "ma5": round(ma5_val, 2),
            "ma10": round(_f(latest.get("ma10")), 2),
            "ma20": round(_f(latest.get("ma20")), 2),
            "signal": sig["signal"],
            "signal_label": sig["signal_label"],
            "ma_aligned": sig["ma_aligned"],
            "ma20_slope_up": sig.get("ma20_slope_up", False),
            "rsi": sig.get("rsi"),
            "return_20d": sig.get("return_20d"),
            "entry_quality": sig.get("entry_quality", 0),
            "risk_flags": sig.get("risk_flags", []),
            "entry_price": round(close_val, 2),
            "stop_loss": round(ma5_val * 0.98, 2) if ma5_val else None,
            "reason": sig["reason"],
            "leader_score": candidate.get("leader_score"),
            "return_5d": candidate.get("return_5d"),
        })

    # 批次A：entry_quality 降序，signal 优先级兜底，leader_score 再兜底
    results.sort(key=lambda x: (
        -x.get("entry_quality", 0),
        _SIGNAL_PRIORITY.get(x["signal"], 9),
        -_f(x.get("leader_score")),
    ))
    return results
