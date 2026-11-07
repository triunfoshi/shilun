"""The current allowlist applies at receipt, queued work, and delivery."""

from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock

from shilun.feishu_codex_bridge import BridgeError, CodexBridge
from shilun.feishu_codex_runner import CodexResult


OWNER = "ou_accessowner"
OTHER = "ou_accessother"
MESSAGE = "om_accesstest"
CHAT = "oc_accesstest"
SESSION = "019a1234-5678-7abc-8def-0123456789ab"


def event(*, kind="text", sender=OTHER, chat_type="p2p", sender_type="user"):
    content = ({"text": "合成问题"} if kind == "text" else
               {"title": "合成问题", "content": [[{"tag": "text", "text": "请回复"}]]})
    return {"event": {
        "sender": {"sender_type": sender_type, "sender_id": {"open_id": sender}},
        "message": {"message_id": MESSAGE, "chat_id": CHAT, "chat_type": chat_type,
                    "message_type": kind, "content": json.dumps(content, ensure_ascii=False)},
    }}


def row(sender=OTHER, **changes):
    return {"message_id": MESSAGE, "chat_id": CHAT, "sender_id": sender,
            "text": "合成问题", "reply": None, "usage": None, "status": "processing",
            "ack_sent": 0, "sent_parts": 0, "attempts": 0, "next_retry": 0, **changes}


class AccessControlTests(unittest.TestCase):
    def setUp(self):
        self.store = Mock()
        self.store.conversation_history.return_value = []
        self.runner = Mock(return_value=CodexResult(
            "已获准的正文。", SESSION, model="gpt-synthetic", input_tokens=10,
            cached_input_tokens=0, output_tokens=2, elapsed_seconds=0.1))
        self.context = Mock(return_value=None)
        self.sender = Mock()
        self.bridge = CodexBridge("cli_accessoffline", object(), users={OWNER},
                                  store=self.store, runner=self.runner,
                                  context_builder=self.context, sender=self.sender)
        self.addCleanup(self.bridge.close)

    def test_other_users_never_enter_queue_or_trigger_a_reply(self):
        for kind in ("text", "post"):
            self.assertFalse(self.bridge.accept(event(kind=kind)))
        self.store.insert.assert_not_called()
        self.store.log.assert_not_called()
        self.context.assert_not_called()
        self.runner.assert_not_called()
        self.sender.assert_not_called()

    def test_owner_group_and_bot_events_still_do_not_enter_queue(self):
        self.assertFalse(self.bridge.accept(event(sender=OWNER, chat_type="group")))
        self.assertFalse(self.bridge.accept(event(sender=OWNER, sender_type="bot")))
        self.store.insert.assert_not_called()
        self.sender.assert_not_called()

    def test_old_queued_user_is_ignored_before_ack_or_model_work(self):
        self.store.claim.return_value = row()
        self.assertTrue(self.bridge.process_once())
        self.store.update.assert_called_once_with(MESSAGE, status="ignored")
        logged = self.store.log.call_args.args[0]
        self.assertEqual(("message_ignored", "sender_not_allowed"),
                         (logged["status"], logged["reason"]))
        self.assertNotIn("合成问题", json.dumps(logged, ensure_ascii=False))
        self.context.assert_not_called()
        self.runner.assert_not_called()
        self.store.conversation_history.assert_not_called()
        self.store.save_answer.assert_not_called()
        self.sender.assert_not_called()

    def test_previously_generated_answer_for_removed_user_is_not_delivered(self):
        self.store.claim.return_value = row(reply="先前已生成的合成回答", ack_sent=1)
        self.assertTrue(self.bridge.process_once())
        self.store.update.assert_called_once_with(MESSAGE, status="ignored")
        self.sender.assert_not_called()
        self.runner.assert_not_called()

    def test_delivery_boundary_blocks_removed_user_for_text_and_card(self):
        for content in ("合成正文", {"schema": "2.0", "body": {"elements": []}}):
            with self.assertRaises(BridgeError):
                self.bridge._deliver(row(), content, "reply:0")
        self.sender.assert_not_called()

    def test_allowed_user_still_receives_ack_and_usage_card(self):
        self.store.claim.return_value = row(sender=OWNER)
        self.assertTrue(self.bridge.process_once())
        self.assertEqual(2, self.sender.call_count)
        ack, answer = self.sender.call_args_list
        self.assertEqual("收到，正在处理。", ack.args[2])
        self.assertEqual("2.0", answer.args[2]["schema"])
        self.assertEqual("已获准的正文。", answer.args[2]["body"]["elements"][0]["content"])
        self.runner.assert_called_once()
        self.assertEqual("sent", self.store.update.call_args.kwargs["status"])

    def test_legacy_sqlite_queue_also_applies_the_new_allowlist(self):
        with TemporaryDirectory(prefix="feishu-access-offline-") as directory:
            bridge = CodexBridge("cli_accessoffline", object(), users={OWNER},
                                 db_path=Path(directory) / "inbox.sqlite3",
                                 runner=self.runner, context_builder=self.context, sender=self.sender)
            bridge._log = Mock()
            try:
                bridge.db.execute(
                    "INSERT INTO inbox(message_id,chat_id,sender_id,text,created_at) VALUES(?,?,?,?,strftime('%s','now'))",
                    (MESSAGE, CHAT, OTHER, "合成的旧排队消息"),
                )
                self.assertTrue(bridge.process_once())
                status = bridge.db.execute("SELECT status FROM inbox WHERE message_id=?", (MESSAGE,)).fetchone()[0]
                self.assertEqual("ignored", status)
                self.sender.assert_not_called()
                self.runner.assert_not_called()
            finally:
                bridge.close()


if __name__ == "__main__":
    unittest.main()
