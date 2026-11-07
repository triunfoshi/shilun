"""Verify data routing reaches the default Feishu bridge before generation."""

from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from shilun import feishu_backend_context as context
from shilun.feishu_codex_bridge import CodexBridge
from shilun.feishu_review_context import ReviewContext


class BackendContextTests(unittest.TestCase):
    def test_statistics_takes_priority_over_single_day_review(self):
        facts = ReviewContext(text='{"source":"shilun_board_effects","highest_avg":1.25}')
        with (patch.object(context, "build_board_effects_context", return_value=facts) as board,
              patch.object(context, "build_review_context") as review):
            result = context.build_backend_context("复盘20261103最近30天最高板效应", max_chars=5000)
        self.assertIs(result, facts)
        board.assert_called_once_with("复盘20261103最近30天最高板效应", max_chars=5000)
        review.assert_not_called()

    def test_daily_review_and_unmatched_chat_keep_existing_behavior(self):
        for facts in (ReviewContext(text='{"analysis_date":"2026-11-03"}'), None):
            with (self.subTest(facts=facts),
                  patch.object(context, "build_board_effects_context", return_value=None),
                  patch.object(context, "build_review_context", return_value=facts)):
                self.assertIs(context.build_backend_context("总结20261103复盘"), facts)

    def test_default_bridge_supplies_statistics_to_model(self):
        facts = ReviewContext(text='{"source":"shilun_board_effects","highest_avg":1.25,"all_avg":0.5}')
        runner = Mock(return_value=SimpleNamespace(text="最高板高0.75个百分点。", session_id="11111111-1111-4111-8111-111111111111"))
        with (TemporaryDirectory() as directory,
              patch.object(context, "build_board_effects_context", return_value=facts),
              patch.object(context, "build_review_context") as review):
            bridge = CodexBridge("cli_test", object(), users={"ou_owner"},
                                 db_path=Path(directory) / "offline.sqlite3", runner=runner)
            self.addCleanup(bridge.close)
            answer, _, _ = bridge._answer({"text": "最高板效应最近30天的期望收益均值比普通涨停高吗？",
                                           "chat_id": "oc_test", "sender_id": "ou_owner"})
            self.assertIn("高0.75", answer)
            self.assertIn(facts.text, runner.call_args.args[0])
            self.assertIn("本次已提供的数据", runner.call_args.args[0])
            review.assert_not_called()

    def test_statistics_failure_notice_bypasses_model(self):
        with (TemporaryDirectory() as directory,
              patch.object(context, "build_board_effects_context", return_value=ReviewContext(notice="所选窗口尚无可核验反馈。"))):
            runner = Mock()
            bridge = CodexBridge("cli_test", object(), users={"ou_owner"},
                                 db_path=Path(directory) / "offline.sqlite3", runner=runner)
            self.addCleanup(bridge.close)
            answer, session, usage = bridge._answer({"text": "最近30天最高板效应", "chat_id": "oc_test", "sender_id": "ou_owner"})
            self.assertEqual("所选窗口尚无可核验反馈。", answer)
            self.assertIsNone(session)
            self.assertIsNone(usage)
            runner.assert_not_called()

    def test_summary_budget_uses_readonly_projection_tools_instead_of_rejecting(self):
        notice = SimpleNamespace(text="", notice="数据摘要超过传递预算。", notice_code="summary_budget")
        runner = Mock(return_value=SimpleNamespace(text="已按字段读取原日期复盘。", session_id="11111111-1111-4111-8111-111111111111"))
        store = SimpleNamespace(conversation_history=Mock(return_value=[]), log=Mock())
        bridge = CodexBridge("cli_test", object(), users={"ou_owner"}, store=store,
                             context_builder=Mock(return_value=notice), runner=runner)
        answer, _, _ = bridge._answer({"text": "读取20261104复盘", "message_id": "om_test",
                                      "chat_id": "oc_test", "sender_id": "ou_owner"})
        self.assertIn("按字段读取", answer)
        self.assertTrue(runner.call_args.kwargs["backend_queries"])
        self.assertIn("投影或分页读取原日期数据", runner.call_args.args[0])


if __name__ == "__main__":
    unittest.main()
