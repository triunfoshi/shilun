from __future__ import annotations

import unittest

import pandas as pd

from shilun.data.providers.tushare_realtime import TushareRealtimeClient


class TushareRealtimeTimestampTests(unittest.TestCase):
    def test_normalize_preserves_source_quote_time(self) -> None:
        client = TushareRealtimeClient.__new__(TushareRealtimeClient)
        raw = pd.DataFrame([{
            "code": "603986",
            "name": "兆易创新",
            "price": "612.00",
            "pre_close": "620.00",
            "open": "618.00",
            "high": "625.00",
            "low": "607.18",
            "volume": "1000",
            "amount": "612000",
            "date": "2026-07-10",
            "time": "15:34:59",
        }])

        result = client._normalize(raw, ["603986.SH"])

        self.assertEqual(result.iloc[0]["ticker"], "603986.SH")
        self.assertEqual(result.iloc[0]["quote_at"], "2026-07-10T15:34:59+08:00")
        self.assertEqual(result.iloc[0]["update_at"], result.iloc[0]["quote_at"])
        self.assertEqual(result.iloc[0]["timestamp_kind"], "source_quote_at")
        self.assertNotEqual(result.iloc[0]["fetched_at"], result.iloc[0]["quote_at"])

    def test_missing_source_time_does_not_fabricate_update_time(self) -> None:
        client = TushareRealtimeClient.__new__(TushareRealtimeClient)
        raw = pd.DataFrame([{
            "code": "603986",
            "name": "兆易创新",
            "price": "612.00",
            "pre_close": "620.00",
        }])

        result = client._normalize(raw, ["603986.SH"])

        self.assertIsNone(result.iloc[0]["quote_at"])
        self.assertIsNone(result.iloc[0]["update_at"])
        self.assertTrue(result.iloc[0]["fetched_at"])

    def test_requested_ticker_disambiguates_index_and_sz_stock_code(self) -> None:
        client = TushareRealtimeClient.__new__(TushareRealtimeClient)
        raw = pd.DataFrame([{
            "code": "000001",
            "name": "平安银行",
            "price": "10.00",
            "pre_close": "9.90",
            "date": "2026-07-10",
            "time": "10:00:00",
        }])

        result = client._normalize(raw, ["000001.SZ"])

        self.assertEqual(result.iloc[0]["ticker"], "000001.SZ")


if __name__ == "__main__":
    unittest.main()
