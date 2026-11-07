"""PRD §4.7 五买点体系 · 形态识别函数（Job B）。

跟指标层 `ma5_features` 的三大买点（ma5_pullback / ma5_breakout / ma5_reclaim）
并存。形态层负责用户表达 —— 每张候选卡对外展示的分类标签就来自这里，评分依然
用指标层，不受本模块影响。

五个买点（用户口径）：

    突破点 tu_po      向上突破 MA5 的第一根阳线，v0.2 第八章有效突破规则
    起涨点 qi_zhang   谷底之后或回踩完毕，阳克阴的第一根阳线
    回踩点 hui_cai    有效突破后上涨中回踩 MA5 时的那根 K
    抄底点 chao_di    MA5 下方 + 局部谷底 + MA7 确认 + MACD 走 W 形态
    追涨点 zhui_zhang 起涨/突破之后上涨中的任一根 K（谨慎使用）

判定优先级：`tu_po > qi_zhang > hui_cai > chao_di > zhui_zhang`。理由见 PRD §4.7
"优先级顺序解释"。识别到任何一挡即停止，不再往下测。

日期上下文（`days_since_*`）由 Job A 的 `build_ma_features` 输出，本模块直接读用。
唯一例外是 `days_since_chao_di`：Job A 里是占位 999，本模块提供 `backfill_days_since_chao_di`
供 `candidates.py` 层集成时调用回填。
"""

from __future__ import annotations

from typing import Any


def _f(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number


# ── 优先级顺序常量 ───────────────────────────────────────────
# 从高到低。上层用它遍历判定函数，避免 if-elif 长链耦合。

BUY_POINT_PATTERNS = ("tu_po", "qi_zhang", "hui_cai", "chao_di", "zhui_zhang")

BUY_POINT_LABELS: dict[str, str] = {
    "tu_po": "突破点",
    "qi_zhang": "起涨点",
    "hui_cai": "回踩点",
    "chao_di": "抄底点",
    "zhui_zhang": "追涨点",
    "none": "-",
}

# 抄底点和追涨点带 note 提醒实战风险
BUY_POINT_NOTES: dict[str, str] = {
    "chao_di": "实战难捕捉，仅提示",
    "zhui_zhang": "不推荐，除非板块极强或有重大利好",
}


# ── 单个买点判定函数 ──────────────────────────────────────────


def _is_tu_po(features: dict[str, Any]) -> dict[str, Any] | None:
    """突破点：v0.2 第八章有效突破规则。

    条件：
      - `ma5_breakout_flag = True`（昨日 close ≤ prev_ma5，今日突破加速）
      - `breakout_volume_ratio > 1.2`
      - `close_position > 0.65`
      - `real_body_ratio > 0.45`
    强突破加分：`previous_high_break_flag` 或 `box_break_flag`。
    """
    if not features.get("ma5_breakout_flag"):
        return None
    if _f(features.get("breakout_volume_ratio"), 0.0) <= 1.2:
        return None
    if _f(features.get("close_position"), 0.0) <= 0.65:
        return None
    if _f(features.get("real_body_ratio"), 0.0) <= 0.45:
        return None
    strength = "strong" if (
        features.get("previous_high_break_flag") or features.get("box_break_flag")
    ) else "valid"
    return {
        "pattern": "tu_po",
        "label": BUY_POINT_LABELS["tu_po"],
        "strength": strength,
        "context": "有效突破 MA5",
    }


def _is_qi_zhang(features: dict[str, Any]) -> dict[str, Any] | None:
    """起涨点：阳克阴 + 位置上下文（谷底之后 / 回踩完毕）。

    - 必要条件：`bullish_engulf_flag = True`（当日阳克阴）
    - 上下文任一：
        (A) 距最近一次 `chao_di_flag` 不超过 3 根 —— 谷底反转
        (B) 距最近一次 pullback 落回 MA5 附近不超过 3 根 且当日 `ma5_reclaim_flag` —— 回踩确认
    """
    if not features.get("bullish_engulf_flag"):
        return None

    days_since_chao_di = int(features.get("days_since_chao_di", 999))
    days_since_pullback_low = int(features.get("days_since_pullback_low", 999))
    reclaim = bool(features.get("ma5_reclaim_flag"))

    if days_since_chao_di <= 3:
        return {
            "pattern": "qi_zhang",
            "label": BUY_POINT_LABELS["qi_zhang"],
            "context": "谷底反转",
        }
    if days_since_pullback_low <= 3 and reclaim:
        return {
            "pattern": "qi_zhang",
            "label": BUY_POINT_LABELS["qi_zhang"],
            "context": "回踩确认",
        }
    return None


def _is_hui_cai(features: dict[str, Any]) -> dict[str, Any] | None:
    """回踩点：已发生突破 且 当前贴 MA5 且 缩量。

    - 距最近一次 `ma5_breakout_flag` 不超过 20 根（近期突破还有效）
    - 当日 `pullback_to_ma5_distance ∈ [-2%, +2%]`（贴 MA5）
    - 量能 `pullback_volume_ratio < 0.9`（缩量回踩）
    """
    if int(features.get("days_since_breakout", 999)) > 20:
        return None
    distance = abs(_f(features.get("pullback_to_ma5_distance")))
    if distance > 0.02:
        return None
    if _f(features.get("pullback_volume_ratio"), 1.0) >= 0.9:
        return None
    return {
        "pattern": "hui_cai",
        "label": BUY_POINT_LABELS["hui_cai"],
        "context": "回踩 MA5",
    }


def _is_chao_di(features: dict[str, Any]) -> dict[str, Any] | None:
    """抄底点：MA5 下方 + 局部谷底 + MA7 确认 + MACD W 形。

    - `close_ma5_ratio < 0`（MA5 下方）
    - `is_local_low_10d = True`（10 日内局部谷底）
    - `close > ma7`（MA7 确认，价格已在 7 日均线上方）
    - `macd_w_pattern_flag = True`

    v0.2 战法明确"实战难捕捉"，输出带 note 提醒。
    """
    if _f(features.get("close_ma5_ratio")) >= 0:
        return None
    if not features.get("is_local_low_10d"):
        return None
    close = _f(features.get("close"))
    ma7 = _f(features.get("ma7"))
    if not (close and ma7 and close > ma7):
        return None
    if not features.get("macd_w_pattern_flag"):
        return None
    return {
        "pattern": "chao_di",
        "label": BUY_POINT_LABELS["chao_di"],
        "context": "MA5 下方谷底 · MA7 确认 · MACD W 形",
        "note": BUY_POINT_NOTES["chao_di"],
    }


def _is_zhui_zhang(features: dict[str, Any]) -> dict[str, Any] | None:
    """追涨点：突破/起涨确立后 5 根内且趋势健康。

    - 距最近一次突破或抄底 5-8 根内已确立前置事件
    - 当日 `close > MA5` 且 `ma5_slope_3d > 0`（趋势没死）

    默认不推荐，仅记录。
    """
    days_since_breakout = int(features.get("days_since_breakout", 999))
    days_since_chao_di = int(features.get("days_since_chao_di", 999))
    if not (days_since_breakout <= 5 or days_since_chao_di <= 8):
        return None
    if _f(features.get("close_ma5_ratio")) <= 0:
        return None
    if _f(features.get("ma5_slope_3d")) <= 0:
        return None
    return {
        "pattern": "zhui_zhang",
        "label": BUY_POINT_LABELS["zhui_zhang"],
        "context": "突破/起涨延续",
        "note": BUY_POINT_NOTES["zhui_zhang"],
    }


# 判定函数按优先级排序。上层 `detect_buy_point_pattern` 顺序遍历，命中即返回。
_PATTERN_DETECTORS = (
    ("tu_po", _is_tu_po),
    ("qi_zhang", _is_qi_zhang),
    ("hui_cai", _is_hui_cai),
    ("chao_di", _is_chao_di),
    ("zhui_zhang", _is_zhui_zhang),
)


def detect_buy_point_pattern(features: dict[str, Any]) -> dict[str, Any]:
    """按优先级判定五买点形态。命中即返回，全部不命中返回 `none`。

    返回 dict 至少含 `pattern` 和 `label`，可能含 `context` / `note` / `strength`。
    这是纯函数，无 IO，方便单测。
    """
    for _, detector in _PATTERN_DETECTORS:
        result = detector(features)
        if result:
            return result
    return {"pattern": "none", "label": BUY_POINT_LABELS["none"]}


# ── days_since_chao_di 回填 ────────────────────────────────
# Job A 里 `build_ma_features` 输出的 `days_since_chao_di` 是占位 999，因为形态识别
# 函数当时还没写。上层（candidates.py）拿到 features + bars 后，可以调本函数回扫
# 历史 bars，找到最近一根命中 `_is_chao_di` 的位置，覆写 features["days_since_chao_di"]。
# 这样起涨点判定"距最近抄底不超过 3 根" 才能真实生效。


def _chao_di_snapshot_from_bar(
    bar: dict[str, Any],
    prior_bars: list[dict[str, Any]],
) -> dict[str, Any]:
    """从一根 bar 构造 `_is_chao_di` 所需的最小 features 集合。

    抄底点判定用到的字段：close_ma5_ratio、is_local_low_10d、close、ma7、macd_w_pattern_flag。
    - close_ma5_ratio：直接从 bar 算
    - close / ma7：直接读 bar 字段
    - is_local_low_10d：用 prior_bars（含当日）判定 low 是最近 10 根内最低
    - macd_w_pattern_flag：需要 prior_bars 里带 MACD 列，用 `_macd_w_pattern_flag`
    """
    from shilun.market.ma5_features import _is_local_low_10d, _macd_w_pattern_flag

    close = _f(bar.get("close"))
    ma5 = _f(bar.get("ma5"))
    close_ma5_ratio = (close / ma5 - 1.0) if (close and ma5) else 0.0
    return {
        "close": close,
        "ma7": _f(bar.get("ma7")),
        "close_ma5_ratio": close_ma5_ratio,
        "is_local_low_10d": _is_local_low_10d(prior_bars),
        "macd_w_pattern_flag": _macd_w_pattern_flag(prior_bars),
    }


def backfill_days_since_chao_di(
    bars: list[dict[str, Any]],
    *,
    max_lookback: int = 20,
) -> int:
    """回扫 bars 找最近一次抄底点命中的距离。

    0 = 今日命中，1 = 昨日命中，999 = 近 max_lookback 根内无命中。

    候选池层调这个后覆写 `features["days_since_chao_di"]`。
    """
    from shilun.market.ma5_features import _DAYS_SINCE_NOT_FOUND

    if not bars:
        return _DAYS_SINCE_NOT_FOUND
    lookback = min(max_lookback, len(bars) - 1)
    n = len(bars)
    for i in range(lookback + 1):
        idx = n - 1 - i
        if idx < 0:
            break
        # prior_bars = 从起点到 idx（含当日），供 `is_local_low_10d` 和 MACD W 判定用窗口
        prior_bars = bars[: idx + 1]
        snapshot = _chao_di_snapshot_from_bar(bars[idx], prior_bars)
        if _is_chao_di(snapshot) is not None:
            return i
    return _DAYS_SINCE_NOT_FOUND
