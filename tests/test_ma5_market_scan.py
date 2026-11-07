import copy
import json
import unittest

import pandas as pd

from research.scan_ma5_market import evaluate_stock, flow_windows, scan


class MA5MarketScanTests(unittest.TestCase):
    def setUp(self):
        self.days = pd.bdate_range("2026-07-01", periods=40).strftime("%Y-%m-%d").tolist()
        self.ticker = "600000.SH"

    def bars(self, *, ticker=None, amount=900_000.0, limit_jump=True):
        close = 10.0
        rows = []
        for index, day in enumerate(self.days):
            close *= 1.10 if limit_jump and index == len(self.days) - 4 else 1.01
            rows.append({"ticker": ticker or self.ticker, "date": day,
                         "close": close, "amount": amount})
        return rows

    def evaluate(self, bars=None, *, name="测试股份", cap=1_100_000.0, ticker=None):
        return evaluate_stock(ticker or self.ticker, name,
                              self.bars() if bars is None else bars, self.days, cap,
                              market_amount_cny=2e12)

    def scan_one(self, *, basics=None, bars=None, daily_basic=None, flows=None, sectors=None):
        return scan(
            [{"ts_code": self.ticker, "name": "测试股份"}] if basics is None else basics,
            self.bars() if bars is None else bars,
            [{"ts_code": self.ticker, "trade_date": self.days[-1].replace("-", ""),
              "total_mv": 1_100_000.0}] if daily_basic is None else daily_basic,
            self.days, [] if flows is None else flows, {} if sectors is None else sectors,
        )

    def flow_lookup(self, *, members=None):
        members = {self.ticker} if members is None else members
        return {(ticker, day): {"net_mf_amount": 10_000.0,
                               "buy_lg_amount": 5_000.0, "buy_elg_amount": 2_000.0,
                               "sell_lg_amount": 20_000.0, "sell_elg_amount": 7_000.0}
                for ticker in members for day in self.days[-5:]}

    def test_amount_boundaries_use_unrounded_seven_day_mean(self):
        for amount, expected, failure in (
            (879_999.999, False, "avg_amount_7d"),
            (880_000.0, True, None),
            (4_999_999.999, True, None),
            (5_000_000.0, False, "avg_amount_7d_upper"),
        ):
            with self.subTest(amount=amount):
                result = self.evaluate(self.bars(amount=amount))
                self.assertTrue(result["data_complete"])
                self.assertEqual(expected, result["passed"])
                if failure:
                    self.assertIn(failure, result["failure_ids"])
        result = self.scan_one(bars=self.bars(amount=5_000_000.0))
        self.assertEqual(1, result["counts"]["original_hard_conditions"])
        self.assertEqual(1, result["counts"]["excluded_by_cap"])
        self.assertEqual(0, result["counts"]["selected"])

    def test_amount_cap_applies_to_mean_not_each_day(self):
        bars = self.bars()
        bars[-1]["amount"] = 6_000_000.0
        result = self.evaluate(bars)
        self.assertTrue(result["passed"])
        self.assertAlmostEqual((6 * 900_000 + 6_000_000) / 7,
                               result["metrics"]["avg_amount_7d"], places=2)
        self.assertEqual(self.days[-7:], result["window_dates"])

    def test_missing_recent_or_ma_history_day_is_not_backfilled(self):
        for missing_date in (self.days[-1], self.days[-4], self.days[-24]):
            with self.subTest(missing_date=missing_date):
                result = self.evaluate([r for r in self.bars() if r["date"] != missing_date])
                self.assertFalse(result["data_complete"])
                self.assertFalse(result["passed"])
                self.assertIn("missing_daily_bars", result["data_errors"])
                self.assertEqual([missing_date], result["missing_dates"])

    def test_future_rows_do_not_change_current_decision_or_cap(self):
        baseline = self.scan_one()
        future_day = (pd.Timestamp(self.days[-1]) + pd.offsets.BDay(1)).strftime("%Y-%m-%d")
        future_bar = {"ticker": self.ticker, "date": future_day, "close": 1.0,
                      "amount": 99_000_000.0}
        result = self.scan_one(
            bars=self.bars() + [future_bar],
            daily_basic=[{"ts_code": self.ticker, "trade_date": self.days[-1], "total_mv": 1_100_000.0},
                         {"ts_code": self.ticker, "trade_date": future_day, "total_mv": 1.0}],
        )
        self.assertEqual(baseline["selected"], result["selected"])

    def test_missing_or_invalid_inputs_never_become_eligible(self):
        cases = [("name", "", "missing_stock_name"),
                 ("cap", None, "missing_current_market_cap")]
        cases.extend((field, value, error)
                     for field, error in (("close", "invalid_close"),
                                          ("amount", "incomplete_amount_7d"),
                                          ("cap", "missing_current_market_cap"))
                     for value in (float("nan"), float("inf"), 0.0))
        for field, value, error in cases:
            with self.subTest(field=field, value=value):
                bars = self.bars()
                kwargs = {}
                if field in ("close", "amount"):
                    bars[-1][field] = value
                else:
                    kwargs[field] = value
                result = self.evaluate(bars, **kwargs)
                self.assertFalse(result["passed"])
                self.assertFalse(result["original_conditions_passed"])
                self.assertIn(error, result["data_errors"])

    def test_invalid_observations_can_still_be_saved_in_strict_json_audit(self):
        for field in ("close", "amount", "cap"):
            for value in (float("nan"), float("inf")):
                with self.subTest(field=field, value=value):
                    bars = self.bars()
                    kwargs = {}
                    if field == "cap":
                        kwargs["cap"] = value
                    else:
                        bars[-1][field] = value
                    result = self.evaluate(bars, **kwargs)
                    self.assertFalse(result["passed"])
                    json.dumps(result, allow_nan=False)

    def test_previous_market_cap_does_not_fill_missing_current_cap(self):
        result = self.scan_one(daily_basic=[{"ts_code": self.ticker,
                                           "trade_date": self.days[-2], "total_mv": 1_100_000.0}])
        self.assertEqual(0, result["counts"]["selected"])
        self.assertEqual(1, result["data_error_counts"]["missing_current_market_cap"])

    def test_all_original_hard_conditions_remain_effective(self):
        flat_then_jump = self.bars()
        for row in flat_then_jump:
            row["close"] = 10.0
        flat_then_jump[-1]["close"] = 11.0
        cases = [(self.bars(amount=879_999.0), {}, "avg_amount_7d"),
                 (self.bars(), {"cap": 999_999.0}, "total_market_cap"),
                 (self.bars(), {"name": "*ST测试"}, "non_st"),
                 (self.bars(limit_jump=False), {}, "limit_up_7d"),
                 (flat_then_jump, {}, "ma_bullish_5d")]
        for bars, kwargs, failure in cases:
            with self.subTest(failure=failure):
                result = self.evaluate(bars, **kwargs)
                self.assertTrue(result["data_complete"])
                self.assertFalse(result["passed"])
                self.assertIn(failure, result["failure_ids"])
        for ticker in ("300001.SZ", "688001.SH", "920001.BJ"):
            with self.subTest(ticker=ticker):
                result = self.evaluate(ticker=ticker)
                self.assertIn("limit_up_7d", result["failure_ids"])

    def test_duplicate_dates_and_short_calendar_fail_explicitly(self):
        bars = self.bars()
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            self.evaluate(bars + [copy.deepcopy(bars[-1])])
        with self.assertRaisesRegex(ValueError, "24"):
            evaluate_stock(self.ticker, "测试股份", bars, self.days[-23:], 1_100_000.0)

    def test_stock_can_pass_without_industry_sector_or_old_candidate_membership(self):
        baseline = self.scan_one()
        self.assertEqual(1, baseline["counts"]["selected"])
        row = baseline["selected"][0]
        self.assertEqual("分类待补齐", row["industry"])
        self.assertFalse(row["in_original_sector_candidate_pool"])
        self.assertEqual([], row["matching_sector_codes"])
        weak_sector = {"sector_name": "弱势大板块", "sector_code": "S1",
                       "member_tickers": [self.ticker] + [f"{n:06}.SZ" for n in range(51)],
                       "metrics": {"mainline_rank": 500}}
        with_sector = self.scan_one(sectors={"all_sectors": [weak_sector], "candidates": []})
        self.assertEqual(1, with_sector["counts"]["selected"])
        self.assertEqual(["S1"], with_sector["selected"][0]["matching_sector_codes"])
        self.assertEqual(row["metrics"], with_sector["selected"][0]["metrics"])

    def test_identical_memberships_are_one_sector_observation_with_aliases(self):
        sectors = {"all_sectors": [
            {"sector_name": "方向甲", "sector_code": "A", "member_tickers": [self.ticker, "600001.SH"],
             "metrics": {"mainline_rank": 10}},
            {"sector_name": "方向乙", "sector_code": "B", "member_tickers": ["600001.SH", self.ticker],
             "metrics": {"mainline_rank": 20}},
        ]}
        result = self.scan_one(sectors=sectors)
        self.assertEqual(1, len(result["sector_crosschecks"]))
        grouped = result["sector_crosschecks"][0]
        self.assertEqual({"A", "B"}, {r["code"] for r in grouped["aliases"]})
        self.assertEqual([self.ticker], grouped["selected_tickers"])
        self.assertEqual(1, result["counts"]["selected"])
        self.assertEqual(1, len(result["selected"][0]["matching_sector_codes"]))

    def test_flow_net_and_large_amounts_are_separate_measures(self):
        result = flow_windows({self.ticker}, self.days, self.flow_lookup())
        for n in (1, 3, 5):
            with self.subTest(n=n):
                window = result[str(n)]
                self.assertTrue(window["net_complete"])
                self.assertTrue(window["large_complete"])
                self.assertEqual(n, window["expected_observations"])
                self.assertEqual(float(n), window["net_yi"])
                self.assertEqual(float(-2 * n), window["large_yi"])
                self.assertEqual(n, window["positive_net_days"])

    def test_missing_flow_is_not_zero_and_each_window_has_its_own_coverage(self):
        lookup = self.flow_lookup()
        del lookup[(self.ticker, self.days[-4])]
        result = flow_windows({self.ticker}, self.days, lookup)
        self.assertTrue(result["1"]["net_complete"])
        self.assertTrue(result["3"]["net_complete"])
        self.assertFalse(result["5"]["net_complete"])
        self.assertFalse(result["5"]["large_complete"])
        self.assertIsNone(result["5"]["net_yi"])
        self.assertIsNone(result["5"]["large_yi"])
        self.assertIsNone(result["5"]["positive_net_days"])
        self.assertEqual(4, result["5"]["net_covered"])
        self.assertEqual([{"ticker": self.ticker, "date": self.days[-4]}], result["5"]["missing_net"])

    def test_net_and_large_completeness_are_independent_and_zero_is_valid(self):
        lookup = self.flow_lookup()
        lookup[(self.ticker, self.days[-1])]["net_mf_amount"] = 0.0
        lookup[(self.ticker, self.days[-1])]["buy_lg_amount"] = float("nan")
        current = flow_windows({self.ticker}, self.days, lookup)["1"]
        self.assertTrue(current["net_complete"])
        self.assertEqual(0.0, current["net_yi"])
        self.assertFalse(current["large_complete"])
        self.assertIsNone(current["large_yi"])
        lookup = self.flow_lookup()
        lookup[(self.ticker, self.days[-1])]["net_mf_amount"] = float("inf")
        current = flow_windows({self.ticker}, self.days, lookup)["1"]
        self.assertFalse(current["net_complete"])
        self.assertIsNone(current["net_yi"])
        self.assertTrue(current["large_complete"])
        self.assertEqual(-2.0, current["large_yi"])

    def test_flow_coverage_counts_every_member_and_empty_group_is_incomplete(self):
        result = flow_windows({self.ticker, "600001.SH"}, self.days, self.flow_lookup())["5"]
        self.assertEqual(10, result["expected_observations"])
        self.assertEqual(5, result["net_covered"])
        self.assertFalse(result["net_complete"])
        self.assertIsNone(result["net_yi"])
        empty = flow_windows(set(), self.days, {})["5"]
        self.assertFalse(empty["net_complete"])
        self.assertIsNone(empty["net_yi"])

    def test_flow_windows_reject_short_duplicate_or_unordered_calendar(self):
        invalid_calendars = [self.days[-4:],
                             self.days[-5:] + [self.days[-1]],
                             list(reversed(self.days[-5:]))]
        for days in invalid_calendars:
            with self.subTest(days=days):
                with self.assertRaisesRegex(ValueError, "ordered, unique"):
                    flow_windows({self.ticker}, days, self.flow_lookup())


if __name__ == "__main__":
    unittest.main()
