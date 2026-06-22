import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import pandas as pd

from shilun.common.config import AppConfig
from shilun.jobs.daily_push_job import DailyPushJob, DailyPushRequest
from shilun.jobs.snapshot_job import SnapshotJobResult


class FakeSnapshotJob:
    def __init__(self, result: SnapshotJobResult | None = None, error: Exception | None = None) -> None:
        self.result = result
        self.error = error
        self.requests = []

    def run(self, request):
        self.requests.append(request)
        if self.error is not None:
            raise self.error
        if self.result is None:
            raise RuntimeError("missing snapshot result")
        return self.result


class FakeMongoStore:
    def __init__(
        self,
        records: list[dict] | None = None,
        candidate_states: list[dict] | None = None,
        candidate_events: list[dict] | None = None,
    ) -> None:
        self.records = records or []
        self.candidate_states = candidate_states or []
        self.candidate_events = candidate_events or []

    def find_market_snapshot_records(self, *, analysis_date: str, exclude_st: bool, limit: int | None = None) -> list[dict]:
        matched = [
            dict(record)
            for record in self.records
            if record.get("analysis_date") == analysis_date and bool(record.get("exclude_st")) == bool(exclude_st)
        ]
        matched.sort(key=lambda record: int(record.get("rank", 0) or 0))
        if limit is not None:
            matched = matched[:limit]
        return matched

    def count_market_snapshot_records(self, *, analysis_date: str, exclude_st: bool) -> int:
        return len(
            [
                record
                for record in self.records
                if record.get("analysis_date") == analysis_date and bool(record.get("exclude_st")) == bool(exclude_st)
            ]
        )

    def find_candidate_pool_states(self, *, analysis_date: str, exclude_st: bool, pool_status: str | None = None, limit: int | None = None) -> list[dict]:
        matched = [
            dict(record)
            for record in self.candidate_states
            if record.get("analysis_date") == analysis_date and bool(record.get("exclude_st")) == bool(exclude_st)
        ]
        if pool_status:
            matched = [record for record in matched if record.get("pool_status") == pool_status]
        matched.sort(key=lambda record: int(record.get("rank", 0) or 0))
        return matched[:limit] if limit is not None else matched

    def find_candidate_pool_events(self, *, analysis_date: str, exclude_st: bool, event_type: str | None = None, limit: int | None = None) -> list[dict]:
        matched = [
            dict(record)
            for record in self.candidate_events
            if record.get("analysis_date") == analysis_date and bool(record.get("exclude_st")) == bool(exclude_st)
        ]
        if event_type:
            matched = [record for record in matched if record.get("event_type") == event_type]
        matched.sort(key=lambda record: int(record.get("rank", 0) or 0))
        return matched[:limit] if limit is not None else matched


class FakeTelegramClient:
    def __init__(self, updates: dict | None = None) -> None:
        self.updates = updates or {"result": []}
        self.messages: list[tuple[int, str]] = []

    def get_updates(self, *, limit: int = 20, timeout: int = 0):
        return self.updates

    def send_message(self, chat_id: int, text: str):
        self.messages.append((chat_id, text))
        return {"ok": True}


class FakeFeishuClient:
    def __init__(self) -> None:
        self.messages: list[str] = []

    def send_text(self, text: str):
        self.messages.append(text)
        return {"code": 0}


class DailyPushJobTests(unittest.TestCase):
    def test_dry_run_builds_message_from_mongo_without_snapshot_run(self) -> None:
        snapshot_job = FakeSnapshotJob(error=RuntimeError("snapshot should not run"))
        mongo_store = FakeMongoStore(
            [
                {
                    "analysis_date": "2026-04-16",
                    "exclude_st": True,
                    "rank": 1,
                    "ticker": "600000.SH",
                    "name": "浦发银行",
                    "execution_score": 31.2,
                    "action_label": "watch",
                    "entry_probability": 0.85,
                    "expected_return_10d": 0.045,
                },
                {
                    "analysis_date": "2026-04-16",
                    "exclude_st": True,
                    "rank": 2,
                    "ticker": "000001.SZ",
                    "name": "平安银行",
                    "execution_score": 30.8,
                    "action_label": "watch",
                    "entry_probability": 0.82,
                    "expected_return_10d": 0.041,
                },
            ]
        )
        job = DailyPushJob(
            config=AppConfig(),
            snapshot_job=snapshot_job,
            mongo_store=mongo_store,
        )

        result = job.run(DailyPushRequest(target_date="2026-04-16", dry_run=True, message_top_k=1))

        self.assertEqual(("dry-run",), result.pushed_channels)
        self.assertEqual("mongo", result.data_source)
        self.assertEqual([], snapshot_job.requests)
        self.assertIn("石论日报 2026-04-16", result.message_text)
        self.assertIn("候选池：2026-04-16 尚未生成状态", result.message_text)
        self.assertIn("Top1：", result.message_text)
        self.assertIn("600000.SH", result.message_text)
        self.assertNotIn("000001.SZ", result.message_text)

    def test_dry_run_includes_candidate_pool_sections_when_available(self) -> None:
        mongo_store = FakeMongoStore(
            records=[
                {
                    "analysis_date": "2026-04-16",
                    "exclude_st": True,
                    "rank": 1,
                    "ticker": "600000.SH",
                    "name": "浦发银行",
                    "execution_score": 31.2,
                    "action_label": "watch",
                    "entry_probability": 0.85,
                    "expected_return_10d": 0.045,
                }
            ],
            candidate_states=[
                {
                    "analysis_date": "2026-04-16",
                    "exclude_st": True,
                    "rank": 1,
                    "ticker": "600000.SH",
                    "name": "浦发银行",
                    "pool_status": "buy_pool",
                    "pool_score": 42.1,
                    "pool_reasons": "执行动作和目标仓位达到买入池门槛",
                },
                {
                    "analysis_date": "2026-04-16",
                    "exclude_st": True,
                    "rank": 2,
                    "ticker": "000001.SZ",
                    "name": "平安银行",
                    "pool_status": "watch_pool",
                    "pool_score": 35.2,
                    "pool_reasons": "命中候选标签",
                },
            ],
            candidate_events=[
                {
                    "analysis_date": "2026-04-16",
                    "exclude_st": True,
                    "rank": 1,
                    "ticker": "600000.SH",
                    "name": "浦发银行",
                    "event_type": "promoted",
                    "pool_status": "buy_pool",
                    "pool_score": 42.1,
                    "pool_reasons": "从观察池升入买入池",
                }
            ],
        )
        job = DailyPushJob(
            config=AppConfig(),
            snapshot_job=FakeSnapshotJob(error=RuntimeError("snapshot should not run")),
            mongo_store=mongo_store,
        )

        result = job.run(DailyPushRequest(target_date="2026-04-16", dry_run=True, message_top_k=1, candidate_pool_top_k=2))

        self.assertIn("候选池状态：", result.message_text)
        self.assertIn("升池：", result.message_text)
        self.assertIn("买入池 Top1：", result.message_text)
        self.assertIn("观察池 Top1：", result.message_text)
        self.assertIn("600000.SH", result.message_text)
        self.assertIn("000001.SZ", result.message_text)

    def test_can_disable_candidate_pool_section(self) -> None:
        mongo_store = FakeMongoStore(
            records=[
                {
                    "analysis_date": "2026-04-16",
                    "exclude_st": True,
                    "rank": 1,
                    "ticker": "600000.SH",
                    "name": "浦发银行",
                    "execution_score": 31.2,
                    "action_label": "watch",
                    "entry_probability": 0.85,
                    "expected_return_10d": 0.045,
                }
            ],
            candidate_states=[
                {
                    "analysis_date": "2026-04-16",
                    "exclude_st": True,
                    "rank": 1,
                    "ticker": "600000.SH",
                    "name": "浦发银行",
                    "pool_status": "buy_pool",
                }
            ],
        )
        job = DailyPushJob(config=AppConfig(), mongo_store=mongo_store)

        result = job.run(DailyPushRequest(target_date="2026-04-16", dry_run=True, include_candidate_pool=False))

        self.assertNotIn("候选池状态：", result.message_text)

    def test_dry_run_can_explicitly_use_snapshot_fallback(self) -> None:
        with TemporaryDirectory() as tmpdir:
            csv_path = Path(tmpdir) / "market_top_100_2026-04-16_no_st.csv"
            report_path = Path(tmpdir) / "market_top_100_2026-04-16_no_st.md"
            pd.DataFrame(
                [
                    {"排名": 1, "股票代码": "600000.SH", "股票名称": "浦发银行", "执行分": 31.2, "动作标签": "watch", "入场概率": "85.00%", "10日预期收益": "4.50%"},
                    {"排名": 2, "股票代码": "000001.SZ", "股票名称": "平安银行", "执行分": 30.8, "动作标签": "watch", "入场概率": "82.00%", "10日预期收益": "4.10%"},
                ]
            ).to_csv(csv_path, index=False)
            report_path.write_text("# report\n", encoding="utf-8")
            snapshot_result = SnapshotJobResult(
                analysis_date="2026-04-16",
                scanned_count=2,
                skipped_count=0,
                report_path=report_path,
                csv_path=csv_path,
                history_cache_path=Path(tmpdir) / "market_history_2026-04-16.pkl",
            )
            job = DailyPushJob(
                config=AppConfig(),
                snapshot_job=FakeSnapshotJob(result=snapshot_result),
            )
            result = job.run(
                DailyPushRequest(
                    dry_run=True,
                    output_dir=tmpdir,
                    message_top_k=1,
                    prefer_mongo=False,
                    allow_snapshot_fallback=True,
                )
            )
            self.assertEqual(("dry-run",), result.pushed_channels)
            self.assertEqual((), result.failed_channels)
            self.assertEqual("snapshot_fallback", result.data_source)
            self.assertIn("石论日报 2026-04-16", result.message_text)
            self.assertIn("Top1：", result.message_text)
            self.assertIn("600000.SH", result.message_text)
            self.assertNotIn("000001.SZ", result.message_text)

    def test_telegram_daily_push_requires_explicit_push_chat_ids(self) -> None:
        updates = {
            "result": [
                {"message": {"chat": {"id": 123}}},
                {"message": {"chat": {"id": 456}}},
                {"message": {"chat": {"id": 123}}},
            ]
        }
        job = DailyPushJob(
            config=AppConfig(),
            snapshot_job=FakeSnapshotJob(error=RuntimeError("unused")),
            telegram_client=FakeTelegramClient(updates=updates),
        )
        self.assertEqual((), job._resolve_telegram_chat_ids())

    def test_telegram_daily_push_uses_explicit_push_chat_ids(self) -> None:
        job = DailyPushJob(
            config=AppConfig(telegram_push_chat_ids=(123, 456)),
            snapshot_job=FakeSnapshotJob(error=RuntimeError("unused")),
            telegram_client=FakeTelegramClient(),
        )
        self.assertEqual((123, 456), job._resolve_telegram_chat_ids())

    def test_fallback_uses_latest_local_snapshot(self) -> None:
        with TemporaryDirectory() as tmpdir:
            older = Path(tmpdir) / "market_top_100_2026-04-14_no_st.csv"
            newer = Path(tmpdir) / "market_top_6000_2026-04-16_no_st.csv"
            for path in (older, newer):
                pd.DataFrame(
                    [{"排名": 1, "股票代码": "600000.SH", "股票名称": "浦发银行", "执行分": 31.2, "动作标签": "watch", "入场概率": "85.00%", "10日预期收益": "4.50%"}]
                ).to_csv(path, index=False)
                path.with_suffix(".md").write_text("# report\n", encoding="utf-8")
            job = DailyPushJob(
                config=AppConfig(),
                snapshot_job=FakeSnapshotJob(error=RuntimeError("boom")),
            )
            result = job.run(DailyPushRequest(dry_run=True, output_dir=tmpdir, fallback_latest_local=True))
            self.assertEqual("2026-04-16", result.analysis_date)
            self.assertEqual("local_csv", result.data_source)

    def test_snapshot_export_grows_to_match_message_top_k(self) -> None:
        captured_top_n: list[int] = []

        class RecordingSnapshotJob:
            def run(self, request):
                captured_top_n.append(request.top_n)
                raise RuntimeError("stop after capture")

        job = DailyPushJob(
            config=AppConfig(),
            snapshot_job=RecordingSnapshotJob(),
        )

        with self.assertRaises(RuntimeError):
            job.run(DailyPushRequest(top_n=100, message_top_k=500, prefer_mongo=False, allow_snapshot_fallback=True))
        self.assertEqual([500], captured_top_n)

    def test_snapshot_fallback_passes_through_tushare_fallback_flags(self) -> None:
        captured_requests = []

        class RecordingSnapshotJob:
            def run(self, request):
                captured_requests.append(request)
                raise RuntimeError("stop after capture")

        job = DailyPushJob(
            config=AppConfig(),
            snapshot_job=RecordingSnapshotJob(),
        )

        with self.assertRaises(RuntimeError):
            job.run(DailyPushRequest(top_n=100, prefer_mongo=False, allow_snapshot_fallback=True))

        self.assertEqual(1, len(captured_requests))
        self.assertFalse(captured_requests[0].prefer_mongo_data)
        self.assertTrue(captured_requests[0].allow_tushare_fallback)

    def test_partial_channel_failure_does_not_abort_successful_pushes(self) -> None:
        with TemporaryDirectory() as tmpdir:
            csv_path = Path(tmpdir) / "market_top_100_2026-04-16_no_st.csv"
            report_path = Path(tmpdir) / "market_top_100_2026-04-16_no_st.md"
            pd.DataFrame(
                [{"排名": 1, "股票代码": "600000.SH", "股票名称": "浦发银行", "执行分": 31.2, "动作标签": "watch", "入场概率": "85.00%", "10日预期收益": "4.50%"}]
            ).to_csv(csv_path, index=False)
            report_path.write_text("# report\n", encoding="utf-8")
            snapshot_result = SnapshotJobResult(
                analysis_date="2026-04-16",
                scanned_count=1,
                skipped_count=0,
                report_path=report_path,
                csv_path=csv_path,
                history_cache_path=Path(tmpdir) / "market_history_2026-04-16.pkl",
            )

            class FailingFeishuClient:
                def send_text(self, text: str):
                    raise RuntimeError("dns failure")

            telegram_client = FakeTelegramClient()
            job = DailyPushJob(
                config=AppConfig(telegram_push_chat_ids=(123,)),
                snapshot_job=FakeSnapshotJob(result=snapshot_result),
                telegram_client=telegram_client,
                feishu_client=FailingFeishuClient(),
            )
            result = job.run(DailyPushRequest(output_dir=tmpdir, prefer_mongo=False, allow_snapshot_fallback=True))
            self.assertEqual(("telegram:1",), result.pushed_channels)
            self.assertEqual(1, len(result.failed_channels))
            self.assertEqual(1, len(telegram_client.messages))

    def test_feishu_only_push_does_not_attempt_telegram_discovery(self) -> None:
        mongo_store = FakeMongoStore(
            [
                {
                    "analysis_date": "2026-04-16",
                    "exclude_st": True,
                    "rank": 1,
                    "ticker": "600000.SH",
                    "name": "浦发银行",
                    "execution_score": 31.2,
                    "action_label": "watch",
                    "entry_probability": 0.85,
                    "expected_return_10d": 0.045,
                }
            ]
        )
        feishu_client = FakeFeishuClient()
        telegram_client = FakeTelegramClient(updates={"result": [{"message": {"chat": {"id": 123}}}]})
        job = DailyPushJob(
            config=AppConfig(),
            mongo_store=mongo_store,
            feishu_client=feishu_client,
            telegram_client=telegram_client,
        )

        result = job.run(DailyPushRequest(target_date="2026-04-16"))

        self.assertEqual(("feishu",), result.pushed_channels)
        self.assertEqual(1, len(feishu_client.messages))
        self.assertEqual([], telegram_client.messages)

    def test_real_push_requires_at_least_one_configured_channel(self) -> None:
        mongo_store = FakeMongoStore(
            [
                {
                    "analysis_date": "2026-04-16",
                    "exclude_st": True,
                    "rank": 1,
                    "ticker": "600000.SH",
                    "name": "浦发银行",
                    "execution_score": 31.2,
                    "action_label": "watch",
                    "entry_probability": 0.85,
                    "expected_return_10d": 0.045,
                }
            ]
        )
        job = DailyPushJob(config=AppConfig(), mongo_store=mongo_store)

        with self.assertRaisesRegex(RuntimeError, "No push channel configured"):
            job.run(DailyPushRequest(target_date="2026-04-16", dry_run=False))

    def test_message_text_is_truncated_when_it_exceeds_max_chars(self) -> None:
        rows = pd.DataFrame(
            [
                {
                    "排名": index,
                    "股票代码": f"{index:06d}.SH",
                    "股票名称": "超长名称测试股份有限公司",
                    "执行分": 30 + index,
                    "动作标签": "watch",
                    "入场概率": "85.00%",
                    "10日预期收益": "4.50%",
                }
                for index in range(1, 6)
            ]
        )

        text = DailyPushJob._build_message_text(
            analysis_date="2026-04-16",
            scanned_count=5,
            skipped_count=0,
            table=rows,
            top_k=5,
            max_chars=180,
        )

        self.assertIn("石论日报 2026-04-16", text)
        self.assertIn("其余", text)
        self.assertNotIn("000005.SH", text)


if __name__ == "__main__":
    unittest.main()
