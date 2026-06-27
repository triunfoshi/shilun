import unittest

import pandas as pd

from shilun.market import evaluate_market_permission


def _index_bars(closes: list[float], amounts: list[float]) -> pd.DataFrame:
    dates = pd.date_range(end="2026-03-25", periods=len(closes), freq="D")
    return pd.DataFrame(
        [
            {
                "ticker": "000300.SH",
                "date": date,
                "open": close * 0.995,
                "high": close * 1.01,
                "low": close * 0.99,
                "close": close,
                "volume": amount,
                "amount": amount,
            }
            for date, close, amount in zip(dates, closes, amounts)
        ]
    )


def _market_bars(
    *,
    stock_count: int,
    target_up_count: int,
    prior_up_count: int,
    target_return_up: float,
    target_return_down: float,
    limit_down_count: int = 0,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    dates = pd.date_range("2026-03-20", periods=6, freq="D")
    rows: list[dict] = []
    basics: list[dict] = []
    for idx in range(stock_count):
        ticker = f"{idx + 1:06d}.SZ"
        industry = "AI算力" if idx < stock_count // 2 else "银行"
        basics.append({"ts_code": ticker, "name": f"样本{idx}", "industry": industry, "market": "主板"})
        previous_close = 10.0
        for offset, date in enumerate(dates):
            is_target = offset == len(dates) - 1
            if is_target:
                if idx < target_up_count:
                    change = target_return_up
                elif idx < target_up_count + limit_down_count:
                    change = -0.10
                else:
                    change = target_return_down
            else:
                change = 0.01 if idx < prior_up_count else -0.004
            close = previous_close * (1.0 + change)
            rows.append(
                {
                    "ticker": ticker,
                    "date": date,
                    "open": previous_close,
                    "high": max(previous_close, close) * 1.002,
                    "low": min(previous_close, close) * 0.998,
                    "close": close,
                    "volume": 1000,
                    "amount": 10000 if industry == "AI算力" else 6000,
                }
            )
            previous_close = close
    return pd.DataFrame(rows), pd.DataFrame(basics)


class MarketPart1Tests(unittest.TestCase):
    def test_attack_permission_when_trend_volume_breadth_and_theme_confirm(self) -> None:
        index_bars = _index_bars(
            closes=[100, 101, 102, 103, 104, 105, 106, 107, 108, 109, 110, 112],
            amounts=[1000, 1000, 1000, 1000, 1000, 1020, 1030, 1040, 1050, 1060, 1070, 1220],
        )
        market_bars, stock_basic = _market_bars(
            stock_count=3200,
            target_up_count=3100,
            prior_up_count=2200,
            target_return_up=0.025,
            target_return_down=-0.003,
        )

        result = evaluate_market_permission(
            analysis_date="2026-03-25",
            benchmark_ticker="000300.SH",
            index_bars=index_bars,
            market_bars=market_bars,
            stock_basic=stock_basic,
        )

        self.assertEqual("attack", result["market_permission"])
        self.assertGreaterEqual(result["scores"]["trend_score"], 3)
        self.assertGreaterEqual(result["scores"]["breadth_score"], 1)
        self.assertEqual("proxy_only", result["data_quality"][2]["status"])
        self.assertIn("interpretation", result)
        self.assertEqual("沪深300", result["benchmark_name"])
        self.assertIn("上证指数", [item["name"] for item in result["benchmark_options"]])
        self.assertEqual("Tushare stock_basic.industry", result["theme_method"]["source"])
        self.assertIn("不是申万", result["theme_method"]["not_sw_index"])
        section_titles = [section["title"] for section in result["interpretation"]["sections"]]
        self.assertEqual(
            ["1. 指数状态", "2. 市场广度和成交额", "3. 主要板块和主线质量", "4. 状态机结论"],
            section_titles,
        )
        self.assertIn("market_amount", result["metrics"])
        self.assertTrue(result["theme_candidates"])
        theme_rows = result["interpretation"]["sections"][2]["rows"]
        self.assertIn("板块划分依据", [row["indicator"] for row in theme_rows])
        self.assertIn("standard", result["interpretation"]["scorecard"][0])
        self.assertIn("meaning", result["interpretation"]["scorecard"][0])
        self.assertEqual("daily", result["chart_data"]["data_frequency"])
        self.assertEqual(10, len(result["chart_data"]["return_distribution"]))
        self.assertTrue(result["chart_data"]["benchmark_series"])
        self.assertTrue(result["chart_data"]["breadth_series"])
        self.assertIn("score", result["chart_data"]["temperature"])

    def test_empty_permission_when_hard_risk_triggers_fire(self) -> None:
        index_bars = _index_bars(
            closes=[105, 106, 107, 108, 109, 110, 111, 112, 111, 110, 108, 90],
            amounts=[1000, 1000, 1000, 1000, 1000, 1000, 1000, 1000, 1050, 1050, 1100, 2200],
        )
        market_bars, stock_basic = _market_bars(
            stock_count=3200,
            target_up_count=500,
            prior_up_count=2300,
            target_return_up=0.002,
            target_return_down=-0.03,
            limit_down_count=80,
        )

        result = evaluate_market_permission(
            analysis_date="2026-03-25",
            benchmark_ticker="000300.SH",
            index_bars=index_bars,
            market_bars=market_bars,
            stock_basic=stock_basic,
        )

        self.assertEqual("empty", result["market_permission"])
        self.assertTrue(result["hard_triggers"])
        self.assertGreaterEqual(result["scores"]["risk_score"], 3)
        self.assertIn("硬风险", result["interpretation"]["headline"])


if __name__ == "__main__":
    unittest.main()
