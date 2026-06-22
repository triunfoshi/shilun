import unittest

import pandas as pd

from shilun.services import AnalyzeRequest, MongoFirstAnalysisService


class FakeAnalysisProvider:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def fetch_daily(self, ts_code: str, start_date: str, end_date: str) -> pd.DataFrame:
        self.calls.append(("daily", ts_code, start_date, end_date))
        if ts_code == "000001.SZ":
            return pd.DataFrame(
                [
                    {"ticker": ts_code, "date": "2026-03-27", "open": 10, "high": 10.2, "low": 9.8, "close": 10.0, "volume": 1000, "amount": 10000},
                    {"ticker": ts_code, "date": "2026-03-30", "open": 10.1, "high": 10.5, "low": 10.0, "close": 10.4, "volume": 1200, "amount": 12480},
                ]
            )
        if ts_code == "000300.SH":
            return pd.DataFrame(
                [
                    {"ticker": ts_code, "date": "2026-03-27", "open": 1, "high": 1.01, "low": 0.99, "close": 1.0, "volume": 10000, "amount": 10000},
                    {"ticker": ts_code, "date": "2026-03-30", "open": 1, "high": 1.02, "low": 0.99, "close": 1.01, "volume": 10000, "amount": 10100},
                ]
            )
        return pd.DataFrame(columns=["ticker", "date", "open", "high", "low", "close", "volume", "amount"])

    def fetch_stock_basic(self, fields: str) -> pd.DataFrame:
        self.calls.append(("stock_basic", fields))
        return pd.DataFrame([{"ts_code": "000001.SZ", "name": "甲公司", "industry": "银行", "market": "主板"}])

    def fetch_daily_basic(self, ts_code: str | None = None, trade_date: str | None = None, fields: str = "") -> pd.DataFrame:
        self.calls.append(("daily_basic", ts_code, trade_date))
        return pd.DataFrame([{"ts_code": ts_code, "trade_date": trade_date, "pe": 10.0, "pb": 1.1}])


class FakeAnalysisPipeline:
    class Config:
        lookback_days = 30
        benchmark_ticker = "000300.SH"

    def __init__(self) -> None:
        self.config = self.Config()
        self.calls: list[dict] = []

    def run_with_bars(self, **kwargs):
        self.calls.append(kwargs)
        return {
            "ticker": kwargs["ticker"],
            "date": kwargs["analysis_date"],
            "snapshot": {
                "metadata_context": kwargs.get("metadata_context"),
                "daily_basic_context": kwargs.get("daily_basic_context"),
            },
            "decision": {"conclusion_label": "confirmation_needed"},
            "explanation": "ok",
        }


class AnalysisServiceTests(unittest.TestCase):
    def test_analyze_reads_mongo_provider_and_calls_run_with_bars(self) -> None:
        provider = FakeAnalysisProvider()
        pipeline = FakeAnalysisPipeline()
        service = MongoFirstAnalysisService(pipeline=pipeline, market_data_provider=provider)

        result = service.analyze(AnalyzeRequest(ticker="000001.SZ", analysis_date="2026-03-30"))

        self.assertEqual("mongo", result["data_source"])
        self.assertEqual("000001.SZ", result["ticker"])
        self.assertEqual("甲公司", pipeline.calls[0]["metadata_context"]["name"])
        self.assertEqual(10.0, pipeline.calls[0]["daily_basic_context"]["pe"])
        self.assertFalse(pipeline.calls[0]["benchmark_bars"].empty)
        self.assertIn(("stock_basic", "ts_code,name,industry,market"), provider.calls)

    def test_analyze_requires_mongo_data_unless_fallback_enabled(self) -> None:
        service = MongoFirstAnalysisService(pipeline=FakeAnalysisPipeline(), market_data_provider=None)

        with self.assertRaisesRegex(ValueError, "Mongo market data is required"):
            service.analyze(AnalyzeRequest(ticker="000001.SZ", analysis_date="2026-03-30"))


if __name__ == "__main__":
    unittest.main()
