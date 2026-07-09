"""MA5 特征提取器与三分制评分的单元测试。

覆盖重点：
- `ma5_breakout_flag` 与 `ma5_reclaim_flag` 的正/反例，确保两者互斥。
- 突破 flag 的四条约束：昨日在 MA5 附近、今日实体大幅站上、real_body、close_position。
- `compute_trade_timing_score()` 在两种 flag 分别成立时给出正确的 `buy_point_type`。
"""

from __future__ import annotations

import unittest

from shilun.market.ma5_features import (
    build_ma_features,
    compute_breakout_quality_score,
    compute_trade_timing_score,
)


def _bar(
    *,
    date: str,
    open_: float,
    high: float,
    low: float,
    close: float,
    ma5: float,
    ma10: float,
    ma20: float,
    volume: float = 1_000_000,
    volume_ratio_20: float = 1.0,
    real_body_ratio: float | None = None,
    close_position: float | None = None,
    upper_shadow_ratio: float = 0.1,
    lower_shadow_ratio: float = 0.1,
    pct_chg: float = 0.0,
) -> dict:
    if real_body_ratio is None:
        rng = max(high - low, 1e-6)
        real_body_ratio = abs(close - open_) / rng
    if close_position is None:
        rng = max(high - low, 1e-6)
        close_position = (close - low) / rng
    return {
        "date": date,
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "ma5": ma5,
        "ma10": ma10,
        "ma20": ma20,
        "volume": volume,
        "volume_ratio_20": volume_ratio_20,
        "real_body_ratio": real_body_ratio,
        "close_position": close_position,
        "upper_shadow_ratio": upper_shadow_ratio,
        "lower_shadow_ratio": lower_shadow_ratio,
        "pct_chg": pct_chg,
    }


def _base_history(days: int = 25, base_close: float = 10.0) -> list[dict]:
    """构造一段温和上涨的历史序列，用来占据前面的日线，确保切片够长。"""
    bars = []
    for i in range(days):
        close = base_close + i * 0.02
        bars.append(
            _bar(
                date=f"2026-06-{(i % 28) + 1:02d}",
                open_=close * 0.998,
                high=close * 1.005,
                low=close * 0.995,
                close=close,
                ma5=close * 0.995,
                ma10=close * 0.99,
                ma20=close * 0.985,
                real_body_ratio=0.3,
                close_position=0.5,
            )
        )
    return bars


class MA5BreakoutReclaimFlagTest(unittest.TestCase):
    """站回（reclaim）与突破加速（breakout）必须互斥、且各有正反例。"""

    def test_reclaim_flag_true_when_prev_below_and_today_above(self) -> None:
        history = _base_history()
        prev = _bar(
            date="2026-07-04",
            open_=10.30,
            high=10.32,
            low=10.20,
            close=10.22,
            ma5=10.30,
            ma10=10.20,
            ma20=10.10,
        )
        latest = _bar(
            date="2026-07-05",
            open_=10.24,
            high=10.42,
            low=10.23,
            close=10.40,
            ma5=10.32,
            ma10=10.22,
            ma20=10.12,
            real_body_ratio=0.85,
            close_position=0.9,
        )
        features = build_ma_features(history + [prev, latest])
        self.assertTrue(features["ma5_reclaim_flag"])
        self.assertFalse(
            features["ma5_breakout_flag"],
            "站回和突破必须互斥：昨日破位后回来算 reclaim，不算 breakout。",
        )

    def test_breakout_flag_true_when_prev_at_ma5_and_today_strong_body(self) -> None:
        history = _base_history()
        prev = _bar(
            date="2026-07-04",
            open_=10.28,
            high=10.34,
            low=10.26,
            close=10.32,
            ma5=10.30,
            ma10=10.20,
            ma20=10.10,
        )
        latest = _bar(
            date="2026-07-05",
            open_=10.33,
            high=10.55,
            low=10.31,
            close=10.53,
            ma5=10.35,
            ma10=10.24,
            ma20=10.13,
            real_body_ratio=0.85,
            close_position=0.9,
        )
        features = build_ma_features(history + [prev, latest])
        self.assertTrue(features["ma5_breakout_flag"])
        self.assertFalse(
            features["ma5_reclaim_flag"],
            "已经在 MA5 上方进一步走强，不该被识别为站回。",
        )

    def test_breakout_flag_false_when_close_position_low(self) -> None:
        """收盘位置低于 0.55 时，即使涨得多也不算突破加速（承接不足）。"""
        history = _base_history()
        prev = _bar(
            date="2026-07-04",
            open_=10.28,
            high=10.34,
            low=10.26,
            close=10.32,
            ma5=10.30,
            ma10=10.20,
            ma20=10.10,
        )
        latest = _bar(
            date="2026-07-05",
            open_=10.33,
            high=10.70,
            low=10.30,
            close=10.42,
            ma5=10.35,
            ma10=10.24,
            ma20=10.13,
            real_body_ratio=0.85,
            close_position=0.30,
        )
        features = build_ma_features(history + [prev, latest])
        self.assertFalse(features["ma5_breakout_flag"])

    def test_breakout_flag_false_when_real_body_thin(self) -> None:
        """实体太薄（<= 0.35）不算突破加速，可能是十字星/放量长影。"""
        history = _base_history()
        prev = _bar(
            date="2026-07-04",
            open_=10.28,
            high=10.34,
            low=10.26,
            close=10.32,
            ma5=10.30,
            ma10=10.20,
            ma20=10.10,
        )
        latest = _bar(
            date="2026-07-05",
            open_=10.33,
            high=10.60,
            low=10.31,
            close=10.50,
            ma5=10.35,
            ma10=10.24,
            ma20=10.13,
            real_body_ratio=0.20,
            close_position=0.90,
        )
        features = build_ma_features(history + [prev, latest])
        self.assertFalse(features["ma5_breakout_flag"])

    def test_breakout_flag_false_when_barely_above_ma5(self) -> None:
        """突破幅度 < 0.5% 视为贴线噪声，不算加速。"""
        history = _base_history()
        prev = _bar(
            date="2026-07-04",
            open_=10.28,
            high=10.34,
            low=10.26,
            close=10.32,
            ma5=10.30,
            ma10=10.20,
            ma20=10.10,
        )
        latest = _bar(
            date="2026-07-05",
            open_=10.33,
            high=10.42,
            low=10.31,
            close=10.37,
            ma5=10.35,
            ma10=10.24,
            ma20=10.13,
            real_body_ratio=0.85,
            close_position=0.90,
        )
        features = build_ma_features(history + [prev, latest])
        self.assertFalse(
            features["ma5_breakout_flag"],
            "close / ma5 - 1 ≈ 0.19% 达不到 0.5% 门槛。",
        )


class TradeTimingBuyPointTypeTest(unittest.TestCase):
    """两个 flag 拆开后，buy_point_type 分支必须能各自命中。"""

    def test_breakout_type_selected_when_breakout_flag_true(self) -> None:
        history = _base_history()
        prev = _bar(
            date="2026-07-04",
            open_=10.28,
            high=10.34,
            low=10.26,
            close=10.32,
            ma5=10.30,
            ma10=10.20,
            ma20=10.10,
            volume_ratio_20=1.0,
        )
        latest = _bar(
            date="2026-07-05",
            open_=10.33,
            high=10.55,
            low=10.31,
            close=10.53,
            ma5=10.35,
            ma10=10.24,
            ma20=10.13,
            real_body_ratio=0.85,
            close_position=0.9,
            volume_ratio_20=2.0,
        )
        features = build_ma_features(history + [prev, latest])
        timing = compute_trade_timing_score(features)
        self.assertIn(timing["buy_point_type"], {"ma5_breakout", "ma5_pullback"})
        if timing["buy_point_type"] == "ma5_breakout":
            self.assertTrue(features["ma5_breakout_flag"])

    def test_reclaim_type_selected_when_only_reclaim_flag_true(self) -> None:
        """仅站回、突破/回踩分数不够时，应落到 ma5_reclaim。"""
        history = _base_history()
        prev = _bar(
            date="2026-07-04",
            open_=10.30,
            high=10.32,
            low=10.20,
            close=10.22,
            ma5=10.30,
            ma10=10.20,
            ma20=10.10,
        )
        latest = _bar(
            date="2026-07-05",
            open_=10.24,
            high=10.30,
            low=10.23,
            close=10.28,
            ma5=10.26,
            ma10=10.22,
            ma20=10.12,
            real_body_ratio=0.20,
            close_position=0.30,
            volume_ratio_20=0.7,
        )
        features = build_ma_features(history + [prev, latest])
        self.assertTrue(features["ma5_reclaim_flag"])
        self.assertFalse(features["ma5_breakout_flag"])
        timing = compute_trade_timing_score(features)
        self.assertIn(
            timing["buy_point_type"],
            {"ma5_reclaim", "watch", "ma5_pullback"},
            "站回后应至少允许落入 reclaim 或 watch，不应该错标为 breakout。",
        )
        self.assertNotEqual(timing["buy_point_type"], "ma5_breakout")


class BreakoutQualityScoreTest(unittest.TestCase):
    """Job 5：compute_breakout_quality_score 支持 breakout_event 注入。"""

    def _features(self, **overrides) -> dict:
        base = {
            "previous_high_hold_ratio": -0.02,   # 明显跌破前高，作为 features 层的"坏值"
            "fall_back_into_box_flag": True,
            "post_breakout_shrink_ratio": 1.5,
            "dynamic_tolerance": 0.01,
        }
        base.update(overrides)
        return base

    def test_fallback_to_features_when_no_event(self) -> None:
        result = compute_breakout_quality_score(self._features())
        self.assertEqual(result["source"], "features")
        # next_day_score 为常量 50（没有事件，未知）
        self.assertEqual(result["parts"]["next_day"], 50)
        # box_hold 因 fall_back_into_box_flag=True 拿 0 分
        self.assertEqual(result["parts"]["box_hold"], 0)
        self.assertIsNone(result["breakout_quality"])
        self.assertEqual(result["tracked_days"], 0)

    def test_event_overrides_features(self) -> None:
        # features 里全是"坏值"，但 event 显示突破真实追踪结果良好
        good_event = {
            "previous_high_hold_ratio": 0.005,
            "fall_back_into_box_flag": False,
            "post_breakout_shrink_ratio": 0.7,
            "next_day_hold_flag": True,
            "tracked_days": 5,
            "breakout_quality": "valid",
        }
        result = compute_breakout_quality_score(self._features(), breakout_event=good_event)
        self.assertEqual(result["source"], "event")
        self.assertEqual(result["tracked_days"], 5)
        self.assertEqual(result["breakout_quality"], "valid")
        # box_hold 现在应该是 80（事件里没跌回箱体）
        self.assertEqual(result["parts"]["box_hold"], 80)
        # next_day_score = 100（事件显示次日守住）
        self.assertEqual(result["parts"]["next_day"], 100)
        # shrink_score = 80（事件里 0.7 < 0.9）
        self.assertEqual(result["parts"]["post_breakout_shrink"], 80)
        # previous_high_hold_ratio 0.005 >= 0 → hold_score = 100
        self.assertEqual(result["parts"]["previous_high_hold"], 100)
        # 总分应显著高于 fallback 情形
        self.assertGreater(result["score"], 85)

    def test_event_with_next_day_lost_gives_zero_next_day_score(self) -> None:
        bad_event = {
            "previous_high_hold_ratio": 0.001,
            "fall_back_into_box_flag": False,
            "post_breakout_shrink_ratio": 0.8,
            "next_day_hold_flag": False,   # 次日就失守
            "tracked_days": 3,
            "breakout_quality": "suspicious",
        }
        result = compute_breakout_quality_score(self._features(), breakout_event=bad_event)
        self.assertEqual(result["parts"]["next_day"], 0)
        self.assertEqual(result["breakout_quality"], "suspicious")

    def test_event_with_unknown_next_day_falls_back_to_neutral(self) -> None:
        """事件刚落库还没回填过：next_day_hold_flag=None，next_day_score=50。"""
        pending_event = {
            "previous_high_hold_ratio": 0.0,
            "fall_back_into_box_flag": False,
            "post_breakout_shrink_ratio": 1.0,
            "next_day_hold_flag": None,
            "tracked_days": 0,
            "breakout_quality": "pending_confirmation",
        }
        result = compute_breakout_quality_score(self._features(), breakout_event=pending_event)
        self.assertEqual(result["parts"]["next_day"], 50)
        self.assertEqual(result["source"], "event")

    def test_trade_timing_forwards_breakout_event(self) -> None:
        """compute_trade_timing_score 也应能透传 breakout_event。"""
        # 构造一个 breakout 分数够高、event 显示追踪失败的场景
        features = {
            "close_ma5_ratio": 0.02, "ma5_slope_3d": 0.015,
            "ma_alignment_score": 80, "close_ma10_ratio": 0.01, "close_ma20_ratio": 0.04,
            "ma5_breakout_flag": True, "breakout_volume_ratio": 1.5,
            "close_position": 0.8, "real_body_ratio": 0.7,
            "previous_high_break_flag": True, "box_break_flag": True,
            "previous_high_hold_ratio": 0.0, "fall_back_into_box_flag": False,
            "post_breakout_shrink_ratio": 0.8, "dynamic_tolerance": 0.01,
            "pullback_depth": 0.02, "dynamic_pullback_min": 0.01, "dynamic_pullback_max": 0.05,
            "pullback_to_ma5_distance": 0.005, "pullback_volume_ratio": 0.7,
            "prior_extension_from_ma5": 0.04, "ma5_reclaim_flag": False,
            "ma5_hold_flag": True, "bullish_engulf_flag": False,
            "strong_real_body_flag": True, "upper_shadow_ratio": 0.1,
        }
        failed_event = {
            "previous_high_hold_ratio": -0.03,
            "fall_back_into_box_flag": True,
            "post_breakout_shrink_ratio": 1.5,
            "next_day_hold_flag": False,
            "tracked_days": 5,
            "breakout_quality": "failed",
        }
        with_event = compute_trade_timing_score(features, breakout_event=failed_event)
        without_event = compute_trade_timing_score(features)
        # 有真实追踪且判失败 → breakout_quality 子分应低于无事件的 fallback
        self.assertLess(
            with_event["parts"]["breakout_quality"]["score"],
            without_event["parts"]["breakout_quality"]["score"],
        )
        self.assertEqual(with_event["parts"]["breakout_quality"]["source"], "event")
        self.assertEqual(with_event["parts"]["breakout_quality"]["breakout_quality"], "failed")


if __name__ == "__main__":
    unittest.main()
