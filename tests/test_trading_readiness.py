from __future__ import annotations

import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

from shilun.trading.readiness import build_trading_readiness


TZ = ZoneInfo("Asia/Shanghai")


class TradingReadinessTests(unittest.TestCase):
    def test_ready_for_collection_is_not_same_as_latency_validated(self) -> None:
        payload = build_trading_readiness(
            holdings=[{"ticker": "603986.SH", "quantity": 100, "cost_basis": 663.673, "stop_loss_1": 620}],
            quote_tickers={"603986.SH", "000001.SH", "399006.SZ"},
            minute_tickers={"603986.SH", "000001.SH", "399006.SZ"},
            latency_summary={"sample_count": 0, "calibration_ready": False},
            api_key_configured=True,
            now=datetime(2026, 7, 12, 10, tzinfo=TZ),
        )

        self.assertTrue(payload["collection_ready"])
        self.assertEqual(payload["job8_status"], "pending_live_samples")
        self.assertFalse(payload["calibration_ready"])

    def test_missing_key_and_position_facts_block_job8(self) -> None:
        payload = build_trading_readiness(
            holdings=[{"ticker": "600584.SH", "quantity": None, "cost_basis": None, "stop_loss_1": None}],
            quote_tickers={"600584.SH", "000001.SH", "399006.SZ"},
            minute_tickers={"600584.SH", "000001.SH", "399006.SZ"},
            latency_summary={"sample_count": 0, "calibration_ready": False},
            api_key_configured=False,
            now=datetime(2026, 7, 12, 10, tzinfo=TZ),
        )

        self.assertFalse(payload["collection_ready"])
        self.assertEqual(payload["job8_status"], "blocked")
        blocked = {item["check_id"] for item in payload["checks"] if not item["passed"]}
        self.assertEqual(blocked, {"api_key", "quantity", "cost_basis", "hard_stop"})

    def test_full_calibration_marks_job_validated(self) -> None:
        payload = build_trading_readiness(
            holdings=[{"ticker": "603986.SH", "quantity": 100, "entry_price": 663.63, "stop_loss_1": 620}],
            quote_tickers={"603986.SH", "000001.SH", "399006.SZ"},
            minute_tickers={"603986.SH", "000001.SH", "399006.SZ"},
            latency_summary={"sample_count": 45, "calibration_ready": True},
            api_key_configured=True,
            now=datetime(2026, 7, 13, 10, tzinfo=TZ),
        )

        self.assertEqual(payload["job8_status"], "validated")
        self.assertEqual(payload["blockers"], [])


if __name__ == "__main__":
    unittest.main()
