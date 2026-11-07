import unittest
from datetime import datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

from shilun.common.config import AppConfig
from shilun.jobs.intraday_notification_job import run_once


class IntradayNotificationJobTests(unittest.TestCase):
    def test_disabled_group_broadcast_does_not_open_store_or_send(self) -> None:
        now = datetime(2026, 7, 24, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
        disabled = AppConfig(intraday_notifications_enabled=False)

        with (
            patch("shilun.jobs.intraday_notification_job.load_config", return_value=disabled),
            patch("shilun.jobs.intraday_notification_job.MongoSnapshotStore") as store_type,
        ):
            result = run_once(now=now)

        self.assertEqual("disabled", result.status)
        self.assertEqual(0, result.sent_count)
        self.assertIn("样本仍继续落库", result.message)
        store_type.assert_not_called()


if __name__ == "__main__":
    unittest.main()
