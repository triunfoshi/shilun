"""Per-invocation query tools with owner gating and server-only audit records."""

from __future__ import annotations

from datetime import datetime, timezone
import os
import re
from typing import Any

from shilun.common.config import load_config


class BackendQueryTools:
    def __init__(self, *, identity: dict[str, str] | None = None, gateway: Any = None,
                 allowed_fn: Any = None, audit_fn: Any = None) -> None:
        self.identity = identity if identity is not None else {
            name: os.environ.get("SHILUN_QUERY_" + name.upper(), "")
            for name in ("app_id", "message_id", "chat_id", "sender_id")}
        self.gateway = gateway
        self.allowed_fn = allowed_fn
        self.audit_fn = audit_fn
        self._client = None

    def _authorized(self) -> bool:
        patterns = {"app_id": r"cli_[A-Za-z0-9]+", "message_id": r"om_[A-Za-z0-9]+",
                    "chat_id": r"oc_[A-Za-z0-9]+", "sender_id": r"ou_[A-Za-z0-9]+"}
        if set(self.identity) != set(patterns) or any(
                not isinstance(self.identity[key], str)
                or not re.fullmatch(pattern, self.identity[key]) for key, pattern in patterns.items()):
            return False
        try:
            allowed = self.allowed_fn
            if allowed is None:
                from shilun.feishu_codex_bridge import allowed_users
                allowed = allowed_users
            return self.identity["sender_id"] in allowed(self.identity["app_id"])
        except (OSError, ValueError, RuntimeError):
            return False

    @staticmethod
    def _denied() -> dict[str, Any]:
        return {"status": "error", "available": False,
                "error": {"code": "owner_context_required", "message": "后台查询需要有效的允许用户及当前聊天审计上下文。"}}

    def _audit(self, event: dict[str, Any]) -> None:
        # Only metadata and validated public strategy/date scope are audited;
        # never persist HTTP payloads, free-form inputs, model text, or local files.
        if self.audit_fn is not None:
            self.audit_fn(event)
            return
        try:
            from pymongo import MongoClient, timeout
            config = load_config()
            if not config.mongo_uri:
                return  # MongoClient(None) would otherwise select a local database.
            if self._client is None:
                self._client = MongoClient(config.mongo_uri, connectTimeoutMS=1500,
                    socketTimeoutMS=2000, serverSelectionTimeoutMS=2000, retryWrites=False,
                    appname="shilun-feishu-readonly-query")
            with timeout(3):
                self._client[config.mongo_db].feishu_codex_logs.insert_one({
                    **event, **self.identity, "status": "backend_query",
                    "logged_at": datetime.now(timezone.utc)})
        except Exception:
            pass  # No local fallback; the main worker independently monitors its ECS lease.

    def list_modules(self) -> dict[str, Any]:
        if not self._authorized():
            return self._denied()
        from shilun.feishu_backend_catalog import list_modules
        result = list_modules()
        self._audit({"tool": "list_backend_modules", "outcome": "ok"})
        return result

    def search(self, module: str = "", query: str = "", offset: int = 0,
               limit: int = 12) -> dict[str, Any]:
        if not self._authorized():
            return self._denied()
        from shilun.feishu_backend_catalog import search_queries
        try:
            result = search_queries(module=module, query=query, offset=offset, limit=limit)
        except (ValueError, TypeError):
            return {"status": "error", "error": {"code": "invalid_discovery", "message": "请使用有效模块、关键词和分页参数。"}}
        self._audit({"tool": "search_backend_queries", "outcome": "ok"})
        return result

    def query(self, query_id: str, parameters: dict[str, Any] | None = None,
              body: dict[str, Any] | None = None, select: list[str] | None = None,
              offset: int = 0, max_items: int = 20) -> dict[str, Any]:
        if not self._authorized():
            return self._denied()
        if self.gateway is None:
            from shilun.feishu_backend_gateway import BackendGateway
            self.gateway = BackendGateway(audit=self._audit)
        return self.gateway.query(query_id, parameters=parameters, body=body, select=select,
                                  offset=offset, max_items=max_items)

    def close(self) -> None:
        if self.gateway is not None:
            self.gateway.close()
        if self._client is not None:
            self._client.close()
