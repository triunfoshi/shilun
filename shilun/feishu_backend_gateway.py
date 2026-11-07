"""Bounded execution of audited backend reads; credentials stay in this process."""
from __future__ import annotations

from collections import OrderedDict
from copy import deepcopy
import json
import re
import threading
import time
from typing import Any, Callable
from urllib.parse import quote

from jsonschema import Draft202012Validator, FormatChecker
import requests

from shilun.common.config import load_config
from shilun.feishu_review_context import _configured_base, _SECRET_KEY


MAX_RESPONSE_BYTES = 5 * 1024 * 1024
MAX_REQUEST_BYTES = 1024 * 1024
MAX_OUTPUT_CHARS = 60000
_SELECT = re.compile(r"[A-Za-z_][A-Za-z0-9_-]*(?:\.[A-Za-z_][A-Za-z0-9_-]*)*")
_PLACEHOLDER = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")
_MISSING = object()
_DATE_FIELDS = ("date", "analysis_date", "requested_date", "as_of", "date_from", "date_to", "window_from", "window_to", "previous_trade_date", "generated_at")


class BackendGateway:
    def __init__(self, *, get_query_fn: Callable[[str], dict] | None = None,
                 session: Any = None, config: Any = None,
                 audit: Callable[[dict], None] | None = None) -> None:
        if get_query_fn is None:
            from shilun.feishu_backend_catalog import get_query
            get_query_fn = get_query
        self.get_query = get_query_fn
        self.session = session if session is not None else requests.Session()
        self.config = config if config is not None else load_config()
        self.base = _configured_base()
        self.audit = audit
        self.secrets = tuple(value for value in (self.config.auth_username, self.config.auth_password) if value)
        self.auth = (self.config.auth_username, self.config.auth_password) if len(self.secrets) == 2 else None
        self.requests_made = 0
        self.cache: OrderedDict[str, tuple[Any, int]] = OrderedDict()
        self.lock = threading.Lock()
        self.contract_lock = threading.Lock()
        self.runtime_schema: dict | None = None
        self.runtime_schema_checked = False
        self.readonly_contracts: set[tuple[str, str]] = set()

    def _event(self, **event: Any) -> None:
        if self.audit is not None:
            try:
                self.audit(event)
            except Exception:
                pass  # Diagnostics never become a second persistence channel.

    @staticmethod
    def _error(code: str, message: str, *, source: dict | None = None, http_status: int | None = None) -> dict:
        result = {"status": "error", "available": False,
                  "error": {"code": code, "message": message},
                  "availability_note": "查询失败或不可核验不等于数据为零。"}
        if source is not None:
            result["source"] = source
        if http_status is not None:
            result["http_status"] = http_status
        return result

    def _string(self, value: str, limit: int = 1200) -> str:
        for secret in self.secrets:
            value = value.replace(secret, "[REDACTED]")
        return value[:limit]

    @staticmethod
    def _validate(value: Any, schema: dict) -> bool:
        # Unresolved references must never fetch a schema over the network.
        def has_reference(item: Any) -> bool:
            if isinstance(item, dict):
                return any(k in item for k in ("$ref", "$dynamicRef", "$recursiveRef")) or any(has_reference(v) for v in item.values())
            return isinstance(item, list) and any(has_reference(v) for v in item)
        if has_reference(schema):
            return False
        return Draft202012Validator(schema, format_checker=FormatChecker()).is_valid(value)

    def _prepare(self, entry: dict, parameters: Any, body: Any) -> tuple[str, dict, Any, dict]:
        if parameters is None:
            parameters = {}
        if not isinstance(parameters, dict) or any(not isinstance(k, str) for k in parameters):
            raise ValueError("invalid_parameters")
        effective = deepcopy(parameters)
        bindings = entry.get("parameters", [])
        names = {p["name"] for p in bindings}
        if set(effective) - names:
            raise ValueError("unknown_parameters")
        for binding in bindings:
            schema = binding.get("schema", {})
            if binding["name"] not in effective and schema.get("default") is not None:
                effective[binding["name"]] = deepcopy(schema["default"])
        for name, value in entry.get("fixed_parameters", {}).items():
            if name not in names:
                raise ValueError("invalid_catalog")
            if name in parameters and (type(parameters[name]) is not type(value) or parameters[name] != value):
                raise ValueError("fixed_parameter_override")
            effective[name] = deepcopy(value)
        if not self._validate(effective, entry["parameter_schema"]):
            raise ValueError("invalid_parameters")
        body_schema = entry.get("body_schema")
        if body_schema is None:
            if body is not None:
                raise ValueError("unexpected_body")
        elif not isinstance(body, dict) or not self._validate(body, body_schema):
            raise ValueError("invalid_body")
        serialized = json.dumps([entry["id"], effective, body], ensure_ascii=False, sort_keys=True, allow_nan=False)
        if len(serialized.encode("utf-8")) > MAX_REQUEST_BYTES:
            raise ValueError("request_too_large")
        method, template = entry["method"], entry["path"]
        if (method not in ("GET", "POST") or not isinstance(template, str)
                or not template.startswith("/api/v1/")
                or any(value in template for value in ("..", "\\", "%", "?", "#", "//"))):
            raise ValueError("invalid_catalog")
        if method == "GET" and body is not None:
            raise ValueError("unexpected_body")
        path_names = {p["name"] for p in bindings if p["in"] == "path"}
        if set(_PLACEHOLDER.findall(template)) != path_names:
            raise ValueError("invalid_catalog")
        def substitute(match: re.Match[str]) -> str:
            value = str(effective[match[1]])
            if (not value or len(value) > 256 or value in (".", "..")
                    or any(c in value for c in ("/", "\\", "%", "?", "#"))
                    or any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in value)):
                raise ValueError("unsafe_path_parameter")
            return quote(value, safe="")
        path = _PLACEHOLDER.sub(substitute, template)
        if "{" in path or "}" in path:
            raise ValueError("invalid_catalog")
        query = {p["name"]: effective[p["name"]] for p in bindings
                 if p["in"] == "query" and p["name"] in effective}
        return path, query, body, effective

    def _read(self, method: str, path: str, parameters: dict, body: Any) -> tuple[Any, int, int]:
        kwargs = {"params": parameters, "auth": self.auth, "timeout": 20,
                  "allow_redirects": False, "stream": True,
                  "headers": {"Accept": "application/json"}}
        if method == "POST":
            kwargs["json"] = body
        response = self.session.request(method, self.base+path, **kwargs)
        try:
            code = int(response.status_code)
            if not 200 <= code < 300:
                if code in (400, 422):
                    # Validation and business-parameter errors can explain how
                    # to correct a read. Never forward server traces or inputs.
                    chunks, size = [], 0
                    for chunk in response.iter_content(chunk_size=8192):
                        size += len(chunk)
                        if size > 65536:
                            return None, code, size
                        chunks.append(chunk)
                    try:
                        payload = json.loads(b"".join(chunks))
                    except (ValueError, TypeError):
                        return None, code, size
                    return {"detail": self._safe_http_detail(payload)}, code, size
                return None, code, 0
            length = response.headers.get("Content-Length", "")
            if str(length).isdigit() and int(length) > MAX_RESPONSE_BYTES:
                raise ValueError("response_too_large")
            chunks, size = [], 0
            for chunk in response.iter_content(chunk_size=65536):
                if not chunk:
                    continue
                size += len(chunk)
                if size > MAX_RESPONSE_BYTES:
                    raise ValueError("response_too_large")
                chunks.append(chunk)
            def nonfinite(_value: str) -> None:
                raise ValueError("invalid_json")
            data = json.loads(b"".join(chunks), parse_constant=nonfinite)
            # Also catches overflow from valid JSON notation such as 1e999.
            json.dumps(data, allow_nan=False)
            return data, code, size
        finally:
            response.close()

    def _safe_http_detail(self, payload: Any) -> str | list[dict] | None:
        def text(value: Any) -> str:
            value = self._string(str(value), 1200)
            value = re.sub(r"\b[A-Za-z][A-Za-z0-9+.-]{1,24}://\S+", "[URL]", value)
            value = re.sub(r"/(?:Users|home|private|var|etc|tmp|opt)/\S+", "[PATH]", value)
            value = re.sub(r"(?i)\b(password|passwd|token|secret|api[_-]?key|authorization)\s*[:=]\s*[^\s,;}]+",
                           r"\1=[REDACTED]", value)
            return " ".join(value.split())
        detail = payload.get("detail") if isinstance(payload, dict) else None
        if isinstance(detail, str):
            return text(detail)
        if isinstance(detail, list):
            return [{key: ([text(v) for v in item[key][:8]] if key == "loc" and isinstance(item[key], list)
                           else text(item[key]))
                     for key in ("loc", "type", "msg") if key in item}
                    for item in detail[:8] if isinstance(item, dict)]
        return None

    def _runtime_readonly_contract(self, entry: dict) -> bool:
        """Older servers can silently ignore a new query flag; fail closed."""
        if entry.get("fixed_parameters", {}).get("read_only") is not True:
            return True
        identity = (entry["method"], entry["path"])
        with self.contract_lock:
            if identity in self.readonly_contracts:
                return True
            if not self.runtime_schema_checked:
                with self.lock:
                    if self.requests_made >= 12:
                        return False
                    self.requests_made += 1
                self.runtime_schema_checked = True
                started = time.monotonic()
                status, size, verified = None, 0, False
                try:
                    schema, status, size = self._read("GET", "/openapi.json", {}, None)
                    if 200 <= status < 300 and isinstance(schema, dict) and isinstance(schema.get("paths"), dict):
                        self.runtime_schema = schema
                        verified = True
                except (requests.RequestException, OSError, ValueError, TypeError, KeyError, OverflowError, RecursionError):
                    pass
                finally:
                    self._event(query_id="readonly_runtime_contract", method="GET", path="/openapi.json",
                                cache_hit=False, http_status=status, response_bytes=size,
                                outcome="ok" if verified else "error", error_kind=None if verified else "readonly_contract_unverified",
                                elapsed_seconds=round(time.monotonic()-started, 3), requests_made=self.requests_made)
            if self.runtime_schema is None:
                return False

            def resolve(value: Any) -> Any:
                # Resolve only local JSON pointers, never external schema URLs.
                for _ in range(12):
                    if not isinstance(value, dict) or "$ref" not in value:
                        return value
                    reference = value["$ref"]
                    if not isinstance(reference, str) or not reference.startswith("#/"):
                        return None
                    target: Any = self.runtime_schema
                    for part in reference[2:].split("/"):
                        if not isinstance(target, dict):
                            return None
                        target = target.get(part.replace("~1", "/").replace("~0", "~"))
                    value = target
                return None

            path = self.runtime_schema["paths"].get(entry["path"])
            if not isinstance(path, dict):
                return False
            operation = path.get(entry["method"].lower())
            if not isinstance(operation, dict):
                return False
            parameters = []
            for owner in (path, operation):
                values = owner.get("parameters", [])
                if not isinstance(values, list):
                    return False
                parameters.extend(resolve(value) for value in values)
            query = {value["name"]: value for value in parameters
                     if isinstance(value, dict) and isinstance(value.get("name"), str) and value.get("in") == "query"}
            if not set(entry["fixed_parameters"]) <= set(query):
                return False
            flag = resolve(query["read_only"].get("schema"))
            if not isinstance(flag, dict) or flag.get("type") != "boolean":
                return False
            self.readonly_contracts.add(identity)
            return True

    def _resolve(self, value: Any, parts: list[str]) -> Any:
        if not parts:
            return value
        if isinstance(value, dict):
            return self._resolve(value[parts[0]], parts[1:]) if parts[0] in value else _MISSING
        if isinstance(value, list):
            result = []
            for item in value:
                selected = self._resolve(item, parts)
                identity = {k: item[k] for k in ("id", "name", "ticker", "code", "date") if isinstance(item, dict) and k in item}
                result.append({**identity, "value": None if selected is _MISSING else selected,
                               **({"_missing": True} if selected is _MISSING else {})})
            return result
        return _MISSING

    def _project(self, payload: Any, select: list[str] | None, offset: int, max_items: int, *, path: str) -> tuple[Any, dict]:
        omitted_raw: list[str] = []
        if path == "/api/v1/review/board-effects" and not select and isinstance(payload, dict):
            payload = dict(payload)
            groups = []
            for group in payload.get("strategies", []):
                if not isinstance(group, dict):
                    groups.append(group)
                    continue
                keep = {k: v for k, v in group.items() if k not in ("rows", "daily", "segments", "by_regime")}
                omitted_raw.extend("strategies."+str(group.get("id", "?"))+"."+k for k in ("rows", "daily", "segments", "by_regime") if k in group)
                groups.append(keep)
            payload["strategies"] = groups
        missing = []
        if select:
            chosen = {}
            for key in select:
                value = self._resolve(payload, key.split("."))
                if value is _MISSING:
                    missing.append(key)
                else:
                    chosen[key] = value
            payload = chosen
        for limit, keys, strings in ((max_items, 80, 1200), (min(max_items, 8), 40, 600), (1, 20, 200)):
            meta: dict[str, Any] = {"applied": bool(omitted_raw or missing), "arrays": {},
                "omitted_fields": {}, "truncated_strings": {}, "depth_omitted": [],
                "missing_selected_paths": missing, "omitted_raw_sections": omitted_raw}
            def project(value: Any, location: str, depth: int = 0) -> Any:
                if depth > 12:
                    meta["applied"] = True
                    if len(meta["depth_omitted"]) < 100:
                        meta["depth_omitted"].append(location)
                    return {"_omitted": "depth_limit"}
                if isinstance(value, dict):
                    usable = [(k, v) for k, v in value.items() if not _SECRET_KEY.search(str(k))]
                    removed = len(value)-min(keys, len(usable))
                    if removed:
                        meta["applied"] = True
                        if len(meta["omitted_fields"]) < 200:
                            meta["omitted_fields"][location] = removed
                    return {self._string(str(k), 200): project(v, location+"."+self._string(str(k), 100), depth+1)
                            for k, v in usable[:keys]}
                if isinstance(value, list):
                    page = value[offset:offset+limit]
                    if len(meta["arrays"]) < 200:
                        meta["arrays"][location] = {"original_count": len(value), "returned_count": len(page),
                            "offset": offset, "has_more": offset+len(page) < len(value)}
                    else:
                        meta["array_metadata_omitted"] = meta.get("array_metadata_omitted", 0)+1
                    if len(page) != len(value):
                        meta["applied"] = True
                    return [project(v, location+f"[{offset+i}]", depth+1) for i, v in enumerate(page)]
                if isinstance(value, str):
                    cleaned = self._string(value, strings)
                    if len(value) > strings:
                        meta["applied"] = True
                        if len(meta["truncated_strings"]) < 200:
                            meta["truncated_strings"][location] = {"original_chars": len(value), "returned_chars": len(cleaned)}
                    return cleaned
                if value is None or isinstance(value, (bool, int, float)):
                    return value
                meta["applied"] = True
                return {"_omitted": "unsupported_value"}
            result = project(payload, "$")
            if len(json.dumps([result, meta], ensure_ascii=False, allow_nan=False)) <= MAX_OUTPUT_CHARS:
                return result, meta
        return {"_omitted": "projection_budget"}, {"applied": True, "reason": "response_projection_budget", "selection_note": "请使用select选择更窄字段；省略内容不是空值。"}

    def query(self, query_id: str, parameters: dict | None = None, body: dict | None = None,
              select: list[str] | None = None, offset: int = 0, max_items: int = 20) -> dict:
        if (not isinstance(query_id, str) or len(query_id) > 160
                or isinstance(offset, bool) or not isinstance(offset, int) or offset < 0
                or isinstance(max_items, bool) or not isinstance(max_items, int) or not 1 <= max_items <= 100
                or select is not None and (not isinstance(select, list) or not 1 <= len(select) <= 12
                    or any(not isinstance(p, str) or len(p) > 160 or not _SELECT.fullmatch(p) for p in select))):
            return self._error("invalid_projection", "请使用有效接口编号、简单字段路径和分页参数。")
        try:
            entry = self.get_query(query_id)
            path, query, prepared_body, effective = self._prepare(entry, parameters, body)
            cache_key = json.dumps([query_id, effective, prepared_body], sort_keys=True, ensure_ascii=False, allow_nan=False)
        except (ValueError, TypeError, KeyError, OverflowError, RecursionError):
            return self._error("invalid_request", "接口未获只读授权或参数不符合接口定义；请先查看目录与参数约束，不覆盖固定只读参数。")
        source = {"query_id": entry["id"], "module": entry["module"],
                  "module_name": entry.get("module_name"), "path": entry["path"], "method": entry["method"]}
        source["parameters"] = self._project(effective, None, 0, 100, path="")[0]
        started = time.monotonic()
        if not self._runtime_readonly_contract(entry):
            return self._error("readonly_contract_unverified", "后台尚未确认支持该接口的只读参数，已停止查询；请更新服务后重试。", source=source)
        with self.lock:
            cached = self.cache.get(cache_key)
            if cached is not None:
                self.cache.move_to_end(cache_key)
            elif self.requests_made >= 12:
                return self._error("request_budget", "本次对话的后台读取次数已达上限，请缩小范围。", source=source)
            else:
                self.requests_made += 1
        status, size = 200, 0
        outcome, error_kind = "error", None
        try:
            if cached is not None:
                payload, size = cached
            else:
                payload, status, size = self._read(entry["method"], path, query, prepared_body)
                if not 200 <= status < 300:
                    error_kind = "http_error"
                    messages = {400: "后台拒绝了本次参数或所选策略，请核对策略标识和数据条件后重试。",
                                422: "后台参数校验未通过，请按接口定义修正参数后重试。",
                                401: "后台访问认证未通过。", 403: "后台未授权本次读取。",
                                404: "后台未提供所选记录或已生成归档；这不代表模块未接入。"}
                    result = self._error("http_error", messages.get(status, "后台接口暂不可用；未跟随重定向。"),
                                         source=source, http_status=status)
                    if isinstance(payload, dict) and payload.get("detail"):
                        result["error"]["backend_detail"] = payload["detail"]
                    return result
                with self.lock:
                    self.cache[cache_key] = (payload, size)
                    self.cache.move_to_end(cache_key)
                    while len(self.cache) > 4:
                        self.cache.popitem(last=False)
            data, truncation = self._project(payload, select, offset, max_items, path=entry["path"])
            if isinstance(payload, dict):
                source["dates"] = {key: self._string(str(payload[key]), 200) for key in _DATE_FIELDS
                                   if key in payload and isinstance(payload[key], (str, int, float)) and not isinstance(payload[key], bool)}
            source["cache_hit"] = cached is not None
            available = payload.get("available") if isinstance(payload, dict) and isinstance(payload.get("available"), bool) else None
            outcome = "ok"
            return {"status": "ok", "available": available, "source": source, "data": data,
                    "truncation": truncation, "availability_note": "空列表、零样本、未归档和字段省略含义不同；以接口可用性及覆盖字段为准，不能把缺失记为零。"}
        except (requests.RequestException, OSError):
            error_kind = "network_error"
            return self._error("network_error", "后台连接暂不可用，请稍后重试。", source=source)
        except (ValueError, TypeError, KeyError, OverflowError, RecursionError):
            error_kind = "invalid_response"
            return self._error("invalid_response", "后台响应过大、格式异常或无法核验；未返回不可靠数据。", source=source)
        finally:
            query_scope = {}
            strategy = effective.get("strategy_id")
            if isinstance(strategy, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,80}", strategy):
                query_scope["strategy_id"] = strategy
            day = effective.get("date")
            if isinstance(day, str) and re.fullmatch(r"20\d{2}-\d{2}-\d{2}", day):
                query_scope["date"] = day
            self._event(query_id=entry["id"], method=entry["method"], path=entry["path"],
                        cache_hit=cached is not None, http_status=status, response_bytes=size,
                        outcome=outcome, error_kind=error_kind,
                        query_scope=query_scope,
                        elapsed_seconds=round(time.monotonic()-started, 3), requests_made=self.requests_made)

    def close(self) -> None:
        self.session.close()
