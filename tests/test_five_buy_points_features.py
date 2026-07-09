"""PRD §4.7 五买点体系 · Job A：`build_ma_features` 补齐的新字段测试。

覆盖：
- `_days_since` 工具的边界（0/1/2/999、max_lookback 限制）
- `_is_local_low_10d` 局部谷底判定
- `_macd_w_pattern_flag` W 形态识别
- `build_ma_features` 输出新字段（ma7/macd/days_since_*/is_local_low_10d 等）
"""

from __future__ import annotations

import unittest

from shilun.market.ma5_features import (
    _DAYS_SINCE_NOT_FOUND,
    _bullish_engulf_flag,
    _days_since,
    _days_since_pullback_low,
    _is_local_low_10d,
    _macd_w_pattern_flag,
    build_ma_features,
)


def _bar(
    *,
    date: str = "2026-07-01",
    open_: float = 100.0,
    high: float = 101.0,
    low: float = 99.0,
    close: float = 100.5,
    ma5: float = 100.0,
    ma7: float | None = None,
    ma8: float = 99.5,
    ma10: float = 99.0,
    ma20: float = 98.0,
    volume: float = 1_000_000,
    volume_ratio_20: float = 1.0,
    real_body_ratio: float = 0.3,
    close_position: float = 0.5,
    upper_shadow_ratio: float = 0.1,
    lower_shadow_ratio: float = 0.1,
    pct_chg: float = 0.005,
    macd_dif: float = 0.0,
    macd_dea: float = 0.0,
    macd_hist: float = 0.0,
) -> dict:
    return {
        "date": date,
        "open": open_, "high": high, "low": low, "close": close,
        "ma5": ma5, "ma7": ma7 if ma7 is not None else ma5 * 0.998,
        "ma8": ma8, "ma10": ma10, "ma20": ma20,
        "volume": volume, "volume_ratio_20": volume_ratio_20,
        "real_body_ratio": real_body_ratio, "close_position": close_position,
        "upper_shadow_ratio": upper_shadow_ratio, "lower_shadow_ratio": lower_shadow_ratio,
        "pct_chg": pct_chg,
        "macd_dif": macd_dif, "macd_dea": macd_dea, "macd_hist": macd_hist,
    }


def _base_history(days: int = 25, base: float = 100.0) -> list[dict]:
    """构造一段温和上涨、无信号的日线，作为背景。"""
    return [_bar(date=f"2026-06-{(i % 28) + 1:02d}", close=base + i * 0.05, ma5=base + i * 0.05 * 0.99) for i in range(days)]


class DaysSinceUtilityTest(unittest.TestCase):
    def test_hit_today_returns_zero(self) -> None:
        # 让最后一根命中 bullish_engulf：前根阴线、当根阳线且吞噬
        history = _base_history(5)
        prev = _bar(open_=101.0, close=99.5, high=101.0, low=99.0)  # 阴
        latest = _bar(open_=99.0, close=101.5, high=102.0, low=98.9)  # 阳克阴
        bars = history + [prev, latest]
        d = _days_since(bars, _bullish_engulf_flag)
        self.assertEqual(d, 0)

    def test_hit_two_days_ago_returns_two(self) -> None:
        history = _base_history(5)
        prev = _bar(open_=101.0, close=99.5)   # 阴（在 latest-3）
        engulf = _bar(open_=99.0, close=101.5)  # 阳克阴（在 latest-2）
        post1 = _bar(open_=101.5, close=101.7)
        post2 = _bar(open_=101.7, close=101.8)  # latest
        bars = history + [prev, engulf, post1, post2]
        d = _days_since(bars, _bullish_engulf_flag)
        self.assertEqual(d, 2)

    def test_no_hit_within_lookback_returns_999(self) -> None:
        bars = _base_history(25)  # 全部温和上涨、没有吞噬
        d = _days_since(bars, _bullish_engulf_flag)
        self.assertEqual(d, _DAYS_SINCE_NOT_FOUND)

    def test_lookback_cap(self) -> None:
        """命中在 max_lookback 之外的旧信号应被排除。"""
        history = _base_history(3)
        # 3 天前吞噬
        prev = _bar(open_=101.0, close=99.5)
        engulf = _bar(open_=99.0, close=101.5)
        # 15 根无信号
        fillers = [_bar(date=f"2026-07-{i:02d}", close=101.5 + i * 0.05) for i in range(1, 16)]
        bars = history + [prev, engulf] + fillers
        # 设置 max_lookback=10，命中距离 15，返回 999
        d = _days_since(bars, _bullish_engulf_flag, max_lookback=10)
        self.assertEqual(d, _DAYS_SINCE_NOT_FOUND)

    def test_empty_or_single_bar(self) -> None:
        self.assertEqual(_days_since([], _bullish_engulf_flag), _DAYS_SINCE_NOT_FOUND)
        self.assertEqual(_days_since([_bar()], _bullish_engulf_flag), _DAYS_SINCE_NOT_FOUND)


class IsLocalLow10dTest(unittest.TestCase):
    def test_current_bar_is_lowest_in_last_10(self) -> None:
        bars = [_bar(low=100 - i * 0.1) for i in range(10)]  # 递减，最后一根最低
        self.assertTrue(_is_local_low_10d(bars))

    def test_current_bar_higher_than_min(self) -> None:
        bars = [_bar(low=99 - i * 0.1) for i in range(9)]
        bars.append(_bar(low=100.0))  # latest > 之前的 low
        self.assertFalse(_is_local_low_10d(bars))

    def test_short_history_returns_false_gracefully(self) -> None:
        self.assertFalse(_is_local_low_10d([_bar()]))
        self.assertFalse(_is_local_low_10d([_bar(), _bar()]))


class MacdWPatternTest(unittest.TestCase):
    def test_valid_w_pattern(self) -> None:
        """标准 W：dif 先降后升，dea 上翘，hist 连续 2 根 > 0。"""
        # dif: 0.5, 0.3, 0.1, 0.2, 0.4 → 拐点在 index=2（0.1）
        # dea: 0.35, 0.30, 0.20, 0.22, 0.28 → 最后两根：0.22 → 0.28 上翘
        # hist: 后两根 > 0
        history = _base_history(5)
        w_bars = [
            _bar(macd_dif=0.5, macd_dea=0.35, macd_hist=0.3),
            _bar(macd_dif=0.3, macd_dea=0.30, macd_hist=0.0),
            _bar(macd_dif=0.1, macd_dea=0.20, macd_hist=-0.2),
            _bar(macd_dif=0.2, macd_dea=0.22, macd_hist=0.04),   # hist > 0
            _bar(macd_dif=0.4, macd_dea=0.28, macd_hist=0.24),  # hist > 0，dea 上翘
        ]
        self.assertTrue(_macd_w_pattern_flag(history + w_bars))

    def test_hist_negative_rejected(self) -> None:
        history = _base_history(5)
        w_bars = [
            _bar(macd_dif=0.1, macd_dea=0.20, macd_hist=-0.2),
            _bar(macd_dif=0.2, macd_dea=0.22, macd_hist=-0.04),  # hist < 0
            _bar(macd_dif=0.4, macd_dea=0.28, macd_hist=0.24),
        ]
        self.assertFalse(_macd_w_pattern_flag(history + w_bars))

    def test_dea_flat_rejected(self) -> None:
        history = _base_history(5)
        w_bars = [
            _bar(macd_dif=0.1, macd_dea=0.28, macd_hist=0.04),
            _bar(macd_dif=0.2, macd_dea=0.28, macd_hist=0.04),  # dea 不升
        ]
        self.assertFalse(_macd_w_pattern_flag(history + w_bars))

    def test_missing_macd_columns_returns_false(self) -> None:
        """上游没喂 MACD 列时保守返回 False。"""
        history = _base_history(5)
        # 全部 macd 为 0
        w_bars = [_bar(macd_dif=0.0, macd_dea=0.0, macd_hist=0.0) for _ in range(3)]
        self.assertFalse(_macd_w_pattern_flag(history + w_bars))


class BuildMaFeaturesNewFieldsTest(unittest.TestCase):
    def test_new_fields_present(self) -> None:
        bars = _base_history(20)
        features = build_ma_features(bars)
        for field in (
            "ma7", "macd_dif", "macd_dea", "macd_hist",
            "is_local_low_10d", "macd_w_pattern_flag",
            "days_since_bullish_engulf", "days_since_breakout",
            "days_since_pullback_low", "days_since_chao_di",
        ):
            self.assertIn(field, features, f"缺字段 {field}")

    def test_days_since_chao_di_is_placeholder(self) -> None:
        """Job A 阶段 days_since_chao_di 应为 999（占位，Job B 会填）。"""
        bars = _base_history(20)
        features = build_ma_features(bars)
        self.assertEqual(features["days_since_chao_di"], _DAYS_SINCE_NOT_FOUND)

    def test_days_since_bullish_engulf_finds_recent_hit(self) -> None:
        """最近一根阳克阴命中时 days_since_bullish_engulf == 0。"""
        history = _base_history(20)
        prev = _bar(date="2026-07-04", open_=101.0, close=99.5)
        latest = _bar(date="2026-07-05", open_=99.0, close=101.5)
        bars = history + [prev, latest]
        features = build_ma_features(bars)
        self.assertEqual(features["days_since_bullish_engulf"], 0)


if __name__ == "__main__":
    unittest.main()
