"""Explicit user process reviews, separate from outcomes and strategy validation."""
from copy import deepcopy

from bson import BSON
from bson.errors import BSONError
from pymongo.errors import DuplicateKeyError

from .ledger import digest
from .service import TradeJournalService, TradeJournalError, _clean, _source_ref

COLLECTION = "trade_journal_reviews"
VERSION = "trade-journal-reviews-v1"
AXES = {"entry": "入场", "hold": "持有", "exit": "退出", "adjustment": "加减仓", "sizing": "仓位", "information": "信息质量"}
JUDGMENTS = {"unknown", "as_planned", "deviation", "not_applicable"}


class JournalReviewError(TradeJournalError):
    pass


def review_fact_context(episode, fills, decisions, checks=(), financial_events=()):
    """Canonical references to one real episode and exact current input versions."""
    episode_fields = ("episode_id", "account_alias", "ticker", "status", "quantity", "opening_quantity",
        "opening_cost_status", "opening_balance_verified", "unknown_fees", "inventory_valid", "buy_cost",
        "sell_proceeds", "closed_pnl", "unknown_cost_sold_quantity", "ledger_version", "matches")
    ids = {row["decision_id"] for row in decisions}
    reference = {"episode_id": episode["episode_id"], "ledger_version": episode["ledger_version"],
        "episode_digest": digest({key: episode.get(key) for key in episode_fields}),
        "fill_refs": [{"fill_id": row["fill_id"], "content_hash": row["content_hash"]} for row in sorted(fills, key=lambda item: item["fill_id"])],
        "financial_refs": [{"financial_event_id": row["financial_event_id"], "content_hash": row["content_hash"]}
            for row in sorted(financial_events, key=lambda item: item["financial_event_id"]) if row["financial_event_id"] in episode["financial_event_ids"]],
        "decision_refs": [{"decision_id": row["decision_id"], "revision": row["revision"], "content_hash": row["content_hash"]}
            for row in sorted(decisions, key=lambda item: item["decision_id"])],
        "expectation_check_refs": [{"check_id": row["check_id"], "decision_id": row["decision_id"],
            "decision_revision": row["decision_revision"], "content_hash": row["content_hash"]}
            for row in sorted(checks, key=lambda item: item["check_id"]) if row["decision_id"] in ids]}
    return {"fact_digest": digest(reference), "facts_reference": reference}


def read_review_records(store, episode_id=None):
    query = {"episode_id": episode_id} if episode_id else {}
    rows = [_clean(row) for row in store.collection(COLLECTION).find(query, {"_id": 0})]
    for row in rows:
        if digest({key: value for key, value in row.items() if key != "content_hash"}) != row.get("content_hash"):
            raise JournalReviewError("不可变过程复盘记录校验失败")
    return rows


def latest_review_records(rows):
    heads = {}
    for row in rows:
        if row["episode_id"] not in heads or row["revision"] > heads[row["episode_id"]]["revision"]:
            heads[row["episode_id"]] = row
    return heads


def project_review(record, context):
    state = "missing" if record is None else record["state"]
    if record is not None and record["fact_digest"] != context["fact_digest"]:
        state = "stale"
    return {"current_state": state, "current_confirmed": state == "confirmed",
        "review_id": record["review_id"] if record else None, "revision": record["revision"] if record else 0,
        "stored_state": record["state"] if record else None,
        "original_fact_digest": record["fact_digest"] if record else None,
        "fact_digest": context["fact_digest"]}


def review_progress(record, context=None, *, saved_note_count=0):
    """A visible completion marker, derived from the existing review contract.

    Only an explicit user-confirmed review of the current exact facts is
    complete. Saved decision notes show that review has started, but market
    checks, profits, and dismissed tasks do not grant completion. Unknown
    judgments may remain in a confirmed review.
    """
    projection = project_review(record, context) if record else {
        "current_state": "missing", "current_confirmed": False,
        "review_id": None, "revision": 0,
    }
    state = projection["current_state"]
    status, label, note = {
        "missing": ("not_started", "未复盘", "尚未保存本轮过程复盘"),
        "draft": ("in_progress", "复盘中", "已保存草稿，尚未确认完成"),
        "stale": ("needs_update", "待更新", "成交、预期或核对结果已变化，请重新确认复盘"),
        "confirmed": ("reviewed", "已复盘", "已保存并确认当前记录的过程复盘；未知判断仍可保留"),
    }[state]
    has_saved_notes = saved_note_count > 0
    if state == "missing" and has_saved_notes:
        status, label = "in_progress", "已填写，待确认"
        note = f"已保存{saved_note_count}条操作复盘记录；尚未确认完成本次复盘；写一句总结并勾选确认后即可完成"
    return {"status": status, "label": label, "note": note,
        "has_saved_notes": has_saved_notes, "saved_note_count": saved_note_count,
        "current_state": state, "current_confirmed": projection["current_confirmed"],
        "review_id": projection["review_id"], "revision": projection["revision"],
        "scope": "复盘完成状态，不代表交割单完整、结论已证明或模式已验证"}


class JournalReviewService:
    def __init__(self, store, *, journal=None):
        self.store = store
        self.journal = journal or TradeJournalService(store)
        self.collection = store.collection(COLLECTION)

    def _context(self, episode_id):
        ledger = self.journal._ledger()
        episode = next((row for row in ledger["episodes"] if row["episode_id"] == episode_id), None)
        if episode is None:
            raise JournalReviewError("交易档案不存在")
        decisions = self.journal._latest_decisions(episode_id)
        context = review_fact_context(episode, [row for row in ledger["fills"] if row["episode_id"] == episode_id],
            decisions, self.journal._all("expectation_checks"), self.journal._facts()[1])
        return episode, context

    def history(self, episode_id):
        if not any(row["episode_id"] == episode_id for row in self.journal._ledger()["episodes"]):
            raise JournalReviewError("交易档案不存在")
        rows = sorted(read_review_records(self.store, episode_id), key=lambda row: row["revision"])
        return {"episode_id": episode_id, "items": rows, "total": len(rows), "latest_revision": rows[-1]["revision"] if rows else 0}

    def get_review(self, episode_id, revision=None):
        _, context = self._context(episode_id)
        rows = self.history(episode_id)["items"]
        head = rows[-1] if rows else None
        selected = head
        if revision is not None:
            if type(revision) is not int or revision < 1:
                raise JournalReviewError("过程复盘revision须为明确正整数")
            selected = next((row for row in rows if row["revision"] == revision), None)
            if selected is None:
                raise JournalReviewError("过程复盘或确切版本不存在")
        return {"episode_id": episode_id, "record": selected, **project_review(head, context),
            "fact_digest": context["fact_digest"], "facts_reference": context["facts_reference"],
            "latest_revision": head["revision"] if head else 0,
            "selected_revision": selected["revision"] if selected else None,
            "state_scope": "current_state表示最新复盘版本与当前事实是否一致；旧record保持原文，不重写原判断",
            "qualification_note": "confirmed仅表示用户实际保存并确认复盘，不证明结论、因果、违规或收益优势已被验证"}

    def _evidence(self, rows, now, cache):
        if rows is None:
            rows = []
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            raise JournalReviewError("复盘证据须为{text,source_ref/snapshot_ref}对象列表，不能用字符串冒充来源")
        result = []
        for raw in rows:
            if raw.get("text") is not None and not isinstance(raw["text"], str):
                raise JournalReviewError("复盘证据原文text须为文字")
            item = {"text": raw.get("text"), "provenance_class": "user_process_review", "captured_at": now}
            if raw.get("source_ref"):
                ref = _source_ref({"source_ref": raw["source_ref"]})
                if not isinstance(ref["source_id"], str):
                    raise JournalReviewError("复盘来源身份须为明确source_id字符串")
                key = ("source", ref["source_id"])
                if key not in cache:
                    try:
                        source = self.journal.get_source(ref["source_id"], include_content=True)
                    except TradeJournalError as error:
                        raise JournalReviewError("复盘来源不可恢复："+str(error)) from error
                    cache[key] = {field: source.get(field) for field in ("source_id", "sha256", "content_hash", "source_type", "recorded_at")}
                saved = cache[key]
                if ref.get("content_hash") and ref["content_hash"] != saved["content_hash"]:
                    raise JournalReviewError("复盘来源与确切内容hash不一致")
                item["source_ref"] = {**ref, **saved}
            if raw.get("snapshot_ref"):
                ref = raw["snapshot_ref"]
                if not isinstance(ref, dict) or not isinstance(ref.get("snapshot_id"), str) or not ref["snapshot_id"]:
                    raise JournalReviewError("复盘快照引用须提供snapshot_id")
                if ref.get("content_hash") is not None and not isinstance(ref["content_hash"], str):
                    raise JournalReviewError("复盘快照hash须为明确字符串")
                key = ("snapshot", ref["snapshot_id"])
                if key not in cache:
                    try:
                        if self.journal._find("snapshots", {"snapshot_id": ref["snapshot_id"]}):
                            snapshot = self.journal.get_snapshot(ref["snapshot_id"], include_content=True)
                            collection = "trade_journal_snapshots"
                        else:
                            from .analytics import PortfolioRiskService, PORTFOLIO_COLLECTION
                            snapshot = PortfolioRiskService(self.store, journal=self.journal).get_snapshot(ref["snapshot_id"], include_content=True)
                            collection = PORTFOLIO_COLLECTION
                    except TradeJournalError as error:
                        raise JournalReviewError("复盘快照不可恢复："+str(error)) from error
                    cache[key] = {"snapshot_id": snapshot["snapshot_id"], "content_hash": snapshot["content_hash"],
                        "content_digest": snapshot.get("content_digest"), "collection": collection, "kind": snapshot.get("kind")}
                saved = cache[key]
                if ref.get("content_hash") and ref["content_hash"] not in {saved["content_hash"], saved.get("content_digest")}:
                    raise JournalReviewError("复盘快照与确切内容hash不一致")
                item["snapshot_ref"] = deepcopy(saved)
            if not item.get("text") and "source_ref" not in item and "snapshot_ref" not in item:
                raise JournalReviewError("复盘证据须保留原文或可恢复来源")
            item["restoration_verified"] = "source_ref" in item or "snapshot_ref" in item
            result.append(item)
        return result

    def save_review(self, episode_id, payload):
        if not isinstance(payload, dict) or payload.get("state") not in {"draft", "confirmed"}:
            raise JournalReviewError("过程复盘state只能为draft或confirmed")
        if type(payload.get("expected_revision")) is not int or payload["expected_revision"] < 0:
            raise JournalReviewError("过程复盘须提交明确非负expected_revision")
        if not isinstance(payload.get("expected_fact_digest"), str) or not payload["expected_fact_digest"]:
            raise JournalReviewError("过程复盘须提交读取时的expected_fact_digest")
        summary = payload.get("summary") or ""
        if not isinstance(summary, str) or payload["state"] == "confirmed" and not summary.strip():
            raise JournalReviewError("确认过程复盘须保留用户实际填写的summary")
        if payload.get("overall_conclusion") is not None and not isinstance(payload["overall_conclusion"], str):
            raise JournalReviewError("过程复盘总判断须保留为原文或unknown")
        judgments = payload.get("judgments") or {}
        if not isinstance(judgments, dict) or set(judgments)-set(AXES):
            raise JournalReviewError("过程复盘仅支持入场/持有/退出/加减仓/仓位/信息质量六维")
        with self.journal.write_guard():
            _, context = self._context(episode_id)
            previous = self.history(episode_id)["items"]
            latest = previous[-1] if previous else None
            revision = latest["revision"] if latest else 0
            if payload["expected_revision"] != revision:
                raise JournalReviewError("过程复盘版本已变化，请读取最新版本后追加")
            if payload["expected_fact_digest"] != context["fact_digest"]:
                raise JournalReviewError("过程复盘事实指纹冲突，原事实或决策已变化，请重新核对")
            if latest and (not isinstance(payload.get("revision_reason"), str) or not payload["revision_reason"].strip()):
                raise JournalReviewError("过程复盘追加修订须填写revision_reason")
            now, cache = self.journal._now(), {}
            normalized = {}
            for axis, label in AXES.items():
                raw = judgments.get(axis)
                if raw is None:
                    normalized[axis] = {"label": label, "judgment": "unknown", "reason": None,
                        "evidence_refs": [], "user_supplied": False, "machine_verified": False}
                    continue
                if not isinstance(raw, dict) or raw.get("judgment", "unknown") not in JUDGMENTS:
                    raise JournalReviewError("各维判断须明确为unknown/as_planned/deviation/not_applicable")
                if raw.get("reason") is not None and not isinstance(raw["reason"], str):
                    raise JournalReviewError("各维判断理由须保留为原文")
                normalized[axis] = {"label": label, "judgment": raw.get("judgment") or "unknown", "reason": raw.get("reason"),
                    "evidence_refs": self._evidence(raw.get("evidence_refs"), now, cache),
                    "user_supplied": True, "machine_verified": False}
            record = {"review_id": "review-"+digest(["process-review", episode_id])[:28], "episode_id": episode_id,
                "revision": revision+1, "previous_revision": revision or None, "version": VERSION,
                "state": payload["state"], "summary": summary, "overall_conclusion": payload.get("overall_conclusion") or "unknown",
                "judgments": normalized, "evidence_refs": self._evidence(payload.get("evidence_refs"), now, cache),
                "fact_digest": context["fact_digest"], "facts_reference": context["facts_reference"],
                "decision_refs": context["facts_reference"]["decision_refs"], "recorded_at": now, "captured_at": now,
                "revision_reason": payload.get("revision_reason"), "evidence_class": "user_process_review",
                "machine_verified": False, "qualification_effect": "none",
                "confirmation_scope": "仅确认用户已保存本次过程复盘；未知结论保留，不推断违规、因果或模式优势"}
            record["content_hash"] = digest(record)
            try:
                if len(BSON.encode(record)) > 15*1024*1024:
                    raise JournalReviewError("过程复盘超过15MiB预算，不截断证据")
            except (BSONError, TypeError, OverflowError) as error:
                raise JournalReviewError("过程复盘不是有效可持久化结构") from error
            self.journal.renew_write_lease()
            key = {"episode_id": episode_id, "revision": revision+1}
            try:
                self.collection.update_one(key, {"$setOnInsert": deepcopy(record)}, upsert=True)
            except DuplicateKeyError:
                pass
            saved = _clean(self.collection.find_one(key, {"_id": 0}))
            if saved.get("content_hash") != record["content_hash"]:
                raise JournalReviewError("过程复盘并发版本冲突，旧记录未被覆盖")
            return {"record": saved, "review": self.get_review(episode_id)}


__all__ = ["JournalReviewService", "JournalReviewError", "COLLECTION", "VERSION", "AXES",
    "review_fact_context", "read_review_records", "latest_review_records", "project_review"]
