import copy
import unittest

import pandas as pd

from shilun.market.candidates import evaluate_ma5_selection_gate
from shilun.market.ma5_liquidity import liquidity_rule, market_turnover
from shilun.market.ma5_universe import scan_universe


class MA5LiquidityTests(unittest.TestCase):
    def gate(self, market_amount, average_amount_cny):
        bars = [{"date": f"2026-09-{day:02d}", "ma5": 12.0,
                 "ma10": 11.0, "ma20": 10.0, "pct_chg": .10 if day == 21 else .01,
                 "amount": average_amount_cny / 1000, "total_mv": 1_100_000.0}
                for day in range(15, 22)]
        return evaluate_ma5_selection_gate(ticker="600000.SH", name="测试股份",
                                          bars=bars, market_amount_cny=market_amount)

    def test_market_thresholds_include_both_boundaries_in_medium_regime(self):
        for amount, regime, cap in (
            (2.1e12 - 1, "low", 5e9),
            (2.1e12, "medium", 1e10),
            (2.3e12, "medium", 1e10),
            (2.3e12 + 1, "high", None),
        ):
            with self.subTest(amount=amount):
                rule = liquidity_rule(amount)
                self.assertTrue(rule["available"])
                self.assertEqual(regime, rule["regime"])
                self.assertEqual(cap, rule["max_avg_amount_cny"])

    def test_stock_upper_limits_are_strict_and_high_regime_has_no_minimum_100yi(self):
        for market_amount, amount, expected in (
            (2e12, 5e9 - 100, True),
            (2e12, 5e9, False),
            (2.1e12, 5e9, True),
            (2.1e12, 1e10 - 100, True),
            (2.1e12, 1e10, False),
            (2.3e12, 1e10, False),
            (2.3e12 + 1, 1e10, True),
            (2.3e12 + 1, 1e11, True),
            (2.3e12 + 1, 8.8e8, True),
            (2.3e12 + 1, 8.8e8 - 100, False),
        ):
            with self.subTest(market_amount=market_amount, average=amount):
                result = self.gate(market_amount, amount)
                self.assertEqual(expected, result["passed"], result["failure_ids"])

    def test_missing_or_nonfinite_market_turnover_fails_closed(self):
        for amount in (None, 0, -1, float("nan"), float("inf"), True, "2300000000000"):
            with self.subTest(amount=amount):
                self.assertFalse(liquidity_rule(amount)["available"])
                gate = self.gate(amount, 9e8)
                self.assertFalse(gate["passed"])
                self.assertIn("avg_amount_7d_upper", gate["failure_ids"])

    @staticmethod
    def market_fixture():
        day = "2026-09-21"
        basics = [{"ts_code": "600000.SH", "name": "正常股份"},
                  {"ts_code": "000001.SZ", "name": "*ST测试"},
                  {"ts_code": "920001.BJ", "name": "北交股份"}]
        bars = [{"ticker": ticker, "date": day, "amount": amount}
                for ticker, amount in (("600000.SH", 1e9), ("000001.SZ", 1e9),
                                       ("920001.BJ", 2e8), ("000001.SH", 9e9),
                                       ("510300.SH", 9e9))]
        daily_basic = [{"ts_code": row["ts_code"], "trade_date": "20260921"}
                       for row in basics]
        return day, basics, bars, daily_basic

    def test_market_turnover_includes_st_and_beijing_and_excludes_indices_etfs(self):
        day, basics, bars, daily = self.market_fixture()
        bars.append({"ticker": "600000.SH", "date": "2026-09-22", "amount": 9e10})
        result = market_turnover(basics, bars, daily, day)
        self.assertTrue(result["available"])
        self.assertEqual(3, result["stock_count"])
        self.assertEqual(2.2e12, result["market_amount_cny"])
        self.assertEqual("medium", result["regime"])

    def test_duplicate_current_stock_rows_are_rejected(self):
        day, basics, bars, daily = self.market_fixture()
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            market_turnover(basics, bars + [copy.deepcopy(bars[0])], daily, day)

    def test_missing_expected_stock_or_invalid_turnover_is_not_partial_market_total(self):
        day, basics, bars, daily = self.market_fixture()
        missing = market_turnover(basics, bars[1:], daily, day)
        self.assertFalse(missing["available"])
        self.assertIsNone(missing["market_amount_cny"])
        self.assertEqual(["600000.SH"], missing["missing_tickers"])
        for amount in (float("nan"), float("inf"), None, -1, True):
            with self.subTest(amount=amount):
                invalid_bars = copy.deepcopy(bars)
                invalid_bars[0]["amount"] = amount
                result = market_turnover(basics, invalid_bars, daily, day)
                self.assertFalse(result["available"])
                self.assertEqual(["600000.SH"], result["invalid_amount_tickers"])
        self.assertFalse(market_turnover(basics, bars, [], day)["available"])

    def test_zero_market_total_is_unavailable(self):
        day, basics, bars, daily = self.market_fixture()
        for row in bars:
            row["amount"] = 0
        self.assertFalse(market_turnover(basics, bars, daily, day)["available"])

    def test_st_contributes_to_regime_but_cannot_enter_selected_universe(self):
        days = pd.bdate_range("2026-07-01", periods=40).strftime("%Y-%m-%d").tolist()
        basics = [{"ts_code": "600000.SH", "name": "正常股份"},
                  {"ts_code": "000001.SZ", "name": "*ST测试"}]
        bars = []
        for ticker in ("600000.SH", "000001.SZ"):
            close = 10.0
            for index, day in enumerate(days):
                close *= 1.10 if index == 36 else 1.01
                bars.append({"ticker": ticker, "date": day, "close": close,
                             "amount": 6e6 if ticker == "600000.SH" else 2.2e9})
        daily = [{"ts_code": row["ts_code"], "trade_date": days[-1], "total_mv": 1.1e6}
                 for row in basics]
        result = scan_universe(basics, bars, daily, days)
        self.assertEqual("medium", result["liquidity"]["regime"])
        selected = [r["ticker"] for r in result["audits"] if r["passed"]]
        self.assertEqual(["600000.SH"], selected)
        st = next(r for r in result["audits"] if r["ticker"] == "000001.SZ")
        self.assertIn("non_st", st["failure_ids"])


if __name__ == "__main__":
    unittest.main()
