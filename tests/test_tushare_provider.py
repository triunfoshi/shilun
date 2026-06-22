import unittest
from pathlib import Path
import sys

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from shilun.data.providers import TushareConfig, TushareDailyClient, TushareDailyImporter


class FakeProClient:
    def daily(self, **kwargs) -> pd.DataFrame:
        if "trade_date" in kwargs:
            return pd.DataFrame(
                [
                    {
                        "ts_code": "000001.SZ",
                        "trade_date": kwargs["trade_date"],
                        "open": 9.46,
                        "high": 9.56,
                        "low": 9.40,
                        "close": 9.45,
                        "vol": 1357217.47,
                        "amount": 1286857.397,
                    },
                    {
                        "ts_code": "000002.SZ",
                        "trade_date": kwargs["trade_date"],
                        "open": 12.00,
                        "high": 12.30,
                        "low": 11.80,
                        "close": 12.10,
                        "vol": 555000.00,
                        "amount": 660000.000,
                    },
                ]
            )
        return pd.DataFrame(
            [
                {
                    "ts_code": "000001.SZ",
                    "trade_date": "20240131",
                    "open": 9.46,
                    "high": 9.56,
                    "low": 9.40,
                    "close": 9.45,
                    "vol": 1357217.47,
                    "amount": 1286857.397,
                },
                {
                    "ts_code": "000001.SZ",
                    "trade_date": "20240130",
                    "open": 9.61,
                    "high": 9.70,
                    "low": 9.48,
                    "close": 9.49,
                    "vol": 1579122.14,
                    "amount": 1515156.604,
                },
                {
                    "ts_code": "000001.SZ",
                    "trade_date": "20240130",
                    "open": 9.61,
                    "high": 9.70,
                    "low": 9.48,
                    "close": 9.49,
                    "vol": 1579122.14,
                    "amount": 1515156.604,
                },
            ]
        )

    def stock_basic(self, **kwargs) -> pd.DataFrame:
        return pd.DataFrame(
            [
                {"ts_code": "000001.SZ", "symbol": "000001", "name": "平安银行", "industry": "银行", "market": "主板"},
                {"ts_code": "000002.SZ", "symbol": "000002", "name": "万科A", "industry": "地产", "market": "主板"},
            ]
        )

    def trade_cal(self, **kwargs) -> pd.DataFrame:
        return pd.DataFrame(
            [
                {"exchange": "SSE", "cal_date": "20240130", "is_open": 1, "pretrade_date": "20240129"},
                {"exchange": "SSE", "cal_date": "20240131", "is_open": 1, "pretrade_date": "20240130"},
            ]
        )

    def moneyflow(self, **kwargs) -> pd.DataFrame:
        return pd.DataFrame(
            [
                {
                    "ts_code": "000001.SZ",
                    "trade_date": kwargs.get("trade_date") or "20240131",
                    "buy_lg_amount": 1200.0,
                    "sell_lg_amount": 800.0,
                    "buy_elg_amount": 900.0,
                    "sell_elg_amount": 500.0,
                    "net_mf_amount": 600.0,
                }
            ]
        )


class TushareProviderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = TushareConfig(token="demo", base_url="http://example.com", min_interval_seconds=0)
        self.client = TushareDailyClient(config=self.config, pro_client=FakeProClient())

    def test_fetch_daily_normalizes_columns(self) -> None:
        df = self.client.fetch_daily("000001.SZ", "20240101", "20240131")
        self.assertEqual(
            ["ticker", "date", "open", "high", "low", "close", "volume", "amount"],
            list(df.columns),
        )
        self.assertEqual("000001.SZ", df.iloc[0]["ticker"])
        self.assertTrue(pd.api.types.is_datetime64_any_dtype(df["date"]))

    def test_import_daily_applies_cleaner_and_counts_skipped_rows(self) -> None:
        importer = TushareDailyImporter(client=self.client)
        cleaned, result = importer.import_daily("000001.SZ", "20240101", "20240131")
        self.assertEqual(2, len(cleaned))
        self.assertEqual(2, result.imported_rows)
        self.assertEqual(1, result.skipped_rows)

    def test_fetch_daily_for_trade_date_normalizes_all_stocks(self) -> None:
        df = self.client.fetch_daily_for_trade_date("20240131")
        self.assertEqual(["ticker", "date", "open", "high", "low", "close", "volume", "amount"], list(df.columns))
        self.assertEqual(2, len(df))
        self.assertEqual("000001.SZ", df.iloc[0]["ticker"])

    def test_fetch_stock_basic_returns_universe(self) -> None:
        df = self.client.fetch_stock_basic(fields="ts_code,symbol,name,industry,market")
        self.assertEqual(["ts_code", "symbol", "name", "industry", "market"], list(df.columns))
        self.assertEqual(2, len(df))

    def test_fetch_trade_calendar_normalizes_open_flag(self) -> None:
        df = self.client.fetch_trade_calendar(start_date="20240130", end_date="20240131")
        self.assertEqual(["exchange", "cal_date", "is_open", "pretrade_date"], list(df.columns))
        self.assertEqual([1, 1], df["is_open"].tolist())

    def test_fetch_moneyflow_normalizes_trade_date_and_amounts(self) -> None:
        df = self.client.fetch_moneyflow(trade_date="20240131")
        self.assertEqual("000001.SZ", df.iloc[0]["ts_code"])
        self.assertTrue(pd.api.types.is_datetime64_any_dtype(df["trade_date"]))
        self.assertEqual(1200.0, df.iloc[0]["buy_lg_amount"])


if __name__ == "__main__":
    unittest.main()
