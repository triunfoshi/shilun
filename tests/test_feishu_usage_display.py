"""Offline checks for reply usage display and server-backed conversation state."""

from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
import json
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from shilun import feishu_codex_bridge as bridge_module
from shilun.feishu_codex_runner import CodexResult


APP_ID = "cli_usageoffline"
OWNER = "ou_usageowner"
CHAT = "oc_usagechat"
SESSION = "019a1234-5678-7abc-8def-0123456789ab"


def _event(text: str = "你好", identifier: str = "om_usage1") -> dict:
    return {"event": {
        "sender": {"sender_type": "user", "sender_id": {"open_id": OWNER}},
        "message": {"message_id": identifier, "chat_id": CHAT, "chat_type": "p2p",
                    "message_type": "text", "content": json.dumps({"text": text}, ensure_ascii=False)},
    }}


def _result(text: str = "这是一段纯正文。") -> CodexResult:
    return CodexResult(
        text=text, session_id=SESSION, model="gpt-test", model_source="configured",
        input_tokens=150, cached_input_tokens=100, output_tokens=20, reasoning_output_tokens=12,
        elapsed_seconds=12.34, reasoning_effort="ultra", reasoning_effort_source="configured",
        service_tier="priority", service_tier_source="configured",
    )


class MemoryStore:
    """A server-store contract double with no filesystem or database access."""

    def __init__(self) -> None:
        self.rows: dict[str, dict] = {}
        self.saved: list[dict] = []
        self.logs: list[dict] = []
        self.history_requests: list[tuple] = []
        self.seed_history: list[dict] = []
        self.reset_requests: list[tuple] = []
        self.closed = False

    def insert(self, incoming) -> bool:
        if incoming.message_id in self.rows:
            return False
        self.rows[incoming.message_id] = {
            "message_id": incoming.message_id, "chat_id": incoming.chat_id,
            "sender_id": incoming.sender_id, "text": incoming.text, "created_at": time.time(),
            "status": "pending", "reply": None, "usage": None, "ack_sent": 0,
            "sent_parts": 0, "attempts": 0, "next_retry": 0,
        }
        return True

    def claim(self):
        for row in self.rows.values():
            if row["status"] == "pending" and row["next_retry"] <= time.time():
                row["status"] = "processing"
                return deepcopy(row)
        return None

    def update(self, message_id: str, **fields) -> None:
        self.rows[message_id].update(deepcopy(fields))

    def save_answer(self, row, answer: str, session_id: str | None, *, metadata=None) -> None:
        self.rows[row["message_id"]].update(reply=answer, usage=deepcopy(metadata))
        self.saved.append({"answer": answer, "session_id": session_id, "metadata": deepcopy(metadata)})

    def conversation_history(self, chat_id: str, sender_id: str, *, limit: int):
        self.history_requests.append((chat_id, sender_id, limit))
        rows = [{"user": row["text"], "assistant": row["reply"]} for row in self.rows.values()
                if row["chat_id"] == chat_id and row["sender_id"] == sender_id
                and row["status"] == "sent" and row["reply"] is not None]
        return deepcopy((self.seed_history + rows)[-limit:])

    def reset_session(self, chat_id: str, sender_id: str, *, row) -> None:
        self.reset_requests.append((chat_id, sender_id, row["message_id"]))

    def log(self, event: dict) -> None:
        self.logs.append(deepcopy(event))

    def close(self) -> None:
        self.closed = True


@contextmanager
def _remote_bridge(*, store=None, runner=None, sender=None, context_builder=None):
    store = store if store is not None else MemoryStore()
    runner = runner if runner is not None else Mock(return_value=_result())
    deliveries: list[tuple[str, str | dict, str]] = []

    def capture(_client, message_id: str, text: str | dict, delivery_id: str) -> None:
        deliveries.append((message_id, text, delivery_id))
        if sender is not None:
            sender(message_id, text, delivery_id)

    forbidden = AssertionError("server-backed bridge must not touch local persistence")
    with (
        patch.object(bridge_module.sqlite3, "connect", side_effect=forbidden),
        patch.object(bridge_module.Path, "open", side_effect=forbidden),
        patch.object(bridge_module.Path, "mkdir", side_effect=forbidden),
        patch.object(bridge_module.fcntl, "flock", side_effect=forbidden),
        patch.object(bridge_module, "allowed_users", side_effect=AssertionError("explicit test user required")),
    ):
        bridge = bridge_module.CodexBridge(
            APP_ID, object(), users={OWNER}, store=store, runner=runner,
            context_builder=context_builder or (lambda _text: None), sender=capture,
        )
        try:
            yield bridge, store, runner, deliveries
        finally:
            bridge.close()


class UsageFooterTests(unittest.TestCase):
    def test_compact_summary_follows_requested_two_line_order(self):
        metadata = bridge_module.usage_metadata(_result())
        footer = bridge_module.usage_footer(metadata)
        self.assertEqual(
            "已完成 · 耗时 12.3s · gpt-test\n"
            "↑ 150 ↓ 20 · 缓存 100/— (67%) · 上下文占用 未返回", footer,
        )
        self.assertEqual(2, len(footer.splitlines()))
        self.assertEqual("configured", metadata["model_source"])
        self.assertNotIn("session_id", metadata)
        self.assertNotIn("text", metadata)
        self.assertNotIn("这是一段纯正文", json.dumps(metadata, ensure_ascii=False))

    def test_reported_model_appears_in_summary_without_verbose_labels(self):
        footer = bridge_module.usage_footer({"model": "gpt-reported", "model_source": "reported"})
        self.assertIn("已完成 · 耗时 — · gpt-reported\n", footer)
        self.assertNotIn("本次配置", footer)

    def test_reported_zero_is_legal_and_missing_values_are_not_zero(self):
        known_zero = CodexResult("正文", SESSION, model="gpt-test", model_source="configured",
                                input_tokens=0, cached_input_tokens=0, output_tokens=0, elapsed_seconds=0.0)
        footer = bridge_module.usage_footer(bridge_module.usage_metadata(known_zero))
        self.assertIn("↑ 0 ↓ 0 · 缓存 0/—", footer)
        self.assertIn("耗时 0.0s", footer)
        unknown = CodexResult("正文", SESSION, model="gpt-test", model_source="configured")
        footer = bridge_module.usage_footer(bridge_module.usage_metadata(unknown))
        self.assertIn("↑ — ↓ — · 缓存 —/—", footer)
        self.assertNotIn("↑ 0", footer)
        self.assertNotIn("(0%)", footer)

    def test_cached_and_reasoning_tokens_are_subsets_of_total(self):
        result = _result()
        metadata = bridge_module.usage_metadata(result)
        self.assertEqual(170, metadata["total_tokens"])
        footer = bridge_module.usage_footer(metadata)
        self.assertIn("↑ 150 ↓ 20 · 缓存 100/— (67%)", footer)
        self.assertNotIn("↑ 50", footer)
        self.assertNotIn("↑ 250", footer)
        self.assertNotIn("↓ 32", footer)

    def test_large_counts_are_compact_and_real_context_is_displayed(self):
        footer = bridge_module.usage_footer({
            "model": "gpt-test", "elapsed_seconds": 46.5,
            "input_tokens": 81500, "output_tokens": 723,
            "cached_input_tokens": 75800, "cache_write_tokens": 0,
            "context_tokens": 76800, "context_window_tokens": 128000,
        })
        self.assertEqual(
            "已完成 · 耗时 46.5s · gpt-test\n"
            "↑ 81.5k ↓ 723 · 缓存 75.8k/0 (93%) · 上下文 76.8k/128k (60%)", footer,
        )

    def test_counts_and_percentages_round_half_up(self):
        footer = bridge_module.usage_footer({"input_tokens": 200, "cached_input_tokens": 105,
                                             "output_tokens": 1250})
        self.assertIn("↓ 1.3k · 缓存 105/— (53%)", footer)
        footer = bridge_module.usage_footer({"input_tokens": 1000, "output_tokens": 999})
        self.assertIn("↑ 1k ↓ 999", footer)

    def test_context_is_not_inferred_from_input_or_an_assumed_limit(self):
        footer = bridge_module.usage_footer({"input_tokens": 76800, "output_tokens": 723})
        self.assertTrue(footer.endswith("上下文占用 未返回"))
        self.assertNotIn("128k", footer)
        footer = bridge_module.usage_footer({"context_tokens": 76800})
        self.assertTrue(footer.endswith("上下文 76.8k/—"))

    def test_empty_metadata_has_no_fabricated_call_footer(self):
        self.assertIsNone(bridge_module.usage_metadata(CodexResult("正文", SESSION)))
        self.assertEqual("", bridge_module.usage_footer(None))
        self.assertEqual("", bridge_module.usage_footer({}))

    def test_invalid_token_values_display_as_not_provided(self):
        footer = bridge_module.usage_footer({
            "model": "gpt-test", "input_tokens": True, "cached_input_tokens": -1,
            "output_tokens": "20", "total_tokens": None, "elapsed_seconds": float("nan"),
        })
        self.assertIn("耗时 —", footer)
        self.assertIn("↑ — ↓ — · 缓存 —/—", footer)
        self.assertNotIn("nan", footer)


class RemoteUsageFlowTests(unittest.TestCase):
    def test_footer_is_only_sent_while_store_and_history_keep_plain_body(self):
        body = "这是一段纯正文。"
        with _remote_bridge(runner=Mock(return_value=_result(body))) as (bridge, store, runner, deliveries):
            self.assertTrue(bridge.accept(_event()))
            self.assertTrue(bridge.process_once())
            self.assertEqual(body, store.saved[0]["answer"])
            self.assertEqual(body, store.rows["om_usage1"]["reply"])
            self.assertEqual(170, store.saved[0]["metadata"]["total_tokens"])
            self.assertEqual(body, store.conversation_history(CHAT, OWNER, limit=8)[-1]["assistant"])
            self.assertEqual("收到，正在处理。", deliveries[0][1])
            card = deliveries[-1][1]
            self.assertEqual(body, card["body"]["elements"][0]["content"])
            footer = card["body"]["elements"][1]["columns"][0]["elements"][0]
            self.assertIn("已完成 · 耗时 12.3s · gpt-test", footer["content"])
            self.assertIn("↑ 150 ↓ 20", footer["content"])
            self.assertEqual("usage", footer["text_size"])
            self.assertNotIn("已完成 · 耗时", store.saved[0]["answer"])
            self.assertFalse(hasattr(bridge, "db"))
            self.assertFalse(hasattr(bridge, "instance_lock"))
            self.assertFalse(bridge.process_once())
            runner.assert_called_once()
        self.assertTrue(store.closed)

    def test_server_history_is_injected_into_ephemeral_call_without_local_resume(self):
        store = MemoryStore()
        store.seed_history = [{"user": "先前的服务器问题", "assistant": "先前的服务器回答"}]
        with _remote_bridge(store=store) as (bridge, store, runner, _deliveries):
            self.assertTrue(bridge.accept(_event("继续解释")))
            self.assertTrue(bridge.process_once())
            prompt = runner.call_args.args[0]
            self.assertIn("继续解释", prompt)
            self.assertIn("先前的服务器问题", prompt)
            self.assertIn("先前的服务器回答", prompt)
            self.assertIn("服务器保存的本聊天中本用户历史", prompt)
            self.assertEqual((CHAT, OWNER, 8), store.history_requests[0])
            self.assertTrue(runner.call_args.kwargs["ephemeral"])
            self.assertIsNone(runner.call_args.kwargs["session_id"])
            self.assertIs(bridge.stop_event, runner.call_args.kwargs["stop_event"])

    def test_followup_history_does_not_reinject_display_footer(self):
        runner = Mock(side_effect=[_result("第一段纯正文"), _result("第二段纯正文")])
        with _remote_bridge(runner=runner) as (bridge, store, runner, _deliveries):
            self.assertTrue(bridge.accept(_event("第一次", "om_usage1")))
            self.assertTrue(bridge.process_once())
            self.assertTrue(bridge.accept(_event("第二次", "om_usage2")))
            self.assertTrue(bridge.process_once())
            second_prompt = runner.call_args_list[1].args[0]
            self.assertIn("第一段纯正文", second_prompt)
            self.assertNotIn("已完成 · 耗时", second_prompt)
            self.assertNotIn("缓存 100/", second_prompt)
            self.assertEqual(["第一段纯正文", "第二段纯正文"], [saved["answer"] for saved in store.saved])

    def test_delivery_retry_reuses_saved_usage_and_body_without_second_model_call(self):
        failed = False

        def fail_first_answer(_message_id, text, _delivery_id):
            nonlocal failed
            if isinstance(text, dict) and not failed:
                failed = True
                raise bridge_module.BridgeError("synthetic delivery failure")

        with _remote_bridge(sender=fail_first_answer) as (bridge, store, runner, deliveries):
            self.assertTrue(bridge.accept(_event()))
            self.assertTrue(bridge.process_once())
            self.assertEqual("pending", store.rows["om_usage1"]["status"])
            self.assertEqual(170, store.rows["om_usage1"]["usage"]["total_tokens"])
            first_text, first_uuid = deliveries[-1][1:]
            store.rows["om_usage1"]["next_retry"] = 0
            self.assertTrue(bridge.process_once())
            self.assertEqual("sent", store.rows["om_usage1"]["status"])
            self.assertEqual((first_text, first_uuid), deliveries[-1][1:])
            runner.assert_called_once()
            self.assertEqual(1, len(store.saved))
            self.assertEqual("这是一段纯正文。", store.saved[0]["answer"])

    def test_explicit_recovery_revision_changes_ids_but_retry_remains_idempotent(self):
        failed = False

        def fail_first_recovered_answer(_message_id, text, _delivery_id):
            nonlocal failed
            if isinstance(text, dict) and not failed:
                failed = True
                raise bridge_module.BridgeError("synthetic recovery delivery failure")

        with _remote_bridge(sender=fail_first_recovered_answer) as (bridge, store, runner, deliveries):
            bridge.accept(_event())
            row = store.rows["om_usage1"]
            bridge._deliver(row, "收到，正在处理。", "ack")
            old_ack_id = deliveries[-1][2]
            bridge._deliver(row, "先前生成超时。", "reply:0")
            old_reply_id = deliveries[-1][2]
            row.update(delivery_revision=0)
            bridge._deliver(row, "先前生成超时。", "reply:0")
            self.assertEqual(old_reply_id, deliveries[-1][2])
            row.update(status="sent", reply="先前生成超时。", ack_sent=1, sent_parts=1)

            # An explicit administrator recovery clears the old generic answer
            # and advances the revision; ordinary delivery retries never do so.
            row.update(status="pending", reply=None, usage=None, ack_sent=0,
                       sent_parts=0, delivery_revision=1)
            bridge.process_once()
            self.assertEqual("pending", row["status"])
            self.assertNotEqual(old_ack_id, deliveries[-2][2])
            first_card, first_id = deliveries[-1][1:]
            self.assertIsInstance(first_card, dict)
            self.assertNotEqual(old_reply_id, first_id)

            row["next_retry"] = 0
            bridge.process_once()
            self.assertEqual("sent", row["status"])
            self.assertEqual((first_card, first_id), deliveries[-1][1:])
            runner.assert_called_once()
            self.assertEqual(1, len(store.saved))

    def test_long_reply_only_adds_usage_card_to_last_part(self):
        body = "甲" * 3000 + "乙" * 3000 + "丙" * 501
        with _remote_bridge(runner=Mock(return_value=_result(body))) as (bridge, store, runner, deliveries):
            bridge.accept(_event())
            bridge.process_once()
            self.assertEqual("甲" * 3000, deliveries[1][1])
            self.assertEqual("乙" * 3000, deliveries[2][1])
            final_card = deliveries[3][1]
            self.assertEqual("丙" * 501, final_card["body"]["elements"][0]["content"])
            self.assertEqual(3, store.rows["om_usage1"]["sent_parts"])
            self.assertEqual(body, store.saved[0]["answer"])
            runner.assert_called_once()

    def test_direct_notice_does_not_invent_model_usage(self):
        notice = "当日复盘尚未生成。"
        context_builder = lambda _text: SimpleNamespace(text="", notice=notice)
        with _remote_bridge(context_builder=context_builder) as (bridge, store, runner, deliveries):
            self.assertTrue(bridge.accept(_event("总结20261102复盘")))
            self.assertTrue(bridge.process_once())
            runner.assert_not_called()
            self.assertEqual(notice, deliveries[-1][1])
            self.assertEqual(notice, store.saved[0]["answer"])
            self.assertIsNone(store.saved[0]["metadata"])


if __name__ == "__main__":
    unittest.main()
