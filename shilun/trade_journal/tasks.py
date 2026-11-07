"""Projected missing-data tasks with append-only, fingerprint-scoped resolutions.

A resolution acknowledges a gap. It never edits evidence, accounting, plans or
qualification, and a changed relevant fact cannot inherit an old dismissal.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from copy import deepcopy

from bson import BSON
from bson.errors import BSONError
from pymongo.errors import DuplicateKeyError

from .ledger import digest
from .reviews import review_fact_context, read_review_records, latest_review_records, project_review
from .service import TradeJournalService, TradeJournalError, _clean, _date, _time, _page, _specified

COLLECTION = "trade_journal_task_events"
VERSION = "trade-journal-tasks-v1"
STATES = {"open", "confirmed_unknown", "ignored", "resolved"}
ACTIONS = {"confirm_unknown": "confirmed_unknown", "ignore": "ignored", "reopen": "open"}
ROOTS = {
    "accounting": {"category": "reconciliation", "title": "待核对成交、库存与成本",
        "impact": ["会计核算资格", "完整清仓收益"],
        "hint": "打开原件和逐笔库存，补充可核实的成本、费用或缺单；无法恢复时可确认未知，不编造金额。"},
    "decision_context": {"category": "attribution", "title": "待补理由、确认模式与参照",
        "impact": ["决策复盘资格", "按意图模式与参照分析"],
        "hint": "保留当时原意和独立理由，明确模式、触发与参照；无法回忆可确认未知，不能从结果反推动机。"},
    "plan_conditions": {"category": "plan", "title": "待补预期、验证与风险条件",
        "impact": ["计划完整度", "预期及失效条件核验"],
        "hint": "补记已有原文和验证时点、失效、期限与风险条件；当时未设则确认未知，历史补记不变成事前计划。"},
    "evidence": {"category": "evidence", "title": "待核对证据与当时时点",
        "impact": ["事前可核验资格", "资料来源与可得时间"],
        "hint": "恢复确切来源、行号或记录定位和观察/可用时点；无法恢复则确认未知，晚到资料不能倒填。"},
    "plan_link": {"category": "plan_link", "title": "待明确成交与原计划的关联",
        "impact": ["原计划追溯", "每次成交的计划覆盖"],
        "hint": "若确有原计划，选择可恢复的确切版本与成交；当时未设或无法证明可确认未知，不新建旧日期计划冒充。"},
    "process_review": {"category": "process_review", "title": "待确认清仓后的过程复盘",
        "impact": ["用户是否已实际保存过程复盘", "原事实变化后的复盘重核"],
        "hint": "清仓后保存独立过程复盘，六维可如实保留unknown；确认未知或忽略待办不等于已完成复盘，也无需虚构验证时点。"},
    "review": {"category": "review", "title": "待核对已到时点的预期",
        "impact": ["原版本预期反馈", "复盘是否已实际记录"],
        "hint": "对原决策版本追加人工或归档数值核对；没有可用事实可如实记录unknown，不用盈亏替代预期反馈。"},
}

FIELD_LABELS = {"mode": "模式", "trigger": "触发", "reason": "独立理由", "reference_tickers": "参照证券",
    "expectation": "预期", "verification_at": "验证时点", "invalidation": "失效条件",
    "holding_horizon": "持有期限", "risk_constraints": "风险约束"}
ROOT_DECISION_FIELDS = {
    "decision_context": ("mode", "mode_id", "trigger", "trigger_id", "reason", "reference_tickers"),
    "plan_conditions": ("expectation", "verification_at", "invalidation", "holding_horizon", "risk_constraints", "frozen_plan_ref"),
}


def _decision_value(row, root):
    if root in ROOT_DECISION_FIELDS:
        return {key: deepcopy(row.get(key)) for key in ROOT_DECISION_FIELDS[root]}
    if root == "evidence":
        return {"evidence_class": row.get("evidence_class"), "temporal_errors": row.get("temporal_errors") or [],
            "evidence": [{key: deepcopy(value) for key, value in item.items() if key not in {"captured_at", "content_hash"}}
                if isinstance(item, dict) else deepcopy(item) for item in row.get("evidence") or []]}
    if root == "plan_link":
        return {"fill_ids": sorted(row.get("fill_ids") or []), "frozen_plan_ref": row.get("frozen_plan_ref"),
            "plan_id": (row.get("frozen_plan") or {}).get("plan_id")}
    raise JournalTaskError("未知待办根因")


def _data_versions(revisions, root):
    # Relevant values returning to an earlier value still represent new facts.
    # Unrelated note-only revisions do not invalidate another root's dismissal.
    states = {}
    for row in sorted(revisions, key=lambda item: (item["decision_id"], item["revision"])):
        fingerprint = digest(_decision_value(row, root))
        previous = states.get(row["decision_id"])
        changed_revision = row["revision"] if previous is None or previous[0] != fingerprint else previous[1]
        states[row["decision_id"]] = (fingerprint, changed_revision)
    return [{"decision_id": identifier, "relevant_data_revision": value[1]}
        for identifier, value in sorted(states.items())]


class JournalTaskError(TradeJournalError):
    pass


def task_id_for(episode_id, root_cause):
    return "task-" + digest(["journal-missing-root", episode_id, root_cause])[:32]


class JournalTaskService:
    def __init__(self, store, *, journal=None):
        self.store = store
        self.journal = journal or TradeJournalService(store)
        self.collection = store.collection(COLLECTION)

    def _events(self):
        rows = [_clean(row) for row in self.collection.find({}, {"_id": 0})]
        for row in rows:
            if digest({key: value for key, value in row.items() if key != "content_hash"}) != row.get("content_hash"):
                raise JournalTaskError("不可变待办处置记录校验失败")
        return rows

    def _facts(self):
        """One shared ledger/qualification pass, without per-episode live reads."""
        ledger = self.journal._ledger()
        decisions = self.journal._all("decisions")
        checks = self.journal._all("expectation_checks")
        financial = self.journal._facts()[1]
        review_records = read_review_records(self.store)
        review_heads = latest_review_records(review_records)
        fills_by_episode = defaultdict(list)
        for row in ledger["fills"]:
            fills_by_episode[row["episode_id"]].append(row)
        now = _time(self.journal._now())
        facts_digest = digest({"ledger_version": ledger["version"],
            "fills": sorted((row["fill_id"], row.get("content_hash")) for row in ledger["fills"]),
            "financial_events": sorted((row["financial_event_id"], row.get("content_hash")) for row in financial),
            "decisions": sorted((row["decision_id"], row["revision"], row.get("content_hash")) for row in decisions),
            "expectation_checks": sorted((row["check_id"], row.get("content_hash")) for row in checks),
            "process_reviews": sorted((row["review_id"], row["revision"], row["content_hash"]) for row in review_records)})
        self._facts_reference = {"ledger_version": ledger["version"], "facts_digest": facts_digest,
            "committed_batch_ids": sorted(row["batch_id"] for row in self.journal._committed_batches()),
            "fill_count": len(ledger["fills"]), "financial_event_count": len(financial),
            "decision_revision_count": len(decisions), "expectation_check_count": len(checks), "process_review_revision_count": len(review_records),
            "computed_at": now.isoformat(), "scope": "当前已提交FIFO事实及决策/核对版本hash；不包括来源大正文或当前生产账户"}
        result = {}
        for episode in ledger["episodes"]:
            episode_id = episode["episode_id"]
            fills = fills_by_episode[episode_id]
            revisions = [row for row in decisions if row["episode_id"] == episode_id]
            latest = sorted(self.journal._latest_decisions(episode_id, revisions), key=lambda row: row["decision_id"])
            qualification, missing = self.journal._qualification(episode, fills, latest, revisions)
            intent_fields = [field for field in missing if field in {"mode", "trigger", "reason", "reference_tickers"}]
            plan_fields = [field for field in missing if field in {"expectation", "verification_at", "invalidation", "holding_horizon", "risk_constraints"}]
            temporal = sorted({message for row in latest for message in row.get("temporal_errors", [])})
            linked_fills = set()
            for row in latest:
                plan = row.get("frozen_plan") or {}
                ref = row.get("frozen_plan_ref") or {}
                if isinstance(plan, dict) and plan.get("plan_id") or isinstance(ref, dict) and ref.get("plan_id"):
                    linked_fills.update(row.get("fill_ids") or [])
            unlinked = sorted(row["fill_id"] for row in fills if row["fill_id"] not in linked_fills)
            due = []
            for row in latest:
                if not _specified(row.get("expectation")) or not row.get("verification_at"):
                    continue
                if _time(row["verification_at"]) > now:
                    continue
                reviewed = any(item["decision_id"] == row["decision_id"] and item["decision_revision"] == row["revision"] for item in checks)
                if not reviewed:
                    due.append({"decision_id": row["decision_id"], "revision": row["revision"],
                        "verification_at": row["verification_at"], "expectation": deepcopy(row["expectation"])})
            process_context = review_fact_context(episode, fills, latest, checks, financial)
            process = project_review(review_heads.get(episode_id), process_context)
            process_missing = episode["status"] == "closed" and not process["current_confirmed"]
            process_reason = ("原过程复盘引用的事实或决策版本已变化，需重核并追加确认" if process["current_state"] == "stale" else
                "过程复盘仍为草稿，尚未由用户实际确认" if process["current_state"] == "draft" else
                "库存轮已清仓，尚无当前已确认的用户过程复盘；无需虚构预期验证时点")
            root_specs = {
                "accounting": {"active": not qualification["accounting"]["eligible"],
                    "missing_fields": ["accounting"] if not qualification["accounting"]["eligible"] else [], "reasons": qualification["accounting"]["reasons"],
                    "facts": {"qualification": qualification["accounting"],
                        "opening_cost_status": episode["opening_cost_status"], "opening_quantity": episode["opening_quantity"],
                        "fill_facts": [{key: row.get(key) for key in ("fill_id", "content_hash", "quantity", "fees", "net_cash", "reported_quantity_after")} for row in fills],
                        "financial_refs": [{"financial_event_id": row["financial_event_id"], "content_hash": row["content_hash"]} for row in sorted(financial, key=lambda item: item["financial_event_id"]) if row["financial_event_id"] in episode["financial_event_ids"]]}},
                "decision_context": {"active": bool(intent_fields), "missing_fields": intent_fields,
                    "reasons": ["仍缺当时模式、触发、独立理由或参照："+"、".join(FIELD_LABELS[field] for field in intent_fields)] if intent_fields else [],
                    "facts": [{key: row.get(key) for key in ("decision_id", "mode", "mode_id", "trigger", "trigger_id", "reason", "reference_tickers")} for row in latest]},
                "plan_conditions": {"active": bool(plan_fields), "missing_fields": plan_fields,
                    "reasons": ["原计划条件尚未明确："+"、".join(FIELD_LABELS[field] for field in plan_fields)] if plan_fields else [],
                    "facts": [{key: row.get(key) for key in ("decision_id", "expectation", "verification_at", "invalidation", "holding_horizon", "risk_constraints", "frozen_plan_ref")} for row in latest]},
                "evidence": {"active": "evidence" in missing or bool(temporal),
                    "missing_fields": (["evidence"] if "evidence" in missing else []) + (["evidence_time_or_provenance"] if temporal else []),
                    "reasons": (["尚缺可定位的原始证据"] if "evidence" in missing else [])+temporal,
                    "facts": [{"decision_id": row["decision_id"], **_decision_value(row, "evidence")} for row in latest]},
                "plan_link": {"active": bool(unlinked), "missing_fields": ["plan_reference"] if unlinked else [],
                    "reasons": [f"{len(unlinked)}次成交尚未明确关联可追溯的原计划；不据此假设当时存在计划"] if unlinked else [],
                    "facts": {"unlinked_fill_ids": unlinked, "links": [{"decision_id": row["decision_id"],
                        "fill_ids": row.get("fill_ids"), "frozen_plan_ref": row.get("frozen_plan_ref"),
                        "plan_id": (row.get("frozen_plan") or {}).get("plan_id")} for row in latest]}},
                "process_review": {"active": process_missing, "missing_fields": ["confirmed_process_review"] if process_missing else [],
                    "reasons": [process_reason] if process_missing else [],
                    "data_reopened": process_missing and process["current_state"] == "stale" and process["stored_state"] == "confirmed",
                    "facts": {"review": process, "facts_reference": process_context["facts_reference"]}},
                "review": {"active": bool(due), "missing_fields": ["expectation_review"] if due else [],
                    "reasons": [f"{len(due)}个原版本的验证时点已到，尚无该版本核对记录"] if due else [],
                    "facts": due},
            }
            for root, spec in root_specs.items():
                if root in {"decision_context", "plan_conditions", "evidence", "plan_link"}:
                    spec["facts"] = {"values": spec["facts"], "data_versions": _data_versions(revisions, root)}
                identifier = task_id_for(episode_id, root)
                fingerprint = digest({"root_cause": root, "active": spec["active"],
                    "missing_fields": spec["missing_fields"], "facts": spec["facts"]})
                result[identifier] = {"task_id": identifier, "version": VERSION, "episode_id": episode_id,
                    "ticker": episode["ticker"], "name": episode["name"], "start_date": episode["start_date"], "end_date": episode["end_date"],
                    "root_cause": root, "category": ROOTS[root]["category"], "title": ROOTS[root]["title"],
                    "actual_fact_missing": spec["active"], "data_reopened": spec.get("data_reopened", False), "missing_fields": spec["missing_fields"],
                    "reasons": spec["reasons"], "impact": ROOTS[root]["impact"], "resolution_hint": ROOTS[root]["hint"],
                    "fact_fingerprint": fingerprint, "current_facts": spec["facts"], "qualification": qualification,
                    "source_status": "current_episode", "source_note": "复用当前FIFO、决策缺项和原版本核对事实；任务处置不改变资格"}
        return result

    @staticmethod
    def _project_state(task, history):
        latest = history[-1] if history else None
        changed = bool(latest and latest["fact_fingerprint"] != task["fact_fingerprint"])
        if not task["actual_fact_missing"]:
            state = "resolved"
        elif latest and not changed:
            state = ACTIONS[latest["action"]]
        else:
            state = "open"
        dismissed = latest and latest["action"] in {"confirm_unknown", "ignore"}
        data_reopened = task.get("data_reopened") and (latest is None or changed)
        automatic = state == "open" and task["actual_fact_missing"] and ((changed and dismissed) or data_reopened)
        return {**task, "state": state, "status": state, "revision": latest["revision"] if latest else 0,
            "last_resolution": {key: latest[key] for key in ("action", "reason", "recorded_at", "revision")} if latest else None,
            "auto_reopened": bool(automatic),
            "reopen_reason": ("已确认的过程复盘所依赖的原事实已变化，需重新核对" if task.get("data_reopened") else
                "相关事实或缺项已变化，旧确认/忽略不能覆盖本次缺口")
                if automatic else None}

    def _build(self):
        facts = self._facts()
        histories = defaultdict(list)
        for row in self._events():
            histories[row["task_id"]].append(row)
        for rows in histories.values():
            rows.sort(key=lambda row: row["revision"])
        # A late correction must not silently move old dispositions to a new
        # episode identity. Keep the old root visible as unavailable evidence.
        for identifier, history in histories.items():
            if identifier not in facts:
                old = deepcopy(history[-1]["task_snapshot"])
                old.update(actual_fact_missing=True, source_status="episode_not_available", qualification=None,
                    missing_fields=["episode_source"], reasons=["原库存轮不在当前账本，须核对来源及轮身份；旧处置不迁移到其他库存轮"],
                    current_facts={"episode_id": old["episode_id"], "source_status": "episode_not_available"},
                    fact_fingerprint=digest(["episode_not_available", old["episode_id"], old["root_cause"]]))
                facts[identifier] = old
        tasks = {identifier: self._project_state(task, histories[identifier]) for identifier, task in facts.items()}
        return tasks, histories

    def list_tasks(self, query="", start_date=None, end_date=None, status=None, episode_id=None, limit=100, offset=0):
        if status is not None and status not in STATES:
            raise JournalTaskError("待办状态必须为open/confirmed_unknown/ignored/resolved")
        tasks, histories = self._build()
        selected = [row for identifier, row in tasks.items() if (row["actual_fact_missing"] or histories[identifier])
            and (not query or query.casefold() in f'{row["ticker"]} {row["name"]} {row["title"]}'.casefold())
            and (not episode_id or row["episode_id"] == episode_id)
            and (not start_date or (row["end_date"] or "9999-12-31") >= _date(start_date))
            and (not end_date or row["start_date"] <= _date(end_date))]
        counts = dict(Counter(row["state"] for row in selected))
        groups = []
        for root, info in ROOTS.items():
            rows = [row for row in selected if row["root_cause"] == root]
            if rows:
                groups.append({"root_cause": root, "title": info["title"], "category": info["category"],
                    "count": len(rows), "state_counts": dict(Counter(row["state"] for row in rows))})
        visible = [row for row in selected if not status or row["state"] == status]
        visible.sort(key=lambda row: (row["state"] != "open", row["start_date"], row["task_id"]))
        compact = [{key: value for key, value in row.items() if key != "current_facts"} for row in visible]
        return {**_page(compact, limit, offset), "state_counts": {state: counts.get(state, 0) for state in sorted(STATES)},
            "root_groups": groups, "facts_reference": deepcopy(self._facts_reference), "scope": "同库存轮同根因稳定归并；确认未知/忽略仅处理当时缺口，不改事实或授予资格"}

    def get_task(self, task_id):
        tasks, histories = self._build()
        if task_id not in tasks:
            raise JournalTaskError("待办不存在")
        task = tasks[task_id]
        return {"task": {key: value for key, value in task.items() if key != "current_facts"},
            "current_facts": task["current_facts"], "history": histories[task_id], "latest_revision": task["revision"],
            "facts_reference": deepcopy(self._facts_reference)}

    def append_resolution(self, task_id, payload):
        if not isinstance(payload, dict) or payload.get("action") not in ACTIONS:
            raise JournalTaskError("处置action必须为confirm_unknown/ignore/reopen；resolved只能由真实数据补齐产生")
        if not isinstance(payload.get("reason"), str) or not payload["reason"].strip():
            raise JournalTaskError("处置必须填写明确reason并保留历史")
        if type(payload.get("expected_revision")) is not int or payload["expected_revision"] < 0:
            raise JournalTaskError("处置须提供读取时的非负整数expected_revision")
        if not isinstance(payload.get("expected_fingerprint"), str) or not payload["expected_fingerprint"]:
            raise JournalTaskError("处置须提供读取时的expected_fingerprint")
        with self.journal.write_guard():
            detail = self.get_task(task_id)
            task = detail["task"]
            if payload["expected_revision"] != task["revision"]:
                raise JournalTaskError("待办版本已变化，请读取最新版本后处置")
            if payload["expected_fingerprint"] != task["fact_fingerprint"]:
                raise JournalTaskError("待办事实指纹冲突，数据已变化，请刷新后处置")
            if not task["actual_fact_missing"]:
                raise JournalTaskError("待办已由事实补齐，无需人工处置；人工不能改写资格")
            revision = task["revision"]+1
            record = {"event_id": f"{task_id}-v{revision}", "task_id": task_id, "episode_id": task["episode_id"],
                "root_cause": task["root_cause"], "version": VERSION, "revision": revision,
                "previous_revision": task["revision"] or None, "action": payload["action"], "reason": payload["reason"].strip(),
                "recorded_at": self.journal._now(), "fact_fingerprint": task["fact_fingerprint"],
                "previous_state": task["state"], "state_at_recording": ACTIONS[payload["action"]],
                "facts_reference": detail["facts_reference"],
                "task_snapshot": {**task, "current_facts": detail["current_facts"]},
                "qualification_note": "只处置当前缺口，不修改原件、真实成交、计划或任何资格"}
            record["content_hash"] = digest(record)
            try:
                if len(BSON.encode(record)) > 15*1024*1024:
                    raise JournalTaskError("待办处置超过15MiB预算，不截断历史")
            except (BSONError, TypeError, OverflowError) as error:
                raise JournalTaskError("待办处置不是有效可持久化结构") from error
            self.journal.renew_write_lease()
            key = {"task_id": task_id, "revision": revision}
            try:
                self.collection.update_one(key, {"$setOnInsert": deepcopy(record)}, upsert=True)
            except DuplicateKeyError:
                pass
            saved = _clean(self.collection.find_one(key, {"_id": 0}))
            if saved.get("content_hash") != record["content_hash"]:
                raise JournalTaskError("待办并发版本冲突，原处置未被覆盖")
            updated = self.get_task(task_id)
            return {"record": saved, "task": updated["task"], "latest_revision": revision}


__all__ = ["JournalTaskService", "JournalTaskError", "COLLECTION", "VERSION", "task_id_for"]
