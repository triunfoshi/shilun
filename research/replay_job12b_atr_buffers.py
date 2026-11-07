"""用本地真实日线和 5 分钟会话检查 Job 12B ATR 缓冲尺度。

本脚本不回测收益，也不把普通 5 分钟振幅等同于真实止损触发。它只回答
0.10/0.15/0.25 ATR 相对盘中噪声与全天振幅处在什么量级，为 Job 12H 的
逐场景历史回放保留一个可复算的前置基线。
"""

from __future__ import annotations

from collections import defaultdict
from statistics import median
from typing import Any

from pymongo import MongoClient

from shilun.common.config import load_config


FACTORS = (0.10, 0.15, 0.25)


def _number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _atr14(rows: list[dict[str, Any]]) -> float | None:
    if len(rows) < 15:
        return None
    true_ranges: list[float] = []
    for index, row in enumerate(rows):
        high = _number(row.get("high"))
        low = _number(row.get("low"))
        close = _number(row.get("close"))
        if high is None or low is None or close is None or high < low:
            return None
        if index == 0:
            true_ranges.append(high - low)
        else:
            previous_close = _number(rows[index - 1].get("close"))
            if previous_close is None:
                return None
            true_ranges.append(max(high - low, abs(high - previous_close), abs(low - previous_close)))
    value = sum(true_ranges[-14:]) / 14
    return value if value > 0 else None


def _percentile(values: list[float], quantile: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    position = (len(ordered) - 1) * quantile
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def build_report() -> str:
    config = load_config()
    if not config.mongo_uri:
        raise RuntimeError("SHILUN_MONGO_URI 未配置")
    client = MongoClient(config.mongo_uri)
    database = client[config.mongo_db]
    sessions = list(database["minute_bars_daily"].find(
        {"interval": "5"},
        {"_id": 0, "ticker": 1, "trade_date": 1, "bars": 1},
    ))
    tickers = sorted({str(item.get("ticker") or "") for item in sessions if item.get("ticker")})
    daily_by_ticker: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in database["market_daily_bars"].find(
        {"ticker": {"$in": tickers}},
        {"_id": 0, "ticker": 1, "date": 1, "high": 1, "low": 1, "close": 1},
    ):
        daily_by_ticker[str(row.get("ticker"))].append(row)
    for rows in daily_by_ticker.values():
        rows.sort(key=lambda item: str(item.get("date") or ""))

    samples: dict[float, dict[str, list[float]]] = {
        factor: defaultdict(list) for factor in FACTORS
    }
    valid_sessions = 0
    valid_bars = 0
    skipped = 0
    for session in sessions:
        ticker = str(session.get("ticker") or "")
        trade_date = str(session.get("trade_date") or "")
        if len(trade_date) != 8:
            skipped += 1
            continue
        date_text = f"{trade_date[:4]}-{trade_date[4:6]}-{trade_date[6:]}"
        prior_daily = [
            row for row in daily_by_ticker.get(ticker, [])
            if str(row.get("date") or "") < date_text
        ][-30:]
        atr14 = _atr14(prior_daily)
        bars = sorted(session.get("bars") or [], key=lambda item: str(item.get("datetime") or ""))
        bar_ranges = [
            high - low
            for bar in bars
            if (high := _number(bar.get("high"))) is not None
            and (low := _number(bar.get("low"))) is not None
            and high >= low
        ]
        closes = [value for bar in bars if (value := _number(bar.get("close"))) is not None]
        if atr14 is None or not bar_ranges or not closes:
            skipped += 1
            continue
        session_range = max(
            _number(bar.get("high")) or 0 for bar in bars
        ) - min(
            _number(bar.get("low")) or float("inf") for bar in bars
        )
        if session_range <= 0:
            skipped += 1
            continue
        reference_price = median(closes)
        median_bar_range = median(bar_ranges)
        valid_sessions += 1
        valid_bars += len(bar_ranges)
        for factor in FACTORS:
            buffer_value = factor * atr14
            bucket = samples[factor]
            bucket["price_pct"].append(buffer_value / reference_price)
            bucket["bar_range_multiple"].append(
                buffer_value / median_bar_range if median_bar_range > 0 else 0
            )
            bucket["session_range_share"].append(buffer_value / session_range)
            bucket["bar_range_cross_rate"].append(
                sum(value >= buffer_value for value in bar_ranges) / len(bar_ranges)
            )

    if not valid_sessions:
        client.close()
        raise RuntimeError("没有可用于 ATR 缓冲检查的有效 5 分钟会话")
    lines = [
        "# Job 12B ATR 缓冲真实数据尺度检查",
        "",
        f"- 有效会话：{valid_sessions}",
        f"- 有效 5 分钟 K：{valid_bars}",
        f"- 跳过会话：{skipped}",
        "",
        "| ATR 系数 | 缓冲/股价中位数 | 缓冲/5分钟振幅中位数 P50（P25–P75） | 单根振幅≥缓冲占比中位数 | 缓冲/全天振幅中位数 |",
        "|---:|---:|---:|---:|---:|",
    ]
    for factor in FACTORS:
        bucket = samples[factor]
        multiples = bucket["bar_range_multiple"]
        lines.append(
            f"| {factor:.2f} | {median(bucket['price_pct']) * 100:.3f}% | "
            f"{median(multiples):.2f}（{_percentile(multiples, 0.25):.2f}–{_percentile(multiples, 0.75):.2f}） | "
            f"{median(bucket['bar_range_cross_rate']) * 100:.1f}% | "
            f"{median(bucket['session_range_share']) * 100:.1f}% |"
        )
    lines.extend([
        "",
        "说明：这是尺度基线，不是收益回测；场景锚点、触发顺序、滑点和成交结果需在 Job 12H 分场景验证。",
    ])
    client.close()
    return "\n".join(lines)


if __name__ == "__main__":
    print(build_report())
