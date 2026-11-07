"""Offline checks for compact usage cards and the SDK reply boundary."""

from __future__ import annotations

import json
import unittest
from unittest.mock import Mock

from shilun.feishu_codex_bridge import reply_text
from shilun.feishu_reply_card import reply_card


class ReplyCardTests(unittest.TestCase):
    def test_answer_and_small_usage_caption_are_separate(self):
        usage = "已完成 · 耗时 46.5s · gpt-test\n↑ 5.3k ↓ 723 · 缓存 —/— · 上下文 未提供"
        card = reply_card("**回答**\n内容", usage)
        answer, caption = card["body"]["elements"]
        self.assertEqual("2.0", card["schema"])
        self.assertEqual("**回答**\n内容", answer["content"])
        footer = caption["columns"][0]["elements"][0]
        self.assertEqual("<font color='grey'>" + usage + "</font>", footer["content"])
        sizes = card["config"]["style"]["text_size"]
        self.assertEqual({"default": "notation", "pc": "notation", "mobile": "notation"},
                         sizes[footer["text_size"]])
        self.assertEqual("normal", sizes[answer["text_size"]]["default"])

    def test_model_authored_html_cannot_introduce_mentions(self):
        card = reply_card("<at id=all></at> & **正常粗体**", "已完成\n↑ 0 ↓ 0")
        content = card["body"]["elements"][0]["content"]
        self.assertNotIn("<at", content)
        self.assertIn("&lt;at id=all&gt;", content)
        self.assertIn("**正常粗体**", content)

    def test_sdk_uses_interactive_type_with_json_card_and_stable_uuid(self):
        client = Mock()
        client.im.v1.message.reply.return_value.success.return_value = True
        card = reply_card("正文", "已完成\n↑ 1 ↓ 2")
        reply_text(client, "om_test", card, "same-uuid")
        request = client.im.v1.message.reply.call_args.args[0]
        self.assertEqual("interactive", request.request_body.msg_type)
        self.assertEqual(card, json.loads(request.request_body.content))
        self.assertEqual("same-uuid", request.request_body.uuid)

    def test_sdk_keeps_non_model_ack_as_text(self):
        client = Mock()
        client.im.v1.message.reply.return_value.success.return_value = True
        reply_text(client, "om_test", "收到，正在处理。", "ack-uuid")
        request = client.im.v1.message.reply.call_args.args[0]
        self.assertEqual("text", request.request_body.msg_type)
        self.assertEqual({"text": "收到，正在处理。"}, json.loads(request.request_body.content))


if __name__ == "__main__":
    unittest.main()
