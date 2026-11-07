from __future__ import annotations

import unittest

import pandas as pd

from shilun.market.stock_events import detect_generic_events_daily, summarize_generic_events


class GenericStockEventsTest(unittest.TestCase):
    def test_events_are_strategy_neutral(self) -> None:
        bars = pd.DataFrame([
            {"date": "2026-07-01", "open": 10.2, "high": 10.3, "low": 9.8, "close": 10.0, "volume": 100},
            {"date": "2026-07-02", "open": 10.5, "high": 11.2, "low": 10.4, "close": 11.0, "volume": 300},
            {"date": "2026-07-03", "open": 11.0, "high": 12.1, "low": 10.9, "close": 11.2, "volume": 100},
        ])
        events = detect_generic_events_daily(bars, ticker="600000.SH")
        self.assertTrue(events)
        self.assertTrue(all(item["category"] == "generic" for item in events))
        self.assertFalse(any(item["code"].startswith("ma5_") for item in events))
        codes = {item["code"] for item in events}
        self.assertIn("gap_up", codes)
        self.assertIn("limit_up_open_board", codes)

    def test_summary_counts_direction_without_strategy_score(self) -> None:
        summary = summarize_generic_events([
            {"direction": "bullish"},
            {"direction": "bearish"},
            {"direction": "warning"},
        ])
        self.assertEqual(summary["scope"], "strategy_neutral")
        self.assertEqual(summary["generic_count"], 3)
        self.assertEqual(summary["bullish_count"], 1)


if __name__ == "__main__":
    unittest.main()
