"""App-scoped Mongo persistence for the Feishu/Codex worker.

No log, inbox, or conversation file is created by this module. A standalone Mongo deployment is
supported: generated answers are authoritative inbox facts, and session audit
projections can be repaired without generating the answer a second time.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import threading
import time
from typing import Any, Callable, Mapping
import uuid

from bson.errors import BSONError
from pymongo import ASCENDING, DESCENDING, MongoClient, ReturnDocument, timeout
from pymongo.errors import DuplicateKeyError, PyMongoError

from shilun.common.config import load_config


class RemoteStoreError(RuntimeError):
    """Safe operational error; never contains a connection URI or credentials."""


class RemoteStoreTransportError(RemoteStoreError):
    """A heartbeat transport failure while the confirmed lease is still valid."""


def _identifier(*parts: str) -> str:
    return hashlib.sha256("\0".join(parts).encode("utf-8")).hexdigest()


class RemoteMongoStore:
    def __init__(self, app_id: str, *, client: Any = None, mongo_uri: str | None = None,
                 db_name: str | None = None, now_fn: Callable[[], float] = time.time,
                 lease_seconds: float = 60, heartbeat_interval: float = 15,
                 operation_timeout: float = 5, start_heartbeat: bool = True) -> None:
        if not app_id or lease_seconds <= 0 or not 0 < heartbeat_interval < lease_seconds:
            raise RemoteStoreError("远端存储应用标识或租约配置无效。")
        if operation_timeout <= 0:
            raise RemoteStoreError("远端存储超时配置无效。")
        self.app_id, self.now = app_id, now_fn
        self.lease_seconds, self.operation_timeout = lease_seconds, operation_timeout
        self.heartbeat_interval = heartbeat_interval
        self.owner = str(uuid.uuid4())
        self._lease_id = "feishu-codex:" + app_id
        self._lease_until = 0.0
        self._failure = False
        self._closed = False
        self._stop = threading.Event()
        self._heartbeat_thread: threading.Thread | None = None
        self._owns_client = client is None
        if client is None:
            config = load_config()
            uri = mongo_uri or config.mongo_uri
            if not uri:
                raise RemoteStoreError("远端存储需要配置 SHILUN_MONGO_URI；不会回退到本地文件。")
            db_name = db_name or config.mongo_db
            client = MongoClient(uri, connectTimeoutMS=1500, socketTimeoutMS=2000,
                                 serverSelectionTimeoutMS=2000, retryWrites=False,
                                 appname="shilun-feishu-codex")
        self.client = client
        self.database = client[db_name or "shilun"]
        self.inbox = self.database["feishu_codex_inbox"]
        self.sessions = self.database["feishu_codex_sessions"]
        self.logs = self.database["feishu_codex_logs"]
        self.leases = self.database["feishu_codex_leases"]
        try:
            with timeout(max(5, operation_timeout)):
                self.inbox.create_index([("app_id", ASCENDING), ("message_id", ASCENDING)], unique=True)
                self.inbox.create_index([("app_id", ASCENDING), ("status", ASCENDING),
                                         ("created_at", ASCENDING), ("message_id", ASCENDING)])
                self.sessions.create_index([("app_id", ASCENDING), ("chat_id", ASCENDING),
                                            ("sender_id", ASCENDING)], unique=True)
                self.logs.create_index([("app_id", ASCENDING), ("logged_at", DESCENDING)])
                self._acquire()
                self.recover_processing()
        except Exception as exc:
            if self._lease_until:
                try:
                    with timeout(operation_timeout):
                        self.leases.delete_one({"_id": self._lease_id, "app_id": self.app_id, "owner": self.owner})
                except PyMongoError:
                    pass  # Lease expiry still bounds an interrupted startup.
            if self._owns_client:
                self.client.close()
            if isinstance(exc, PyMongoError):
                raise RemoteStoreError("无法初始化 ECS Mongo 存储；请检查连接及索引权限。") from exc
            raise
        if start_heartbeat:
            self._heartbeat_thread = threading.Thread(target=self._heartbeat, name="feishu-mongo-lease", daemon=True)
            self._heartbeat_thread.start()

    def _acquire(self) -> None:
        current = self.now()
        try:
            document = self.leases.find_one_and_update(
                {"_id": self._lease_id, "app_id": self.app_id,
                 "$or": [{"expires_at": {"$lte": current}}, {"owner": self.owner}]},
                {"$set": {"app_id": self.app_id, "owner": self.owner,
                          "expires_at": current + self.lease_seconds, "heartbeat_at": current}},
                upsert=True, return_document=ReturnDocument.AFTER,
            )
            if document is None:
                raise RemoteStoreError("此飞书应用已有在线机器人持有远端租约。")
            self._lease_until = current + self.lease_seconds
        except DuplicateKeyError as exc:
            raise RemoteStoreError("此飞书应用已有在线机器人持有远端租约。") from exc
        except PyMongoError as exc:
            raise RemoteStoreError("无法取得 ECS Mongo 租约；不会启动本地存储。") from exc

    def assert_active(self) -> None:
        if self._closed or self._failure or self.now() >= self._lease_until:
            raise RemoteStoreError("远端存储租约已失效，已停止处理消息。")

    @property
    def healthy(self) -> bool:
        return not (self._closed or self._failure or self.now() >= self._lease_until)

    def _heartbeat(self) -> None:
        delay = self.heartbeat_interval
        while not self._stop.wait(delay):
            try:
                self.renew_lease()
                delay = self.heartbeat_interval
            except RemoteStoreTransportError:
                # A dropped response must not revoke a lease we have already
                # confirmed. Retry briefly, never beyond its original deadline.
                delay = min(2, max(0, self._lease_until - self.now()))
            except RemoteStoreError:
                self._failure = True
                self._stop.set()

    def renew_lease(self) -> None:
        self.assert_active()
        current = self.now()
        try:
            with timeout(self.operation_timeout):
                result = self.leases.update_one(
                    {"_id": self._lease_id, "app_id": self.app_id,
                     "owner": self.owner, "expires_at": {"$gt": current}},
                    {"$set": {"expires_at": current + self.lease_seconds, "heartbeat_at": current}},
                )
            if result.matched_count != 1:
                self._failure = True
                raise RemoteStoreError("机器人远端租约已被释放或接管，已停止处理消息。")
            self._lease_until = current + self.lease_seconds
        except PyMongoError as exc:
            if self.now() >= self._lease_until:
                self._failure = True
                raise RemoteStoreError("ECS Mongo 租约已过期，已停止处理消息。") from exc
            raise RemoteStoreTransportError("ECS Mongo 租约心跳暂时失败，将在有效期内重试。") from exc

    def _call(self, operation: Callable[[], Any]) -> Any:
        self.assert_active()
        try:
            with timeout(self.operation_timeout):
                return operation()
        except RemoteStoreError:
            raise
        except (PyMongoError, BSONError) as exc:
            raise RemoteStoreError("ECS Mongo 读写失败；不会回退到本地存储。") from exc

    def _scope(self, **fields: Any) -> dict[str, Any]:
        return {**fields, "app_id": self.app_id}

    def _row(self, document: Mapping[str, Any]) -> dict[str, Any]:
        row = dict(document)
        row.pop("_id", None)
        row.setdefault("usage", None)
        row.setdefault("reply", None)
        for name in ("ack_sent", "sent_parts", "attempts", "next_retry"):
            row.setdefault(name, 0)
        return row

    def insert(self, incoming: Any) -> bool:
        def read(name: str) -> Any:
            return incoming[name] if isinstance(incoming, Mapping) else getattr(incoming, name)

        document = self._scope(**{name: read(name) for name in ("message_id", "chat_id", "sender_id", "text")})
        document.update(_id=_identifier(self.app_id, document["message_id"]), created_at=self.now(),
                        status="pending", reply=None, usage=None, ack_sent=0, sent_parts=0, attempts=0, next_retry=0)

        def insert() -> bool:
            try:
                self.inbox.insert_one(document)
                return True
            except DuplicateKeyError:
                return False

        return self._call(insert)

    def recover_processing(self) -> int:
        def recover() -> int:
            result = self.inbox.update_many(self._scope(status="processing"), {"$set": {"status": "pending"}})
            for row in self.inbox.find(self._scope(status="pending")):
                if row.get("reply") is not None and row.get("answer_session_id"):
                    self._project_session(row, row["answer_session_id"])
            return result.modified_count

        return self._call(recover)

    def claim(self, now: float | None = None, max_age: float = 1800) -> dict[str, Any] | None:
        current = self.now() if now is None else now

        def claim() -> dict[str, Any] | None:
            self.inbox.update_many(self._scope(status="pending", created_at={"$lt": current - max_age}),
                                   {"$set": {"status": "expired"}})
            row = self.inbox.find_one(self._scope(status="pending"), sort=[("created_at", ASCENDING), ("message_id", ASCENDING)])
            if row is None or row.get("next_retry", 0) > current:
                return None
            claimed = self.inbox.find_one_and_update(
                self._scope(message_id=row["message_id"], status="pending"),
                {"$set": {"status": "processing"}}, return_document=ReturnDocument.AFTER,
            )
            if claimed is not None and claimed.get("reply") is not None and claimed.get("answer_session_id"):
                self._project_session(claimed, claimed["answer_session_id"])
            return self._row(claimed) if claimed else None

        return self._call(claim)

    def update(self, message_id: str, fields: Mapping[str, Any] | None = None, **kwargs: Any) -> None:
        values = {**dict(fields or {}), **kwargs}
        allowed = {"status", "reply", "usage", "ack_sent", "sent_parts", "attempts", "next_retry", "completed_at"}
        if not values or not set(values) <= allowed:
            raise ValueError("unsupported inbox update")
        if values.get("status") == "sent":
            values.setdefault("completed_at", self.now())

        def update() -> None:
            result = self.inbox.update_one(self._scope(message_id=message_id), {"$set": deepcopy(values)})
            if result.matched_count != 1:
                raise RemoteStoreError("远端消息记录不存在，未更新任何其他应用记录。")

        self._call(update)

    def _project_session(self, row: Mapping[str, Any], session_id: str | None, *, reset: bool = False) -> None:
        identifier = _identifier(self.app_id, row["chat_id"], row["sender_id"])
        stamp, message_id = row["created_at"], row["message_id"]
        fields = self._scope(chat_id=row["chat_id"], sender_id=row["sender_id"], session_id=session_id,
                             last_created_at=stamp, last_message_id=message_id)
        if reset:
            fields.update(reset_after_created_at=stamp, reset_after_message_id=message_id)
        try:
            self.sessions.update_one(
                {"_id": identifier, "app_id": self.app_id,
                 "$or": [{"last_created_at": {"$exists": False}},
                    {"last_created_at": {"$lt": stamp}},
                    {"last_created_at": stamp, "last_message_id": {"$lte": message_id}}]},
                {"$set": fields}, upsert=True,
            )
        except DuplicateKeyError:
            # An audit projection from a later message or reset already exists.
            pass

    def get_session(self, chat: str, sender: str) -> str | None:
        def get() -> str | None:
            row = self.sessions.find_one(self._scope(chat_id=chat, sender_id=sender))
            return row.get("session_id") if row else None

        return self._call(get)

    def save_answer(self, row: Mapping[str, Any], answer: str, session_id: str | None,
                    metadata: Mapping[str, Any] | None = None) -> None:
        def save() -> None:
            result = self.inbox.update_one(self._scope(message_id=row["message_id"]), {"$set": {
                "reply": answer, "usage": deepcopy(metadata), "answer_session_id": session_id,
                "answer_saved_at": self.now(),
            }})
            if result.matched_count != 1:
                raise RemoteStoreError("远端消息记录不存在，未保存回复。")
            if session_id is not None:
                self._project_session(row, session_id)

        self._call(save)

    def reset_session(self, chat: str, sender: str, *, row: Mapping[str, Any] | None = None) -> None:
        def reset() -> None:
            current = row or self.inbox.find_one(self._scope(chat_id=chat, sender_id=sender, status="processing"))
            current = current or {"chat_id": chat, "sender_id": sender, "created_at": self.now(), "message_id": "~reset"}
            self._project_session(current, None, reset=True)

        self._call(reset)

    def conversation_history(self, chat: str, sender: str, limit: int = 8) -> list[dict[str, str]]:
        if not 1 <= limit <= 100:
            raise ValueError("history limit must be between 1 and 100")

        def history() -> list[dict[str, str]]:
            query = self._scope(chat_id=chat, sender_id=sender, status="sent")
            session = self.sessions.find_one(self._scope(chat_id=chat, sender_id=sender))
            if session and "reset_after_created_at" in session:
                stamp, identifier = session["reset_after_created_at"], session["reset_after_message_id"]
                query["$or"] = [{"created_at": {"$gt": stamp}}, {"created_at": stamp, "message_id": {"$gt": identifier}}]
            rows = list(self.inbox.find(query).sort([("created_at", DESCENDING), ("message_id", DESCENDING)]).limit(limit))
            return [{"user": item["text"], "assistant": item["reply"]} for item in reversed(rows)
                    if isinstance(item.get("text"), str) and isinstance(item.get("reply"), str)]

        return self._call(history)

    def import_history(self, inbox_rows: Any, session_rows: Any) -> dict[str, int]:
        messages, sessions = [dict(row) for row in inbox_rows], [dict(row) for row in session_rows]

        def migrate() -> dict[str, int]:
            counts = {"messages_inserted": 0, "sessions_inserted": 0}
            for row in messages:
                fields = {key: deepcopy(row[key]) for key in ("message_id", "chat_id", "sender_id", "text", "created_at")}
                for key, default in (("status", "pending"), ("reply", None), ("usage", None), ("ack_sent", 0),
                                     ("sent_parts", 0), ("attempts", 0), ("next_retry", 0)):
                    fields[key] = deepcopy(row.get(key, default))
                if fields["status"] == "processing":
                    fields["status"] = "pending"
                for key in ("answer_session_id", "answer_saved_at", "completed_at"):
                    if key in row:
                        fields[key] = deepcopy(row[key])
                fields["app_id"] = self.app_id
                result = self.inbox.update_one({"_id": _identifier(self.app_id, fields["message_id"]), "app_id": self.app_id},
                                               {"$setOnInsert": fields}, upsert=True)
                counts["messages_inserted"] += int(result.upserted_id is not None)
            for row in sessions:
                scope = self._scope(chat_id=row["chat_id"], sender_id=row["sender_id"], session_id=row["session_id"])
                result = self.sessions.update_one({"_id": _identifier(self.app_id, row["chat_id"], row["sender_id"]),
                                                   "app_id": self.app_id},
                                                  {"$setOnInsert": scope}, upsert=True)
                counts["sessions_inserted"] += int(result.upserted_id is not None)
            return counts

        return self._call(migrate)

    def log(self, event: Mapping[str, Any]) -> None:
        entry = deepcopy(dict(event))
        entry.pop("_id", None)
        entry.update(app_id=self.app_id, logged_at=datetime.now(timezone.utc))
        self._call(lambda: self.logs.insert_one(entry))

    def close(self) -> None:
        if self._closed:
            return
        self._stop.set()
        if self._heartbeat_thread is not None:
            self._heartbeat_thread.join(timeout=self.operation_timeout + 1)
        try:
            with timeout(self.operation_timeout):
                self.leases.delete_one({"_id": self._lease_id, "app_id": self.app_id, "owner": self.owner})
        except PyMongoError as exc:
            raise RemoteStoreError("释放远端租约失败；租约将在心跳超时后失效。") from exc
        finally:
            self._closed = True
            if self._owns_client:
                self.client.close()
