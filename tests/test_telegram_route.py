import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from shilun.api.routes import telegram


class TelegramRouteTests(unittest.TestCase):
    def test_parse_command_with_explicit_date(self) -> None:
        ticker, analysis_date = telegram.parse_telegram_command("/analyze 600132.SH 2026-03-25")
        self.assertEqual("600132.SH", ticker)
        self.assertEqual("2026-03-25", analysis_date)

    def test_parse_command_rejects_unknown_command(self) -> None:
        with self.assertRaises(ValueError):
            telegram.parse_telegram_command("/start")

    def test_build_telegram_text_contains_key_fields(self) -> None:
        result = {
            "ticker": "600132.SH",
            "date": "2026-03-25",
            "snapshot": {
                "structure_type": "trend_continue",
                "regime_label": "strong_up",
                "p_continue_10d": 0.72,
                "p_fail_5d": 0.25,
                "support_main": 56.82,
                "pressure_main": 57.59,
                "invalidation_level": 55.01,
                "structure_assessment": {
                    "structure_stage": "trend_advancing",
                    "confirmation_state": "failed",
                    "confirmation_score": 40,
                },
                "evidence_sections": {
                    "market": ["当前数据源未返回可用基准行情，因此相对强弱仍按个股自身特征解释"],
                },
            },
            "decision": {
                "conclusion_label": "defense_first",
                "watching_action": "stand_aside",
                "holding_action": "exit_on_invalidation",
                "confirmation_needed": ["需要突破后的量价确认"],
            },
        }
        text = telegram.build_telegram_text(result)
        self.assertIn("【总览】当前更偏 防守优先", text)
        self.assertIn("【趋势判断】", text)
        self.assertIn("支撑 56.82 / 确认区 57.59 / 失效 55.01", text)
        self.assertIn("【概率矩阵】", text)

    def test_webhook_returns_send_message_payload_when_bot_token_missing(self) -> None:
        original_service = telegram.analysis_service
        original_client = telegram.get_telegram_client

        class DummyAnalysisService:
            def analyze(self, request):
                return {
                    "ticker": request.ticker,
                    "date": request.analysis_date,
                    "snapshot": {
                        "structure_type": "trend_continue",
                        "regime_label": "strong_up",
                        "p_continue_10d": 0.72,
                        "p_fail_5d": 0.25,
                        "support_main": 56.82,
                        "pressure_main": 57.59,
                        "invalidation_level": 55.01,
                        "structure_assessment": {
                            "structure_stage": "trend_advancing",
                            "confirmation_state": "failed",
                            "confirmation_score": 40,
                        },
                        "evidence_sections": {"market": ["当前没有可用市场基准上下文"]},
                    },
                    "decision": {
                        "conclusion_label": "defense_first",
                        "watching_action": "stand_aside",
                        "holding_action": "exit_on_invalidation",
                        "confirmation_needed": ["需要突破后的量价确认"],
                    },
                }

        telegram.analysis_service = DummyAnalysisService()
        telegram.get_telegram_client = lambda: None
        try:
            payload = telegram.telegram_webhook(
                telegram.TelegramUpdate(
                    update_id=1,
                    message=telegram.TelegramMessage(
                        message_id=10,
                        text="/analyze 600132.SH 2026-03-25",
                        chat=telegram.TelegramChat(id=12345),
                    ),
                )
            )
        finally:
            telegram.analysis_service = original_service
            telegram.get_telegram_client = original_client

        self.assertEqual("sendMessage", payload["method"])
        self.assertEqual(12345, payload["chat_id"])
        self.assertIn("600132.SH 2026-03-25", payload["text"])

    def test_webhook_uses_real_client_when_available(self) -> None:
        original_service = telegram.analysis_service
        original_client = telegram.get_telegram_client

        class DummyAnalysisService:
            def analyze(self, request):
                return {
                    "ticker": request.ticker,
                    "date": request.analysis_date,
                    "snapshot": {
                        "structure_type": "trend_continue",
                        "regime_label": "strong_up",
                        "p_continue_10d": 0.72,
                        "p_fail_5d": 0.25,
                        "support_main": 56.82,
                        "pressure_main": 57.59,
                        "invalidation_level": 55.01,
                        "structure_assessment": {
                            "structure_stage": "trend_advancing",
                            "confirmation_state": "failed",
                            "confirmation_score": 40,
                        },
                        "evidence_sections": {"market": ["当前没有可用市场基准上下文"]},
                    },
                    "decision": {
                        "conclusion_label": "defense_first",
                        "watching_action": "stand_aside",
                        "holding_action": "exit_on_invalidation",
                        "confirmation_needed": ["需要突破后的量价确认"],
                    },
                }

        class DummyClient:
            def __init__(self):
                self.calls = []

            def send_message(self, chat_id: int, text: str):
                self.calls.append((chat_id, text))
                return {"ok": True}

        client = DummyClient()
        telegram.analysis_service = DummyAnalysisService()
        telegram.get_telegram_client = lambda: client
        try:
            payload = telegram.telegram_webhook(
                telegram.TelegramUpdate(
                    update_id=1,
                    message=telegram.TelegramMessage(
                        message_id=10,
                        text="/analyze 600132.SH 2026-03-25",
                        chat=telegram.TelegramChat(id=12345),
                    ),
                )
            )
        finally:
            telegram.analysis_service = original_service
            telegram.get_telegram_client = original_client

        self.assertEqual({"ok": True}, payload)
        self.assertEqual(1, len(client.calls))
        self.assertEqual(12345, client.calls[0][0])

    def test_set_webhook_requires_configured_base_url(self) -> None:
        original_client = telegram.get_telegram_client
        telegram.get_telegram_client = lambda: object()
        try:
            with self.assertRaises(telegram.HTTPException):
                telegram.telegram_set_webhook(telegram.TelegramWebhookSetupRequest())
        finally:
            telegram.get_telegram_client = original_client


if __name__ == "__main__":
    unittest.main()
