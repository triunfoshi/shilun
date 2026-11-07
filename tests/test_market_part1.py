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


def _peer_index_bars() -> pd.DataFrame:
    dates = pd.date_range(end="2026-03-25", periods=12, freq="D")
    final_changes = {
        "000001.SH": -0.0100,
        "000300.SH": -0.0196,
        "399001.SZ": -0.0229,
        "399006.SZ": -0.0437,
        "000688.SH": -0.0553,
    }
    rows = []
    for ticker, final_change in final_changes.items():
        previous = 100.0
        for index, date in enumerate(dates):
            change = final_change if index == len(dates) - 1 else 0.002
            close = previous * (1 + change)
            rows.append({
                "ticker": ticker,
                "date": date,
                "open": previous * 1.004 if index == len(dates) - 1 else previous,
                "high": max(previous, close) * 1.008,
                "low": min(previous, close) * 0.996,
                "close": close,
                "volume": 1000,
                "amount": 1000,
            })
            previous = close
    return pd.DataFrame(rows)


class MarketPart1Tests(unittest.TestCase):
    def test_cross_index_stress_adds_risk_and_keeps_selected_structure_distinct(self) -> None:
        market_bars, stock_basic = _market_bars(
            stock_count=3200,
            target_up_count=900,
            prior_up_count=2200,
            target_return_up=0.01,
            target_return_down=-0.02,
        )
        peers = _peer_index_bars()
        selected = peers.loc[peers["ticker"] == "399006.SZ"].copy()

        result = evaluate_market_permission(
            analysis_date="2026-03-25",
            benchmark_ticker="399006.SZ",
            index_bars=selected,
            peer_index_bars=peers,
            market_bars=market_bars,
            stock_basic=stock_basic,
        )

        self.assertEqual("创业板指", result["benchmark_name"])
        self.assertAlmostEqual(-0.0437, result["metrics"]["index_pct_chg"], places=4)
        self.assertEqual(5, len(result["cross_index_context"]["items"]))
        self.assertEqual("科创50", result["cross_index_context"]["worst_name"])
        self.assertGreaterEqual(result["scores"]["risk_score"], 4)
        self.assertTrue(any("多指数交叉确认" in item for item in result["risk_evidence"]))

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
        self.assertEqual("东方财富三级行业 · 全站统一目录", result["theme_method"]["source"])
        self.assertEqual("eastmoney-taxonomy-v1", result["theme_method"]["taxonomy_version"])
        section_titles = [section["title"] for section in result["interpretation"]["sections"]]
        self.assertEqual(
            ["0. 波浪结构 + 明日关键位", "1. 指数状态", "2. 市场广度和成交额", "3. 主要板块和主线质量", "4. 状态机结论"],
            section_titles,
        )
        self.assertIn("market_amount", result["metrics"])
        self.assertIn("limit_up_count_ma5", result["metrics"])
        self.assertIn("limit_down_count_ma5", result["metrics"])
        self.assertTrue(result["theme_candidates"])
        theme_rows = result["interpretation"]["sections"][3]["rows"]
        self.assertIn("板块划分依据", [row["indicator"] for row in theme_rows])
        self.assertIn("standard", result["interpretation"]["scorecard"][0])
        self.assertIn("meaning", result["interpretation"]["scorecard"][0])
        self.assertEqual("daily", result["chart_data"]["data_frequency"])
        self.assertEqual(10, len(result["chart_data"]["return_distribution"]))
        self.assertTrue(result["chart_data"]["benchmark_series"])
        self.assertTrue(result["chart_data"]["wave_series"])
        self.assertFalse(any(
            "外部波浪理论文档" in item["field"]
            for item in result["pattern_forecast"]["data_gaps"]
        ))
        self.assertIn("open", result["chart_data"]["wave_series"][-1])
        self.assertIn("high", result["chart_data"]["wave_series"][-1])
        self.assertIn("low", result["chart_data"]["wave_series"][-1])
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
