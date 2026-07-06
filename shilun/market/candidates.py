from __future__ import annotations

from typing import Any

import pandas as pd

from shilun.market.ma5_features import (
    build_ma_features,
    build_trade_plan,
    compute_final_trade_score,
    compute_risk_adjustment,
    compute_stock_quality_score,
    compute_trade_timing_score,
)

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


def _active_buy_label(ratio: float, structure: float) -> str:
    """给主动买入结构一个人类可读标签。

    ratio: 外盘占比（主动买入 / 全部主动），0.5=平衡
    structure: 主力主动占比 - 散户主动占比，> 0 = 主力主导
    """
    if not ratio:
        return "无资金流数据"
    if ratio > 0.55 and structure > 0.1:
        return "主力强势买入"
    if ratio > 0.55 and structure < -0.05:
        return "散户情绪追涨"
    if ratio > 0.55:
        return "多空主动买入"
    if ratio < 0.45 and structure < -0.05:
        return "主力砸盘"
    if ratio < 0.45:
        return "被主动打压"
    return "多空平衡"


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


def _build_trading_levels(bars: list[dict[str, Any]], signal: str) -> dict[str, Any]:
    """为 MA5 趋势战法输出可执行价位，避免只展示形态分。"""
    latest = bars[-1] if bars else {}
    close = _f(latest.get("close"))
    ma5 = _f(latest.get("ma5"))
    ma8 = _f(latest.get("ma8"))
    ma10 = _f(latest.get("ma10"))
    ma20 = _f(latest.get("ma20"))
    highs = [_f(b.get("high") or b.get("close")) for b in bars if _f(b.get("high") or b.get("close")) > 0]
    lows = [_f(b.get("low") or b.get("close")) for b in bars if _f(b.get("low") or b.get("close")) > 0]
    recent_high = max(highs) if highs else close
    recent_low = min(lows[-10:]) if lows else close

    support_candidates = [
        ("MA8", ma8),
        ("MA5", ma5),
        ("MA10", ma10),
        ("MA20", ma20),
        ("10日低点", recent_low),
    ]
    below_supports = [(label, value) for label, value in support_candidates if value > 0 and value <= close * 1.01]
    support_label, support_price = max(below_supports, key=lambda item: item[1]) if below_supports else ("MA5", ma5 or close)

    pressure_price = recent_high if recent_high > close * 1.01 else close * 1.08
    pressure_label = "20日高点" if recent_high > close * 1.01 else "8%目标位"

    if signal == "pullback_to_ma5":
        buy_price = max(support_price, min(close, (ma5 or close) * 1.01))
        buy_label = "回踩确认买点"
    elif signal == "breakout_confirm":
        buy_price = close
        buy_label = "突破确认买点"
    elif signal == "gentle_rise":
        buy_price = max(ma5 or close, ma8 or 0)
        buy_label = "放量确认买点"
    else:
        buy_price = support_price
        buy_label = "观察低吸买点"

    stop_loss = support_price * 0.98 if support_price else (ma5 * 0.98 if ma5 else None)
    expected_sell = pressure_price if pressure_price > buy_price * 1.02 else buy_price * 1.08
    risk_reward = None
    if stop_loss and buy_price > stop_loss:
        risk_reward = (expected_sell - buy_price) / (buy_price - stop_loss)

    return {
        "predicted_buy_price": round(buy_price, 2) if buy_price else None,
        "predicted_buy_label": buy_label,
        "support_price": round(support_price, 2) if support_price else None,
        "support_source": support_label,
        "pressure_price": round(pressure_price, 2) if pressure_price else None,
        "pressure_source": pressure_label,
        "ma8": round(ma8, 2) if ma8 else None,
        "expected_sell_price": round(expected_sell, 2) if expected_sell else None,
        "expected_sell_label": "先看压力位，若放量突破再抬高止盈",
        "risk_reward_ratio": round(risk_reward, 2) if risk_reward is not None else None,
    }


def _apply_market_gate(candidate: dict[str, Any], gate: dict[str, Any] | None) -> dict[str, Any]:
    """把 market_gate 应用到单只候选上，输出降级后的信号。

    始终保留原始信号（original_signal），只覆盖 final_signal / final_signal_label 和
    仓位相关字段。不删除候选，只降级。
    """
    original_signal = candidate.get("signal", "watch")
    original_signal_label = candidate.get("signal_label", "仅关注")
    candidate["original_signal"] = original_signal
    candidate["original_signal_label"] = original_signal_label

    if not gate:
        candidate["final_signal"] = original_signal
        candidate["final_signal_label"] = original_signal_label
        candidate["gate_downgraded"] = False
        candidate["gate_downgrade_reason"] = None
        candidate["size_hint"] = 0.5  # 无 gate 时默认半仓保守处理
        candidate["allowed_new_position"] = True
        candidate["market_multiplier"] = 1.0
        return candidate

    downgrade_map = gate.get("signal_downgrade_map", {})
    default_final = downgrade_map.get(original_signal, original_signal)

    # 高质量例外：某些 gate 允许极高质量信号绕过降级
    exception = gate.get("high_quality_exception") or {}
    entry_quality = candidate.get("entry_quality", 0) or 0
    exception_applies = False
    if exception.get("enabled"):
        min_q = exception.get("min_entry_quality", 999)
        allowed = exception.get("allowed_signals", [])
        if entry_quality >= min_q and original_signal in allowed:
            exception_applies = True

    # 决定 final_signal
    if exception_applies:
        final_signal = original_signal
        final_signal_label = original_signal_label
        gate_downgraded = False
        gate_downgrade_reason = f"高质量例外（entry_quality={entry_quality} ≥ {exception.get('min_entry_quality')}），仓位受限"
        size_hint = exception.get("override_size_hint", 0.3)
    else:
        final_signal = default_final
        final_signal_label = _SIGNAL_LABELS.get(final_signal, final_signal)
        gate_downgraded = final_signal != original_signal
        gate_downgrade_reason = gate.get("gate_reason") if gate_downgraded else None
        size_hint = gate.get("size_hint", 0.5)

    candidate["final_signal"] = final_signal
    candidate["final_signal_label"] = final_signal_label
    candidate["gate_downgraded"] = gate_downgraded
    candidate["gate_downgrade_reason"] = gate_downgrade_reason
    candidate["size_hint"] = size_hint
    candidate["allowed_new_position"] = bool(gate.get("allow_new_position", True)) or exception_applies
    candidate["market_multiplier"] = gate.get("market_multiplier", 1.0)
    return candidate


def build_candidates(
    top_sectors: list[dict[str, Any]],
    stock_frame: pd.DataFrame,
    analysis_date: str,
    market_gate: dict[str, Any] | None = None,
    trend_sectors: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """
    从 top_sectors 的 leader_candidates + zhongjun_candidates 中筛出 MA5 趋势战法候选。
    stock_frame 已含 ma5/ma10/ma20（由 sector._prepare_stock_frame() 计算）。

    market_gate 由 PART1 输出，用于降级候选的信号（不删除候选，只把 signal 映射到
    降级后的 final_signal，并提供 size_hint / allowed_new_position 等机器可执行字段）。
    """
    if stock_frame.empty:
        return []

    frame = stock_frame.copy()
    if "volume_ma5" not in frame.columns:
        frame["volume_ma5"] = (
            frame.groupby("ticker", group_keys=False)["volume"]
            .transform(lambda s: s.rolling(5, min_periods=1).mean())
        )
    if "ma8" not in frame.columns:
        frame["ma8"] = (
            frame.groupby("ticker", group_keys=False)["close"]
            .transform(lambda s: s.rolling(8, min_periods=1).mean())
        )

    trend_rank: dict[str, dict[str, Any]] = {}
    for rank, trend in enumerate(trend_sectors or [], start=1):
        name = str(trend.get("sector_name") or "")
        if name:
            trend_rank[name] = {
                "trend_rank": rank,
                "trend_score": _f(trend.get("trend_score")),
                "sector_mainline_score": _f(trend.get("sector_mainline_score") or trend.get("trend_score")),
                "trend_label": trend.get("trend_label") or "",
                "sector_state": trend.get("sector_state") or "",
                "sector_state_label": trend.get("sector_state_label") or "",
                "sector_multiplier": trend.get("sector_multiplier"),
                "retreat_flag": bool(trend.get("retreat_flag")),
                "resonance_score": 0.0,  # 稍后从 top_sectors 补
            }

    # 从 top_sectors 里补全周期字段；trend_sectors 现在是 60 日主线榜，
    # top_sectors 则携带当天龙头/中军深度评分。
    for sector in top_sectors:
        name = str(sector.get("sector_name") or "")
        if not name:
            continue
        resonance = _f((sector.get("metrics") or {}).get("resonance_score"))
        sector_multiplier = sector.get("sector_multiplier") or (sector.get("metrics") or {}).get("sector_multiplier")
        sector_mainline_score = sector.get("sector_mainline_score") or (sector.get("scores") or {}).get("sector_mainline_score")
        if name in trend_rank:
            trend_rank[name]["resonance_score"] = resonance
            if sector_multiplier is not None:
                trend_rank[name]["sector_multiplier"] = sector_multiplier
            if sector_mainline_score is not None:
                trend_rank[name]["sector_mainline_score"] = _f(sector_mainline_score)
            trend_rank[name]["sector_state"] = sector.get("sector_state") or trend_rank[name].get("sector_state", "")
            trend_rank[name]["sector_state_label"] = sector.get("sector_state_label") or trend_rank[name].get("sector_state_label", "")
            trend_rank[name]["retreat_flag"] = bool(sector.get("retreat_flag") or trend_rank[name].get("retreat_flag"))
        else:
            # top_sectors 里的板块不在 trend_sectors 里，也建条目
            trend_rank[name] = {
                "trend_rank": 999,
                "trend_score": 0.0,
                "sector_mainline_score": _f(sector_mainline_score),
                "trend_label": "",
                "sector_state": sector.get("sector_state") or "",
                "sector_state_label": sector.get("sector_state_label") or "",
                "sector_multiplier": sector_multiplier,
                "retreat_flag": bool(sector.get("retreat_flag")),
                "resonance_score": resonance,
            }

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
        feature_rows = ticker_frame[ticker_frame["ticker"] == ticker].tail(140)
        if feature_rows.empty:
            continue

        cols = [
            col for col in ("date", "open", "high", "low", "close", "ma5", "ma8", "ma10", "ma20", "volume", "volume_ma5",
                             "pct_chg", "atr_14", "atr_pct_14", "median_abs_return_20d", "volume_ratio_20", "amount_ratio_60",
                             "range_ratio_20", "volume_percentile_120", "return_percentile_120", "extension_percentile_120",
                             "close_position", "real_body_ratio", "upper_shadow_ratio", "lower_shadow_ratio",
                             "active_buy_ratio", "active_buy_structure", "main_active_buy", "retail_active_buy")
            if col in feature_rows.columns
        ]
        feature_bars = feature_rows[cols].to_dict("records")
        signal_bars = feature_rows.tail(20)[cols].to_dict("records")
        sig = detect_ma5_signal(signal_bars)

        latest = feature_bars[-1]
        ma5_val = _f(latest.get("ma5"))
        ma8_val = _f(latest.get("ma8"))
        close_val = _f(latest.get("close"))
        # 主动买入分析（当日）
        active_buy_ratio = _f(latest.get("active_buy_ratio"))
        active_buy_structure = _f(latest.get("active_buy_structure"))
        sector_name = candidate.get("sector_name") or candidate.get("_sector_name", "")
        trend_info = trend_rank.get(str(sector_name), {})
        trend_score = _f(trend_info.get("trend_score"))
        mainline_score = _f(trend_info.get("sector_mainline_score") or trend_score)
        trend_boost = min(20.0, mainline_score * 0.14)
        # 板块乘数 v3：由 PART2 60 日主线状态输出，退潮/分歧会直接降权。
        resonance = _f(trend_info.get("resonance_score"))
        sector_multiplier = trend_info.get("sector_multiplier")
        if sector_multiplier is None:
            sector_multiplier = 1.0 + min(0.3, resonance / 100.0 * 0.3)
        sector_multiplier = _f(sector_multiplier)
        trading_levels = _build_trading_levels(signal_bars, sig["signal"])
        ma5_features = build_ma_features(feature_bars)
        trade_plan = build_trade_plan(ma5_features)
        stock_quality = compute_stock_quality_score(
            ma5_features,
            sector_score=mainline_score or trend_score or resonance or 50.0,
        )
        trade_timing = compute_trade_timing_score(ma5_features)
        risk_adjustment = compute_risk_adjustment(
            ma5_features,
            market_gate=market_gate,
            sector_state=str(trend_info.get("sector_state") or ""),
        )

        results.append({
            "ticker": ticker,
            "name": candidate.get("name", ticker),
            "sector_name": sector_name,
            "sector_rank": candidate.get("_sector_rank", 99),
            "role_label": candidate.get("role_label", ""),
            "close": round(close_val, 2),
            "ma5": round(ma5_val, 2),
            "ma8": round(ma8_val, 2) if ma8_val else None,
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
            "entry_price": trading_levels["predicted_buy_price"],
            "predicted_buy_price": trading_levels["predicted_buy_price"],
            "predicted_buy_label": trading_levels["predicted_buy_label"],
            "support_price": trading_levels["support_price"],
            "support_source": trading_levels["support_source"],
            "pressure_price": trading_levels["pressure_price"],
            "pressure_source": trading_levels["pressure_source"],
            "expected_sell_price": trading_levels["expected_sell_price"],
            "expected_sell_label": trading_levels["expected_sell_label"],
            "risk_reward_ratio": trading_levels["risk_reward_ratio"],
            "stop_loss": round((trading_levels["support_price"] or ma5_val) * 0.98, 2) if (trading_levels["support_price"] or ma5_val) else None,
            "reason": sig["reason"],
            "leader_score": candidate.get("leader_score"),
            "return_5d": candidate.get("return_5d"),
            "trend_rank": trend_info.get("trend_rank"),
            "trend_score": round(trend_score, 2) if trend_info else None,
            "sector_mainline_score": round(mainline_score, 2) if trend_info else None,
            "trend_label": trend_info.get("trend_label"),
            "trend_boost": round(trend_boost, 2) if trend_info else 0,
            "sector_multiplier": round(sector_multiplier, 3),
            "sector_state": trend_info.get("sector_state"),
            "sector_state_label": trend_info.get("sector_state_label"),
            "sector_retreat_flag": bool(trend_info.get("retreat_flag")),
            "sector_resonance_score": round(resonance, 1) if trend_info else 0,
            # 主动买入结构（v2）
            "active_buy_ratio": round(active_buy_ratio, 4) if active_buy_ratio else None,
            "active_buy_structure": round(active_buy_structure, 4) if active_buy_structure else None,
            "active_buy_label": _active_buy_label(active_buy_ratio, active_buy_structure),
            # MA5 v0.2 三分制评分（股票质量 != 买点质量）
            "stock_quality_score": stock_quality["score"],
            "trade_timing_score": trade_timing["score"],
            "risk_adjustment": risk_adjustment["score"],
            "buy_point_type": trade_timing["buy_point_type"],
            "buy_point_label": trade_timing["buy_point_label"],
            "score_breakdown": {
                "stock_quality_score": stock_quality,
                "trade_timing_score": trade_timing,
                "risk_adjustment": risk_adjustment,
                "formula": "final_trade_score = trade_timing_score * market_multiplier * sector_multiplier * risk_adjustment",
            },
            "ma5_feature_snapshot": {
                "close_ma5_ratio": ma5_features.get("close_ma5_ratio"),
                "ma5_slope_3d": ma5_features.get("ma5_slope_3d"),
                "pullback_depth": ma5_features.get("pullback_depth"),
                "pullback_to_ma5_distance": ma5_features.get("pullback_to_ma5_distance"),
                "volume_ratio_20": ma5_features.get("volume_ratio_20"),
                "close_position": ma5_features.get("close_position"),
                "real_body_ratio": ma5_features.get("real_body_ratio"),
                "atr_pct_14": ma5_features.get("atr_pct_14"),
                "dynamic_tolerance": ma5_features.get("dynamic_tolerance"),
            },
            "trade_plan": trade_plan,
        })

    # Layer 1b：对每只候选应用 market_gate，得出 final_signal + size_hint
    for r in results:
        _apply_market_gate(r, market_gate)
        r["final_trade_score"] = compute_final_trade_score(
            r.get("trade_timing_score", 0),
            market_multiplier=_f(r.get("market_multiplier", 1.0)) or 1.0,
            sector_multiplier=_f(r.get("sector_multiplier", 1.0)) or 1.0,
            risk_adjustment=_f(r.get("risk_adjustment", 1.0)) or 1.0,
        )

    # v4 排序：MA5 v0.2 final_trade_score 优先；旧 entry_quality 作为兜底兼容。
    # market_multiplier 来自大盘闸门（进攻/持有/防守/空仓）
    # sector_multiplier 来自 PART2 主线状态，退潮/分歧硬降权。
    def _final_score(x: dict[str, Any]) -> float:
        if x.get("final_trade_score") is not None:
            return _f(x.get("final_trade_score")) + _f(x.get("trend_boost", 0)) * 0.25
        return _f(x.get("entry_quality", 0)) * _f(x.get("market_multiplier", 1.0)) * _f(x.get("sector_multiplier", 1.0)) + _f(x.get("trend_boost", 0))

    results.sort(key=lambda x: (
        -_final_score(x),
        x.get("trend_rank") or 999,
        _SIGNAL_PRIORITY.get(x.get("final_signal", "watch"), 9),
        -_f(x.get("leader_score")),
    ))
    return results
