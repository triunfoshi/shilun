import unittest

import pandas as pd

from shilun.market import evaluate_daily_leaders, evaluate_sector_trends


def _benchmark_bars() -> pd.DataFrame:
    dates = pd.date_range("2026-03-16", periods=10, freq="D")
    closes = [100, 100.4, 100.8, 101.1, 101.4, 101.7, 102.0, 102.2, 102.4, 102.7]
    rows = []
    for date, close in zip(dates, closes):
        rows.append(
            {
                "ticker": "000001.SH",
                "date": date,
                "open": close * 0.998,
                "high": close * 1.004,
                "low": close * 0.996,
                "close": close,
                "amount": 100000,
            }
        )
    return pd.DataFrame(rows)


def _market_rows() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    dates = pd.date_range("2026-03-16", periods=10, freq="D")
    basics = []
    daily_basic = []
    rows = []
    sector_specs = [
        ("光通信", "300", [0.012, 0.018, 0.025, 0.031, 0.040, 0.018, 0.026, 0.035, 0.022, 0.045], 18000),
        ("银行", "601", [-0.002, 0.001, 0.000, -0.001, 0.002, -0.003, 0.001, 0.000, -0.002, 0.001], 7000),
    ]
    for sector_name, prefix, changes, base_amount in sector_specs:
        for stock_idx in range(6):
            ticker = f"{prefix}{stock_idx + 1:03d}.SZ" if prefix == "300" else f"{prefix}{stock_idx + 1:03d}.SH"
            name = f"{sector_name}{stock_idx + 1}"
            basics.append({"ts_code": ticker, "name": name, "industry": sector_name, "market": "主板"})
            previous_close = 10.0 + stock_idx
            for offset, date in enumerate(dates):
                change = changes[offset]
                if sector_name == "光通信" and stock_idx == 0 and offset in {4, 9}:
                    change = 0.098
                if sector_name == "光通信" and stock_idx == 1:
                    change = min(change * 0.75, 0.035)
                close = previous_close * (1.0 + change)
                amount = base_amount * (1.0 + stock_idx * 0.08)
                if sector_name == "光通信" and offset == len(dates) - 1:
                    amount *= 1.8
                rows.append(
                    {
                        "ticker": ticker,
                        "date": date,
                        "open": previous_close,
                        "high": close * 1.004,
                        "low": min(previous_close, close) * 0.995,
                        "close": close,
                        "volume": amount / close,
                        "amount": amount,
                    }
                )
                if offset == len(dates) - 1:
                    daily_basic.append(
                        {
                            "ts_code": ticker,
                            "trade_date": date.strftime("%Y%m%d"),
                            "turnover_rate": 6.0 if sector_name == "光通信" else 1.5,
                            "circ_mv": 800000 + stock_idx * 50000 if sector_name == "光通信" and stock_idx == 1 else 200000 + stock_idx * 10000,
                        }
                    )
                previous_close = close
    return pd.DataFrame(rows), pd.DataFrame(basics), pd.DataFrame(daily_basic)


def _moneyflow_rows(market_bars: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for record in market_bars.to_dict(orient="records"):
        ticker = str(record["ticker"])
        is_hot = ticker.startswith("300")
        rows.append(
            {
                "ts_code": ticker,
                "trade_date": record["date"].strftime("%Y%m%d"),
                "buy_lg_amount": 120.0 if is_hot else 20.0,
                "sell_lg_amount": 40.0 if is_hot else 35.0,
                "buy_elg_amount": 100.0 if is_hot else 10.0,
                "sell_elg_amount": 30.0 if is_hot else 30.0,
                "net_mf_amount": 150.0 if is_hot else -20.0,
            }
        )
    return pd.DataFrame(rows)


class MarketSectorTests(unittest.TestCase):
    def test_sector_trends_identify_mainline_and_core_candidates(self) -> None:
        market_bars, stock_basic, daily_basic = _market_rows()

        result = evaluate_sector_trends(
            analysis_date="2026-03-25",
            benchmark_ticker="000001.SH",
            benchmark_bars=_benchmark_bars(),
            market_bars=market_bars,
            stock_basic=stock_basic,
            daily_basic=daily_basic,
            top_n=3,
        )

        self.assertEqual("market_sector_v4_ma5_v02_relative_mainline", result["engine_version"])
        self.assertEqual("上证指数", result["benchmark_name"])
        self.assertIn("不是申万", result["sector_source_note"])
        leader_sector = result["top_sectors"][0]
        self.assertEqual("光通信", leader_sector["sector_name"])
        self.assertIn(leader_sector["stage"], {"confirm", "main_uptrend", "accelerate", "repair"})
        self.assertGreater(leader_sector["scores"]["sector_score"], 40)
        self.assertTrue(leader_sector["leader_candidates"])
        self.assertTrue(leader_sector["zhongjun_candidates"])
        self.assertEqual("moneyflow_data_pending", leader_sector["fund_flow"]["data_status"])
        self.assertIn("成交额活跃", result["summary"]["warning"])
        data_quality = {item["field"]: item["status"] for item in result["data_quality"]}
        self.assertEqual("data_pending", data_quality["moneyflow"])
        definitions = [item["indicator"] for item in result["indicator_definitions"]]
        self.assertIn("leader_score", definitions)
        self.assertIn("repair_confirmed", definitions)

    def test_sector_trends_use_moneyflow_when_available(self) -> None:
        market_bars, stock_basic, daily_basic = _market_rows()

        result = evaluate_sector_trends(
            analysis_date="2026-03-25",
            benchmark_ticker="000001.SH",
            benchmark_bars=_benchmark_bars(),
            market_bars=market_bars,
            stock_basic=stock_basic,
            daily_basic=daily_basic,
            moneyflow=_moneyflow_rows(market_bars),
            top_n=3,
        )

        leader_sector = result["top_sectors"][0]
        self.assertEqual("implemented", leader_sector["fund_flow"]["data_status"])
        self.assertIsNotNone(leader_sector["fund_flow"]["main_net_inflow"])
        self.assertGreater(leader_sector["metrics"]["positive_moneyflow_ratio"], 0)
        data_quality = {item["field"]: item["status"] for item in result["data_quality"]}
        self.assertEqual("implemented", data_quality["moneyflow"])
        self.assertTrue(result["daily_leaders"])

    def test_sector_trends_support_lightweight_initial_response(self) -> None:
        market_bars, stock_basic, daily_basic = _market_rows()

        result = evaluate_sector_trends(
            analysis_date="2026-03-25",
            benchmark_ticker="000001.SH",
            benchmark_bars=_benchmark_bars(),
            market_bars=market_bars,
            stock_basic=stock_basic,
            daily_basic=daily_basic,
            top_n=3,
            include_daily_leaders=False,
            include_all_sectors=False,
        )

        self.assertTrue(result["top_sectors"])
        self.assertTrue(result["trend_sectors"])
        self.assertEqual(60, result["trend_lookback_days"])
        trend = result["trend_sectors"][0]
        self.assertIn("sector_mainline_score", trend)
        self.assertIn("sector_multiplier", trend)
        self.assertIn("sector_state", trend)
        self.assertIn("mainline_rank", trend)
        self.assertIn("excess_return_20d_score", trend["scores"])
        self.assertIn("sector_amount_ratio_score", trend["scores"])
        self.assertIn("score_references", trend["scores"])
        self.assertIn("20日相对强度", trend["scores"]["score_references"])
        self.assertTrue(result["candidates"])
        candidate = result["candidates"][0]
        self.assertIn("predicted_buy_price", candidate)
        self.assertIn("support_price", candidate)
        self.assertIn("pressure_price", candidate)
        self.assertIn("ma8", candidate)
        self.assertIn("expected_sell_price", candidate)
        self.assertIn("trend_boost", candidate)
        self.assertIn("sector_mainline_score", candidate)
        self.assertIn("sector_multiplier", candidate)
        self.assertIn("stock_quality_score", candidate)
        self.assertIn("trade_timing_score", candidate)
        self.assertIn("risk_adjustment", candidate)
        self.assertIn("final_trade_score", candidate)
        self.assertIn("score_breakdown", candidate)
        self.assertIn("trade_plan", candidate)
        # Job 6：默认无 lookup 时，breakout_tracking=None，特征快照不含事件字段覆写
        self.assertIsNone(candidate.get("breakout_tracking"))
        breakout_quality_source = candidate["score_breakdown"]["trade_timing_score"]["parts"]["breakout_quality"]["source"]
        self.assertEqual(breakout_quality_source, "features")
        self.assertEqual([], result["daily_leaders"])
        self.assertEqual([], result["all_sectors"])

    def test_sector_trends_injects_breakout_events_lookup(self) -> None:
        """Job 6：传入 breakout_events_lookup 时，候选卡带 breakout_tracking 且评分接了事件。"""
        market_bars, stock_basic, daily_basic = _market_rows()

        # 找一只票，把它假设为已落库的突破，构造 event
        sample_ticker = str(market_bars["ticker"].iloc[0])
        event = {
            "ticker": sample_ticker,
            "breakout_date": "2026-03-24",
            "status": "settled",
            "tracked_days": 5,
            "breakout_quality": "valid",
            "next_day_hold_flag": True,
            "previous_high_hold_ratio": 0.008,
            "post_breakout_shrink_ratio": 0.7,
            "fall_back_into_box_flag": False,
        }
        lookup = {sample_ticker: event}

        result = evaluate_sector_trends(
            analysis_date="2026-03-25",
            benchmark_ticker="000001.SH",
            benchmark_bars=_benchmark_bars(),
            market_bars=market_bars,
            stock_basic=stock_basic,
            daily_basic=daily_basic,
            top_n=3,
            include_daily_leaders=False,
            include_all_sectors=False,
            breakout_events_lookup=lookup,
        )

        matched = next(
            (c for c in result["candidates"] if c["ticker"] == sample_ticker),
            None,
        )
        if matched is None:
            self.skipTest(f"{sample_ticker} 未进入候选池，跳过 lookup 注入验证。")
        self.assertIsNotNone(matched["breakout_tracking"])
        self.assertEqual(matched["breakout_tracking"]["breakout_quality"], "valid")
        self.assertEqual(matched["breakout_tracking"]["tracked_days"], 5)
        # 评分层显示 source=event
        bq = matched["score_breakdown"]["trade_timing_score"]["parts"]["breakout_quality"]
        self.assertEqual(bq["source"], "event")
        self.assertEqual(bq["breakout_quality"], "valid")
        # 特征快照的追踪字段应从事件覆写而来
        snapshot = matched["ma5_feature_snapshot"]
        self.assertAlmostEqual(snapshot["previous_high_hold_ratio"], 0.008)
        self.assertAlmostEqual(snapshot["post_breakout_shrink_ratio"], 0.7)
        self.assertFalse(snapshot["fall_back_into_box_flag"])

    def test_daily_leaders_can_be_calculated_separately(self) -> None:
        market_bars, stock_basic, daily_basic = _market_rows()

        result = evaluate_daily_leaders(
            analysis_date="2026-03-25",
            benchmark_ticker="000001.SH",
            benchmark_bars=_benchmark_bars(),
            market_bars=market_bars,
            stock_basic=stock_basic,
            daily_basic=daily_basic,
            top_n=3,
        )

        self.assertTrue(result["daily_leaders"])
        self.assertLessEqual(len(result["daily_leaders"][-1]["leaders"]), 3)
        self.assertTrue(result["leader_summary"]["ranking"])
        self.assertLessEqual(result["leader_summary"]["trading_day_count"], 30)
        self.assertIn("上榜次数", result["leader_summary"]["formula"])


if __name__ == "__main__":
    unittest.main()
