"""Offline Mongo contract checks; no credentials or network are accessed."""

from copy import deepcopy
import io
from contextlib import redirect_stdout, redirect_stderr
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from pymongo.errors import AutoReconnect, DuplicateKeyError

from shilun.feishu_codex_store import RemoteMongoStore, RemoteStoreError, RemoteStoreTransportError


def matches(document, query):
    for key, value in query.items():
        if key == "$or":
            if not any(matches(document, branch) for branch in value):
                return False
            continue
        actual = document.get(key)
        if isinstance(value, dict):
            for operator, expected in value.items():
                if operator == "$exists":
                    valid = (key in document) == expected
                elif operator == "$in":
                    valid = actual in expected
                elif actual is None:
                    valid = False
                elif operator == "$lt":
                    valid = actual < expected
                elif operator == "$lte":
                    valid = actual <= expected
                elif operator == "$gt":
                    valid = actual > expected
                else:
                    raise AssertionError(operator)
                if not valid:
                    return False
        elif actual != value:
            return False
    return True


class Cursor:
    def __init__(self, rows):
        self.rows = deepcopy(rows)

    def sort(self, order):
        for key, direction in reversed(order):
            self.rows.sort(key=lambda row: row.get(key, 0), reverse=direction < 0)
        return self

    def limit(self, count):
        self.rows = self.rows[:count]
        return self

    def __iter__(self):
        return iter(self.rows)


class Collection:
    def __init__(self):
        self.rows = {}
        self.failure = None

    def check(self):
        if self.failure is not None:
            failure, self.failure = self.failure, None
            raise failure

    def create_index(self, *_args, **_kwargs):
        self.check()

    def find(self, query):
        self.check()
        return Cursor([row for row in self.rows.values() if matches(row, query)])

    def find_one(self, query, sort=None):
        cursor = self.find(query)
        if sort:
            cursor.sort(sort)
        return deepcopy(next(iter(cursor), None))

    def insert_one(self, row):
        self.check()
        identifier = row.get("_id", "generated-" + str(len(self.rows)))
        if identifier in self.rows:
            raise DuplicateKeyError("duplicate synthetic key")
        self.rows[identifier] = {**deepcopy(row), "_id": identifier}
        return SimpleNamespace(inserted_id=identifier)

    def update_one(self, query, update, upsert=False):
        self.check()
        for row in self.rows.values():
            if matches(row, query):
                before = deepcopy(row)
                row.update(deepcopy(update.get("$set", {})))
                return SimpleNamespace(matched_count=1, modified_count=int(row != before), upserted_id=None)
        if not upsert:
            return SimpleNamespace(matched_count=0, modified_count=0, upserted_id=None)
        row = {key: value for key, value in query.items() if not key.startswith("$") and not isinstance(value, dict)}
        row.update(deepcopy(update.get("$setOnInsert", {})))
        row.update(deepcopy(update.get("$set", {})))
        result = self.insert_one(row)
        return SimpleNamespace(matched_count=0, modified_count=0, upserted_id=result.inserted_id)

    def update_many(self, query, update):
        self.check()
        changed = 0
        for row in self.rows.values():
            if matches(row, query):
                row.update(deepcopy(update.get("$set", {})))
                changed += 1
        return SimpleNamespace(modified_count=changed)

    def find_one_and_update(self, query, update, upsert=False, return_document=None):
        original = self.find_one(query)
        self.update_one(query, update, upsert=upsert)
        if original:
            return self.find_one({"_id": original["_id"]})
        return self.find_one({"_id": query.get("_id")}) if upsert else None

    def delete_one(self, query):
        self.check()
        for identifier, row in list(self.rows.items()):
            if matches(row, query):
                del self.rows[identifier]
                return SimpleNamespace(deleted_count=1)
        return SimpleNamespace(deleted_count=0)


class Database:
    def __init__(self):
        self.collections = {}

    def __getitem__(self, name):
        return self.collections.setdefault(name, Collection())


class Client:
    def __init__(self):
        self.databases = {}

    def __getitem__(self, name):
        return self.databases.setdefault(name, Database())


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.client = Client()
        self.clock = [1000.0]
        self.stores = []

    def store(self, app="cli_one"):
        store = RemoteMongoStore(app, client=self.client, now_fn=lambda: self.clock[0], start_heartbeat=False)
        self.stores.append(store)
        return store

    def tearDown(self):
        for store in self.stores:
            store.close()

    def message(self, identifier="om_one", text="你好", chat="oc_one"):
        return {"message_id": identifier, "chat_id": chat, "sender_id": "ou_one", "text": text}

    def save(self, store, identifier, text, reply, *, sid="session-one"):
        store.insert(self.message(identifier, text))
        row = store.claim()
        store.save_answer(row, reply, sid, metadata={"input_tokens": 12})
        store.update(identifier, status="sent")
        self.clock[0] += 1
        return row

    def test_insert_deduplicates_and_apps_are_isolated(self):
        first, second = self.store(), self.store("cli_two")
        self.assertTrue(first.insert(self.message()))
        self.assertFalse(first.insert(self.message()))
        self.assertTrue(second.insert(self.message()))
        row = first.claim()
        self.assertEqual("cli_one", row["app_id"])
        self.assertIsNone(row["usage"])
        first.update(row["message_id"], status="sent")
        self.assertEqual("pending", second.inbox.find_one({"app_id": "cli_two"})["status"])

    def test_claim_respects_oldest_retry_and_expires_stale_pending(self):
        store = self.store()
        store.insert(self.message())
        store.update("om_one", next_retry=1010)
        store.insert(self.message("om_two"))
        self.assertIsNone(store.claim())
        self.clock[0] = 1011
        self.assertEqual("om_one", store.claim()["message_id"])
        store.update("om_one", status="sent")
        store.close()
        self.clock[0] = 3000
        store = self.store()
        self.assertIsNone(store.claim())
        self.assertEqual("expired", store.inbox.find_one({"message_id": "om_two"})["status"])

    def test_lease_blocks_another_instance_and_can_expire(self):
        first = self.store()
        with self.assertRaisesRegex(RemoteStoreError, "租约"):
            self.store()
        self.clock[0] += 61
        replacement = self.store()
        with self.assertRaises(RemoteStoreError):
            first.insert(self.message())
        first.close()
        self.assertEqual(replacement.owner, replacement.leases.find_one({"app_id": "cli_one"})["owner"])

    def test_single_heartbeat_transport_failure_recovers_within_confirmed_lease(self):
        store = self.store()
        store.leases.failure = AutoReconnect("sensitive-connection-information")
        output, errors = io.StringIO(), io.StringIO()
        with redirect_stdout(output), redirect_stderr(errors):
            with self.assertRaises(RemoteStoreTransportError) as raised:
                store.renew_lease()
            self.assertTrue(store.healthy)
            self.clock[0] += 2
            store.renew_lease()
            store.log({"status": "heartbeat_recovered"})
        lease = store.leases.find_one({"app_id": "cli_one"})
        self.assertEqual(1062, lease["expires_at"])
        self.assertEqual(lease["expires_at"], store._lease_until)
        self.assertNotIn("sensitive-connection-information", str(raised.exception))
        self.assertEqual("", output.getvalue() + errors.getvalue())

    def test_heartbeat_retries_transport_failure_after_two_seconds(self):
        store = self.store()
        store.leases.failure = AutoReconnect("temporary transport failure")
        delays = []

        def wait(delay):
            delays.append(delay)
            if len(delays) == 3:
                return True
            self.clock[0] += delay
            return False

        with patch.object(store._stop, "wait", side_effect=wait):
            store._heartbeat()
        self.assertEqual([15, 2, 15], delays)
        self.assertTrue(store.healthy)
        self.assertEqual(1077, store._lease_until)

    def test_transport_failure_crossing_lease_deadline_fails_closed(self):
        store = self.store()

        def disconnected(*_args, **_kwargs):
            self.clock[0] = 1061
            raise AutoReconnect("sensitive-connection-information")

        with patch.object(store.leases, "update_one", side_effect=disconnected):
            with self.assertRaises(RemoteStoreError) as raised:
                store.renew_lease()
        self.assertNotIsInstance(raised.exception, RemoteStoreTransportError)
        self.assertFalse(store.healthy)
        with self.assertRaises(RemoteStoreError):
            store.log({"status": "cannot-write"})
        self.assertNotIn("sensitive-connection-information", str(raised.exception))

    def test_expired_lease_cannot_be_renewed_or_used_for_writes(self):
        store = self.store()
        self.clock[0] = 1060
        with self.assertRaises(RemoteStoreError):
            store.renew_lease()
        with self.assertRaises(RemoteStoreError):
            store.insert(self.message())
        self.assertFalse(store.healthy)

    def test_owner_loss_invalidates_immediately_without_retry_grace(self):
        store = self.store()
        lease = store.leases.rows[store._lease_id]
        lease["owner"] = "replacement-owner"
        with self.assertRaises(RemoteStoreError) as raised:
            store.renew_lease()
        self.assertNotIsInstance(raised.exception, RemoteStoreTransportError)
        self.assertFalse(store.healthy)
        with self.assertRaises(RemoteStoreError):
            store.log({"status": "cannot-write"})
        self.assertEqual("replacement-owner", lease["owner"])

    def test_answer_fact_survives_session_projection_failure_and_restart(self):
        first = self.store()
        first.insert(self.message())
        row = first.claim()
        first.sessions.failure = AutoReconnect("projection temporary failure")
        with self.assertRaises(RemoteStoreError):
            first.save_answer(row, "真实模型回答", "session-one", {"output_tokens": 7})
        saved = first.inbox.find_one({"message_id": "om_one"})
        self.assertEqual("真实模型回答", saved["reply"])
        first.close()
        replacement = self.store()
        restored = replacement.claim()
        self.assertEqual("真实模型回答", restored["reply"])
        self.assertEqual({"output_tokens": 7}, restored["usage"])
        self.assertEqual("session-one", replacement.get_session("oc_one", "ou_one"))

    def test_history_is_sent_only_plain_ordered_and_reset_scoped(self):
        store = self.store()
        self.save(store, "om_one", "第一问", "第一答")
        self.save(store, "om_two", "第二问", "第二答")
        store.insert(self.message("om_reset", "/reset"))
        reset = store.claim()
        store.reset_session("oc_one", "ou_one", row=reset)
        store.save_answer(reset, "已重置", None)
        store.update("om_reset", status="sent")
        self.clock[0] += 1
        self.save(store, "om_three", "新问题", "新回答", sid="session-two")
        store.insert(self.message("om_not_sent", "未完成问题"))
        self.assertEqual([{"user": "新问题", "assistant": "新回答"}], store.conversation_history("oc_one", "ou_one"))
        self.assertEqual([], store.conversation_history("oc_other", "ou_one"))

    def test_repaired_old_answer_does_not_overwrite_later_session(self):
        store = self.store()
        old = self.save(store, "om_one", "旧问", "旧答", sid="old-session")
        self.save(store, "om_two", "新问", "新答", sid="new-session")
        store.save_answer(old, "旧答", "old-session")
        self.assertEqual("new-session", store.get_session("oc_one", "ou_one"))

    def test_import_is_idempotent_preserves_server_state_and_converts_processing(self):
        store = self.store()
        local = [{**self.message(), "created_at": 999, "status": "processing", "reply": "已生成",
                  "sent_parts": 1, "ack_sent": 1}]
        sessions = [{"chat_id": "oc_one", "sender_id": "ou_one", "session_id": "historical"}]
        self.assertEqual({"messages_inserted": 1, "sessions_inserted": 1}, store.import_history(local, sessions))
        self.assertEqual("pending", store.inbox.find_one({"message_id": "om_one"})["status"])
        store.update("om_one", status="sent", sent_parts=2)
        self.assertEqual({"messages_inserted": 0, "sessions_inserted": 0}, store.import_history(local, sessions))
        restored = store.inbox.find_one({"message_id": "om_one"})
        self.assertEqual("sent", restored["status"])
        self.assertEqual(2, restored["sent_parts"])
        self.assertIsNone(restored["usage"])

    def test_logs_live_only_in_scoped_remote_collection_without_ttl(self):
        store = self.store()
        output, errors = io.StringIO(), io.StringIO()
        with redirect_stdout(output), redirect_stderr(errors):
            store.log({"status": "reply_sent", "app_id": "spoofed", "parts": 2})
        entry = store.logs.find_one({"app_id": "cli_one"})
        self.assertEqual("reply_sent", entry["status"])
        self.assertEqual(2, entry["parts"])
        self.assertIsNotNone(entry["logged_at"])
        self.assertEqual("", output.getvalue() + errors.getvalue())

    def test_close_releases_only_owned_lease_and_is_idempotent(self):
        store = self.store()
        store.close()
        store.close()
        self.assertIsNone(store.leases.find_one({"app_id": "cli_one"}))


if __name__ == "__main__":
    unittest.main()
