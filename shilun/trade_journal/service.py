"""Append-only research journal on the application's configured Mongo store.

Historical imports write journal collections only. In particular this module
does not call record_sell_execution, update holdings, or debit account cash.
"""
from __future__ import annotations

import base64
from collections import Counter, defaultdict
from copy import deepcopy
from contextlib import contextmanager
from functools import wraps
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
import hashlib
import json
import re
from threading import RLock
import uuid

from bson import BSON
from bson.errors import BSONError
from pymongo.errors import DuplicateKeyError
from zoneinfo import ZoneInfo

from .ledger import LEDGER_VERSION, build_ledger, digest


TZ = ZoneInfo("Asia/Shanghai")
LOCK = RLock()
COLLECTIONS = {name: "trade_journal_" + name for name in
    ("sources", "batches", "fills", "financial_events", "decisions", "links", "expectation_checks", "observations", "snapshots", "write_locks")}
EVIDENCE_CLASSES = {"contemporaneous", "historical_reconstruction", "retrospective_recall", "unknown"}
QUALIFICATION_KEYS = ("accounting", "decision_review", "ex_ante", "rule_backtest")


class TradeJournalError(ValueError):
    """Input cannot be reconciled or an append-only contract was violated."""


def _time(value, *, allow_none=False):
    if value in (None, "") and allow_none:
        return None
    try:
        result = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError) as error:
        raise TradeJournalError("时间必须为有效ISO日期时间") from error
    return result.replace(tzinfo=TZ) if result.tzinfo is None else result.astimezone(TZ)


def _iso(value):
    return _time(value).isoformat(timespec="seconds")


def _date(value):
    text = value.date().isoformat() if isinstance(value, datetime) else str(value or "")[:10]
    try:
        return date.fromisoformat(text).isoformat()
    except ValueError as error:
        raise TradeJournalError("日期必须为YYYY-MM-DD") from error


def _ticker(value, *, cash=False):
    text = str(value or "").strip().upper().replace(".XSHG", ".SH").replace(".XSHE", ".SZ")
    if cash and text in {"", "-", "CASH"}:
        return "CASH"
    if text.isdigit() and len(text) <= 6:
        text = text.zfill(6)
        text += ".SH" if text[0] in "569" else ".BJ" if text[0] in "48" else ".SZ"
    if not re.fullmatch(r"\d{6}\.(SH|SZ|BJ)", text):
        raise TradeJournalError("证券代码必须为六位代码及SH/SZ/BJ市场")
    return text


def _number(value, label, *, nullable=False, minimum=None, integer=False):
    if value in (None, "") and nullable:
        return None
    try:
        if isinstance(value, bool):
            raise ValueError
        result = Decimal(str(value))
        if not result.is_finite() or (minimum is not None and result < Decimal(str(minimum))):
            raise ValueError
        if integer and result != result.to_integral_value():
            raise ValueError
        return int(result) if integer else float(result)
    except (InvalidOperation, ValueError, TypeError) as error:
        raise TradeJournalError(f"{label}必须为有效{'整数' if integer else '数值'}") from error


def _pick(record, *keys):
    return next((record[k] for k in keys if k in record and record[k] is not None), None)


def _clean(document):
    return {k: deepcopy(v) for k, v in (document or {}).items() if k != "_id"}


def _specified(value):
    if isinstance(value, str):
        return value.strip().lower() not in {"", "unknown", "未知", "待补", "待确认", "未设", "未设定", "不详", "mode.unknown"}
    return bool(value)


def _source_ref(record):
    ref = deepcopy(record.get("source_ref") or record.get("source") or {})
    if not isinstance(ref, dict):
        raise TradeJournalError("来源引用须为对象")
    ref["source_id"] = ref.get("source_id") or record.get("source_id")
    ref["row"] = _pick(ref, "row", "source_row", "row_number", "source_row_number")
    if not ref["source_id"]:
        raise TradeJournalError("每条事实必须引用source_ref.source_id")
    if ref["row"] is not None:
        ref["row"] = _number(ref["row"], "来源行号", minimum=1, integer=True)
    if ref["row"] is None and not ref.get("record_id"):
        raise TradeJournalError("来源引用必须提供正行号或record_id")
    return {k: v for k, v in ref.items() if v is not None}


def _evidence_class(value):
    text = str(value or "unknown").removeprefix("evidence.")
    aliases = {"recalled": "retrospective_recall", "archived": "contemporaneous"}
    text = aliases.get(text, text)
    if text not in EVIDENCE_CLASSES:
        raise TradeJournalError("证据类别必须为当时归档、历史重建、事后回忆或未知")
    return text


def _page(items, limit, offset):
    limit = _number(limit, "limit", minimum=1, integer=True)
    offset = _number(offset, "offset", minimum=0, integer=True)
    if limit > 1000:
        raise TradeJournalError("limit不能超过1000")
    return {"items": items[offset:offset+limit], "total": len(items), "limit": limit, "offset": offset}


def _serialized(method):
    @wraps(method)
    def call(self, *args, **kwargs):
        with self._write_guard():
            return method(self, *args, **kwargs)
    return call


class TradeJournalService:
    def __init__(self, store, *, now_factory=None):
        self.store = store
        self.now_factory = now_factory or (lambda: datetime.now(TZ))
        self._cache = {}
        self._write_depth = 0
        self._lease_token = None
        self._lease_checked_at = None

    def _now(self):
        return _time(self.now_factory()).isoformat(timespec="seconds")

    def _collection(self, name):
        return self.store.collection(COLLECTIONS[name])

    @staticmethod
    def _projection(name, include_content=False):
        projection = {"_id": 0}
        if not include_content:
            if name in {"sources", "snapshots"}:
                projection["content"] = 0
                if name == "snapshots":
                    projection["live_reference.content"] = 0
            if name == "observations":
                projection.update(members=0, mode_research_snapshot=0)
        return projection

    def _invalidate(self):
        self._cache.clear()

    def _all(self, name):
        # Service instances live for one request. Heavy immutable originals are
        # never fetched on list/summary paths, even when a caller discards them.
        key = ("all", name)
        if key not in self._cache:
            self._cache[key] = [_clean(r) for r in self._collection(name).find({}, self._projection(name))]
        return deepcopy(self._cache[key])

    def _find(self, name, query, *, include_content=False):
        result = self._collection(name).find_one(query, self._projection(name, include_content))
        return _clean(result) if result is not None else None

    def _lease_clock(self):
        # Wall clock for DB locking is deliberately independent of research
        # now_factory, which tests may freeze to a historical decision time.
        return datetime.now(TZ)

    def _renew_lease(self):
        if self._lease_token is None:
            return
        now = self._lease_clock()
        if self._lease_checked_at and (now - self._lease_checked_at).total_seconds() < 30:
            return
        coll = self._collection("write_locks")
        until = (self._lease_clock() + timedelta(seconds=300)).isoformat()
        coll.update_one({"_id": "journal-write", "owner": self._lease_token}, {"$set": {"lease_until": until}})
        saved = coll.find_one({"_id": "journal-write"})
        if not saved or saved.get("owner") != self._lease_token:
            raise TradeJournalError("写入租约已失效，请重新读取后重试；未提交批次不可见")
        self._lease_checked_at = now

    @contextmanager
    def _write_guard(self):
        with LOCK:
            if self._write_depth:
                self._write_depth += 1
                try:
                    yield
                finally:
                    self._write_depth -= 1
                return
            coll, token = self._collection("write_locks"), uuid.uuid4().hex
            for _ in range(3):
                old = coll.find_one({"_id": "journal-write"})
                now = self._lease_clock()
                if old and old.get("owner") and _time(old["lease_until"]) > now:
                    raise TradeJournalError("并发写入：另一个请求正在写入交易账本，请稍后重试")
                until = (now + timedelta(seconds=300)).isoformat()
                try:
                    if old:
                        coll.update_one({"_id": "journal-write", "owner": old.get("owner"), "lease_until": old.get("lease_until")},
                            {"$set": {"owner": token, "lease_until": until}})
                    else:
                        coll.update_one({"_id": "journal-write"}, {"$setOnInsert": {"owner": token, "lease_until": until}}, upsert=True)
                except DuplicateKeyError:
                    continue
                saved = coll.find_one({"_id": "journal-write"})
                if saved and saved.get("owner") == token:
                    break
            else:
                raise TradeJournalError("并发写入争用，请重试")
            if not saved or saved.get("owner") != token:
                raise TradeJournalError("并发写入争用，请重试")
            self._write_depth, self._lease_token = 1, token
            self._lease_checked_at = self._lease_clock()
            self._invalidate()
            try:
                yield
            finally:
                self._write_depth, self._lease_token = 0, None
                coll.update_one({"_id": "journal-write", "owner": token},
                    {"$set": {"owner": None, "lease_until": self._lease_clock().isoformat()}})
                self._invalidate()

    def write_guard(self):
        """Shared cross-worker journal write lease for adjacent domain owners."""
        return self._write_guard()

    def renew_write_lease(self):
        """Renew a held shared lease before an adjacent owner's durable write."""
        self._renew_lease()

    def _insert(self, name, key, record):
        try:
            if len(BSON.encode(record)) > 15 * 1024 * 1024:
                raise TradeJournalError("不可变记录超过15MiB存储预算；须分片保存，禁止截断原件")
        except (BSONError, OverflowError, TypeError) as error:
            raise TradeJournalError("记录不是可持久化的有限结构") from error
        self._renew_lease()
        old = self._find(name, key)
        if old is not None:
            if old.get("content_hash") != record.get("content_hash"):
                raise TradeJournalError("不可变记录标识冲突；请审查更正，不可覆盖原记录")
            return old, False
        try:
            self._collection(name).update_one(key, {"$setOnInsert": deepcopy(record)}, upsert=True)
        except DuplicateKeyError:
            # Unique indexes are the final arbiter across processes. A racing
            # identical insert is idempotent; a different revision is rejected.
            pass
        self._invalidate()
        saved = self._find(name, key)
        if saved is None or saved.get("content_hash") != record.get("content_hash"):
            raise TradeJournalError("并发写入冲突，原始记录未被覆盖")
        return saved, True

    def _committed_batches(self):
        return [r for r in self._all("batches") if r.get("status") == "committed"]

    def _facts(self):
        batches = self._committed_batches()
        fill_ids = {item for r in batches for item in r.get("fill_ids", [])}
        financial_ids = {item for r in batches for item in r.get("financial_event_ids", [])}
        return ([r for r in self._all("fills") if r["fill_id"] in fill_ids],
            [r for r in self._all("financial_events") if r["financial_event_id"] in financial_ids])

    def _normalize(self, payload):
        if not isinstance(payload, dict):
            raise TradeJournalError("导入必须为对象")
        account = str(payload.get("account_alias") or "primary").strip()
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_.:-]{0,63}", account):
            raise TradeJournalError("账户必须使用内部别名，不能传入券商账号")
        for category in ("sources", "fills", "financial_events"):
            rows = payload.get(category, [])
            if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
                raise TradeJournalError(f"{category}须为对象列表")
        sources, fills, financial = [], [], []
        for raw in payload.get("sources") or []:
            record = deepcopy(raw)
            sha = str(record.get("sha256") or "").lower()
            if not re.fullmatch(r"[0-9a-f]{64}", sha):
                raise TradeJournalError("原始来源缺少有效SHA256")
            encoding = record.get("content_encoding") or "utf-8"
            content = record.get("content")
            if not isinstance(content, str):
                raise TradeJournalError("原始来源必须包含可恢复的content，不能只有路径")
            try:
                decoded = base64.b64decode(content, validate=True) if encoding == "base64" else content.encode("utf-8") if encoding == "utf-8" else None
            except ValueError as error:
                raise TradeJournalError("来源base64无效") from error
            if decoded is None or len(decoded) > 10 * 1024 * 1024:
                raise TradeJournalError("来源编码不支持或超过10MB")
            if hashlib.sha256(decoded).hexdigest() != sha:
                raise TradeJournalError("来源content与SHA256不一致")
            source = {"source_id": str(record.get("source_id") or "src-" + sha), "sha256": sha,
                "filename": str(record.get("filename") or ""), "content_encoding": encoding,
                "content": content, "bytes": len(decoded), "source_type": record.get("source_type") or "broker_archive",
                "evidence_class": _evidence_class(record.get("evidence_class") or "historical_reconstruction"),
                "metadata": deepcopy(record.get("metadata") or {})}
            source["content_hash"] = digest(source)
            sources.append(source)
        source_ids = {r["source_id"] for r in sources} | {r["source_id"] for r in self._all("sources")}
        occurrences = Counter()
        for raw in payload.get("fills") or []:
            ticker = _ticker(_pick(raw, "ticker", "code"))
            side = str(_pick(raw, "side", "kind") or "")
            side = {"证券买入": "buy", "买": "buy", "证券卖出": "sell", "卖": "sell"}.get(side, side)
            if side not in {"buy", "sell"}:
                raise TradeJournalError("成交side必须为buy/sell，非交易事件另存financial_events")
            quantity = _number(_pick(raw, "quantity", "qty"), "成交数量", minimum=1, integer=True)
            price = _number(raw.get("price"), "成交价", minimum=0.00000001)
            amount = _number(_pick(raw, "amount", "gross", "gross_amount") if _pick(raw, "amount", "gross", "gross_amount") is not None else quantity * price,
                "成交金额", minimum=0)
            fee = _number(_pick(raw, "fees", "fee"), "费用", nullable=True, minimum=0)
            net = _number(_pick(raw, "net_cash", "net", "cash_delta"), "净现金流", nullable=True)
            expected = (-amount if side == "buy" else amount) - fee if fee is not None else None
            if net is None:
                net = expected
            if net is not None and expected is not None and abs(net - expected) > 0.021:
                raise TradeJournalError("成交金额、费用与净交割现金流不一致")
            at = raw.get("executed_at")
            day = _date(at or raw.get("date"))
            clock = _time(at).strftime("%H:%M:%S") if at else str(raw.get("time") or "")
            if _time(at or f"{day}T{clock or '00:00:00'}+08:00") > _time(self._now()):
                raise TradeJournalError("实际成交不能发生于未来")
            if clock and not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d:[0-5]\d", clock):
                raise TradeJournalError("成交时间必须为HH:MM:SS")
            precision = raw.get("time_precision") or ("execution_time" if raw.get("time_is_execution_verified") else "statement_event_time" if clock else "date_only")
            if precision not in {"execution_time", "statement_event_time", "date_only", "unknown"}:
                raise TradeJournalError("时间可信度无效")
            ref = _source_ref(raw)
            if ref["source_id"] not in source_ids:
                raise TradeJournalError("成交引用的原始来源不存在")
            balance = _number(_pick(raw, "reported_quantity_after", "quantity_after", "balance_qty", "broker_quantity_after"), "券商股票余额", nullable=True, minimum=0, integer=True)
            broker_id = str(raw.get("broker_execution_id") or "")
            identity = [account, ticker, day, clock, side, quantity, price, amount, balance]
            signature = digest(identity)
            occurrences[(ref["source_id"], signature)] += 1
            occurrence = _number(raw.get("occurrence") or (1 if broker_id or raw.get("fill_id") else occurrences[(ref["source_id"], signature)]), "重复出现序号", minimum=1, integer=True)
            fill_id = str(raw.get("fill_id") or "fill-" + digest([account, day, ticker, broker_id] if broker_id else [*identity, occurrence])[:40])
            fill = {"fill_id": fill_id, "account_alias": account, "ticker": ticker, "code": ticker.split(".")[0],
                "name": str(_pick(raw, "name", "security_name") or ticker), "date": day, "time": clock,
                "executed_at": _iso(at) if at else f"{day}T{clock or '00:00:00'}+08:00", "time_precision": precision,
                "side": side, "quantity": quantity, "price": price, "amount": amount, "fees": fee, "net_cash": net,
                "reported_quantity_after": balance, "source_ref": ref, "occurrence": occurrence,
                "broker_execution_id": broker_id or None,
                "statement_event_time": raw.get("statement_event_time"),
                "inventory_order": raw.get("inventory_order", raw.get("within_security_order", 10**9)),
                "origin": "historical_import", "note": raw.get("note")}
            # A second source can corroborate the same immutable fill. Provenance
            # belongs to the batch receipt, not to an overwritten fill document.
            fill["content_hash"] = digest({k: v for k, v in fill.items() if k not in {"source_ref", "note", "inventory_order", "name"}})
            fills.append(fill)
        kinds = {"证券转银行": "cash_withdrawal", "银行转证券": "cash_deposit", "新股入账": "stock_credit",
            "配售缴款": "ipo_payment", "利息归本": "interest", "股息红利差异扣税": "dividend_tax", "红利入账": "dividend"}
        financial_occurrences = Counter()
        for raw in payload.get("financial_events") or []:
            kind = str(raw.get("kind") or "")
            kind = kinds.get(kind, kind)
            if kind not in set(kinds.values()) | {"corporate_action", "other"}:
                raise TradeJournalError("非交易事件kind无效")
            ticker = _ticker(_pick(raw, "ticker", "code"), cash=True)
            ref = _source_ref(raw)
            if ref["source_id"] not in source_ids:
                raise TradeJournalError("非交易事件引用的来源不存在")
            event = {"account_alias": account, "ticker": ticker, "name": raw.get("name") or ticker,
                "kind": kind, "date": _date(raw.get("date")), "time": str(raw.get("time") or ""),
                "settlement_date": _date(raw.get("settlement_date") or raw.get("date")),
                "quantity": _number(_pick(raw, "quantity", "qty") or 0, "事件数量", minimum=0, integer=True),
                "net_cash": _number(_pick(raw, "net_cash", "net", "cash_delta"), "事件现金流", nullable=True),
                "reported_quantity_after": _number(_pick(raw, "reported_quantity_after", "quantity_after", "balance_qty"), "股票余额", nullable=True, minimum=0, integer=True),
                "linked_ticker": _ticker(raw["linked_ticker"]) if raw.get("linked_ticker") else None,
                "source_ref": ref, "note": raw.get("note")}
            if kind in {"ipo_payment", "cash_withdrawal", "dividend_tax"} and event["net_cash"] is not None and event["net_cash"] > 0:
                raise TradeJournalError("付款、转出和扣税现金流不能为正")
            if kind in {"cash_deposit", "dividend", "interest"} and event["net_cash"] is not None and event["net_cash"] < 0:
                raise TradeJournalError("存入、分红和利息现金流不能为负")
            if kind == "stock_credit" and event["net_cash"] not in (None, 0):
                raise TradeJournalError("股票入账本身不能重复计现金；配售缴款另列")
            if kind == "stock_credit" and event["quantity"] <= 0:
                raise TradeJournalError("股票入账必须有正股数")
            identity = [account, ticker, kind, event["date"], event["time"], event["quantity"], event["net_cash"], event["reported_quantity_after"], event["linked_ticker"]]
            signature = digest(identity)
            financial_occurrences[(ref["source_id"], signature)] += 1
            occurrence = raw.get("occurrence") or financial_occurrences[(ref["source_id"], signature)]
            event["financial_event_id"] = str(raw.get("financial_event_id") or "fin-" + digest([*identity, occurrence])[:40])
            event["content_hash"] = digest({k: v for k, v in event.items() if k not in {"source_ref", "note"}})
            financial.append(event)
        normalized = {"account_alias": account, "sources": sorted(sources, key=lambda r: r["source_id"]),
            "fills": sorted(fills, key=lambda r: r["fill_id"]), "financial_events": sorted(financial, key=lambda r: r["financial_event_id"])}
        if "identity_links" in payload:
            from .import_identity import ImportIdentityError, normalize_identity_links
            try:
                normalized["identity_links"] = normalize_identity_links(payload["identity_links"])
            except ImportIdentityError as error:
                raise TradeJournalError(str(error)) from error
        return normalized

    def preview_import(self, payload):
        from .import_identity import reconcile_import
        requested = self._normalize(payload)
        # This digest never depends on today's aliases or canonical-source
        # choices. Repeating a committed request keeps its original batch ID.
        preview_digest = digest(requested)
        old_fills, old_financial = self._facts()
        requested_shas = {s["sha256"] for s in requested["sources"]}
        old_sources = [self._find("sources", {"source_id": s["source_id"]}, include_content=True)
            if s.get("sha256") in requested_shas else s for s in self._all("sources")]
        reconciled = reconcile_import(requested, existing_sources=old_sources, existing_fills=old_fills,
            existing_financial=old_financial, committed_batches=self._committed_batches())
        data = reconciled["data"]
        conflicts, warnings, counts = list(reconciled["conflicts"]), [], {"sources": len(data["sources"]), "fills": len(data["fills"]),
            "financial_events": len(data["financial_events"]), "new_sources": 0, "new_fills": 0, "duplicate_fills": 0,
            "new_financial_events": 0, "duplicate_financial_events": 0, "input_fill_rows": len(data["fills"]), "payload_duplicate_fill_rows": 0}
        for category, key, new_key, duplicate_key in (("sources", "source_id", "new_sources", None),
                ("fills", "fill_id", "new_fills", "duplicate_fills"), ("financial_events", "financial_event_id", "new_financial_events", "duplicate_financial_events")):
            seen = {}
            for record in data[category]:
                repeated = seen.get(record[key])
                existing = repeated or self._find(category, {key: record[key]})
                if category == "fills" and repeated:
                    counts["payload_duplicate_fill_rows"] += 1
                    if repeated["source_ref"] != record["source_ref"] and not (record.get("broker_execution_id") and record.get("broker_execution_id") == repeated.get("broker_execution_id")):
                        conflicts.append({"kind": category, "id": record[key], "code": "duplicate_fill_id_different_rows",
                            "message": "不同来源行复用同一成交标识且无券商成交号佐证，可能少计成交，必须审查"})
                if category == "financial_events" and repeated and repeated["source_ref"] != record["source_ref"]:
                    conflicts.append({"kind": category, "id": record[key], "code": "duplicate_financial_id_different_rows",
                        "message": "不同来源行复用同一现金事件标识，必须审查以免少计"})
                if existing:
                    if existing["content_hash"] != record["content_hash"]:
                        conflicts.append({"kind": category, "id": record[key], "code": "immutable_identity_conflict",
                            "message": "相同标识内容不同，必须审查更正，不能覆盖"})
                    elif duplicate_key:
                        counts[duplicate_key] += 1
                else:
                    counts[new_key] += 1
                seen[record[key]] = record
        counts["fills"] = len({r["fill_id"] for r in data["fills"]})
        broker_ids = {}
        for row in old_fills + data["fills"]:
            if row.get("broker_execution_id"):
                identity = tuple(row.get(k) for k in ("account_alias", "date", "ticker", "broker_execution_id"))
                if identity in broker_ids and broker_ids[identity] != row["fill_id"]:
                    conflicts.append({"kind": "fills", "id": row["fill_id"], "code": "broker_execution_identity_conflict",
                        "message": "同一券商成交号对应多个fill_id，不可重复入账"})
                broker_ids[identity] = row["fill_id"]
        combined_fills = {r["fill_id"]: r for r in old_fills}
        combined_financial = {r["financial_event_id"]: r for r in old_financial}
        combined_fills.update({r["fill_id"]: r for r in data["fills"]})
        combined_financial.update({r["financial_event_id"]: r for r in data["financial_events"]})
        ledger = build_ledger(list(combined_fills.values()), list(combined_financial.values()))
        conflicts.extend(ledger["conflicts"])
        active_episode_ids = {r["episode_id"] for r in ledger["episodes"]}
        from .reviews import COLLECTION as REVIEW_COLLECTION
        from .tasks import COLLECTION as TASK_COLLECTION
        from .allocations import COLLECTION as ALLOCATION_COLLECTION
        from .execution_matches import COLLECTION as MATCH_COLLECTION
        protected_refs = {}
        for row in self._all("decisions"):
            if row.get("episode_id"):protected_refs.setdefault(row["episode_id"],set()).add("decisions")
        for owner, collection in (("reviews",REVIEW_COLLECTION),("tasks",TASK_COLLECTION),("allocations",ALLOCATION_COLLECTION),("execution_matches",MATCH_COLLECTION)):
            for row in self.store.collection(collection).find({}, {"_id":0,"episode_id":1}):
                if row.get("episode_id"):protected_refs.setdefault(row["episode_id"],set()).add(owner)
        for episode_id in set(protected_refs) - active_episode_ids:
            conflicts.append({"kind": "episodes", "id": episode_id, "code": "episode_identity_changed",
                "reference_owners": sorted(protected_refs[episode_id]),
                "message": "补录将改变已有决策/复盘/待办/分配/成交核对所引用的库存轮，必须先审查迁移，不静默改绑或孤立历史"})
        if any(r["opening_cost_status"] == "unknown" for r in ledger["episodes"]):
            warnings.append("存在期初或公司行为成本未知的库存轮；对应收益保持未知")
        if any(r["unknown_fees"] for r in ledger["episodes"]):
            warnings.append("存在费用缺失；不能将缺失当作零")
        if ledger["repairs"]:
            warnings.append("存在根据券商股数余额唯一重建的库存顺序，未改写原始时间")
        counts["conflicts"] = len(conflicts)
        return {"status": "conflict" if conflicts else "ready", "preview_digest": preview_digest,
            "batch_id": "batch-" + preview_digest, "counts": counts,
            "sources": [{k: r[k] for k in ("source_id", "filename", "sha256", "bytes", "source_type")} for r in data["sources"]],
            "conflicts": conflicts, "warnings": warnings, "can_import": not conflicts,
            "identity_candidates": reconciled["identity_candidates"],
            "identity_reconciliations": reconciled["identity_reconciliations"],
            "projected_episode_count": len(ledger["episodes"]), "inventory_repairs": ledger["repairs"]}

    def import_batch(self, payload, preview_digest=None):
        preview = self.preview_import(payload)
        if preview_digest is not None and preview_digest != preview["preview_digest"]:
            raise TradeJournalError("填报内容与对账预览不同，请重新预览")
        old = self._find("batches", {"batch_id": preview["batch_id"]})
        if old and old.get("status") == "committed" and preview["can_import"]:
            return {"status": "already_imported", "batch_id": old["batch_id"], "counts": preview["counts"],
                "episode_count": len(self._ledger()["episodes"]), "conflicts": [], "warnings": preview["warnings"], "idempotent": True}
        if not preview["can_import"]:
            raise TradeJournalError("导入存在冲突，需先审查；未写入任何事实")
        with self._write_guard():
            preview = self.preview_import(payload)
            if preview_digest is not None and preview_digest != preview["preview_digest"]:
                raise TradeJournalError("填报内容与对账预览不同，请重新预览")
            if not preview["can_import"]:
                raise TradeJournalError("导入存在冲突，需先审查；未写入任何事实")
            old = self._find("batches", {"batch_id": preview["batch_id"]})
            if old and old.get("status") == "committed":
                return {"status": "already_imported", "batch_id": old["batch_id"], "counts": preview["counts"],
                    "episode_count": len(self._ledger()["episodes"]), "conflicts": [], "warnings": preview["warnings"], "idempotent": True}
            from .import_identity import reconcile_import
            requested, now = self._normalize(payload), self._now()
            old_fills, old_financial = self._facts()
            requested_shas = {s["sha256"] for s in requested["sources"]}
            old_sources = [self._find("sources", {"source_id": s["source_id"]}, include_content=True)
                if s.get("sha256") in requested_shas else s for s in self._all("sources")]
            reconciled = reconcile_import(requested, existing_sources=old_sources, existing_fills=old_fills,
                existing_financial=old_financial, committed_batches=self._committed_batches())
            if reconciled["conflicts"]:
                raise TradeJournalError("导入身份接合存在冲突，需重新预览；未写入任何事实")
            data = reconciled["data"]
            receipt = {"batch_id": preview["batch_id"], "preview_digest": preview["preview_digest"],
                "account_alias": data["account_alias"], "recorded_at": now, "status": "committed", "counts": preview["counts"],
                "source_ids": sorted({r["source_id"] for r in data["sources"]} | {r["source_ref"]["source_id"] for r in data["fills"] + data["financial_events"]}),
                "fill_ids": [r["fill_id"] for r in data["fills"]],
                "financial_event_ids": [r["financial_event_id"] for r in data["financial_events"]],
                "fill_source_refs": [{"fill_id": r["fill_id"], "source_ref": r["source_ref"]} for r in data["fills"]],
                "financial_source_refs": [{"financial_event_id": r["financial_event_id"], "source_ref": r["source_ref"]} for r in data["financial_events"]],
                "source_imports": reconciled["source_imports"],
                "identity_reconciliations": reconciled["identity_reconciliations"]}
            receipt["content_hash"] = digest({k: v for k, v in receipt.items() if k != "recorded_at"})
            if len(BSON.encode(receipt)) > 14 * 1024 * 1024:
                raise TradeJournalError("完整导入回执超过14MiB预算，请拆分原始导出；不截断身份确认和原行审计")
            for category, key in (("sources", "source_id"), ("fills", "fill_id"), ("financial_events", "financial_event_id")):
                for record in data[category]:
                    self._insert(category, {key: record[key]}, {**record, "recorded_at": now})
            self._insert("batches", {"batch_id": receipt["batch_id"]}, receipt)
            return {"status": "imported", "batch_id": receipt["batch_id"], "counts": preview["counts"],
                "episode_count": len(self._ledger()["episodes"]), "conflicts": [], "warnings": preview["warnings"], "idempotent": False}

    def _ledger(self):
        if "ledger" not in self._cache:
            fills, financial = self._facts()
            self._cache["ledger"] = build_ledger(fills, financial)
        return deepcopy(self._cache["ledger"])

    def _latest_decisions(self, episode_id=None, records=None):
        latest = {}
        for r in (records if records is not None else self._all("decisions")):
            if episode_id is not None and r["episode_id"] != episode_id:
                continue
            if r["decision_id"] not in latest or r["revision"] > latest[r["decision_id"]]["revision"]:
                latest[r["decision_id"]] = r
        return sorted(latest.values(), key=lambda r: (r["observed_at"], r["recorded_at"]))

    def _qualification(self, episode, fills, decisions, all_decision_revisions=None):
        missing = []
        for field in ("mode", "trigger", "reason", "expectation", "verification_at", "invalidation", "holding_horizon", "risk_constraints", "reference_tickers", "evidence"):
            if not any(_specified(r.get(field)) or _specified(r.get(field + "_id")) for r in decisions):
                missing.append(field)
        accounting_reasons = []
        if episode["opening_cost_status"] != "known" or episode["unknown_cost_sold_quantity"]:
            accounting_reasons.append("期初或已卖股数的成本未知")
        if episode["unknown_fees"]:
            accounting_reasons.append("成交费用缺失")
        if not episode["inventory_valid"]:
            accounting_reasons.append("库存不连续")
        if not episode["opening_balance_verified"]:
            accounting_reasons.append("没有证据证明期初股票余额")
        unknown_modes = {None, "", "unknown", "未知", "待补", "待确认", "mode.unknown"}
        review = any((r.get("mode_id") not in unknown_modes or r.get("mode") not in unknown_modes)
            and r.get("reason") and r.get("evidence") for r in decisions)
        pretrade = []
        historical = all_decision_revisions if all_decision_revisions is not None else decisions
        for fill in fills:
            matching = []
            for r in historical:
                if fill["fill_id"] not in r.get("fill_ids", []) or fill.get("time_precision") != "execution_time":
                    continue
                plan = r.get("frozen_plan") or {}
                bound_before = (plan.get("origin") == "journal_plan_reference" and plan.get("historical_freeze_verified")
                    and plan.get("ex_ante_verifiable") and plan.get("target_date") == fill["date"]
                    and _time(plan["captured_at"]) < _time(fill["executed_at"]))
                contemporaneous = (r.get("is_contemporaneous") and _time(r["recorded_at"]) < _time(fill["executed_at"])
                    and plan.get("snapshot_complete") and not r.get("temporal_errors") and r.get("evidence"))
                if bound_before or contemporaneous:
                    matching.append(r)
            if not matching:
                pretrade.append(fill["fill_id"])
        ex_ante_reasons = (["缺少覆盖每次成交且当时已冻结的计划与可用事实"] if pretrade or not fills else [])
        if missing:
            ex_ante_reasons.append("计划或参照、验证时点、失效及风险条件不完整")
        result = {
            "accounting": {"eligible": not accounting_reasons, "reasons": accounting_reasons},
            "decision_review": {"eligible": review, "reasons": [] if review else ["尚缺模式、独立理由或可定位证据，可保留为待补记录"]},
            "ex_ante": {"eligible": not ex_ante_reasons, "reasons": ex_ante_reasons,
                "scope": "核对每次成交前已冻结的完整计划、来源时点及目标日期；可核验不代表实际按计划执行"},
            "rule_backtest": {"eligible": False, "reasons": ["尚未关联机器规则验证、完整候选分母与可成交约束；人工标签不授予回测资格"]}}
        for key, value in result.items():
            value["definition_ref"] = "qualification." + key
        return result, missing

    def list_episodes(self, query="", start_date=None, end_date=None, status=None, qualification=None, limit=100, offset=0, mode_id=None):
        from .reviews import latest_review_records, read_review_records, review_fact_context, review_progress

        ledger = self._ledger()
        decision_records = self._all("decisions")
        review_heads = latest_review_records(read_review_records(self.store))
        checks = self._all("expectation_checks") if review_heads else []
        financial = self._facts()[1] if review_heads else []
        fills_by_episode = defaultdict(list)
        for row in ledger["fills"]:
            fills_by_episode[row["episode_id"]].append(row)
        items = []
        for ep in ledger["episodes"]:
            if query and query.casefold() not in f'{ep["ticker"]} {ep["name"]} {" ".join(ep["names"])}'.casefold():
                continue
            if start_date and (ep["end_date"] or "9999-12-31") < _date(start_date):
                continue
            if end_date and ep["start_date"] > _date(end_date):
                continue
            if status and ep["status"] != status:
                continue
            decisions = self._latest_decisions(ep["episode_id"], decision_records)
            if mode_id and not any(r.get("mode_id") == mode_id for r in decisions):
                continue
            rows = fills_by_episode[ep["episode_id"]]
            q, missing = self._qualification(ep, rows, decisions, [r for r in decision_records if r["episode_id"] == ep["episode_id"]])
            if qualification:
                key = qualification.removeprefix("qualification.")
                if key not in q:
                    raise TradeJournalError("未知资格维度")
                if not q[key]["eligible"]:
                    continue
            record = review_heads.get(ep["episode_id"])
            progress = review_progress(record,
                review_fact_context(ep, rows, decisions, checks, financial) if record else None,
                saved_note_count=len(decisions))
            items.append({**ep, "qualification": q, "missing_fields": missing, "decision_count": len(decisions),
                "review_progress": progress,
                "mode_ids": sorted({r["mode_id"] for r in decisions if r.get("mode_id")}),
                "mode": next((r.get("mode") or r.get("mode_id") for r in reversed(decisions) if r.get("mode") or r.get("mode_id")), None)})
        items.sort(key=lambda r: (r["end_date"] or "9999", r["start_date"], r["episode_id"]), reverse=True)
        return {**_page(items, limit, offset), "missing_fields_summary": dict(Counter(k for r in items for k in r["missing_fields"])),
            "summary": {"closed": sum(r["status"] == "closed" for r in items),
            "open": sum(r["status"] == "open" for r in items), "qualification_counts": {k: sum(r["qualification"][k]["eligible"] for r in items) for k in QUALIFICATION_KEYS}}}

    def list_fills(self, query="", start_date=None, end_date=None, episode_id=None, limit=200, offset=0, source_id=None):
        rows = self._ledger()["fills"]
        selected = [r for r in rows if (not query or query.casefold() in f'{r["ticker"]} {r["name"]}'.casefold())
            and (not start_date or r["date"] >= _date(start_date)) and (not end_date or r["date"] <= _date(end_date))
            and (not episode_id or r["episode_id"] == episode_id)
            and (not source_id or r["source_ref"]["source_id"] == source_id)]
        selected.sort(key=lambda r: (r["date"], r["time"], r["fill_id"]), reverse=True)
        return _page(selected, limit, offset)

    def get_episode(self, episode_id):
        from .reviews import latest_review_records, read_review_records, review_fact_context, review_progress

        ledger = self._ledger()
        episode = next((r for r in ledger["episodes"] if r["episode_id"] == episode_id), None)
        if episode is None:
            raise TradeJournalError("交易档案不存在")
        fills = [r for r in ledger["fills"] if r["episode_id"] == episode_id]
        revisions = [r for r in self._all("decisions") if r["episode_id"] == episode_id]
        decisions = self._latest_decisions(episode_id, revisions)
        qualification, missing = self._qualification(episode, fills, decisions, revisions)
        ids = {r["decision_id"] for r in decisions}
        financial = [r for r in self._facts()[1] if r["financial_event_id"] in episode["financial_event_ids"]]
        checks = [r for r in self._all("expectation_checks") if r["decision_id"] in ids]
        record = latest_review_records(read_review_records(self.store, episode_id)).get(episode_id)
        progress = review_progress(record,
            review_fact_context(episode, fills, decisions, checks, financial) if record else None,
            saved_note_count=len(decisions))
        return {"episode": {**episode, "qualification": qualification, "missing_fields": missing,
                "review_progress": progress}, "review_progress": progress, "fills": fills,
            "financial_events": financial, "decisions": decisions,
            "security_financial_events": [r for r in self._facts()[1] if r["ticker"] == episode["ticker"] and r["account_alias"] == episode["account_alias"]],
            "cash_pnl_scope": "FIFO买卖含费结果；分红与延期红利税另列，未强行分摊到某一库存轮",
            "links": [r for r in self._all("links") if r["from_decision_id"] in ids or r["to_decision_id"] in ids],
            "qualification": qualification, "missing_fields": missing,
            "expectation_checks": checks,
            "live_references": self._live_references(episode["ticker"])}

    def _live_references(self, ticker):
        allowed = {"execution_id", "order_id", "plan_id", "ticker", "side", "quantity", "price", "fees", "executed_at", "source"}
        executions = [{k: v for k, v in _clean(r).items() if k in allowed} for r in self.store.collection("trade_executions").find({"ticker": ticker}, {"_id": 0})]
        plans = [{k: v for k, v in _clean(r).items() if k in {"plan_id", "ticker", "strategy_id", "updated_at", "created_at", "version"}}
            for r in self.store.collection("trading_watch_plans").find({"ticker": ticker}, {"_id": 0})]
        return {"executions": executions, "plans": plans, "read_only": True,
            "note": "现有实盘事实只读引用；当前可变计划不构成历史冻结证据，不参与重复核算"}

    def get_summary(self):
        fills, financial = self._facts()
        result = self.list_episodes(limit=1000)
        dates = sorted({r["date"] for r in fills})
        batches = self._committed_batches()
        sources = {s for r in batches for s in r.get("source_ids", [])}
        return {"counts": {"sources": len(sources), "batches": len(batches), "fills": len(fills),
            "financial_events": len(financial), "episodes": result["total"], "securities": len({r["ticker"] for r in fills}),
            "decisions": len(self._latest_decisions()), "observations": len(self._all("observations")),
            "plans": len({r["plan_id"] for r in self._all("snapshots") if r.get("kind") == "journal_plan"}),
            "plan_revisions": sum(r.get("kind") == "journal_plan" for r in self._all("snapshots"))},
            "coverage": {"from": dates[0] if dates else None, "to": dates[-1] if dates else None, "trade_dates": len(dates),
                "scope": "已导入账户成交；不代表完整市场机会或全部观察名单"},
            "qualification_counts": result["summary"]["qualification_counts"],
            "missing_fields": result["missing_fields_summary"],
            "ledger_version": LEDGER_VERSION, "closed": result["summary"]["closed"], "open": result["summary"]["open"]}

    def list_financial_events(self, query="", start_date=None, end_date=None, limit=100, offset=0):
        rows = [r for r in self._facts()[1] if (not query or query.casefold() in f'{r["ticker"]} {r["name"]} {r["kind"]}'.casefold())
            and (not start_date or r["date"] >= _date(start_date)) and (not end_date or r["date"] <= _date(end_date))]
        rows.sort(key=lambda r: (r["date"], r["time"], r["financial_event_id"]), reverse=True)
        return _page(rows, limit, offset)

    def list_sources(self, limit=100, offset=0):
        active = {source_id for batch in self._committed_batches() for source_id in batch.get("source_ids", [])}
        rows = [{k: v for k, v in r.items() if k != "content"} for r in self._all("sources") if r["source_id"] in active]
        rows.sort(key=lambda r: (r["recorded_at"], r["source_id"]), reverse=True)
        return _page(rows, limit, offset)

    def get_source(self, source_id, *, include_content=False):
        record = self._find("sources", {"source_id": source_id}, include_content=include_content)
        if record is None:
            raise TradeJournalError("原始来源不存在")
        if include_content:
            try:
                raw = base64.b64decode(record["content"], validate=True) if record["content_encoding"] == "base64" else record["content"].encode("utf-8")
            except (ValueError, KeyError, TypeError) as error:
                raise TradeJournalError("原始来源无法恢复，需审查存储完整性") from error
            hashed = {k: v for k, v in record.items() if k not in {"recorded_at", "content_hash"}}
            if hashlib.sha256(raw).hexdigest() != record["sha256"] or digest(hashed) != record["content_hash"]:
                raise TradeJournalError("原始来源校验失败，需审查存储完整性")
        return record

    def list_batches(self, limit=100, offset=0):
        rows = self._committed_batches()
        rows.sort(key=lambda r: (r["recorded_at"], r["batch_id"]), reverse=True)
        return _page(rows, limit, offset)

    def _definitions(self, payload):
        from .definition_refs import resolve_definition_references
        refs = deepcopy(payload.get("definition_refs") or [])
        if not isinstance(refs,list):
            raise TradeJournalError("definition_refs必须是确切术语引用的列表")
        for field, category in (("mode_id", "mode"), ("trigger_id", "trigger"), ("action_id", "action")):
            if payload.get(field):
                if not isinstance(payload[field],str):
                    raise TradeJournalError(f"{field}必须为术语ID字符串，版本放在definition_refs")
                matching = [r for r in refs if (r if isinstance(r,str) else r.get("id") if isinstance(r,dict) else None) == payload[field]]
                # A declared explicit version is authoritative; never add an
                # unversioned duplicate that could rebind the current catalogue.
                reference = matching[0] if matching else payload[field]
                validated = resolve_definition_references(self.store,[reference],expected_category=category)
                if not validated["valid"]:
                    raise TradeJournalError("；".join(e["message"] for e in validated["errors"]))
                if not matching:
                    refs.append(reference)
        result = resolve_definition_references(self.store,refs)
        if not result["valid"]:
            raise TradeJournalError("；".join(e["message"] for e in result["errors"]))
        return result

    def _evidence(self, items, decision_at, now):
        if items is not None and not isinstance(items, list):
            raise TradeJournalError("证据须为列表")
        output, errors = [], []
        for raw in items or []:
            item = deepcopy(raw) if isinstance(raw, dict) else {"text": str(raw)}
            item["evidence_class"] = _evidence_class(item.get("evidence_class"))
            item["observed_at"] = _iso(item["observed_at"]) if item.get("observed_at") else None
            item["available_at"] = _iso(item["available_at"]) if item.get("available_at") else None
            item["captured_at"] = now
            if not item["observed_at"] or not item["available_at"]:
                errors.append("证据时点或当时可用时间未知")
            elif _time(item["available_at"]) < _time(item["observed_at"]):
                errors.append("证据可用时间早于发生时间")
            elif _time(item["observed_at"]) > decision_at or _time(item["available_at"]) > decision_at:
                errors.append("决策后才发生或可用的证据不能作为事前依据")
            if item["evidence_class"] != "contemporaneous":
                errors.append("历史重建、回忆或未知证据只能用于复盘")
            reference = item.get("source_ref") or {}
            if not isinstance(reference, dict):
                raise TradeJournalError("证据来源引用须为对象")
            stored = self._find("sources", {"source_id": reference.get("source_id")}) if reference.get("source_id") else None
            if not stored or not reference.get("row") and not reference.get("record_id") or stored and (_time(stored["recorded_at"]) > decision_at or stored.get("evidence_class") != "contemporaneous"):
                errors.append("没有可恢复且在决策前已保存的来源证据")
            item["content_hash"] = digest({k: v for k, v in item.items() if k != "captured_at"})
            output.append(item)
        return output, sorted(set(errors))

    @_serialized
    def append_decision(self, episode_id, payload):
        self.get_episode(episode_id)
        data = deepcopy(payload)
        now = self._now()
        decision_id = str(data.get("decision_id") or "decision-" + uuid.uuid4().hex)
        with LOCK:
            history = [r for r in self._all("decisions") if r["decision_id"] == decision_id]
            previous = max(history, key=lambda r: r["revision"]) if history else None
            if previous and previous["episode_id"] != episode_id:
                raise TradeJournalError("决策不能修改所属库存轮")
            expected = data.get("expected_revision")
            if previous and (type(expected) is not int or expected != previous["revision"]):
                raise TradeJournalError("决策版本已变化，请读取最新版本后追加")
            if previous and not data.get("revision_reason"):
                raise TradeJournalError("修订必须填写revision_reason并保留旧版本")
            merged = {**(previous or {}), **data}
            for field in ("action", "action_id", "mode", "mode_id", "trigger", "trigger_id", "reason"):
                if merged.get(field) is not None and not isinstance(merged[field], str):
                    raise TradeJournalError(f"{field}必须保留为原始文字；定义引用另用definition_refs")
            if not (merged.get("action") or merged.get("action_id")) or not str(merged.get("reason") or "").strip():
                raise TradeJournalError("至少填写动作及独立理由；其他缺项可以保留未知")
            at = _time(merged.get("observed_at") or now)
            if at > _time(now):
                raise TradeJournalError("决策发生时间不能在未来；下一验证时点单独填写")
            evidence, temporal_errors = self._evidence(merged.get("evidence"), at, now)
            declared = _evidence_class(merged.get("evidence_class"))
            retrospective = _time(now) > at
            effective = "retrospective_recall" if declared == "contemporaneous" and retrospective else declared
            definitions = self._definitions(merged)
            fills = {r["fill_id"] for r in self.get_episode(episode_id)["fills"]}
            fill_ids = list(dict.fromkeys(merged.get("fill_ids") or []))
            if not set(fill_ids) <= fills:
                raise TradeJournalError("决策关联成交不属于该库存轮")
            inherited_plan = (previous or {}).get("frozen_plan") or {}
            plan_ref = merged.get("frozen_plan_ref")
            bound_plan = self.get_plan(plan_ref["plan_id"], plan_ref.get("revision")) if isinstance(plan_ref, dict) and plan_ref.get("plan_id") else None
            if bound_plan and bound_plan["ticker"] != self.get_episode(episode_id)["episode"]["ticker"]:
                raise TradeJournalError("计划与成交证券不同")
            plan_input = data.get("frozen_plan") if "frozen_plan" in data else inherited_plan.get("external_reference") if inherited_plan.get("origin") == "journal_plan" else inherited_plan
            plan = deepcopy(plan_input) or None
            if plan is not None and not isinstance(plan, dict):
                raise TradeJournalError("冻结计划须为对象或独立计划引用")
            if plan:
                # The payload is archived now. A user-supplied earlier timestamp
                # cannot turn a current recollection into an earlier frozen plan.
                plan["captured_at"] = now
                plan["content_hash"] = digest({k: v for k, v in plan.items() if k not in {"captured_at", "content_hash"}})
                plan["historical_freeze_verified"] = False
                plan["snapshot_complete"] = isinstance(plan.get("content"), dict) and bool(plan["content"])
                plan["reference_only"] = not plan["snapshot_complete"]
            plan_fields = ("mode", "mode_id", "trigger", "trigger_id", "expectation", "verification_at", "invalidation", "holding_horizon", "risk_constraints", "reference_tickers")
            if bound_plan:
                plan_ref = {"plan_id": bound_plan["plan_id"], "revision": bound_plan["revision"], "snapshot_id": bound_plan["snapshot_id"]}
                plan = {**plan_ref, "origin": "journal_plan_reference", "captured_at": bound_plan["captured_at"],
                    "target_date": bound_plan["target_date"], "snapshot_complete": bound_plan["complete"],
                    "ex_ante_verifiable": bound_plan["ex_ante_verifiable"], "content_hash": bound_plan["content_digest"],
                    "historical_freeze_verified": True, "reference_only": False}
            elif (merged.get("mode") or merged.get("mode_id")) and (merged.get("trigger") or merged.get("trigger_id")) and all(merged.get(k) for k in ("expectation", "verification_at", "invalidation", "holding_horizon", "risk_constraints")):
                # A complete plan entered through this journal is itself a new,
                # immutable plan version. It never inherits an external mutable
                # plan's claimed old creation time.
                plan_content = {k: deepcopy(merged.get(k)) for k in plan_fields}
                plan = {"plan_id": f'{decision_id}-plan-{previous["revision"] + 1 if previous else 1}',
                    "content": plan_content, "content_hash": digest(plan_content), "captured_at": now,
                    "origin": "journal_plan",
                    "snapshot_complete": True, "reference_only": False, "historical_freeze_verified": False,
                    "external_reference": plan}
            keep = ("action", "action_id", "mode", "mode_id", "trigger", "trigger_id", "reason", "expectation", "verification_at",
                "invalidation", "holding_horizon", "risk_constraints", "reference_tickers", "environment", "emotion", "cycle", "role",
                "revision_reason", "author", "note", "strategy_id", "mode_allocation", "execution_difference")
            record = {k: deepcopy(merged.get(k)) for k in keep}
            record.update(decision_id=decision_id, revision=(previous["revision"] + 1 if previous else 1), episode_id=episode_id,
                observed_at=at.isoformat(timespec="seconds"), available_at=_iso(merged["available_at"]) if merged.get("available_at") else None,
                recorded_at=now, evidence_class=effective, declared_evidence_class=declared,
                is_contemporaneous=effective == "contemporaneous" and not retrospective,
                is_retrospective=retrospective or effective in {"retrospective_recall", "historical_reconstruction"},
                temporal_errors=temporal_errors, evidence=evidence, frozen_plan=plan, frozen_plan_ref=plan_ref, fill_ids=fill_ids,
                definition_refs=deepcopy(merged.get("definition_refs") or []), definition_snapshots=definitions["resolved"],
                definitions_version=definitions["version"], previous_revision=previous["revision"] if previous else None)
            if record["verification_at"] is None and isinstance(record["expectation"], dict):
                record["verification_at"] = record["expectation"].get("verification_at")
            if record["verification_at"] not in (None, ""):
                record["verification_at"] = _iso(record["verification_at"])
            else:
                record["verification_at"] = None
            if record["available_at"] and _time(record["available_at"]) > at:
                record["temporal_errors"].append("决策声明的可用时间晚于决策时点")
            record["content_hash"] = digest(record)
            saved, _ = self._insert("decisions", {"decision_id": decision_id, "revision": record["revision"]}, record)
        detail = self.get_episode(episode_id)
        return {"decision_id": decision_id, "revision": saved["revision"], "record": saved,
            "qualification": detail["qualification"], "missing_fields": detail["missing_fields"]}

    def get_decision_history(self, decision_id):
        records = sorted([r for r in self._all("decisions") if r["decision_id"] == decision_id], key=lambda r: r["revision"])
        if not records:
            raise TradeJournalError("决策不存在")
        return {"decision_id": decision_id, "episode_id": records[0]["episode_id"], "items": records,
            "total": len(records), "latest_revision": records[-1]["revision"]}

    @_serialized
    def link_decisions(self, payload):
        left = self.get_decision_history(payload.get("from_decision_id"))["items"][-1]
        right = self.get_decision_history(payload.get("to_decision_id"))["items"][-1]
        if left["decision_id"] == right["decision_id"]:
            raise TradeJournalError("不能将决策关联到自身")
        if not str(payload.get("reason") or "").strip():
            raise TradeJournalError("跨决策关联必须保留理由，不从资金流推断心理")
        record = {"from_decision_id": left["decision_id"], "from_revision": left["revision"],
            "to_decision_id": right["decision_id"], "to_revision": right["revision"],
            "from_episode_id": left["episode_id"], "to_episode_id": right["episode_id"],
            "relation": str(payload.get("relation") or "related"), "reason": payload["reason"],
            "evidence": deepcopy(payload.get("evidence") or []), "evidence_class": _evidence_class(payload.get("evidence_class")),
            "recorded_at": self._now()}
        record["link_id"] = str(payload.get("link_id") or "link-" + digest({k: v for k, v in record.items() if k != "recorded_at"})[:32])
        record["content_hash"] = digest({k: v for k, v in record.items() if k != "recorded_at"})
        saved, _ = self._insert("links", {"link_id": record["link_id"]}, record)
        return {"link_id": saved["link_id"], "record": saved}

    @_serialized
    def append_expectation_check(self, decision_id, payload):
        history = self.get_decision_history(decision_id)
        revision = payload.get("decision_revision") or history["latest_revision"]
        decision = next((r for r in history["items"] if r["revision"] == revision), None)
        if decision is None:
            raise TradeJournalError("回证引用的决策版本不存在")
        evaluation_class = payload.get("evaluation_class") or "manual_assessment"
        now = self._now()
        requested = payload.get("status") or payload.get("state") or "unknown"
        if requested not in {"supported", "refuted", "unknown"}:
            raise TradeJournalError("回证只能为supported/refuted/unknown")
        reasons, state, machine = [], "unknown", False
        actual_provenance = {"provenance_class": "unknown", "market_data_verified": False}
        actual = deepcopy(payload.get("actual"))
        expectation = decision.get("expectation")
        target_at = decision.get("verification_at") or (expectation.get("verification_at") if isinstance(expectation, dict) else None)
        if payload.get("target_at") and target_at and _time(payload["target_at"]) != _time(target_at):
            raise TradeJournalError("回证不能改写该版本计划的验证时点")
        target_at = target_at or payload.get("target_at")
        future_actual = isinstance(actual, dict) and any(actual.get(k) and _time(actual[k]) > _time(now) for k in ("observed_at", "available_at"))
        if (target_at and _time(target_at) > _time(now)) or future_actual:
            reasons.append("验证时点未到或证据尚未发生/可用，保持未知")
        elif evaluation_class == "manual_assessment":
            state = requested
            reasons.append("人工复盘评估；未声称机器核验了行情或事前计划")
        elif evaluation_class == "numeric_comparison":
            if not isinstance(expectation, dict) or not isinstance(actual, dict):
                reasons.append("缺机器可判定的预期或数值事实")
            elif not target_at or _time(target_at) > _time(now):
                reasons.append("验证时点缺失或尚未到达")
            else:
                source_ref = actual.get("source_ref") or {}
                source = self.get_source(source_ref["source_id"], include_content=True) if source_ref.get("source_id") and self._find("sources", {"source_id": source_ref["source_id"]}) else None
                if source:
                    actual_provenance = {"source_id": source["source_id"], "source_type": source.get("source_type"),
                        "provenance_class": (source.get("metadata") or {}).get("provenance_class") or "archived_source",
                        "market_data_verified": False, "source_recorded_at": source.get("recorded_at")}
                observed, available = actual.get("observed_at"), actual.get("available_at")
                value_path = actual.get("value_path")
                stored_value = None
                if source and source.get("content_encoding") == "utf-8" and value_path:
                    try:
                        stored_value = json.loads(source["content"])
                        for part in str(value_path).split("."):
                            stored_value = stored_value[int(part)] if isinstance(stored_value, list) else stored_value[part]
                    except (ValueError, KeyError, TypeError, IndexError):
                        stored_value = None
                if expectation.get("metric") and actual.get("metric") != expectation["metric"]:
                    reasons.append("实际指标与预期指标不一致")
                elif not observed or not available or _time(available) < _time(observed) or _time(observed) < _time(target_at) or _time(observed) > _time(now) or _time(available) > _time(now):
                    reasons.append("数值事实缺时点、未覆盖验证时点或尚不可用")
                elif stored_value is None or stored_value != actual.get("value"):
                    reasons.append("数值未与不可变来源的value_path对上，保持未知")
                else:
                    value = _number(stored_value, "实际值")
                    target = _number(expectation.get("target"), "目标值")
                    op = expectation.get("operator")
                    operations = {">": lambda: value > target, ">=": lambda: value >= target,
                        "<": lambda: value < target, "<=": lambda: value <= target, "==": lambda: value == target}
                    if op not in operations:
                        reasons.append("不支持的数值比较条件")
                    else:
                        state = "supported" if operations[op]() else "refuted"
                        machine = True
                        reasons.append("仅比较已归档数值与冻结条件；来源真实性及策略有效性另行判断")
                        if actual_provenance["provenance_class"] == "manual_observation":
                            reasons.append("来源为人工提交的观察原文，未核验为实测市场行情")
        else:
            raise TradeJournalError("未知回证evaluation_class")
        record = {"check_id": "check-" + uuid.uuid4().hex, "decision_id": decision_id, "decision_revision": revision,
            "episode_id": decision["episode_id"], "recorded_at": now, "status": state, "requested_status": requested,
            "evaluation_class": evaluation_class, "machine_verified": machine, "actual": actual,
            "actual_provenance": actual_provenance, "comparison_scope": "archived_numeric_value_only" if machine else "manual_or_unknown",
            "expectation": deepcopy(expectation), "target_at": target_at, "reasons": reasons,
            "evidence": deepcopy(payload.get("evidence") or []), "note": payload.get("note"),
            "plan_was_ex_ante": bool((decision.get("frozen_plan") or {}).get("ex_ante_verifiable") or
                (decision.get("is_contemporaneous") and (decision.get("frozen_plan") or {}).get("snapshot_complete")
                 and not decision.get("temporal_errors") and bool(decision.get("evidence"))))}
        record["content_hash"] = digest(record)
        saved, _ = self._insert("expectation_checks", {"check_id": record["check_id"]}, record)
        return {"record": saved, "status": state, "reasons": reasons}

    @_serialized
    def freeze_plan(self, payload):
        """Save an independent pre-order plan; no episode or broker action needed."""
        if not isinstance(payload, dict):
            raise TradeJournalError("计划必须为对象")
        now, data = self._now(), deepcopy(payload)
        plan_id = str(data.get("plan_id") or "plan-" + uuid.uuid4().hex)
        rows = [row for row in self._all("snapshots") if row.get("kind") == "journal_plan" and row.get("plan_id") == plan_id]
        latest = max(rows, key=lambda row: row["revision"]) if rows else None
        if latest and (type(data.get("expected_revision")) is not int or data["expected_revision"] != latest["revision"]):
            raise TradeJournalError("计划版本已变化，请读取最新版本后追加")
        if latest and not str(data.get("revision_reason") or "").strip():
            raise TradeJournalError("计划修订必须填写revision_reason，旧版本保留")
        prior = self.get_plan(plan_id, latest["revision"])["content"] if latest else {}
        content = {**prior, **{k: deepcopy(v) for k, v in data.items() if k not in {"plan_id", "expected_revision", "recorded_at", "captured_at"}}}
        ticker = _ticker(content.get("ticker"))
        if latest and ticker != latest["ticker"]:
            raise TradeJournalError("计划修订不能更换证券，请另建独立计划")
        content["ticker"] = ticker
        target_date = _date(content.get("target_date")) if content.get("target_date") else None
        content["target_date"] = target_date
        observed = _iso(content.get("observed_at") or now)
        if _time(observed) > _time(now):
            raise TradeJournalError("计划观察时间不能在未来")
        available = _iso(content["available_at"]) if content.get("available_at") else now
        content.update(observed_at=observed, available_at=available)
        if _time(available) < _time(observed) or _time(available) > _time(now):
            raise TradeJournalError("计划可用时点须在观察之后且不晚于冻结时间")
        definitions = self._definitions(content)
        inline_sources = content.pop("sources", None)
        inline_source_batch_id = None
        if inline_sources is not None:
            if not isinstance(inline_sources, list) or any(not isinstance(row, dict) for row in inline_sources):
                raise TradeJournalError("预案来源须为可恢复原件对象列表")
            evidence_sources = []
            for row in inline_sources:
                metadata = row.get("metadata") or {}
                if not isinstance(metadata, dict):
                    raise TradeJournalError("来源metadata须为对象")
                evidence_sources.append({"evidence_class": "contemporaneous", **row,
                    "source_type": "journal_evidence", "metadata": {**metadata,
                        "original_source_type": row.get("source_type"), "provenance_class": "manual_observation",
                        "capture_method": "user_supplied_journal_evidence", "market_data_verified": False}})
            source_import = self.import_batch({"account_alias": data.get("account_alias") or "primary",
                "sources": evidence_sources, "fills": [], "financial_events": []})
            inline_source_batch_id = source_import["batch_id"]
            # Archiving may take time. Capture only after originals are durable;
            # never inherit the earlier request-start or caller timestamps.
            now = self._now()
        evidence, errors = self._evidence(content.get("evidence"), _time(now), now)
        portfolio_ref = content.get("portfolio_snapshot_ref")
        if portfolio_ref:
            from .analytics import PortfolioRiskService
            owner = PortfolioRiskService(self.store,journal=self)
            portfolio_record = owner.resolve_reference(portfolio_ref,before_at=now)
            content["portfolio_snapshot_ref"] = owner.reference(portfolio_record["snapshot_id"])
        declared = _evidence_class(content.get("evidence_class") or "contemporaneous")
        if declared != "contemporaneous":
            errors.append("该计划声明为历史重建、回忆或未知，不能充当前瞻计划")
        if target_date and target_date < now[:10]:
            errors.append("目标日期早于本次真实冻结日期")
        if content.get("verification_at"):
            content["verification_at"] = _iso(content["verification_at"])
            if _time(content["verification_at"]) <= _time(now):
                errors.append("验证时点未晚于本次冻结时间")
        live_reference = None
        if content.get("live_plan_ref"):
            ref = content["live_plan_ref"]
            if not isinstance(ref, dict) or not ref.get("plan_id") or ref.get("version") is None:
                raise TradeJournalError("现有计划引用须提供plan_id和确切version")
            live = _clean(self.store.collection("trading_watch_plans").find_one({"plan_id": ref["plan_id"]}, {"_id": 0}))
            actual_version = live.get("plan_revision") if type(ref["version"]) is int else live.get("playbook_version", live.get("version"))
            if not live or actual_version != ref["version"] or live.get("ticker") != ticker or (ref.get("content_hash") and ref["content_hash"] != digest(live)):
                raise TradeJournalError("现有计划确切版本不可恢复或证券不一致，不能冒充历史版本")
            live_reference = {"collection": "trading_watch_plans", "plan_id": ref["plan_id"], "version": actual_version,
                "content_hash": digest(live), "content": live, "retrieved_at": now,
                "note": "只读当前确切版本，真实冻结时间为本次captured_at；不继承旧frozen_at"}
        content["evidence"] = evidence
        missing = [key for key in ("target_date", "reason", "expectation", "verification_at", "invalidation", "holding_horizon", "risk_constraints", "reference_tickers", "evidence") if not _specified(content.get(key))]
        if not (_specified(content.get("mode")) or _specified(content.get("mode_id"))):
            missing.append("mode")
        if not (_specified(content.get("trigger")) or _specified(content.get("trigger_id"))):
            missing.append("trigger")
        revision = latest["revision"] + 1 if latest else 1
        record = {"snapshot_id": f"{plan_id}-v{revision}", "kind": "journal_plan", "plan_id": plan_id,
            "revision": revision, "previous_revision": latest["revision"] if latest else None,
            "ticker": ticker, "name": content.get("name"), "target_date": target_date, "status": "frozen",
            "complete": not missing, "missing_fields": missing, "content": content, "content_digest": digest(content),
            "captured_at": now, "recorded_at": now, "observed_at": observed,
            "evidence_class": declared, "temporal_errors": sorted(set(errors)),
            "ex_ante_verifiable": not missing and not errors, "live_reference": live_reference,
            "definition_snapshots": definitions["resolved"], "definitions_version": definitions["version"],
            "inline_source_batch_id": inline_source_batch_id,
            "qualification_note": "完整且时点可核对的独立计划；没有下单，不代表执行或模式有效"}
        record["content_hash"] = digest({k: v for k, v in record.items() if k != "recorded_at"})
        self._insert("snapshots", {"snapshot_id": record["snapshot_id"]}, record)
        return {"plan_id": plan_id, "revision": revision, "record": record,
            "missing_fields": missing, "ex_ante_verifiable": record["ex_ante_verifiable"]}

    def list_plans(self, query="", start_date=None, end_date=None, status=None, limit=100, offset=0, date=None, ticker=None):
        rows = [r for r in self._all("snapshots") if r.get("kind") == "journal_plan"]
        latest = {}
        for row in rows:
            if row["plan_id"] not in latest or row["revision"] > latest[row["plan_id"]]["revision"]:
                latest[row["plan_id"]] = row
        for row in latest.values():
            row["revisions"] = sorted(r["revision"] for r in rows if r["plan_id"] == row["plan_id"])
        selected = [r for r in latest.values()
            if (not query or query.casefold() in f'{r["ticker"]} {r.get("name") or ""} {r["plan_id"]}'.casefold())
            and (not ticker or r["ticker"] == _ticker(ticker))
            and (not date or r.get("target_date") == _date(date))
            and (not start_date or (r.get("target_date") or "") >= _date(start_date))
            and (not end_date or (r.get("target_date") or "9999") <= _date(end_date))
            and (not status or status == r["status"] or status == ("complete" if r["complete"] else "incomplete"))]
        selected.sort(key=lambda r: (r["captured_at"], r["plan_id"]), reverse=True)
        return _page(selected, limit, offset)

    def get_plan(self, plan_id, revision=None):
        rows = [r for r in self._all("snapshots") if r.get("kind") == "journal_plan" and r.get("plan_id") == plan_id]
        if revision is not None:
            if type(revision) is not int:
                raise TradeJournalError("计划revision必须为整数")
            rows = [r for r in rows if r["revision"] == revision]
        if not rows:
            raise TradeJournalError("独立计划或确切版本不存在")
        selected = max(rows, key=lambda r: r["revision"])
        return self.get_snapshot(selected["snapshot_id"], include_content=True)

    @_serialized
    def bind_plan(self, plan_id, episode_id, payload):
        if type(payload.get("revision")) is not int or payload["revision"] < 1:
            raise TradeJournalError("绑定必须提供确切计划revision正整数，不得隐式选择版本")
        plan = self.get_plan(plan_id, payload.get("revision"))
        detail = self.get_episode(episode_id)
        if detail["episode"]["ticker"] != plan["ticker"]:
            raise TradeJournalError("计划与成交证券不同")
        fill_ids = list(dict.fromkeys(payload.get("fill_ids") or []))
        if not fill_ids or not set(fill_ids) <= {r["fill_id"] for r in detail["fills"]}:
            raise TradeJournalError("绑定须明确选择该库存轮的成交，不能自动推断已按计划执行")
        content = plan["content"]
        fields = ("mode", "mode_id", "trigger", "trigger_id", "expectation", "verification_at", "invalidation", "holding_horizon", "risk_constraints", "reference_tickers", "definition_refs")
        decision = {key: deepcopy(content.get(key)) for key in fields}
        decision.update(action=payload.get("action") or "关联已冻结计划", action_id=payload.get("action_id") or "action.watch",
            reason=payload.get("reason") or "补记与独立冻结计划的关联；不据此宣称已按计划执行",
            evidence=deepcopy(content.get("evidence") or []), evidence_class="retrospective_recall", observed_at=self._now(),
            fill_ids=fill_ids, frozen_plan_ref={"plan_id": plan_id, "revision": plan["revision"]})
        return self.append_decision(episode_id, decision)

    @_serialized
    def append_observation(self, payload):
        """Freeze an exact candidate cohort separately from real transactions."""
        now = self._now()
        scope = payload.get("scope") or "manual_watchlist"
        if scope not in {"manual_watchlist", "strategy_cohort"}:
            raise TradeJournalError("观察范围必须为人工观察池或策略候选群体")
        state = payload.get("state") or "watching"
        if state not in {"watching", "not_bought", "not_triggered", "unfilled", "abandoned", "removed"}:
            raise TradeJournalError("观察状态无效")
        members = deepcopy(payload.get("members") or [])
        if not isinstance(members, list) or any(not isinstance(row, dict) for row in members):
            raise TradeJournalError("观察成员必须为对象列表")
        ticker = _ticker(payload["ticker"]) if payload.get("ticker") else None
        if ticker is None and not members and scope != "strategy_cohort":
            raise TradeJournalError("观察必须有证券或完整成员列表")
        observed = _iso(payload.get("observed_at") or now)
        available = _iso(payload["available_at"]) if payload.get("available_at") else None
        if _time(observed) > _time(now):
            raise TradeJournalError("观察发生时间不能在未来")
        if available and (_time(available) < _time(observed) or _time(available) > _time(now)):
            raise TradeJournalError("观察可用时间须在发生时间之后且不晚于保存时间")
        definitions = self._definitions(payload)
        snapshot_ref, scope_metadata, source_ref = None, None, deepcopy(payload.get("source_ref"))
        if scope == "strategy_cohort":
            from shilun.decision_research.service import load_mode_research
            from .research_bridge import freeze_strategy_cohort, ResearchBridgeError
            ref = payload.get("mode_research_ref") or {}
            research = load_mode_research(ref.get("analysis_date"), ref.get("snapshot_id"))
            try:
                frozen = freeze_strategy_cohort(research, mode_research_ref=ref, source_ref=source_ref, members=members)
            except (ResearchBridgeError, TypeError, KeyError) as error:
                raise TradeJournalError(str(error)) from error
            if _time(research["built_at"]) > _time(now):
                raise TradeJournalError("研究快照在未来，不能归档为当前已知候选")
            members, source_ref, scope_metadata = frozen["members"], frozen["source_ref"], frozen["scope_metadata"]
            snapshot_id = "snapshot-" + frozen["content_hash"]
            snapshot = {"snapshot_id": snapshot_id, "kind": "strategy_cohort", "content": frozen["content"],
                "content_digest": frozen["content_hash"], "mode_research_ref": frozen["mode_research_ref"],
                "source_ref": source_ref, "scope_metadata": scope_metadata, "recorded_at": now}
            snapshot["content_hash"] = digest({k: v for k, v in snapshot.items() if k != "recorded_at"})
            self._insert("snapshots", {"snapshot_id": snapshot_id}, snapshot)
            snapshot_ref = {"snapshot_id": snapshot_id, "content_hash": frozen["content_hash"], "kind": "strategy_cohort"}
        elif payload.get("mode_research_ref"):
            raise TradeJournalError("人工观察池不能声明完整研究引用，请使用strategy_cohort精确归档")
        parent_id = payload.get("parent_observation_id")
        if parent_id and not self._find("observations", {"observation_id": parent_id}):
            raise TradeJournalError("父观察记录不存在")
        declared = _evidence_class(payload.get("evidence_class"))
        # The full daily cohort contains feedback outcomes and was frozen now.
        # Its complete denominator never implies formation-day foreknowledge.
        evidence_class = "historical_reconstruction" if scope == "strategy_cohort" else (
            "retrospective_recall" if declared == "contemporaneous" and _time(observed) < _time(now) else declared)
        record = {"observation_id": str(payload.get("observation_id") or "observation-" + uuid.uuid4().hex),
            "ticker": ticker, "name": payload.get("name"), "scope": scope, "state": state,
            "members": members if scope == "manual_watchlist" else None,
            "member_tickers": [row.get("ticker") for row in members if row.get("ticker")],
            "member_count": len(members), "cohort_digest": digest(members), "snapshot_ref": snapshot_ref,
            "scope_metadata": scope_metadata, "reason": payload.get("reason"),
            "observed_at": observed, "recorded_at": now, "available_at": available,
            "evidence_class": evidence_class, "declared_evidence_class": declared, "source_ref": source_ref,
            "mode_research_ref": deepcopy(payload.get("mode_research_ref")), "definition_refs": deepcopy(payload.get("definition_refs") or []),
            "definition_snapshots": definitions["resolved"], "definitions_version": definitions["version"],
            "parent_observation_id": parent_id,
            "denominator_note": "仅代表这份人工观察名单" if scope == "manual_watchlist" else "与服务端冻结群体逐项相等；不等于全市场、事前或可成交样本"}
        record["content_hash"] = digest(record)
        saved, _ = self._insert("observations", {"observation_id": record["observation_id"]}, record)
        return {"record": saved}

    def list_observations(self, query="", start_date=None, end_date=None, limit=100, offset=0):
        rows = [r for r in self._all("observations") if (not query or query.casefold() in f'{r.get("ticker")} {r.get("name")} {" ".join(r.get("member_tickers") or [])}'.casefold())
            and (not start_date or r["observed_at"][:10] >= _date(start_date)) and (not end_date or r["observed_at"][:10] <= _date(end_date))]
        rows.sort(key=lambda r: (r["observed_at"], r["recorded_at"]), reverse=True)
        return _page(rows, limit, offset)

    def get_observation(self, observation_id):
        record = self._find("observations", {"observation_id": observation_id}, include_content=True)
        if record is None:
            raise TradeJournalError("观察记录不存在")
        return record

    def get_snapshot(self, snapshot_id, *, include_content=False):
        record = self._find("snapshots", {"snapshot_id": snapshot_id}, include_content=include_content)
        if record is None:
            raise TradeJournalError("冻结快照不存在")
        if include_content:
            if digest(record["content"]) != record["content_digest"] or digest({k: v for k, v in record.items() if k not in {"recorded_at", "content_hash"}}) != record["content_hash"]:
                raise TradeJournalError("冻结快照内容校验失败")
        return record
