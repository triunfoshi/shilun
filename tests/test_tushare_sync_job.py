import unittest

import pandas as pd

from shilun.jobs import TushareSyncJob, TushareSyncRequest


class FakeSyncClient:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def fetch_stock_basic(self, fields: str):
        self.calls.append("stock_basic")
        return pd.DataFrame([{"ts_code": "000001.SZ", "name": "甲公司", "industry": "银行", "market": "主板"}])

    def fetch_trade_calendar(self, start_date: str, end_date: str, exchange: str = "SSE"):
        self.calls.append("trade_calendar")
        return pd.DataFrame(
            [
                {"exchange": exchange, "cal_date": "20260330", "is_open": 1, "pretrade_date": "20260327"},
                {"exchange": exchange, "cal_date": "20260331", "is_open": 1, "pretrade_date": "20260330"},
            ]
        )

    def fetch_daily_for_trade_date(self, trade_date: str):
        self.calls.append(f"daily:{trade_date}")
        return pd.DataFrame(
            [
                {
                    "ticker": "000001.SZ",
                    "date": pd.Timestamp(trade_date),
                    "open": 10.0,
                    "high": 10.5,
                    "low": 9.8,
                    "close": 10.2,
                    "volume": 1000,
                    "amount": 10200,
                }
            ]
        )

    def fetch_daily_basic(self, trade_date: str):
        self.calls.append("daily_basic")
        return pd.DataFrame([{"ts_code": "000001.SZ", "trade_date": trade_date, "pe": 10.0}])

    def fetch_moneyflow(self, trade_date: str):
        self.calls.append(f"moneyflow:{trade_date}")
        return pd.DataFrame(
            [
                {
                    "ts_code": "000001.SZ",
                    "trade_date": trade_date,
                    "buy_lg_amount": 100.0,
                    "sell_lg_amount": 40.0,
                    "buy_elg_amount": 80.0,
                    "sell_elg_amount": 20.0,
                    "net_mf_amount": 120.0,
                }
            ]
        )

    def fetch_daily(self, ts_code: str, start_date: str, end_date: str):
        self.calls.append(f"benchmark:{ts_code}")
        return pd.DataFrame(
            [
                {
                    "ticker": ts_code,
                    "date": pd.Timestamp("2026-03-30"),
                    "open": 1.0,
                    "high": 1.1,
                    "low": 0.9,
                    "close": 1.0,
                    "volume": 10000,
                    "amount": 10000,
                }
            ]
        )

    def fetch_index_daily(self, ts_code: str, start_date: str, end_date: str):
        self.calls.append(f"benchmark:{ts_code}")
        return pd.DataFrame(
            [
                {
                    "ticker": ts_code,
                    "date": pd.to_datetime(end_date, format="%Y%m%d"),
                    "open": 1.0,
                    "high": 1.1,
                    "low": 0.9,
                    "close": 1.0,
                    "volume": 10000,
                    "amount": 10000,
                }
            ]
        )


class FakeSyncStore:
    def __init__(self) -> None:
        self.stock_basic_rows = 0
        self.trade_calendar_rows = 0
        self.daily_bar_rows = 0
        self.daily_basic_rows = 0
        self.moneyflow_rows = 0
        self.existing_daily_bars: list[dict] = []
        self.existing_moneyflow: list[dict] = []
        self.sync_states: dict[tuple[str, str], dict] = {}

    def upsert_stock_basic(self, rows) -> int:
        self.stock_basic_rows += len(rows)
        return len(rows)

    def upsert_trade_calendar(self, rows) -> int:
        self.trade_calendar_rows += len(rows)
        return len(rows)

    def upsert_daily_bars(self, rows) -> int:
        self.daily_bar_rows += len(rows)
        return len(rows)

    def upsert_daily_basic(self, rows) -> int:
        self.daily_basic_rows += len(rows)
        return len(rows)

    def upsert_moneyflow(self, rows) -> int:
        self.moneyflow_rows += len(rows)
        return len(rows)

    def find_moneyflow(self, *, start_date: str, end_date: str, ts_code: str | None = None) -> list[dict]:
        start_text = start_date.replace("-", "")
        end_text = end_date.replace("-", "")
        matched = []
        for record in self.existing_moneyflow:
            trade_date = str(record.get("trade_date") or record.get("date") or "").replace("-", "")
            ticker = str(record.get("ts_code") or record.get("ticker") or "")
            if ts_code and ticker != ts_code:
                continue
            if start_text <= trade_date <= end_text:
                matched.append(dict(record))
        return matched

    def find_daily_bars_for_trade_date(self, trade_date: str) -> list[dict]:
        return [dict(record) for record in self.existing_daily_bars if record.get("date") == trade_date or record.get("date") == f"{trade_date[:4]}-{trade_date[4:6]}-{trade_date[6:]}"]

    def find_daily_bars(self, *, start_date: str, end_date: str, tickers: list[str] | None = None) -> list[dict]:
        start_text = f"{start_date[:4]}-{start_date[4:6]}-{start_date[6:]}" if len(start_date) == 8 else start_date
        end_text = f"{end_date[:4]}-{end_date[4:6]}-{end_date[6:]}" if len(end_date) == 8 else end_date
        ticker_set = set(tickers or [])
        matched = []
        for record in self.existing_daily_bars:
            ticker = str(record.get("ticker") or record.get("ts_code") or "")
            date = str(record.get("date") or record.get("trade_date") or "")
            if ticker_set and ticker not in ticker_set:
                continue
            if start_text <= date <= end_text:
                matched.append(dict(record))
        return matched

    def upsert_sync_state(self, payload: dict) -> int:
        key = (str(payload.get("dataset")), str(payload.get("scope") or "default"))
        self.sync_states[key] = dict(payload)
        return 1

    def find_sync_state(self, *, dataset: str, scope: str = "default") -> dict | None:
        state = self.sync_states.get((dataset, scope))
        return dict(state) if state else None

    def close(self) -> None:
        return None


class TushareSyncJobTests(unittest.TestCase):
    def test_sync_job_writes_raw_data_to_mongo_store_without_strategy_execution(self) -> None:
        client = FakeSyncClient()
        store = FakeSyncStore()
        job = TushareSyncJob(client=client, mongo_store=store)

        result = job.run(
            TushareSyncRequest(
                target_date="2026-03-30",
                start_date="20260330",
                end_date="20260331",
                benchmark_ticker="000300.SH",
            )
        )

        self.assertEqual(1, result.stock_basic_count)
        self.assertEqual(2, result.trade_calendar_count)
        self.assertEqual(2, result.daily_bar_count)
        self.assertEqual(1, result.daily_basic_count)
        self.assertEqual(2, result.moneyflow_count)
        self.assertEqual(1, result.benchmark_bar_count)
        self.assertEqual(
            [
                "stock_basic",
                "trade_calendar",
                "daily:20260330",
                "moneyflow:20260330",
                "daily:20260331",
                "moneyflow:20260331",
                "daily_basic",
                "benchmark:000300.SH",
            ],
            client.calls,
        )

    def test_latest_only_sync_writes_latest_open_trade_date(self) -> None:
        client = FakeSyncClient()
        store = FakeSyncStore()
        job = TushareSyncJob(client=client, mongo_store=store)

        result = job.run(
            TushareSyncRequest(
                target_date="2026-03-31",
                latest_only=True,
                benchmark_ticker="000300.SH",
            )
        )

        self.assertFalse(result.skipped)
        self.assertEqual("2026-03-31", result.sync_trade_date)
        self.assertEqual(1, result.daily_bar_count)
        self.assertEqual(1, result.daily_basic_count)
        self.assertEqual(1, result.moneyflow_count)
        self.assertEqual(["trade_calendar", "stock_basic", "daily:20260331", "daily_basic", "moneyflow:20260331", "benchmark:000300.SH"], client.calls)

    def test_latest_only_sync_can_fill_all_supported_benchmarks(self) -> None:
        client = FakeSyncClient()
        store = FakeSyncStore()
        job = TushareSyncJob(client=client, mongo_store=store)

        result = job.run(
            TushareSyncRequest(
                target_date="2026-03-31",
                latest_only=True,
                benchmark_ticker="000001.SH",
                sync_all_benchmarks=True,
            )
        )

        self.assertEqual(4, result.benchmark_bar_count)
        self.assertEqual(
            {
                "benchmark:000001.SH",
                "benchmark:000300.SH",
                "benchmark:399001.SZ",
                "benchmark:399006.SZ",
            },
            {call for call in client.calls if call.startswith("benchmark:")},
        )

    def test_latest_only_sync_skips_when_mongo_already_has_trade_date(self) -> None:
        client = FakeSyncClient()
        store = FakeSyncStore()
        store.existing_daily_bars = [
            {"ticker": "000001.SZ", "date": "2026-03-31"},
            *[
                {"ticker": "000300.SH", "date": f"2026-03-{day:02d}"}
                for day in range(1, 32)
            ],
        ]
        store.existing_moneyflow = [{"ts_code": "000001.SZ", "trade_date": "20260331"}]
        job = TushareSyncJob(client=client, mongo_store=store)

        result = job.run(
            TushareSyncRequest(
                target_date="2026-03-31",
                latest_only=True,
                skip_if_exists=True,
            )
        )

        self.assertTrue(result.skipped)
        self.assertEqual("2026-03-31", result.sync_trade_date)
        self.assertEqual(2, result.daily_bar_count)
        self.assertEqual(["trade_calendar"], client.calls)

    def test_latest_only_sync_backfills_missing_benchmark_even_when_market_exists(self) -> None:
        client = FakeSyncClient()
        store = FakeSyncStore()
        store.existing_daily_bars = [{"ticker": "000001.SZ", "date": "2026-03-31"}]
        job = TushareSyncJob(client=client, mongo_store=store)

        result = job.run(
            TushareSyncRequest(
                target_date="2026-03-31",
                latest_only=True,
                skip_if_exists=True,
            )
        )

        self.assertFalse(result.skipped)
        self.assertEqual(1, result.benchmark_bar_count)
        self.assertIn("benchmark:000300.SH", client.calls)

    def test_latest_only_does_not_treat_old_benchmark_window_as_target_date_ready(self) -> None:
        client = FakeSyncClient()
        store = FakeSyncStore()
        store.existing_daily_bars = [{"ticker": "000001.SZ", "date": "2026-03-31"}]
        store.existing_daily_bars.extend(
            {
                "ticker": "000300.SH",
                "date": f"2026-03-{day:02d}",
            }
            for day in range(1, 21)
        )
        job = TushareSyncJob(client=client, mongo_store=store)

        result = job.run(
            TushareSyncRequest(
                target_date="2026-03-31",
                latest_only=True,
                skip_if_exists=True,
                benchmark_ticker="000300.SH",
            )
        )

        self.assertFalse(result.skipped)
        self.assertEqual(1, result.benchmark_bar_count)
        self.assertIn("benchmark:000300.SH", client.calls)

    def test_incremental_sync_uses_watermark_and_requests_only_missing_dates(self) -> None:
        client = FakeSyncClient()
        store = FakeSyncStore()
        store.existing_daily_bars = [{"ticker": "000001.SZ", "date": "2026-03-30"}]
        store.existing_moneyflow = [{"ts_code": "000001.SZ", "trade_date": "20260330"}]
        store.sync_states[("tushare_daily", "SSE")] = {"last_trade_date_raw": "20260330"}
        store.sync_states[("tushare_stock_basic", "all")] = {"status": "success"}
        job = TushareSyncJob(client=client, mongo_store=store)

        result = job.run(
            TushareSyncRequest(
                target_date="2026-03-31",
                incremental=True,
                skip_if_exists=True,
                benchmark_ticker=None,
                incremental_overlap_days=1,
            )
        )

        self.assertFalse(result.skipped)
        self.assertEqual("2026-03-31", result.sync_trade_date)
        self.assertEqual(("2026-03-31",), result.synced_trade_dates)
        self.assertEqual(("2026-03-30",), result.skipped_trade_dates)
        self.assertEqual(1, result.daily_bar_count)
        self.assertEqual(1, result.daily_basic_count)
        self.assertEqual(1, result.moneyflow_count)
        self.assertEqual(["trade_calendar", "daily:20260331", "daily_basic", "moneyflow:20260331"], client.calls)
        state = store.sync_states[("tushare_daily", "SSE")]
        self.assertEqual("20260331", state["last_trade_date_raw"])
        self.assertEqual("success", state["status"])


if __name__ == "__main__":
    unittest.main()
