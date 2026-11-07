"""Offline regression checks for private-chat routing and durable replies."""

from __future__ import annotations

import json
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from shilun.feishu_codex_bridge import BridgeError, CodexBridge, allowed_users, parse_message


APP_ID = "cli_offline1"
OWNER = "ou_owner1"
OTHER = "ou_other1"
SESSION_A = "11111111-1111-4111-8111-111111111111"
SESSION_B = "22222222-2222-4222-8222-222222222222"


def incoming(message_id="om_test1", text="你好", chat_id="oc_chat1", *, sender_id=OWNER,
             sender_type="user", chat_type="p2p", message_type="text"):
    return {"event": {
        "sender": {"sender_type": sender_type, "sender_id": {"open_id": sender_id}},
        "message": {"message_id": message_id, "chat_id": chat_id, "chat_type": chat_type,
                    "message_type": message_type, "content": json.dumps({"text": text}, ensure_ascii=False)},
    }}


def namespace(value):
    if isinstance(value, dict):
        return SimpleNamespace(**{key: namespace(item) for key, item in value.items()})
    return value


class MessageRoutingTests(unittest.TestCase):
    def test_owner_private_text_accepts_both_dict_and_sdk_object_shapes(self):
        for event in (incoming(text="  你好，石论  "), namespace(incoming(text="  你好，石论  "))):
            with self.subTest(shape=type(event).__name__):
                parsed = parse_message(event, {OWNER})
                self.assertEqual(("om_test1", "oc_chat1", OWNER, "你好，石论"),
                                 (parsed.message_id, parsed.chat_id, parsed.sender_id, parsed.text))

    def test_non_owner_group_bot_non_text_and_invalid_payloads_are_ignored(self):
        rejected = [incoming(sender_id=OTHER), incoming(sender_type="app"),
                    incoming(sender_type="bot"), incoming(chat_type="group"),
                    incoming(message_type="image"), incoming(text=" "),
                    incoming(text="x" * 8001), incoming(message_id="wrong_id"),
                    incoming(chat_id="wrong_id"), {}, {"event": None}]
        for content in ("not json", "{}", "[]", "null", '{"text":42}'):
            event = incoming()
            event["event"]["message"]["content"] = content
            rejected.append(event)
        for event in rejected:
            with self.subTest(event=event):
                self.assertIsNone(parse_message(event, {OWNER}))

    def test_default_owner_is_selected_from_the_matching_cli_app(self):
        config = {"apps": [
            {"appId": "cli_other", "users": [{"userOpenId": OTHER}]},
            {"appId": APP_ID, "users": [{"userOpenId": OWNER}]},
        ]}
        with (patch("shilun.feishu_codex_bridge._read_env_file", return_value={}),
              patch.dict("os.environ", {}, clear=True),
              patch.object(Path, "read_text", return_value=json.dumps(config))):
            self.assertEqual({OWNER}, allowed_users(APP_ID))

    def test_multiple_cli_users_need_explicit_configuration(self):
        config = {"apps": [{"appId": APP_ID, "users": [
            {"userOpenId": OWNER}, {"userOpenId": OTHER},
        ]}]}
        with (patch("shilun.feishu_codex_bridge._read_env_file", return_value={}),
              patch.dict("os.environ", {}, clear=True),
              patch.object(Path, "read_text", return_value=json.dumps(config))):
            with self.assertRaises(BridgeError):
                allowed_users(APP_ID)
        with (patch("shilun.feishu_codex_bridge._read_env_file", return_value={}),
              patch.dict("os.environ", {"SHILUN_FEISHU_ALLOWED_OPEN_IDS": OWNER}, clear=True),
              patch.object(Path, "read_text", side_effect=AssertionError("unexpected CLI read"))):
            self.assertEqual({OWNER}, allowed_users(APP_ID))


class DurableBridgeTests(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory(prefix="shilun-feishu-bridge-offline-")
        self.db_path = Path(self.directory.name) / "bridge.sqlite3"
        self.runner = Mock(return_value=SimpleNamespace(text="你好，已连接。", session_id=SESSION_A))
        self.context = Mock(return_value=None)
        self.sender = Mock()
        self.bridge = None

    def tearDown(self):
        if self.bridge is not None:
            self.bridge.close()
        self.directory.cleanup()

    def create(self):
        bridge = CodexBridge(APP_ID, object(), users={OWNER}, db_path=self.db_path,
                             runner=self.runner, context_builder=self.context, sender=self.sender)
        bridge._log = Mock()
        self.bridge = bridge
        return bridge

    def row(self, identifier="om_test1"):
        return self.bridge.db.execute("SELECT * FROM inbox WHERE message_id=?", (identifier,)).fetchone()

    def due_now(self):
        self.bridge.db.execute("UPDATE inbox SET next_retry=0")

    def test_callback_only_enqueues_and_message_id_is_deduplicated(self):
        bridge = self.create()
        self.assertTrue(bridge.accept(incoming(text="first version")))
        self.assertFalse(bridge.accept(incoming(text="changed duplicate")))
        self.assertEqual("pending", self.row()["status"])
        self.assertEqual("first version", self.row()["text"])
        self.assertEqual(1, bridge.db.execute("SELECT COUNT(*) FROM inbox").fetchone()[0])
        self.runner.assert_not_called()
        self.context.assert_not_called()
        self.sender.assert_not_called()

    def test_same_chat_resumes_its_session_and_other_chat_starts_fresh(self):
        bridge = self.create()
        self.runner.side_effect = [SimpleNamespace(text="first", session_id=SESSION_A),
                                   SimpleNamespace(text="second", session_id=SESSION_A),
                                   SimpleNamespace(text="other", session_id=SESSION_B)]
        for event in (incoming(), incoming("om_test2", "继续"),
                      incoming("om_test3", "你好", "oc_chat2")):
            bridge.accept(event)
            self.assertTrue(bridge.process_once())
        self.assertEqual([None, SESSION_A, None],
                         [call.kwargs["session_id"] for call in self.runner.call_args_list])
        saved = dict(bridge.db.execute("SELECT chat_id,session_id FROM sessions").fetchall())
        self.assertEqual({"oc_chat1": SESSION_A, "oc_chat2": SESSION_B}, saved)

    def test_successful_generation_is_reused_when_delivery_retries(self):
        bridge = self.create()
        attempts = []
        fail = [True]

        def deliver(_client, message_id, text, delivery_id):
            attempts.append((message_id, text, delivery_id))
            if text == "你好，已连接。" and fail[0]:
                fail[0] = False
                raise BridgeError("synthetic delivery failure")

        self.sender.side_effect = deliver
        bridge.accept(incoming())
        bridge.process_once()
        self.assertEqual("pending", self.row()["status"])
        self.assertEqual("你好，已连接。", self.row()["reply"])
        self.assertEqual(SESSION_A, bridge.db.execute("SELECT session_id FROM sessions").fetchone()[0])
        self.due_now()
        bridge.process_once()
        self.assertEqual("sent", self.row()["status"])
        self.assertEqual(1, self.runner.call_count)
        self.assertEqual(1, sum(text == "收到，正在处理。" for _, text, _ in attempts))
        final_ids = [identifier for _, text, identifier in attempts if text == "你好，已连接。"]
        self.assertEqual(2, len(final_ids))
        self.assertEqual(final_ids[0], final_ids[1])

    def test_long_reply_resumes_at_unsent_part_with_stable_delivery_id(self):
        bridge = self.create()
        answer = "甲" * 3000 + "乙" * 3000 + "丙" * 501
        self.runner.return_value = SimpleNamespace(text=answer, session_id=SESSION_A)
        attempts, fail = [], [True]

        def deliver(_client, _message_id, text, delivery_id):
            attempts.append((text, delivery_id))
            if text == "乙" * 3000 and fail[0]:
                fail[0] = False
                raise BridgeError("synthetic second-part failure")

        self.sender.side_effect = deliver
        bridge.accept(incoming())
        bridge.process_once()
        self.assertEqual(1, self.row()["sent_parts"])
        self.due_now()
        bridge.process_once()
        self.assertEqual("sent", self.row()["status"])
        self.assertEqual(3, self.row()["sent_parts"])
        self.assertEqual(1, self.runner.call_count)
        self.assertEqual(1, sum(text == "甲" * 3000 for text, _ in attempts))
        retry_ids = [identifier for text, identifier in attempts if text == "乙" * 3000]
        self.assertEqual(2, len(retry_ids))
        self.assertEqual(retry_ids[0], retry_ids[1])
        self.assertEqual(1, sum(text == "丙" * 501 for text, _ in attempts))

    def test_reset_removes_only_its_session_without_calling_model(self):
        bridge = self.create()
        bridge.db.executemany("INSERT INTO sessions VALUES(?,?,?)", [
            ("oc_chat1", OWNER, SESSION_A), ("oc_chat2", OWNER, SESSION_B),
        ])
        bridge.accept(incoming(text="/reset"))
        bridge.process_once()
        self.runner.assert_not_called()
        self.context.assert_not_called()
        self.assertEqual([("oc_chat2", SESSION_B)],
                         [tuple(row) for row in bridge.db.execute("SELECT chat_id,session_id FROM sessions")])
        self.assertIn("已开始新对话", self.row()["reply"])

    def test_missing_review_notice_is_replied_without_calling_model(self):
        bridge = self.create()
        self.context.return_value = SimpleNamespace(notice="当日复盘尚未生成。", text="")
        bridge.accept(incoming(text="总结20261102复盘"))
        bridge.process_once()
        self.runner.assert_not_called()
        self.assertEqual("当日复盘尚未生成。", self.row()["reply"])
        self.assertEqual("sent", self.row()["status"])

    def test_review_context_is_given_to_the_model(self):
        bridge = self.create()
        self.context.return_value = SimpleNamespace(notice=None, text='{"analysis_date":"2026-11-02"}')
        bridge.accept(incoming(text="总结20261102复盘"))
        bridge.process_once()
        prompt = self.runner.call_args.args[0]
        self.assertIn("总结20261102复盘", prompt)
        self.assertIn('"analysis_date":"2026-11-02"', prompt)

    def test_reopen_recovers_processing_and_reuses_persisted_reply_parts(self):
        bridge = self.create()
        bridge.accept(incoming())
        bridge.db.execute("UPDATE inbox SET status='processing',reply=?,ack_sent=1,sent_parts=1", ("x" * 3500,))
        bridge.close()
        self.bridge = None
        bridge = self.create()
        self.assertEqual("pending", self.row()["status"])
        bridge.process_once()
        self.runner.assert_not_called()
        self.context.assert_not_called()
        self.assertEqual(1, self.sender.call_count)
        self.assertEqual("x" * 500, self.sender.call_args.args[2])
        self.assertEqual("sent", self.row()["status"])

    def test_session_and_answer_are_saved_in_one_transaction(self):
        bridge = self.create()
        bridge.accept(incoming())
        bridge.db.execute("""CREATE TRIGGER reject_reply BEFORE UPDATE OF reply ON inbox
                             BEGIN SELECT RAISE(ABORT,'synthetic write failure'); END""")
        with self.assertRaises(sqlite3.IntegrityError):
            bridge._save_answer(self.row(), "not committed", SESSION_A)
        self.assertIsNone(self.row()["reply"])
        self.assertEqual(0, bridge.db.execute("SELECT COUNT(*) FROM sessions").fetchone()[0])

    def test_second_instance_is_rejected_until_first_closes(self):
        bridge = self.create()
        with self.assertRaises(BridgeError):
            self.create()
        bridge.close()
        self.bridge = None
        reopened = self.create()
        self.assertIsNotNone(reopened)


if __name__ == "__main__":
    unittest.main()
