import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import pandas as pd

from shilun.common.config import AppConfig
from shilun.jobs.automation_push_job import AutomationPushJob, AutomationPushRequest, discover_telegram_chat_ids
from shilun.jobs.daily_push_job import DailyPushJob, DailyPushResult


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


class AutomationPushJobTests(unittest.TestCase):
    def test_discover_telegram_chat_ids_prefers_recent_unique_chats(self) -> None:
        client = FakeTelegramClient(
            updates={
                "result": [
                    {"message": {"chat": {"id": 100}}},
                    {"message": {"chat": {"id": 200}}},
                    {"edited_message": {"chat": {"id": 100}}},
                    {"message": {"chat": {"id": 300}}},
                ]
            }
        )

        self.assertEqual((300, 100, 200), discover_telegram_chat_ids(client))

    def test_automation_push_uses_discovered_telegram_chat_ids(self) -> None:
        with TemporaryDirectory() as tmpdir:
            csv_path = Path(tmpdir) / "market_top_100_2026-04-16_no_st.csv"
            report_path = Path(tmpdir) / "market_top_100_2026-04-16_no_st.md"
            pd.DataFrame(
                [{"排名": 1, "股票代码": "600000.SH", "股票名称": "浦发银行", "执行分": 31.2, "动作标签": "watch", "入场概率": "85.00%", "10日预期收益": "4.50%"}]
            ).to_csv(csv_path, index=False)
            report_path.write_text("# report\n", encoding="utf-8")

            telegram_client = FakeTelegramClient(updates={"result": [{"message": {"chat": {"id": 12345}}}]})
            feishu_client = FakeFeishuClient()
            original_build_feishu = DailyPushJob._build_feishu_client
            DailyPushJob._build_feishu_client = lambda self: feishu_client
            try:
                job = AutomationPushJob(
                    config=AppConfig(
                        telegram_bot_token="token",
                        feishu_webhook_url="https://example.invalid/hook",
                    ),
                    telegram_client=telegram_client,
                )

                result = job.run(
                    AutomationPushRequest(
                        target_date="2026-05-25",
                        output_dir=tmpdir,
                        prefer_mongo=True,
                        allow_snapshot_fallback=False,
                        fallback_latest_local=True,
                    )
                )
            finally:
                DailyPushJob._build_feishu_client = original_build_feishu

            self.assertEqual("discovered", result.telegram_chat_ids_source)
            self.assertEqual((12345,), result.telegram_chat_ids)
            self.assertEqual(("feishu", "telegram:1"), result.push_result.pushed_channels)
            self.assertEqual(1, len(feishu_client.messages))
            self.assertEqual(1, len(telegram_client.messages))

    def test_automation_push_defaults_to_mongo_then_tushare_fallback(self) -> None:
        captured: dict[str, object] = {}
        original_run = DailyPushJob.run

        def fake_run(self, request):
            captured["prefer_mongo"] = request.prefer_mongo
            captured["allow_snapshot_fallback"] = request.allow_snapshot_fallback
            captured["fallback_latest_local"] = request.fallback_latest_local
            return DailyPushResult(
                analysis_date="2026-05-25",
                report_path=Path("outputs/mock.md"),
                csv_path=Path("outputs/mock.csv"),
                pushed_channels=("dry-run",),
                failed_channels=(),
                message_text="mock",
                data_source="mongo",
            )

        DailyPushJob.run = fake_run
        try:
            job = AutomationPushJob(config=AppConfig())
            result = job.run(AutomationPushRequest(require_feishu=False, require_telegram=False))
        finally:
            DailyPushJob.run = original_run

        self.assertTrue(captured["prefer_mongo"])
        self.assertTrue(captured["allow_snapshot_fallback"])
        self.assertFalse(captured["fallback_latest_local"])
        self.assertEqual("unavailable", result.telegram_chat_ids_source)

    def test_automation_push_can_opt_in_to_stale_local_fallback(self) -> None:
        captured: dict[str, object] = {}
        original_run = DailyPushJob.run

        def fake_run(self, request):
            captured["fallback_latest_local"] = request.fallback_latest_local
            return DailyPushResult(
                analysis_date="2026-05-25",
                report_path=Path("outputs/mock.md"),
                csv_path=Path("outputs/mock.csv"),
                pushed_channels=("dry-run",),
                failed_channels=(),
                message_text="mock",
                data_source="local_csv",
            )

        DailyPushJob.run = fake_run
        try:
            job = AutomationPushJob(config=AppConfig())
            result = job.run(
                AutomationPushRequest(
                    fallback_latest_local=True,
                    require_feishu=False,
                    require_telegram=False,
                )
            )
        finally:
            DailyPushJob.run = original_run

        self.assertTrue(captured["fallback_latest_local"])
        self.assertEqual("local_csv", result.push_result.data_source)

    def test_automation_push_falls_back_when_mongo_init_fails(self) -> None:
        calls: list[str | None] = []
        original_init = DailyPushJob.__init__
        original_run = DailyPushJob.run

        def fake_init(self, *, config=None, snapshot_job=None, mongo_store=None, market_snapshot_store=None, candidate_pool_store=None, telegram_client=None, feishu_client=None):
            calls.append(None if config is None else config.mongo_uri)
            if config is not None and config.mongo_uri:
                raise RuntimeError("mongo unavailable")
            self.config = config
            self.telegram_client = telegram_client
            self.feishu_client = feishu_client
            self.mongo_store = None

        def fake_run(self, request):
            return DailyPushResult(
                analysis_date="2026-05-25",
                report_path=Path("outputs/mock.md"),
                csv_path=Path("outputs/mock.csv"),
                pushed_channels=("dry-run",),
                failed_channels=(),
                message_text="mock",
                data_source="snapshot_fallback",
            )

        DailyPushJob.__init__ = fake_init
        DailyPushJob.run = fake_run
        try:
            job = AutomationPushJob(config=AppConfig(mongo_uri="mongodb://localhost:27017", telegram_bot_token=None))
            result = job.run(AutomationPushRequest(require_feishu=False, require_telegram=False))
        finally:
            DailyPushJob.__init__ = original_init
            DailyPushJob.run = original_run

        self.assertEqual(["mongodb://localhost:27017", None], calls)
        self.assertEqual("snapshot_fallback", result.push_result.data_source)

    def test_automation_push_requires_telegram_delivery_by_default(self) -> None:
        with TemporaryDirectory() as tmpdir:
            csv_path = Path(tmpdir) / "market_top_100_2026-04-16_no_st.csv"
            report_path = Path(tmpdir) / "market_top_100_2026-04-16_no_st.md"
            pd.DataFrame(
                [{"排名": 1, "股票代码": "600000.SH", "股票名称": "浦发银行", "执行分": 31.2, "动作标签": "watch", "入场概率": "85.00%", "10日预期收益": "4.50%"}]
            ).to_csv(csv_path, index=False)
            report_path.write_text("# report\n", encoding="utf-8")

            feishu_client = FakeFeishuClient()
            original_build_feishu = DailyPushJob._build_feishu_client
            DailyPushJob._build_feishu_client = lambda self: feishu_client
            try:
                job = AutomationPushJob(
                    config=AppConfig(
                        telegram_bot_token="token",
                        feishu_webhook_url="https://example.invalid/hook",
                    ),
                    telegram_client=FakeTelegramClient(updates={"result": []}),
                )

                with self.assertRaisesRegex(RuntimeError, "requires Telegram delivery"):
                    job.run(
                        AutomationPushRequest(
                            target_date="2026-05-25",
                            output_dir=tmpdir,
                            prefer_mongo=True,
                            allow_snapshot_fallback=False,
                            fallback_latest_local=True,
                        )
                    )
            finally:
                DailyPushJob._build_feishu_client = original_build_feishu

    def test_automation_push_can_make_telegram_optional(self) -> None:
        with TemporaryDirectory() as tmpdir:
            csv_path = Path(tmpdir) / "market_top_100_2026-04-16_no_st.csv"
            report_path = Path(tmpdir) / "market_top_100_2026-04-16_no_st.md"
            pd.DataFrame(
                [{"排名": 1, "股票代码": "600000.SH", "股票名称": "浦发银行", "执行分": 31.2, "动作标签": "watch", "入场概率": "85.00%", "10日预期收益": "4.50%"}]
            ).to_csv(csv_path, index=False)
            report_path.write_text("# report\n", encoding="utf-8")

            feishu_client = FakeFeishuClient()
            original_build_feishu = DailyPushJob._build_feishu_client
            DailyPushJob._build_feishu_client = lambda self: feishu_client
            try:
                job = AutomationPushJob(
                    config=AppConfig(
                        telegram_bot_token="token",
                        feishu_webhook_url="https://example.invalid/hook",
                    ),
                    telegram_client=FakeTelegramClient(updates={"result": []}),
                )
                result = job.run(
                    AutomationPushRequest(
                        target_date="2026-05-25",
                        output_dir=tmpdir,
                        prefer_mongo=True,
                        allow_snapshot_fallback=False,
                        fallback_latest_local=True,
                        require_telegram=False,
                    )
                )
            finally:
                DailyPushJob._build_feishu_client = original_build_feishu

            self.assertEqual(("feishu",), result.push_result.pushed_channels)
            self.assertEqual("discovery_empty", result.telegram_chat_ids_source)
