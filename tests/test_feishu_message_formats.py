"""Message-format routing checks using synthetic Feishu event payloads."""

from __future__ import annotations

import json
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from shilun.feishu_codex_bridge import parse_message


OWNER = "ou_formatowner"


def event(content, *, kind="post", sender=OWNER, chat_type="p2p", sender_type="user"):
    return {"event": {
        "sender": {"sender_type": sender_type, "sender_id": {"open_id": sender}},
        "message": {"message_id": "om_formattest", "chat_id": "oc_formattest",
                    "chat_type": chat_type, "message_type": kind,
                    "content": json.dumps(content, ensure_ascii=False)},
    }}


def namespace(value):
    if isinstance(value, dict):
        return SimpleNamespace(**{name: namespace(item) for name, item in value.items()})
    return value


def post(text="读取石论 2026-11-03 的复盘", *, title=""):
    return {"title": title, "content": [[{"tag": "text", "text": text}]]}


class MessageFormatsTests(unittest.TestCase):
    def test_plain_text_still_accepts_sdk_and_dict_events(self):
        payload = event({"text": "  你好，石论  "}, kind="text")
        for incoming in (payload, namespace(payload)):
            result = parse_message(incoming, {OWNER})
            self.assertIsNotNone(result)
            self.assertEqual("你好，石论", result.text)

    def test_review_post_retains_title_paragraphs_and_numbered_sections(self):
        body = {
            "title": "复盘任务",
            "content": [
                [{"tag": "text", "text": "读取石论 2026-11-03 的复盘，按以下结构总结："}],
                [],
                [{"tag": "text", "text": "1. 一句话判断市场状态"}],
                [{"tag": "text", "text": "2. 指数、量能和市场情绪"}],
                [{"tag": "text", "text": "3. 主要题材及核心个股"}],
            ],
        }
        expected = ("复盘任务\n读取石论 2026-11-03 的复盘，按以下结构总结：\n\n"
                    "1. 一句话判断市场状态\n2. 指数、量能和市场情绪\n3. 主要题材及核心个股")
        for incoming in (event(body), namespace(event(body))):
            result = parse_message(incoming, {OWNER})
            self.assertIsNotNone(result)
            self.assertEqual(expected, result.text)

    def test_localized_posts_choose_chinese_then_english(self):
        cases = [({"zh_cn": post("中文")}, "中文"),
                 ({"en_us": post("English")}, "English"),
                 ({"en_us": post("English"), "zh_cn": post("中文")}, "中文")]
        for body, expected in cases:
            with self.subTest(body=body):
                self.assertEqual(expected, parse_message(event(body), {OWNER}).text)

    def test_adjacent_text_segments_links_and_mentions_keep_visible_meaning(self):
        body = {"title": "", "content": [[
            {"tag": "text", "text": "1. ", "style": ["bold"]},
            {"tag": "text", "text": "请参考 "},
            {"tag": "a", "text": "飞书文档", "href": "https://example.test/document"},
            {"tag": "text", "text": " 并告诉 "},
            {"tag": "at", "user_id": "ou_visibleperson", "user_name": "诗人"},
        ]]}
        result = parse_message(event(body), {OWNER})
        self.assertEqual("1. 请参考 飞书文档 (https://example.test/document) 并告诉 @诗人", result.text)

    def test_link_and_mention_fallbacks_need_no_directory_lookup(self):
        body = {"title": "", "content": [[
            {"tag": "a", "href": "https://example.test"},
            {"tag": "text", "text": " "},
            {"tag": "at", "user_id": "ou_visibleperson"},
        ]]}
        self.assertEqual("https://example.test @ou_visibleperson", parse_message(event(body), {OWNER}).text)

    def test_markdown_and_code_nodes_use_their_text(self):
        body = {"content": [[{"tag": "md", "text": "1. **市场状态**"}],
                            [{"tag": "code_block", "language": "text", "text": "2026-11-03"}]]}
        self.assertEqual("1. **市场状态**\n2026-11-03", parse_message(event(body), {OWNER}).text)

    def test_image_only_post_is_ignored_and_mixed_post_excludes_media_keys(self):
        image = {"tag": "img", "image_key": "img_synthetic_only"}
        media = {"tag": "media", "file_key": "file_synthetic_only"}
        self.assertIsNone(parse_message(event({"content": [[image, media]]}), {OWNER}))
        body = {"content": [[image, {"tag": "text", "text": "文字问题"}, media]]}
        self.assertEqual("文字问题", parse_message(event(body), {OWNER}).text)

    def test_malformed_post_payloads_are_ignored_without_exceptions(self):
        invalid = [None, [], "text", {}, {"zh_cn": None}, {"zh_cn": []},
                   {"title": 3, "content": []}, {"content": "text"},
                   {"content": [None]}, {"content": [[None]]}, {"content": [[{}]]},
                   {"content": [[{"tag": []}]]}, {"content": [[{"tag": "text", "text": 3}]]},
                   {"content": [[{"tag": "a", "text": [], "href": "https://example.test"}]]},
                   {"content": [[{"tag": "at", "user_id": {}}]]}]
        for body in invalid:
            with self.subTest(body=body):
                self.assertIsNone(parse_message(event(body), {OWNER}))
        for raw in ("not json", "[" * 1200 + "]" * 1200):
            incoming = event(post())
            incoming["event"]["message"]["content"] = raw
            self.assertIsNone(parse_message(incoming, {OWNER}))

    def test_post_format_cannot_bypass_user_and_private_chat_gates(self):
        for changes in ({"sender": "ou_other"}, {"sender": {}}, {"sender_type": "bot"},
                        {"sender_type": "app"}, {"chat_type": "group"}, {"kind": "image"}):
            with self.subTest(changes=changes):
                self.assertIsNone(parse_message(event(post(), **changes), {OWNER}))

    def test_total_post_visible_text_limit_applies_before_joining(self):
        self.assertEqual("甲" * 8000, parse_message(event(post("甲" * 8000)), {OWNER}).text)
        self.assertIsNone(parse_message(event(post("甲" * 8001)), {OWNER}))
        self.assertIsNone(parse_message(event(post("甲" * 7999, title="标题")), {OWNER}))
        body = {"content": [[{"tag": "text", "text": "甲" * 4000}],
                            [{"tag": "text", "text": "乙" * 4000}]]}
        self.assertIsNone(parse_message(event(body), {OWNER}))

    def test_oversized_serialized_post_is_rejected_before_json_decode(self):
        incoming = event(post())
        incoming["event"]["message"]["content"] = " " * 64001
        with patch("shilun.feishu_codex_bridge.json.loads", side_effect=AssertionError("must not decode")):
            self.assertIsNone(parse_message(incoming, {OWNER}))


if __name__ == "__main__":
    unittest.main()
