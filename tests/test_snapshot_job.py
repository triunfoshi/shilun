import tempfile
import unittest
from pathlib import Path
import sys

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from shilun.jobs import SnapshotJob, SnapshotJobRequest


class FakeSnapshotClient:
    def fetch_stock_basic(self, fields: str = "ts_code,name,industry,market") -> pd.DataFrame:
        return pd.DataFrame(
            [
                {"ts_code": "000001.SZ", "name": "甲公司", "industry": "银行", "market": "主板"},
                {"ts_code": "000002.SZ", "name": "乙公司", "industry": "地产", "market": "主板"},
                {"ts_code": "000003.SZ", "name": "丙公司", "industry": "软件", "market": "创业板"},
            ]
        )

    def fetch_trade_calendar(self, start_date: str, end_date: str, exchange: str = "SSE") -> pd.DataFrame:
        return pd.DataFrame(
            [
                {"exchange": exchange, "cal_date": "20260327", "is_open": 1, "pretrade_date": "20260326"},
                {"exchange": exchange, "cal_date": "20260330", "is_open": 1, "pretrade_date": "20260327"},
                {"exchange": exchange, "cal_date": "20260331", "is_open": 1, "pretrade_date": "20260330"},
                {"exchange": exchange, "cal_date": "20260401", "is_open": 1, "pretrade_date": "20260331"},
                {"exchange": exchange, "cal_date": "20260402", "is_open": 1, "pretrade_date": "20260401"},
                {"exchange": exchange, "cal_date": "20260403", "is_open": 1, "pretrade_date": "20260402"},
                {"exchange": exchange, "cal_date": "20260406", "is_open": 1, "pretrade_date": "20260403"},
                {"exchange": exchange, "cal_date": "20260407", "is_open": 1, "pretrade_date": "20260406"},
                {"exchange": exchange, "cal_date": "20260408", "is_open": 1, "pretrade_date": "20260407"},
                {"exchange": exchange, "cal_date": "20260409", "is_open": 1, "pretrade_date": "20260408"},
            ]
        )

    def fetch_daily_for_trade_date(self, trade_date: str) -> pd.DataFrame:
        close_map = {
            "20260327": {"000001.SZ": 10.0, "000002.SZ": 8.0, "000003.SZ": 6.0},
            "20260330": {"000001.SZ": 10.2, "000002.SZ": 8.1, "000003.SZ": 5.9},
            "20260331": {"000001.SZ": 10.6, "000002.SZ": 8.0, "000003.SZ": 5.8},
            "20260401": {"000001.SZ": 10.9, "000002.SZ": 8.2, "000003.SZ": 5.7},
            "20260402": {"000001.SZ": 11.0, "000002.SZ": 8.3, "000003.SZ": 5.6},
            "20260403": {"000001.SZ": 11.2, "000002.SZ": 8.15, "000003.SZ": 5.5},
            "20260406": {"000001.SZ": 11.3, "000002.SZ": 8.0, "000003.SZ": 5.4},
            "20260407": {"000001.SZ": 11.4, "000002.SZ": 7.95, "000003.SZ": 5.3},
            "20260408": {"000001.SZ": 11.45, "000002.SZ": 7.9, "000003.SZ": 5.2},
            "20260409": {"000001.SZ": 11.5, "000002.SZ": 7.85, "000003.SZ": 5.1},
        }
        closes = close_map[trade_date]
        rows = [
            {"ticker": "000001.SZ", "date": trade_date, "open": closes["000001.SZ"] * 0.99, "high": closes["000001.SZ"] * 1.02, "low": closes["000001.SZ"] * 0.98, "close": closes["000001.SZ"], "volume": 1000, "amount": closes["000001.SZ"] * 1000},
            {"ticker": "000002.SZ", "date": trade_date, "open": closes["000002.SZ"] * 0.99, "high": closes["000002.SZ"] * 1.02, "low": closes["000002.SZ"] * 0.98, "close": closes["000002.SZ"], "volume": 800, "amount": closes["000002.SZ"] * 800},
            {"ticker": "000003.SZ", "date": trade_date, "open": closes["000003.SZ"] * 0.99, "high": closes["000003.SZ"] * 1.02, "low": closes["000003.SZ"] * 0.98, "close": closes["000003.SZ"], "volume": 600, "amount": closes["000003.SZ"] * 600},
        ]
        frame = pd.DataFrame(rows)
        frame["date"] = pd.to_datetime(frame["date"], format="%Y%m%d")
        return frame

    def fetch_daily(self, ts_code: str, start_date: str, end_date: str) -> pd.DataFrame:
        benchmark_closes = {
            "20260330": 1.00,
            "20260331": 1.01,
            "20260401": 1.015,
            "20260402": 1.018,
            "20260403": 1.022,
            "20260406": 1.025,
            "20260407": 1.028,
            "20260408": 1.029,
            "20260409": 1.03,
        }
        rows = []
        for trade_date, close in benchmark_closes.items():
            rows.append(
                {
                    "ticker": ts_code,
                    "date": trade_date,
                    "open": close * 0.99,
                    "high": close * 1.01,
                    "low": close * 0.98,
                    "close": close,
                    "volume": 10000,
                    "amount": close * 10000,
                }
            )
        frame = pd.DataFrame(rows)
        frame["date"] = pd.to_datetime(frame["date"], format="%Y%m%d")
        return frame


class FakePipelineConfig:
    lookback_days = 30
    benchmark_ticker = "000300.SH"


class FakePipeline:
    def __init__(self) -> None:
        self.config = FakePipelineConfig()

    def _load_daily_bars(self, ticker: str, analysis_date: str) -> pd.DataFrame:
        return pd.DataFrame(
            [
                {"ticker": ticker, "date": "2026-03-27", "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1, "amount": 1},
                {"ticker": ticker, "date": "2026-03-30", "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1, "amount": 1},
            ]
        )

    def run_with_bars(
        self,
        ticker: str,
        analysis_date: str,
        bars: pd.DataFrame,
        benchmark_bars: pd.DataFrame | None = None,
        sector_context: dict | None = None,
        fundamental_context: dict | None = None,
        daily_basic_context: dict | None = None,
        chip_perf_context: dict | None = None,
        fina_indicator_context: dict | None = None,
        metadata_context: dict | None = None,
    ) -> dict:
        if ticker == "000003.SZ":
            raise ValueError("not enough bars")

        payloads = {
            "000001.SZ": {
                "structure_score": 82,
                "p_continue_10d": 0.71,
                "p_breakout_success": 0.62,
                "expected_return_10d": 0.058,
                "risk_score": 28,
                "risk_level": 0.26,
                "execution_score": 41.0,
                "execution_risk_score": 26.0,
                "target_position_pct": 35,
                "action_label": "build",
                "entry_style": "breakout_only",
                "trend_stage": "mid",
                "entry_probability": 0.72,
                "p_acceptance_1d": 0.66,
                "p_fail_fast_3d": 0.12,
                "gentle_expand_score": 71.0,
                "pullback_shrink_score": 52.0,
                "distribution_score": 16.0,
                "stall_score": 12.0,
                "early_stage_score": 44.0,
                "mid_stage_score": 78.0,
                "late_stage_score": 18.0,
                "market_context": {"market_trend_score": 66},
                "sector_context": {"sector_trend_score": 63, "sector_strength_score": 61},
                "fundamental_context": {"fundamental_score": 58},
                "chip_context": {"overhang_ratio": 0.03},
            },
            "000002.SZ": {
                "structure_score": 70,
                "p_continue_10d": 0.61,
                "p_breakout_success": 0.53,
                "expected_return_10d": 0.041,
                "risk_score": 35,
                "risk_level": 0.33,
                "execution_score": 28.0,
                "execution_risk_score": 39.0,
                "target_position_pct": 20,
                "action_label": "probe",
                "entry_style": "pullback_only",
                "trend_stage": "early",
                "entry_probability": 0.61,
                "p_acceptance_1d": 0.54,
                "p_fail_fast_3d": 0.22,
                "gentle_expand_score": 48.0,
                "pullback_shrink_score": 64.0,
                "distribution_score": 20.0,
                "stall_score": 18.0,
                "early_stage_score": 70.0,
                "mid_stage_score": 52.0,
                "late_stage_score": 14.0,
                "market_context": {"market_trend_score": 66},
                "sector_context": {"sector_trend_score": 49, "sector_strength_score": 46},
                "fundamental_context": {"fundamental_score": 44},
                "chip_context": {"overhang_ratio": 0.08},
            },
        }
        snapshot = {
            "ticker": ticker,
            "invalidation_level": 9.5,
            **payloads[ticker],
        }
        return {
            "ticker": ticker,
            "snapshot": snapshot,
            "decision": {"conclusion_label": "high_quality_continuation"},
        }


class FakeMongoStore:
    def __init__(self) -> None:
        self.analysis_payloads: list[dict] = []
        self.market_snapshot_payload: dict | None = None
        self.market_snapshot_records: list[dict] = []

    def upsert_analysis_batch(self, payloads: list[dict]) -> None:
        self.analysis_payloads.extend(payloads)

    def upsert_market_snapshot(self, *, analysis_date: str, top_n: int, exclude_st: bool, records: list[dict]) -> None:
        self.market_snapshot_payload = {
            "analysis_date": analysis_date,
            "top_n": top_n,
            "exclude_st": exclude_st,
            "records": records,
        }

    def upsert_market_snapshot_records(self, *, analysis_date: str, exclude_st: bool, records: list[dict]) -> None:
        self.market_snapshot_records = list(records)

    def close(self) -> None:
        return None


class FakeRawMongoStore(FakeMongoStore):
    def __init__(self) -> None:
        super().__init__()
        self.client = FakeSnapshotClient()

    def find_stock_basic(self) -> list[dict]:
        return self.client.fetch_stock_basic().to_dict(orient="records")

    def find_trade_calendar(self, *, start_date: str, end_date: str, exchange: str = "SSE") -> list[dict]:
        frame = self.client.fetch_trade_calendar(start_date=start_date, end_date=end_date, exchange=exchange)
        return frame.to_dict(orient="records")

    def find_daily_bars_for_trade_date(self, trade_date: str) -> list[dict]:
        return self.client.fetch_daily_for_trade_date(trade_date).to_dict(orient="records")

    def find_daily_bars(
        self,
        *,
        start_date: str,
        end_date: str,
        tickers: list[str] | None = None,
    ) -> list[dict]:
        ticker = tickers[0] if tickers else "000300.SH"
        return self.client.fetch_daily(ticker, start_date=start_date, end_date=end_date).to_dict(orient="records")

    def find_daily_basic(self, *, trade_date: str, ts_code: str | None = None) -> list[dict]:
        return []


class SnapshotJobTests(unittest.TestCase):
    def test_snapshot_job_exports_ranked_top_table_and_persists_data(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            job = SnapshotJob(pipeline=FakePipeline(), client=FakeSnapshotClient())
            mongo_store = FakeMongoStore()
            job.mongo_store = mongo_store
            result = job.run(
                SnapshotJobRequest(
                    target_date="2026-03-30",
                    top_n=2,
                    output_dir=temp_dir,
                )
            )

            self.assertEqual("2026-03-30", result.analysis_date)
            self.assertEqual(2, result.scanned_count)
            self.assertEqual(1, result.skipped_count)
            self.assertTrue(result.csv_path.exists())
            self.assertTrue(result.report_path.exists())

            exported = pd.read_csv(result.csv_path)
            self.assertEqual(["000001.SZ", "000002.SZ"], exported["股票代码"].tolist())
            self.assertEqual([1, 2], exported["排名"].tolist())
            self.assertIn("动作标签", exported.columns)
            self.assertIn("执行分", exported.columns)
            self.assertIn("仓位", exported.columns)
            self.assertIn("Early分", exported.columns)
            self.assertIn("Mid分", exported.columns)
            self.assertIn("Late分", exported.columns)
            self.assertIn("候选标签", exported.columns)
            self.assertIn("策略ID", exported.columns)
            self.assertIn("rps_breakout", exported.loc[0, "候选标签"])
            self.assertIn("shilun_v1_rps_breakout_test", exported.loc[0, "策略ID"])
            self.assertIsNotNone(mongo_store.market_snapshot_payload)
            self.assertEqual(2, len(mongo_store.market_snapshot_payload["records"]))
            self.assertEqual(["000001.SZ", "000002.SZ"], [row["ticker"] for row in mongo_store.market_snapshot_records])
            self.assertEqual([1, 2], [row["rank"] for row in mongo_store.market_snapshot_records])
            self.assertEqual(3, mongo_store.market_snapshot_records[0]["candidate_tag_count"])
            self.assertIn("turtle_breakout", mongo_store.market_snapshot_records[0]["candidate_tags"])
            self.assertIn("high_tight_flag:", mongo_store.market_snapshot_records[0]["candidate_tag_reasons"])
            self.assertEqual(4, mongo_store.market_snapshot_records[0]["strategy_signal_count"])
            self.assertIn("shilun_v1", mongo_store.market_snapshot_records[0]["strategy_ids"])
            self.assertIn("shilun_v1_high_tight_test", mongo_store.market_snapshot_records[0]["strategy_ids"])
            self.assertEqual("shilun_v1", mongo_store.market_snapshot_records[0]["strategy_signals"][0]["strategy_id"])
            self.assertEqual(0.0392, mongo_store.market_snapshot_records[0]["future_return_1d"])
            self.assertEqual(0.0784, mongo_store.market_snapshot_records[0]["future_return_3d"])
            self.assertEqual(0.1, mongo_store.market_snapshot_records[0]["future_max_runup_3d"])
            self.assertEqual(0.0, mongo_store.market_snapshot_records[0]["future_max_drawdown_3d"])
            self.assertEqual(0.01, mongo_store.market_snapshot_records[0]["benchmark_future_return_1d"])
            self.assertEqual(0.025, mongo_store.market_snapshot_records[0]["benchmark_future_return_5d"])
            self.assertEqual(1, mongo_store.market_snapshot_records[0]["outperform_benchmark_3d"])
            self.assertEqual(-0.0123, mongo_store.market_snapshot_records[1]["future_return_5d"])
            self.assertEqual(0.0452, mongo_store.market_snapshot_records[1]["future_max_runup_5d"])
            self.assertEqual(-0.0321, mongo_store.market_snapshot_records[1]["future_max_drawdown_5d"])
            self.assertEqual(0, mongo_store.market_snapshot_records[1]["outperform_benchmark_5d"])
            self.assertEqual(2, len(mongo_store.analysis_payloads))
            self.assertEqual(["000001.SZ", "000002.SZ"], [payload["ticker"] for payload in mongo_store.analysis_payloads])
            self.assertEqual([1, 2], [payload["rank"] for payload in mongo_store.analysis_payloads])
            self.assertTrue(all("snapshot" in payload for payload in mongo_store.analysis_payloads))
            self.assertTrue(all("latest_bar" in payload for payload in mongo_store.analysis_payloads))

    def test_snapshot_job_reads_market_data_from_mongo_provider_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            mongo_store = FakeRawMongoStore()
            job = SnapshotJob(pipeline=FakePipeline(), mongo_store=mongo_store)
            result = job.run(
                SnapshotJobRequest(
                    target_date="2026-03-30",
                    top_n=2,
                    output_dir=temp_dir,
                )
            )

            self.assertEqual("2026-03-30", result.analysis_date)
            self.assertEqual(["000001.SZ", "000002.SZ"], [row["ticker"] for row in mongo_store.market_snapshot_records])
            self.assertEqual(4, mongo_store.market_snapshot_records[0]["strategy_signal_count"])

    def test_snapshot_job_requires_mongo_data_unless_tushare_fallback_is_explicit(self) -> None:
        job = SnapshotJob(pipeline=FakePipeline())

        with self.assertRaisesRegex(ValueError, "Mongo market data is required"):
            job.run(SnapshotJobRequest(target_date="2026-03-30"))


if __name__ == "__main__":
    unittest.main()
