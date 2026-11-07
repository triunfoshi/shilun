"""Only the configured sender's genuine group mention can enter the bridge."""

from __future__ import annotations

import json
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from shilun.feishu_codex_bridge import CodexBridge, parse_message
from shilun.feishu_codex_store import RemoteMongoStore
from test_feishu_codex_store import Client


OWNER = "ou_groupowner"
OTHER = "ou_groupother"
BOT = "ou_groupbot"
OTHER_BOT = "ou_otherbot"
KEY = "@_user_1"
QUESTION = "总结2026-11-03复盘"


def mention(identifier=BOT, *, key=KEY, name="诗人"):
    return {"key": key, "id": {"open_id": identifier}, "mentioned_type": "bot", "name": name}


def event(content=None, *, kind="text", sender=OWNER, chat_type="group", mentions=None,
          sender_type="user"):
    body = {"text": KEY + " " + QUESTION} if content is None else content
    message = {"message_id": "om_groupmention", "chat_id": "oc_groupmention",
               "chat_type": chat_type, "message_type": kind,
               "content": json.dumps(body, ensure_ascii=False)}
    if mentions is not None:
        message["mentions"] = mentions
    return {"event": {
        "sender": {"sender_type": sender_type, "sender_id": {"open_id": sender}},
        "message": message,
    }}


def namespace(value):
    if isinstance(value, dict):
        return SimpleNamespace(**{key: namespace(item) for key, item in value.items()})
    if isinstance(value, list):
        return [namespace(item) for item in value]
    return value


def post(identifier=BOT, *, name="诗人"):
    return {"title": "", "content": [[
        {"tag": "at", "user_id": identifier, "user_name": name},
        {"tag": "text", "text": " " + QUESTION},
    ]]}


class GroupMentionTests(unittest.TestCase):
    def parse(self, payload, *, bot=BOT):
        return parse_message(payload, {OWNER}, bot_open_id=bot)

    def test_owner_private_message_does_not_require_a_bot_id_or_mention(self):
        payload = event({"text": QUESTION}, chat_type="p2p")
        for incoming in (payload, namespace(payload)):
            parsed = self.parse(incoming, bot=None)
            self.assertIsNotNone(parsed)
            self.assertEqual(QUESTION, parsed.text)

    def test_owner_genuine_text_mention_accepts_dict_and_sdk_shapes(self):
        payload = event(mentions=[mention()])
        for incoming in (payload, namespace(payload)):
            parsed = self.parse(incoming)
            self.assertIsNotNone(parsed)
            self.assertEqual(QUESTION, parsed.text)
            self.assertEqual((OWNER, "oc_groupmention"), (parsed.sender_id, parsed.chat_id))
            self.assertNotIn(KEY, parsed.text)

    def test_group_admission_fails_closed_without_the_known_bot_open_id(self):
        for incoming in (event(mentions=[mention()]), event(post(), kind="post")):
            self.assertIsNone(self.parse(incoming, bot=None))

    def test_other_users_never_qualify_even_with_a_genuine_bot_mention(self):
        cases = [event(sender=OTHER, mentions=[mention()]),
                 event(post(), kind="post", sender=OTHER),
                 event({"text": QUESTION}, chat_type="p2p", sender=OTHER)]
        for payload in cases:
            for incoming in (payload, namespace(payload)):
                self.assertIsNone(self.parse(incoming))

    def test_owner_without_mention_or_with_a_typed_display_name_is_ignored(self):
        for text in (QUESTION, "@诗人 " + QUESTION, BOT + " " + QUESTION,
                     '<at user_id="' + BOT + '">诗人</at> ' + QUESTION):
            self.assertIsNone(self.parse(event({"text": text})))

    def test_mentioning_someone_else_or_everyone_does_not_trigger_the_bot(self):
        for target in (OTHER, OTHER_BOT, "all"):
            self.assertIsNone(self.parse(event(mentions=[mention(target)])))
            self.assertIsNone(self.parse(event(post(target), kind="post")))

    def test_metadata_without_the_actual_placeholder_does_not_qualify(self):
        self.assertIsNone(self.parse(event({"text": QUESTION}, mentions=[mention()])))
        # A mention key is an entity token, not a prefix of a different token.
        self.assertIsNone(self.parse(event({"text": "@_user_10 " + QUESTION}, mentions=[mention()])))
        for key in (None, "", [], {}):
            self.assertIsNone(self.parse(event(mentions=[mention(key=key)])))

    def test_multiple_mentions_remove_only_the_actual_bot_placeholder(self):
        payload = event({"text": KEY + " " + QUESTION + "，稍后告诉 @_user_2"}, mentions=[
            mention(), mention(OTHER, key="@_user_2", name="其他人"),
        ])
        parsed = self.parse(payload)
        self.assertIsNotNone(parsed)
        self.assertIn(QUESTION, parsed.text)
        self.assertNotIn(KEY, parsed.text)
        self.assertIn("稍后告诉", parsed.text)

    def test_direct_and_localized_rich_posts_require_the_real_at_node(self):
        for body in (post(), {"zh_cn": post()}, {"en_us": post()}):
            for payload in (event(body, kind="post"), namespace(event(body, kind="post"))):
                parsed = self.parse(payload)
                self.assertIsNotNone(parsed)
                self.assertEqual(QUESTION, parsed.text)
                self.assertNotIn(BOT, parsed.text)

    def test_rich_post_placeholder_requires_matching_mention_metadata(self):
        for reference in (KEY, KEY.removeprefix("@")):
            payload = event(post(reference), kind="post", mentions=[mention()])
            self.assertEqual(QUESTION, self.parse(payload).text)
            self.assertEqual(QUESTION, self.parse(namespace(payload)).text)
        self.assertIsNone(self.parse(event(post(KEY), kind="post")))
        self.assertIsNone(self.parse(event(post(KEY), kind="post", mentions=[mention(OTHER)])))

    def test_rich_post_metadata_or_text_alone_is_not_a_genuine_at(self):
        for node in ({"tag": "text", "text": "@诗人 " + QUESTION},
                     {"tag": "text", "text": KEY + " " + QUESTION}):
            body = {"title": "", "content": [[node]]}
            self.assertIsNone(self.parse(event(body, kind="post", mentions=[mention()])))
        self.assertIsNone(self.parse(event(post(OTHER, name="诗人"), kind="post")))

    def test_bridge_uses_its_authenticated_bot_identity_when_enqueuing(self):
        store, model, sender = Mock(), Mock(), Mock()
        store.insert.return_value = True
        bridge = CodexBridge("cli_groupoffline", object(), users={OWNER}, bot_open_id=BOT,
                             store=store, runner=model, context_builder=Mock(), sender=sender)
        try:
            self.assertTrue(bridge.accept(event(mentions=[mention()])))
            queued = store.insert.call_args.args[0]
            self.assertEqual((OWNER, "oc_groupmention", QUESTION),
                             (queued.sender_id, queued.chat_id, queued.text))
            self.assertFalse(bridge.accept(event(mentions=[mention()], sender=OTHER)))
            self.assertFalse(bridge.accept(event({"text": QUESTION})))
            store.insert.assert_called_once()
            model.assert_not_called()
            sender.assert_not_called()
        finally:
            bridge.close()

    def test_bots_unknown_chat_types_and_a_bare_mention_are_ignored(self):
        for payload in (event(mentions=[mention()], sender_type="bot"),
                        event(mentions=[mention()], chat_type="unknown"),
                        event({"text": KEY}, mentions=[mention()]),
                        event({"content": [[{"tag": "at", "user_id": BOT}]]}, kind="post")):
            self.assertIsNone(self.parse(payload))


class GroupHistoryIsolationTests(unittest.TestCase):
    def test_private_chats_each_group_and_other_senders_keep_separate_history(self):
        clock = [1000.0]
        store = RemoteMongoStore("cli_groupoffline", client=Client(),
                                 now_fn=lambda: clock[0], start_heartbeat=False)
        self.addCleanup(store.close)

        def save(identifier, chat, sender, text, reply):
            store.insert({"message_id": identifier, "chat_id": chat, "sender_id": sender, "text": text})
            row = store.claim()
            self.assertIsNotNone(row)
            store.save_answer(row, reply, "synthetic-session-" + identifier)
            store.update(identifier, status="sent")
            clock[0] += 1

        save("om_private", "oc_private", OWNER, "私聊问题", "私聊回答")
        save("om_groupone", "oc_groupone", OWNER, "第一群问题", "第一群回答")
        save("om_grouptwo", "oc_grouptwo", OWNER, "第二群问题", "第二群回答")
        save("om_other", "oc_groupone", OTHER, "别人问题", "别人回答")
        for chat, question, answer in (("oc_private", "私聊问题", "私聊回答"),
                                       ("oc_groupone", "第一群问题", "第一群回答"),
                                       ("oc_grouptwo", "第二群问题", "第二群回答")):
            self.assertEqual([{"user": question, "assistant": answer}],
                             store.conversation_history(chat, OWNER))
        self.assertEqual([{"user": "别人问题", "assistant": "别人回答"}],
                         store.conversation_history("oc_groupone", OTHER))


if __name__ == "__main__":
    unittest.main()
