"""分时共振（v6）计算器测试。"""

from __future__ import annotations

import unittest

from shilun.market.intraday_resonance import compute_intraday_resonance_score


def _make_bar(dt: str, open_: float, close: float, volume: float = 1000) -> dict:
    return {
        "datetime": dt,
        "open": open_,
        "high": max(open_, close) * 1.001,
        "low": min(open_, close) * 0.999,
        "close": close,
        "volume": volume,
        "amount": volume * close,
    }


def _stock_intraday_bars(direction: str = "strong_up") -> list[dict]:
    """构造一整天分时数据。

    direction:
        strong_up  – 全天跟涨且强度大
        weak_up    – 全天跟涨但弱
        divergent  – 早盘涨、午后跌（背离）
        anti_dump  – 大盘跌但个股涨（抗跌）
    """
    profile = {
        "strong_up": [(1.00, 1.02), (1.02, 1.03), (1.03, 1.035), (1.035, 1.05), (1.05, 1.07)],
        "weak_up":   [(1.00, 1.005), (1.005, 1.008), (1.008, 1.01), (1.01, 1.012), (1.012, 1.015)],
        "divergent": [(1.00, 1.03), (1.03, 1.02), (1.02, 1.02), (1.02, 0.99), (0.99, 0.98)],
        "anti_dump": [(1.00, 1.01), (1.01, 1.02), (1.02, 1.03), (1.03, 1.04), (1.04, 1.05)],
    }
    times = [
        "2026-07-09T09:35:00",  # early_open
        "2026-07-09T10:30:00",  # morning
        "2026-07-09T13:15:00",  # afternoon_open
        "2026-07-09T13:45:00",  # afternoon_mid
        "2026-07-09T14:45:00",  # tail
    ]
    return [_make_bar(t, o, c, volume=1500 if i == 4 else 1000) for i, (t, (o, c)) in enumerate(zip(times, profile[direction]))]


def _benchmark_bars(direction: str = "up") -> list[dict]:
    profile = {
        "up":   [(1.00, 1.005), (1.005, 1.01), (1.01, 1.012), (1.012, 1.015), (1.015, 1.018)],
        "down": [(1.00, 0.998), (0.998, 0.996), (0.996, 0.995), (0.995, 0.993), (0.993, 0.99)],
        "flat": [(1.00, 1.001), (1.001, 1.001), (1.001, 1.000), (1.000, 1.001), (1.001, 1.001)],
    }
    times = [
        "2026-07-09T09:35:00",
        "2026-07-09T10:30:00",
        "2026-07-09T13:15:00",
        "2026-07-09T13:45:00",
        "2026-07-09T14:45:00",
    ]
    return [_make_bar(t, o, c) for t, (o, c) in zip(times, profile[direction])]


class IntradayResonanceTest(unittest.TestCase):
    def test_no_data_returns_neutral(self) -> None:
        r = compute_intraday_resonance_score(None, None)
        self.assertEqual(r["score"], 50.0)
        self.assertFalse(r["has_data"])

    def test_strong_up_with_up_market_gets_high_score(self) -> None:
        stock = _stock_intraday_bars("strong_up")
        bench = _benchmark_bars("up")
        r = compute_intraday_resonance_score(stock, bench)
        self.assertGreater(r["score"], 85.0)
        self.assertTrue(r["has_data"])
        # 各时段基础分应为 100（强度 > 大盘 1.2 倍）
        for name in ("early_open", "morning", "afternoon_open", "afternoon_mid", "tail"):
            self.assertEqual(r["segments"][name]["score"], 100.0)

    def test_weak_up_with_up_market_gets_medium_score(self) -> None:
        stock = _stock_intraday_bars("weak_up")
        bench = _benchmark_bars("up")
        r = compute_intraday_resonance_score(stock, bench)
        # 强度接近 1.0 → 80 分基础
        self.assertGreater(r["score"], 60.0)
        self.assertLess(r["score"], 95.0)

    def test_divergent_gets_low_score(self) -> None:
        stock = _stock_intraday_bars("divergent")
        bench = _benchmark_bars("up")
        r = compute_intraday_resonance_score(stock, bench)
        # 早盘还同向，后半程反向 → 应低于 60
        self.assertLess(r["score"], 70.0)

    def test_anti_dump_gets_perfect_score(self) -> None:
        """大盘跌 + 个股涨 = 抗跌，五段都拿 100 分。"""
        stock = _stock_intraday_bars("anti_dump")
        bench = _benchmark_bars("down")
        r = compute_intraday_resonance_score(stock, bench)
        self.assertGreater(r["score"], 90.0)

    def test_tail_volume_boost(self) -> None:
        """尾盘成交量 1500 vs 其他四段各 1000 → 尾盘 / 全天均值 = 1.25 → boost 8。"""
        stock = _stock_intraday_bars("strong_up")
        bench = _benchmark_bars("up")
        r = compute_intraday_resonance_score(stock, bench)
        self.assertGreaterEqual(r["volume_boost"], 8.0)

    def test_output_has_all_segments(self) -> None:
        stock = _stock_intraday_bars("strong_up")
        bench = _benchmark_bars("up")
        r = compute_intraday_resonance_score(stock, bench)
        for name in ("early_open", "morning", "afternoon_open", "afternoon_mid", "tail"):
            self.assertIn(name, r["segments"])

    def test_sparse_bars_do_not_pass_formal_data_gate(self) -> None:
        stock = _stock_intraday_bars("strong_up")[:2]
        bench = _benchmark_bars("up")[:2]

        result = compute_intraday_resonance_score(stock, bench)

        self.assertFalse(result["has_data"])
        self.assertEqual(50.0, result["score"])
        self.assertLess(result["valid_segment_count"], result["required_segment_count"])


if __name__ == "__main__":
    unittest.main()
