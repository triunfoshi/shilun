"""分时强度契合（v6）计算器。

用户口径（原话）：
    单日交易里，分时很重要。你可以看三个时间点：
      - 10:00 前：是否跟随大盘开盘方向
      - 午后 13:30 - 14:00：大盘二次上攻/杀跌时，个股是否同步
      - 尾盘 14:30 后：大盘拉升/跳水时，个股是否同步放量

    强共振分时一般是：
      - 大盘上攻 → 个股几乎同步上攻
      - 大盘回落 → 个股抗跌
      - 大盘二次拉升 → 个股放量创新高

    伪共振分时一般是：
      - 大盘拉升时个股不动
      - 大盘回落时个股先跌
      - 尾盘被动拉红但全天均价线下方运行

本模块把这些口径量化：把交易日切成 5 个时段，对每段独立评分，
最后按权重合成 `intraday_resonance_score` (0-100)。

时段定义（5min K 线组合）：
    early_open    09:30-10:00   跟随大盘开盘方向         权重 25%
    morning       10:00-11:30   中段跟随度              权重 20%
    afternoon_open 13:00-13:30  二次启动                权重 15%
    afternoon_mid  13:30-14:00  大盘二次上攻/杀跌同步    权重 20%
    tail          14:30-15:00   尾盘同步放量           权重 20%

每段评分：
    - 反向（个股 pct 与大盘 pct 方向相反）：      20 分
    - 同向 + 强度 < 大盘 * 0.8（弱跟随）：       55 分
    - 同向 + 强度 ≈ 大盘 [0.8, 1.2]：            80 分
    - 同向 + 强度 > 大盘 * 1.2（强共振）：      100 分
    - 无数据（该时段个股或大盘 bar 缺失）：      50 分（中性）

尾盘段单独看放量：个股尾盘平均成交量 / 全天平均成交量，> 1.0 说明尾盘活跃。
"""

from __future__ import annotations

from datetime import time
from typing import Any

# 五时段定义（左闭右开）
_SEGMENTS: list[tuple[str, time, time, float]] = [
    ("early_open",     time(9, 30),  time(10, 0),  0.25),
    ("morning",        time(10, 0),  time(11, 30), 0.20),
    ("afternoon_open", time(13, 0),  time(13, 30), 0.15),
    ("afternoon_mid",  time(13, 30), time(14, 0),  0.20),
    ("tail",           time(14, 30), time(15, 0),  0.20),
]


def _parse_dt(dt: Any):
    """把 "2026-07-09T09:35:00" 或 datetime 对象转成 datetime。失败返 None。"""
    import datetime as _dt
    if hasattr(dt, "time"):
        return dt
    if not dt:
        return None
    try:
        return _dt.datetime.fromisoformat(str(dt).replace("Z", ""))
    except (ValueError, TypeError):
        return None


def _segment_pct_and_volume(bars: list[dict[str, Any]], start: time, end: time) -> tuple[float | None, float]:
    """返回时段内的涨幅（末 close / 首 open - 1）和总成交量。

    - 涨幅 None 表示时段内 bar 太少（0 或 1 根），无法算涨幅
    - 成交量若无数据返 0
    """
    picked = []
    for bar in bars:
        dt = _parse_dt(bar.get("datetime"))
        if dt is None:
            continue
        t = dt.time()
        if start <= t < end:
            picked.append(bar)
    if len(picked) < 2:
        # 只有 1 根 bar 时用它自己算 pct_chg（用 close/open-1 单根近似）
        if len(picked) == 1:
            b = picked[0]
            open_v = _num(b.get("open"))
            close_v = _num(b.get("close"))
            vol = _num(b.get("volume")) or 0.0
            if open_v and close_v:
                return (close_v / open_v - 1.0, vol)
            return (None, vol)
        return (None, 0.0)
    first_open = _num(picked[0].get("open"))
    last_close = _num(picked[-1].get("close"))
    total_vol = sum(_num(b.get("volume")) or 0.0 for b in picked)
    if not first_open or not last_close:
        return (None, total_vol)
    return (last_close / first_open - 1.0, total_vol)


def _num(v: Any) -> float | None:
    try:
        n = float(v)
    except (TypeError, ValueError):
        return None
    import math
    if math.isnan(n) or math.isinf(n):
        return None
    return n


def _score_segment(stock_pct: float | None, benchmark_pct: float | None) -> float:
    """单个时段评分：见模块 docstring。"""
    if stock_pct is None or benchmark_pct is None:
        return 50.0  # 中性
    # 大盘涨/跌方向
    bench_dir = 1 if benchmark_pct > 0 else -1 if benchmark_pct < 0 else 0
    stock_dir = 1 if stock_pct > 0 else -1 if stock_pct < 0 else 0
    # 两者都平（震荡）
    if bench_dir == 0 and stock_dir == 0:
        return 70.0
    # 大盘平但个股动：算中性偏正
    if bench_dir == 0:
        return 60.0
    # 方向不同 → 反向
    if stock_dir != bench_dir:
        # 但大盘跌、个股涨（抗跌上涨）算强共振
        if bench_dir == -1 and stock_dir == 1:
            return 100.0
        # 其他反向情形（大盘涨、个股跌 / 大盘跌、个股跟跌得更狠）都算差
        return 20.0
    # 方向相同 → 比强度
    abs_bench = abs(benchmark_pct) or 1e-6
    ratio = abs(stock_pct) / abs_bench
    if ratio < 0.8:
        return 55.0
    if ratio <= 1.2:
        return 80.0
    return 100.0


def _tail_volume_boost(stock_bars: list[dict[str, Any]], tail_vol: float) -> float:
    """尾盘放量加分：
       尾盘时段总成交量 / 全天平均单时段成交量。
       > 1.5 加 15 分；> 1.0 加 8 分；否则 0。
    """
    total = 0.0
    seg_count = 0
    for name, start, end, _ in _SEGMENTS:
        v = 0.0
        for b in stock_bars:
            dt = _parse_dt(b.get("datetime"))
            if dt and start <= dt.time() < end:
                v += _num(b.get("volume")) or 0.0
        total += v
        if v > 0:
            seg_count += 1
    if not seg_count:
        return 0.0
    avg = total / seg_count
    if avg <= 0:
        return 0.0
    ratio = tail_vol / avg
    if ratio >= 1.5:
        return 15.0
    if ratio >= 1.0:
        return 8.0
    return 0.0


def compute_intraday_resonance_score(
    stock_bars: list[dict[str, Any]] | None,
    benchmark_bars: list[dict[str, Any]] | None,
) -> dict[str, Any]:
    """主入口：给一只票的分钟 K 线 + 大盘分钟 K 线，输出分时共振结果。

    返回:
        {
            score: float 0-100,
            segments: {
                early_open: {stock_pct, benchmark_pct, score},
                ...
            },
            tail_volume_ratio: float,   # 尾盘成交量 / 全天平均
            has_data: bool              # 若无股票分钟数据则 False，score 退化为 50
        }
    """
    if not stock_bars or not benchmark_bars:
        return {
            "score": 50.0,
            "segments": {},
            "tail_volume_ratio": None,
            "valid_segment_count": 0,
            "required_segment_count": 4,
            "has_data": False,
        }

    segments_out: dict[str, dict[str, Any]] = {}
    weighted_sum = 0.0
    weight_total = 0.0
    tail_vol = 0.0
    valid_segment_count = 0
    for name, start, end, weight in _SEGMENTS:
        stock_pct, stock_vol = _segment_pct_and_volume(stock_bars, start, end)
        bench_pct, _ = _segment_pct_and_volume(benchmark_bars, start, end)
        if stock_pct is not None and bench_pct is not None:
            valid_segment_count += 1
        seg_score = _score_segment(stock_pct, bench_pct)
        segments_out[name] = {
            "stock_pct": round(stock_pct, 4) if stock_pct is not None else None,
            "benchmark_pct": round(bench_pct, 4) if bench_pct is not None else None,
            "score": round(seg_score, 2),
        }
        weighted_sum += seg_score * weight
        weight_total += weight
        if name == "tail":
            tail_vol = stock_vol

    base_score = weighted_sum / weight_total if weight_total else 50.0
    volume_boost = _tail_volume_boost(stock_bars, tail_vol)
    final_score = max(0.0, min(100.0, base_score + volume_boost))
    # 正式排名不能把“一两根零散 K 线”当成完整分钟事实。五个关键时段
    # 至少四段同时具备个股和基准涨幅，才允许 has_data=True；否则保留
    # 诊断明细，但上游严格资格门会把该票留在待补区而不是正式前三。
    has_data = valid_segment_count >= 4
    if not has_data:
        final_score = 50.0

    # 尾盘放量比率（供前端展示）
    total_vol = sum(_num(b.get("volume")) or 0.0 for b in stock_bars)
    tail_ratio = None
    if total_vol > 0 and tail_vol > 0:
        tail_ratio = tail_vol / (total_vol / max(1, len(_SEGMENTS)))

    return {
        "score": round(final_score, 2),
        "segments": segments_out,
        "tail_volume_ratio": round(tail_ratio, 4) if tail_ratio is not None else None,
        "volume_boost": round(volume_boost, 2),
        "valid_segment_count": valid_segment_count,
        "required_segment_count": 4,
        "has_data": has_data,
    }
