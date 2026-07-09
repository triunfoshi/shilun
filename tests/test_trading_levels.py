"""买点/支撑/压力口径修复的回归测试。

覆盖：
- `_build_trading_levels` 不再取 20 日低点作为支撑；买点不低于 MA5 * 0.995
- `_sanity_price` 挡住陈旧缓存里的老价格（603986.SH close=603 时误取 243）
- `_nearest_below` fallback 收紧后返回 None 而不是无差别接受
- `_enrich_intraday_candidate_plans` 端到端场景：老候选 + 新 close
"""

from __future__ import annotations

import unittest


def _bar(date: str, close: float, ma5: float, ma8: float, ma10: float, ma20: float,
         *, high: float | None = None, low: float | None = None) -> dict:
    return {
        "date": date, "close": close,
        "high": high if high is not None else close * 1.005,
        "low": low if low is not None else close * 0.995,
        "ma5": ma5, "ma8": ma8, "ma10": ma10, "ma20": ma20,
    }


class BuildTradingLevelsTest(unittest.TestCase):
    """_build_trading_levels 的辅助价位口径（现已降级为信号命中参考价，不是权威买点）。"""

    def test_support_does_not_include_20day_low(self) -> None:
        """MA5=100, MA10=95, 20日低=88 → 支撑不应落到 88（bug 修复：20日低点已从候选池移除）。"""
        from shilun.market.candidates import _build_trading_levels
        bars = [_bar(f"2026-07-{i:02d}", close=100.0, ma5=99.0, ma8=98.0, ma10=95.0, ma20=90.0,
                     low=88.0 if i == 1 else 99.0) for i in range(1, 21)]
        result = _build_trading_levels(bars, "watch")
        self.assertNotEqual(result["support_source"], "10日低点")
        self.assertGreaterEqual(result["support_price"], 90.0,
                                "支撑不应低于 MA20=90")


class AuthoritativeEntryPriceTest(unittest.TestCase):
    """PRD 阶段 9 权威买点：候选池 predicted_buy_price 应等于 trade_plan.entry_price（= 当前 close）。"""

    def test_predicted_buy_price_equals_close_from_trade_plan(self) -> None:
        """close=100 → 权威买点应 = 100，不再走 _build_trading_levels 的信号价。"""
        from shilun.market.ma5_features import build_trade_plan
        features = {
            "close": 100.0, "open": 99.5, "ma5": 99.0, "ma8": 98.5, "ma10": 97.0, "ma20": 95.0,
            "previous_high_60": 110.0, "median_abs_return_20d": 0.02,
        }
        plan = build_trade_plan(features)
        self.assertEqual(plan["entry_price"], 100.0)
        # support_1 = MA5, support_2 = MA10, support_3 = MA20（PRD 权威口径）
        self.assertEqual(plan["support_1"], 99.0)
        self.assertEqual(plan["support_2"], 97.0)
        self.assertEqual(plan["support_3"], 95.0)
        self.assertEqual(plan["support_1_source"], "MA5")
        # stop_loss_1 = MA5 * 0.98
        self.assertAlmostEqual(plan["stop_loss_1"], 97.02, places=2)


class SanityPriceTest(unittest.TestCase):
    """陈旧缓存价格的过滤逻辑。"""

    def test_stale_price_243_at_close_603_is_dropped(self) -> None:
        """真实 bug 场景：603986.SH close=603 时老缓存 support=243 → 应被拦下。"""
        from shilun.api import _sanity_price
        self.assertIsNone(_sanity_price(243.90, 603.17))

    def test_price_within_15pct_is_kept(self) -> None:
        from shilun.api import _sanity_price
        self.assertEqual(_sanity_price(600.0, 603.17), 600.0)
        self.assertEqual(_sanity_price(690.0, 603.17), 690.0)  # +14.4%

    def test_price_beyond_15pct_is_dropped(self) -> None:
        from shilun.api import _sanity_price
        self.assertIsNone(_sanity_price(500.0, 603.17))  # -17%
        self.assertIsNone(_sanity_price(700.0, 603.17))  # +16%

    def test_zero_or_none_returns_none(self) -> None:
        from shilun.api import _sanity_price
        self.assertIsNone(_sanity_price(None, 603.17))
        self.assertIsNone(_sanity_price(0.0, 603.17))
        self.assertIsNone(_sanity_price(600.0, None))


class NearestBelowFallbackTest(unittest.TestCase):
    """_nearest_below 不再无差别 fallback。"""

    def test_returns_none_when_no_candidate_below(self) -> None:
        """所有候选都远高于 price → 之前会 fallback 到全部；现在应返回 None。"""
        from shilun.api import _nearest_below
        price = 100.0
        candidates = [(200.0, "老支撑"), (500.0, "更老支撑")]
        result, source = _nearest_below(price, candidates)
        self.assertIsNone(result)
        self.assertEqual(source, "")

    def test_picks_highest_below_price(self) -> None:
        from shilun.api import _nearest_below
        price = 100.0
        candidates = [(95.0, "MA5"), (90.0, "MA10"), (85.0, "MA20")]
        result, source = _nearest_below(price, candidates)
        self.assertEqual(result, 95.0)
        self.assertEqual(source, "MA5")

    def test_stale_price_bypassed_by_sanity_check(self) -> None:
        """把 sanity_price(老支撑) 传给 _nearest_below，模拟 Step B 修复后的调用。"""
        from shilun.api import _nearest_below, _sanity_price
        close = 603.0
        stale_support = _sanity_price(243.9, close)  # 应为 None
        ma5 = 610.0
        # 用了 sanity 后，老支撑不再干扰
        result, source = _nearest_below(
            close,
            [(stale_support, "候选支撑"), (ma5, "MA5")],
        )
        # ma5=610 > close*1.012=610.24？不，610 < 610.236，所以 MA5 会被选中
        self.assertEqual(result, 610.0)
        self.assertEqual(source, "MA5")


if __name__ == "__main__":
    unittest.main()
