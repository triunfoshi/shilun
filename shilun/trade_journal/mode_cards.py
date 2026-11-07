"""Append-only user methods and registered experiments on the journal store.

Usage states are not evidence of an edge. Historical references require a
specific revision. This module never enables a strategy, executes a trade,
changes a source record, or manufactures a backtest from a manual label.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import date
import re
import uuid

from bson import BSON
from bson.errors import BSONError
from pymongo.errors import DuplicateKeyError

from shilun.strategies.registry import get_strategy
from shilun.strategies.contracts import StrategyError
from shilun.trade_journal.definitions import _definition, _source, validate_definition_references
from shilun.trade_journal.ledger import digest
from shilun.trade_journal.service import TradeJournalError, TradeJournalService

VERSION = "trade-journal-mode-cards-v1"
MODE_COLLECTION = "trade_journal_mode_cards"
HYPOTHESIS_COLLECTION = "trade_journal_hypotheses"
STATES = {"draft", "trial", "active", "retired"}
MODE_FIELDS = ("purpose", "allowed_environments", "blocked_environments", "entry_triggers", "expectation",
               "verification", "invalidation", "holding_horizon", "risk_constraints", "sources", "rationale")
PROTOCOL_FIELDS = ("question", "development_window", "validation_window", "universe", "comparison_group",
                   "exclusion_policy", "target_metrics", "entry_rule_refs", "exit_rule_refs", "execution_constraints")
# Optional references still change the experiment identity when adopted/removed.
# They are deliberately excluded from the mandatory-field completeness list.
PROTOCOL_IDENTITY_FIELDS = (*PROTOCOL_FIELDS, "mode_ref")
CONTROL_FIELDS = {"mode_id", "hypothesis_id", "expected_revision", "revision_reason", "revision", "version",
                  "rule_version", "recorded_at", "captured_at", "content_hash", "content_digest",
                  "definition_snapshot", "preregistration", "protocol_registered_at", "protocol_digest",
                  "parameter_attempts", "validation_results", "rule_snapshots", "mode_snapshot", "revisions"}


class ModeCardError(TradeJournalError):
    pass


def _clean(row):
    return {key: deepcopy(value) for key, value in (row or {}).items() if key != "_id"}


def _day(value, label):
    try:
        result = date.fromisoformat(value).isoformat()
    except (TypeError, ValueError) as error:
        raise ModeCardError(f"{label}须为YYYY-MM-DD") from error
    if value != result:
        raise ModeCardError(f"{label}须为YYYY-MM-DD")
    return result


def _window(value, label):
    if value in (None, "", {}):
        return None
    if not isinstance(value, dict):
        raise ModeCardError(f"{label}须含start/end")
    start, end = _day(value.get("start"), label + "开始"), _day(value.get("end"), label + "结束")
    if start > end:
        raise ModeCardError(f"{label}开始不能晚于结束")
    return {"start": start, "end": end}


def _page(rows, limit, offset):
    if type(limit) is not int or not 1 <= limit <= 1000 or type(offset) is not int or offset < 0:
        raise ModeCardError("分页须为1—1000条及非负offset")
    return {"items": rows[offset:offset+limit], "total": len(rows), "limit": limit, "offset": offset}


class ModeCardService:
    def __init__(self, store, *, now_factory=None):
        self.store = store
        self.journal = TradeJournalService(store, now_factory=now_factory)

    def _rows(self, collection, query):
        return [_clean(row) for row in self.store.collection(collection).find(query, {"_id": 0})]

    def _get(self, collection, identity_key, identity, revision):
        if not isinstance(identity, str) or not identity:
            raise ModeCardError("记录身份须为稳定字符串")
        if type(revision) is not int or revision < 1:
            raise ModeCardError("历史引用必须提供确切正整数revision，不静默选择latest")
        record = _clean(self.store.collection(collection).find_one({identity_key: identity, "revision": revision}, {"_id": 0}))
        if not record:
            raise ModeCardError("指定记录或确切版本不存在")
        if digest(record["content"]) != record["content_digest"] or digest({k: v for k, v in record.items() if k != "content_hash"}) != record["content_hash"]:
            raise ModeCardError("不可变模式/假设记录校验失败")
        return record

    def _current_list(self, collection, identity_key, query, status, limit, offset):
        if status is not None and (not isinstance(status, str) or status not in STATES):
            raise ModeCardError("未知实验状态")
        latest, revisions = {}, {}
        for row in self._rows(collection, {}):
            revisions.setdefault(row[identity_key], []).append(row["revision"])
            if row[identity_key] not in latest or row["revision"] > latest[row[identity_key]]["revision"]:
                latest[row[identity_key]] = row
        selected = [row for row in latest.values() if (not status or row["status"] == status)
                    and (not query or query.casefold() in str(row.get("name") or row.get("question") or "").casefold()
                         or query.casefold() in row[identity_key].casefold())]
        selected = [{**row, "revisions": sorted(revisions[row[identity_key]])} for row in selected]
        selected.sort(key=lambda row: (row["captured_at"], row[identity_key]), reverse=True)
        return _page(selected, limit, offset)

    def list_cards(self, query="", status=None, limit=100, offset=0):
        """The explicitly current catalogue; historical consumers use get_card."""
        return self._current_list(MODE_COLLECTION, "mode_id", query, status, limit, offset)

    def get_card(self, mode_id, revision):
        return self._get(MODE_COLLECTION, "mode_id", mode_id, revision)

    def list_hypotheses(self, query="", status=None, limit=100, offset=0):
        return self._current_list(HYPOTHESIS_COLLECTION, "hypothesis_id", query, status, limit, offset)

    def get_hypothesis(self, hypothesis_id, revision):
        return self._get(HYPOTHESIS_COLLECTION, "hypothesis_id", hypothesis_id, revision)

    def _prepare(self, collection, identity_key, prefix, payload):
        if not isinstance(payload, dict):
            raise ModeCardError("记录须为对象")
        identity = payload.get(identity_key) or prefix + uuid.uuid4().hex
        if not isinstance(identity, str) or not re.fullmatch(re.escape(prefix) + r"[A-Za-z0-9_-]{1,80}", identity):
            raise ModeCardError(f"稳定身份必须使用{prefix}命名空间")
        # The entire caller holds the same shared journal lease while re-reading.
        rows = self._rows(collection, {identity_key: identity})
        previous = max(rows, key=lambda row: row["revision"]) if rows else None
        expected = payload.get("expected_revision")
        if previous and (type(expected) is not int or expected != previous["revision"]):
            raise ModeCardError("版本已变化，必须提交读取时的expected_revision")
        if not previous and expected is not None and (type(expected) is not int or expected != 0):
            raise ModeCardError("新记录的expected_revision须为0或空")
        if previous and not str(payload.get("revision_reason") or "").strip():
            raise ModeCardError("每次修订或退役须填写revision_reason，旧版本不会删除")
        if previous:
            previous = self._get(collection, identity_key, identity, previous["revision"])
        content = {**deepcopy(previous["content"] if previous else {}),
                   **{key: deepcopy(value) for key, value in payload.items() if key not in CONTROL_FIELDS}}
        status = content.get("status") or "draft"
        if not isinstance(status, str) or status not in STATES:
            raise ModeCardError("状态只能为draft/trial/active/retired，均不代表策略优势已验证")
        content["status"] = status
        return identity, previous, content

    def _persist(self, collection, identity_key, record):
        try:
            digest(record)
            size = len(BSON.encode(record))
        except (BSONError, OverflowError, TypeError, ValueError) as error:
            raise ModeCardError("模式/假设记录不是可持久化的有限JSON结构") from error
        if size > 15 * 1024 * 1024:
            raise ModeCardError("模式/假设记录超过15MiB存储预算，禁止裁断历史")
        self.journal.renew_write_lease()
        coll = self.store.collection(collection)
        key = {identity_key: record[identity_key], "revision": record["revision"]}
        old = _clean(coll.find_one(key, {"_id": 0}))
        if old:
            if old.get("content_hash") != record["content_hash"]:
                raise ModeCardError("不可变版本冲突，原记录未覆盖")
            return old
        try:
            coll.insert_one(deepcopy(record))
        except DuplicateKeyError:
            pass
        saved = _clean(coll.find_one(key, {"_id": 0}))
        if not saved or saved.get("content_hash") != record["content_hash"]:
            raise ModeCardError("并发保存冲突，原记录未覆盖，请重新读取版本")
        return saved

    def save_card(self, payload):
        with self.journal.write_guard():
            mode_id, previous, content = self._prepare(MODE_COLLECTION, "mode_id", "mode.user.", payload)
            if not isinstance(content.get("name"), str) or not content["name"].strip():
                raise ModeCardError("模式卡需要name；其他未定义项可保持未知")
            strategy_id, strategy_version = content.get("strategy_id"), content.get("strategy_version")
            if bool(strategy_id) != bool(strategy_version):
                raise ModeCardError("策略绑定须同时提供strategy_id和确切strategy_version；人工试验可两项都空")
            unchanged_binding = previous and all(previous["content"].get(key) == content.get(key) for key in ("strategy_id", "strategy_version"))
            if strategy_id and not unchanged_binding:
                try:
                    strategy = get_strategy(strategy_id)
                except StrategyError as error:
                    raise ModeCardError(str(error)) from error
                if strategy.version != strategy_version or strategy.strategy_id != strategy_id:
                    raise ModeCardError("策略ID/版本与现有registry不一致，不静默绑定默认策略")
            revision = previous["revision"] + 1 if previous else 1
            now = self.journal._now()
            record = {"mode_id": mode_id, "name": content["name"], "revision": revision,
                      "version": f"mode-card-v{revision}", "rule_version": f"mode-card-v{revision}",
                      "previous_revision": previous["revision"] if previous else None,
                      "status": content["status"], "content": content, "content_digest": digest(content),
                      "captured_at": now, "recorded_at": now, "registry_version": VERSION,
                      "revision_reason": payload.get("revision_reason"),
                      "missing_fields": [key for key in MODE_FIELDS if not content.get(key)],
                      "advantage_verified": False,
                      "state_note": "仅为使用/实验状态；active不证明收益优势，也不启用自动策略"}
            record["content_hash"] = digest(record)
            saved = self._persist(MODE_COLLECTION, "mode_id", record)
            return {"mode_id": mode_id, "revision": revision, "record": saved,
                    "definition_snapshot": self.get_definition_snapshot(mode_id, revision)}

    def get_definition_snapshot(self, mode_id, revision=None, *, rule_version=None):
        if rule_version is not None:
            match = re.fullmatch(r"mode-card-v([1-9][0-9]*)", str(rule_version))
            if not match:
                raise ModeCardError("用户模式引用需要确切mode-card-vN版本")
            parsed = int(match.group(1))
            if revision is not None and revision != parsed:
                raise ModeCardError("用户模式revision与rule_version不一致")
            revision = parsed
        card = self.get_card(mode_id, revision)
        content = card["content"]
        definition = _definition(mode_id, card["name"], str(content.get("purpose") or "用户定义模式；条件尚未完整"),
            source=_source("shilun/trade_journal/mode_cards.py", "ModeCardService.get_card", card["rule_version"]),
            status="user_mode_definition", rule_version=card["rule_version"], axis="mode",
            parameters={"card_revision": card["revision"], "card_content": deepcopy(content),
                        "card_content_hash": card["content_hash"]},
            limitations=["用户模式及实验状态不证明优势，未指定规则/数据时不能机器判定或回测。",
                         "历史引用须带确切rule_version，不可用当前最新模式替换；退役保留旧定义。"])
        definition["source_record"] = {"collection": MODE_COLLECTION, "mode_id": mode_id,
                                       "revision": card["revision"], "content_hash": card["content_hash"]}
        return definition

    def _rule_refs(self, refs, previous_snapshots=()):
        if refs in (None, []):
            return []
        if not isinstance(refs, list):
            raise ModeCardError("规则引用须为带id/rule_version的数组")
        resolved = []
        for ref in refs:
            if not isinstance(ref, dict) or not isinstance(ref.get("id"), str) or not ref["id"] or not isinstance(ref.get("rule_version"), str) or not ref["rule_version"]:
                raise ModeCardError("固定规则必须明确id及rule_version，不读取最新版本")
            frozen = next((row for row in previous_snapshots
                           if row.get("id") == ref["id"] and row.get("rule_version") == ref["rule_version"]), None)
            if frozen is not None:
                # The enclosing previous record was hash-verified by _prepare.
                # An owner upgrade must not force a historical experiment rebind.
                resolved.append(deepcopy(frozen))
                continue
            if ref["id"].startswith("mode.user."):
                resolved.append(self.get_definition_snapshot(ref["id"], rule_version=ref["rule_version"]))
            else:
                result = validate_definition_references([ref])
                if not result["valid"]:
                    raise ModeCardError("规则引用未知或版本不符")
                resolved.extend(result["resolved"])
        return resolved

    def _data_refs(self, refs):
        if not isinstance(refs, list) or not refs:
            raise ModeCardError("数值结果必须引用已归档data_refs，不能无数据填写胜率")
        resolved = []
        for ref in refs:
            if not isinstance(ref, dict):
                raise ModeCardError("data_refs须为来源/快照身份对象")
            if ref.get("source_id"):
                if not isinstance(ref["source_id"], str):
                    raise ModeCardError("data_ref来源身份须为稳定字符串")
                record = self.journal.get_source(ref["source_id"])
                fills = self.journal._facts()[0]
                identity = {"source_id": ref["source_id"], "collection": "trade_journal_sources",
                            "has_actual_fills": any(row.get("source_ref", {}).get("source_id") == ref["source_id"] for row in fills)}
            elif ref.get("snapshot_id"):
                if not isinstance(ref["snapshot_id"], str):
                    raise ModeCardError("data_ref快照身份须为稳定字符串")
                record = self.journal.get_snapshot(ref["snapshot_id"])
                identity = {"snapshot_id": ref["snapshot_id"], "collection": "trade_journal_snapshots", "kind": record.get("kind")}
            else:
                raise ModeCardError("数值结果的data_ref须指向已有source_id或snapshot_id")
            if ref.get("content_hash") and ref["content_hash"] != record.get("content_hash"):
                raise ModeCardError("结果数据引用hash不一致")
            resolved.append({**identity, "content_hash": record.get("content_hash")})
        return resolved

    def _append_items(self, previous, supplied, kind, now, protocol_ref):
        if supplied is None:
            return deepcopy(previous)
        if not isinstance(supplied, list):
            raise ModeCardError("阈值尝试与验证结果须为追加数组")
        result = deepcopy(previous)
        id_key = "attempt_id" if kind == "attempt" else "result_id"
        indexed = {row[id_key]: row for row in result}
        for raw in supplied:
            if not isinstance(raw, dict):
                raise ModeCardError("实验追加项须为对象")
            identity = raw.get(id_key) or kind + "-" + uuid.uuid4().hex
            if not isinstance(identity, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", identity):
                raise ModeCardError("实验追加项须用稳定字符串身份")
            content = deepcopy(raw.get("content")) if isinstance(raw.get("content"), dict) else {
                key: deepcopy(value) for key, value in raw.items() if key not in {id_key, "captured_at", "recorded_at", "content_digest"}}
            if kind == "result":
                outcome = content.get("outcome_type", "inconclusive")
                if not isinstance(outcome, str) or outcome not in {"actual_trades", "simulation", "observation", "inconclusive"}:
                    raise ModeCardError("结果须明确actual_trades/simulation/observation/inconclusive")
                content["outcome_type"] = outcome
                metrics = content.get("metrics") or {}
                if not isinstance(metrics, dict):
                    raise ModeCardError("结果metrics须为带单位口径的对象")
                if metrics:
                    count = content.get("sample_count")
                    if type(count) is not int or count <= 0 or outcome == "inconclusive":
                        raise ModeCardError("数值结果需要正整数样本分母及明确结果类型")
                    content["data_refs"] = self._data_refs(content.get("data_refs"))
                    cutoff = _day(content.get("data_cutoff"), "结果数据截止日")
                    if cutoff > now[:10]:
                        raise ModeCardError("验证结果不能使用尚未发生的未来数据")
                    if outcome == "actual_trades" and (not any(ref.get("has_actual_fills") for ref in content["data_refs"])
                            or any(ref.get("kind") == "strategy_cohort" for ref in content["data_refs"])):
                        raise ModeCardError("真实交易结果须引用实际成交来源，观察群体不能冒充交易收益")
                    if outcome == "observation" and any(re.search(r"win|pnl|realiz|trade|胜率|已实现|净收益|交易收益", str(key), re.I) for key in metrics):
                        raise ModeCardError("观察反馈不能冒充真实交易胜率、已实现盈亏或交易收益")
                    if "win_rate" in metrics:
                        wins, rate = content.get("winning_count"), metrics["win_rate"]
                        if type(wins) is not int or not 0 <= wins <= count or type(rate) not in (int, float) or not 0 <= rate <= 1 or abs(rate-wins/count) > 1e-12:
                            raise ModeCardError("胜率须以winning_count/sample_count核对，比例为0—1")
                supersedes = content.get("supersedes_result_id")
                if supersedes and supersedes not in indexed:
                    raise ModeCardError("更正结果须指向已有result_id，旧结果不删除")
                content["machine_verified"] = False
                content["metric_note"] = "记录有来源的报告值；未验证原数据真实性、成本完整性或统计优势。观察/模拟与真实交易分别标识。"
            hashed = digest(content)
            if identity in indexed:
                if indexed[identity]["content_digest"] != hashed:
                    raise ModeCardError("已记录阈值尝试/结果不可覆盖，须追加新项及更正关系")
                continue
            row = {id_key: identity, "content": content, "content_digest": hashed, "captured_at": now,
                   "protocol_ref": deepcopy(protocol_ref)}
            if kind == "result":
                window = protocol_ref.get("validation_window")
                cutoff = content.get("data_cutoff")
                row["evaluation_scope"] = ("not_numeric" if not content.get("metrics") else
                    "within_registered_window" if window and cutoff and window["start"] <= cutoff <= window["end"] else
                    "outside_registered_validation_window")
                row["protocol_predates_validation"] = bool(window and protocol_ref["protocol_registered_at"][:10] < window["start"])
                row["validation_note"] = "窗口内仅表示日期范围相符，不证明本次参数事先登记、样本完整或结果已验证；窗口外属于探索/历史报告。"
            result.append(row)
            indexed[identity] = row
        return result

    def save_hypothesis(self, payload):
        with self.journal.write_guard():
            identity, previous, content = self._prepare(HYPOTHESIS_COLLECTION, "hypothesis_id", "hypothesis.user.", payload)
            if not isinstance(content.get("question"), str) or not content["question"].strip():
                raise ModeCardError("研究假设需要question，不能用单次盈亏代替研究问题")
            development = _window(content.get("development_window"), "开发区间")
            validation = _window(content.get("validation_window"), "验证区间")
            if development and validation and development["end"] >= validation["start"]:
                raise ModeCardError("开发与验证日期须严格不重叠且开发在前")
            content.update(development_window=development, validation_window=validation)
            rule_snapshots = {key: self._rule_refs(content.get(key), (previous or {}).get("rule_snapshots", {}).get(key, []))
                              for key in ("entry_rule_refs", "exit_rule_refs")}
            mode_snapshot = None
            if content.get("mode_ref"):
                ref = content["mode_ref"]
                if not isinstance(ref, dict):
                    raise ModeCardError("模式引用须含mode_id和revision")
                mode_snapshot = self.get_definition_snapshot(ref.get("mode_id"), ref.get("revision"))
            now = self.journal._now()
            prior_content = previous["content"] if previous else {}
            protocol_digest = digest({key: content.get(key) for key in PROTOCOL_IDENTITY_FIELDS})
            registered_at = previous["protocol_registered_at"] if previous and previous["protocol_digest"] == protocol_digest else now
            revision = previous["revision"] + 1 if previous else 1
            protocol_ref = {"hypothesis_id": identity, "revision": revision, "protocol_digest": protocol_digest,
                            "protocol_registered_at": registered_at, "validation_window": validation}
            content["parameter_attempts"] = self._append_items(prior_content.get("parameter_attempts", []), payload.get("parameter_attempts"), "attempt", now, protocol_ref)
            content["validation_results"] = self._append_items(prior_content.get("validation_results", []), payload.get("validation_results"), "result", now, protocol_ref)
            missing = [key for key in PROTOCOL_FIELDS if not content.get(key)]
            state = "incomplete" if missing else "prospective" if validation["start"] > registered_at[:10] else "retrospective"
            record = {"hypothesis_id": identity, "question": content["question"], "revision": revision,
                      "version": f"hypothesis-v{revision}", "previous_revision": previous["revision"] if previous else None,
                      "status": content["status"], "content": content, "content_digest": digest(content),
                      "captured_at": now, "recorded_at": now, "protocol_registered_at": registered_at,
                      "protocol_digest": protocol_digest, "preregistration": state, "missing_fields": missing,
                      "mode_snapshot": mode_snapshot, "rule_snapshots": rule_snapshots, "registry_version": VERSION,
                      "revision_reason": payload.get("revision_reason"), "advantage_verified": False,
                      "state_note": "实验状态及报告值不证明优势；预登记只描述主协议，后加阈值按自己的captured_at/protocol_ref判断；历史区间登记不冒充事先预登记，失败尝试和结果永久保留"}
            record["content_hash"] = digest(record)
            saved = self._persist(HYPOTHESIS_COLLECTION, "hypothesis_id", record)
            return {"hypothesis_id": identity, "revision": revision, "record": saved,
                    "missing_fields": missing, "preregistration": state}


__all__ = ["VERSION", "MODE_COLLECTION", "HYPOTHESIS_COLLECTION", "ModeCardError", "ModeCardService"]
