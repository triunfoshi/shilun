"""Read-only joins to existing research owners; no scores, orders, or DB writes.

The caller loads an exact, persisted research snapshot using its current owner.
Completeness below means equality to that frozen research cohort, not complete
market data, historical availability, execution feasibility, or profitability.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import date
from hashlib import sha256
import gzip
import json
from typing import Any, Mapping

from bson import BSON

from shilun.decision_research.mode_research import reference, validate
from shilun.review.theme_cycles import VERSION as THEME_CYCLE_VERSION
from shilun.review.theme_rules import rule_config
from shilun.trade_journal.ledger import digest

BRIDGE_VERSION = "trade-journal-research-bridge-v1"
# A storage budget below Mongo's 16 MiB document ceiling, not a trading rule.
# Callers store this content once; metadata and indexes are kept separately.
MAX_FROZEN_COHORT_BYTES = 14 * 1024 * 1024


class ResearchBridgeError(ValueError):
    """A claimed research identity or cohort cannot be verified."""


class ResearchContextMissing(ResearchBridgeError):
    """An explicitly requested persisted input is absent; never fall back."""


def _snapshot(research: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(research, Mapping):
        raise ResearchBridgeError("缺少已归档玩法评估快照")
    research = dict(research)  # Do not duplicate the entire multi-mode research in memory.
    try:
        validate(research)
        _day(research.get("analysis_date"), "评估日期")
        reference(research)
    except (ValueError, TypeError, KeyError) as error:
        raise ResearchBridgeError("玩法评估快照身份、版本或内容校验失败") from error
    return research


def _day(value: Any, label: str) -> str:
    try:
        result = date.fromisoformat(value).isoformat()
    except (TypeError, ValueError) as error:
        raise ResearchBridgeError(f"{label}须为YYYY-MM-DD") from error
    if result != value:
        raise ResearchBridgeError(f"{label}须为YYYY-MM-DD")
    return result


def _member_map(rows: Any, label: str) -> dict[str, dict[str, Any]]:
    if not isinstance(rows, list):
        raise ResearchBridgeError(f"{label}须为完整成员数组")
    result = {}
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("ticker"), str) or not row["ticker"]:
            raise ResearchBridgeError(f"{label}成员缺证券身份")
        if row["ticker"] in result:
            raise ResearchBridgeError(f"{label}含重复证券，不能声明完整分母")
        try:
            digest(row)
        except (TypeError, ValueError) as error:
            raise ResearchBridgeError(f"{label}成员不是有限JSON事实") from error
        result[row["ticker"]] = row
    return result


def freeze_strategy_cohort(research: Mapping[str, Any], *, mode_research_ref: Mapping[str, Any],
                           source_ref: Mapping[str, Any], members: list[dict[str, Any]]) -> dict[str, Any]:
    """Verify all members against one exact owner daily cohort and freeze it.

    ``source_ref`` selects snapshot_id/mode_key/window/formation_date/feedback_date.
    Member order is immaterial; member identities and all fact fields must match.
    A missing or filtered loser, a duplicated stock or an edited value is rejected.
    """
    snapshot = _snapshot(research)
    expected_ref = reference(snapshot)
    if not isinstance(mode_research_ref, Mapping) or dict(mode_research_ref) != expected_ref:
        raise ResearchBridgeError("玩法评估日期、版本、snapshot_id或built_at与归档不一致")
    if not isinstance(source_ref, Mapping):
        raise ResearchBridgeError("缺少完整候选分母选择器source_ref")
    if source_ref.get("snapshot_id") != expected_ref["snapshot_id"]:
        raise ResearchBridgeError("候选分母选择器与评估快照不一致")
    formation = _day(source_ref.get("formation_date"), "名单形成日")
    feedback = _day(source_ref.get("feedback_date"), "反馈日")
    if not formation < feedback <= snapshot["analysis_date"]:
        raise ResearchBridgeError("形成日、反馈日与归档评估日顺序不一致")
    window = str(source_ref.get("window", ""))
    mode_key = source_ref.get("mode_key")
    windows = snapshot.get("assessments", {}).get("windows", {})
    modes = windows.get(window, {}) if isinstance(windows, dict) else {}
    if not isinstance(mode_key, str) or mode_key not in modes:
        raise ResearchBridgeError("候选玩法或窗口不属于该冻结评估")
    assessment = modes[mode_key]
    rows = [row for row in assessment.get("daily", [])
            if row.get("date") == feedback and row.get("formation_date") == formation]
    if len(rows) != 1:
        raise ResearchBridgeError("候选日未唯一匹配该冻结玩法daily群体")
    daily = rows[0]
    expected_members = daily.get("members")
    expected = _member_map(expected_members, "服务端冻结群体")
    supplied = _member_map(members, "请求群体")
    if type(daily.get("cohort_n")) is not int or daily["cohort_n"] != len(expected):
        raise ResearchBridgeError("服务端冻结群体计数与成员不一致，须修复来源")
    if set(expected) != set(supplied):
        missing, extra = sorted(set(expected)-set(supplied)), sorted(set(supplied)-set(expected))
        raise ResearchBridgeError(f"候选全集不一致；缺少{missing}，多出{extra}")
    if any(digest(expected[ticker]) != digest(supplied[ticker]) for ticker in expected):
        raise ResearchBridgeError("候选事实与冻结原件不一致，不得改写后声明完整")
    selector = {"snapshot_id": expected_ref["snapshot_id"], "mode_key": mode_key,
                "window": window, "formation_date": formation, "feedback_date": feedback}
    content = {"bridge_version": BRIDGE_VERSION, "mode_research_ref": expected_ref,
               "source_ref": selector, "daily": deepcopy(daily),
               "cohort_rule": assessment.get("cohort"),
               "method": deepcopy(snapshot.get("method")),
               "provenance": deepcopy(snapshot.get("provenance"))}
    frozen_bytes = len(BSON.encode(content))
    if frozen_bytes > MAX_FROZEN_COHORT_BYTES:
        raise ResearchBridgeError("完整候选快照超过14MiB存储预算；需按有序分片持久化后再归档，禁止裁断成员")
    scope_metadata = {"verified_complete": True, "member_count": len(expected),
                      "scope": "精确冻结玩法的当日完整研究群体；不是全市场机会",
                      "source_version": snapshot["version"], "bridge_version": BRIDGE_VERSION,
                      "frozen_bson_bytes": frozen_bytes,
                      "evidence_class": "historical_reconstruction",
                      "empty_cohort": not expected,
                      "limitations": ["相等仅证明与归档群体一致，不证明来源覆盖完整、当时已保存、可以买到或模式有效。",
                                      "daily包含反馈日结果，不得倒填为形成日已知事实。",
                                      "零样本可完整保存，但不授予收益、事前或回测资格。"]}
    return {"valid": True, "errors": [], "verified_complete": True,
            "mode_research_ref": deepcopy(expected_ref), "source_ref": selector,
            "members": deepcopy(expected_members), "member_count": len(expected),
            "scope_metadata": scope_metadata, "snapshot": content,
            "content": content, "content_hash": digest(content)}


def _change(events, *, kind, field, before, after, before_ref, after_ref):
    """Describe different owner outputs, without inferring causation or scoring."""
    if before is not None and after is not None and before != after:
        events.append({"kind": kind, "field": field, "before": deepcopy(before), "after": deepcopy(after),
                       "before_ref": deepcopy(before_ref), "after_ref": deepcopy(after_ref),
                       "evidence_class": "historical_reconstruction", "inference": "既有所有者输出变化；不认定因果或自动交易许可"})


def _theme_context(payload, ticker, *, not_after):
    if payload is None:
        return None, [], ["未关联题材周期归档；板块、核心与后排关系未知"]
    if not isinstance(payload, Mapping):
        raise ResearchBridgeError("题材周期须为所有者归档对象")
    day = _day(payload.get("date"), "题材周期日期")
    if day > not_after:
        raise ResearchBridgeError("题材周期晚于当前研究日，不得引入未来关系")
    if payload.get("version") != THEME_CYCLE_VERSION or payload.get("rules") != rule_config() or not payload.get("revision"):
        return None, [], ["题材周期缺精确修订/规则身份或版本已变；不猜测角色转换"]
    ref = {"date": day, "version": payload["version"], "revision": payload["revision"],
           "content_digest": digest(dict(payload)), "owner": "shilun.review.theme_cycle_store.day_payload"}
    rows = []
    for active, cycles in ((True, payload.get("active", [])), (False, payload.get("archived", []))):
        for cycle in cycles:
            core = (cycle.get("core") or {}).get("ticker")
            role = "core" if ticker == core else "front" if ticker in cycle.get("front", []) else "back" if ticker in cycle.get("back", []) else None
            if ticker and role:
                rows.append({"cycle_id": cycle["id"], "topic": cycle.get("name"), "role": role,
                             "core_ticker": core, "active": active, "stage": cycle.get("cycle_stage"),
                             "last_confirmed_date": cycle.get("last_confirmed_date"),
                             "exit_date": cycle.get("exit_date"), "post_exit": deepcopy(cycle.get("post_exit")),
                             "scope": "该题材原固定队列；核心资格不等于真龙头"})
    gaps = [] if payload.get("status") in {"ready", "empty"} else ["题材周期有缺项，仅展示既有关系；不判角色转换"]
    return ref, rows, gaps


def _replay_links(records):
    owners = {
        "execution_scenario_replays": ("shilun.trading.scenario_replay.replay_persisted_scenario_session", "session_id"),
        "trading_replay_materialization_reports": ("shilun.jobs.trading_replay_materialization_job", "report_id"),
        "trading_replay_gate_reports": ("shilun.jobs.trading_replay_gate_job", "report_id"),
    }
    keep = {"session_id", "report_id", "trade_date", "plan_id", "playbook_id", "playbook_version", "status",
            "audit_eligible", "audit_exclusion_reasons", "output_digest", "input_digest", "output_digests",
            "session_ids", "ready_for_launch", "decision", "blockers", "coverage", "gates",
            "materialized_replay_count", "audit_eligible_replay_count", "skipped_count", "inventory_fingerprint",
            "generated_at", "materialized_at"}
    links = []
    for item in records or []:
        if not isinstance(item, Mapping) or item.get("collection") not in owners or not isinstance(item.get("record"), Mapping):
            raise ResearchBridgeError("回放证据须带受支持的所有者collection与record")
        owner, identity_key = owners[item["collection"]]
        record = item["record"]
        if not record.get(identity_key):
            raise ResearchBridgeError("回放证据缺稳定session_id/report_id，不用标题冒充验收")
        content = {key: deepcopy(value) for key, value in record.items() if key in keep}
        links.append({"collection": item["collection"], "owner": owner,
                      "id": record[identity_key], "content": content, "content_digest": digest(content),
                      "journal_qualification_effect": "none",
                      "note": "调用方读取的既有验收证据；不在本桥重算上线门禁，不证明本交易轮可回测或数据已齐备。"})
    return links


def build_journal_context(research: Mapping[str, Any], *, decision=None, mode_key=None, window=None,
                          previous_research=None, theme_snapshot=None, previous_theme_snapshot=None,
                          journal_qualification=None, replay_evidence=()) -> dict[str, Any]:
    """Project exact owner evidence into a journal view; never grant permission.

    Research comparisons describe two provided dates, not consecutive sessions
    unless the owner supplies that coverage. Closing research is a current or
    retrospective context, never proof that an intraday decision knew it.
    """
    current = _snapshot(research)
    current_ref, decision = reference(current), decision or {}
    gaps, market_events, role_events = [], [], []
    mappings = {"mode.first_board_arbitrage": "trend", "mode.relay": "relay", "mode.core_crowding": "crowding"}
    named_mode = mappings.get(decision.get("mode_id"))
    if named_mode and mode_key and mode_key != named_mode:
        raise ResearchBridgeError("显式玩法与所记录模式不一致；不得静默改绑计划")
    selected_key = mode_key or named_mode
    assessments = current.get("assessments", {})
    selected_window = str(window if window is not None else assessments.get("policy", {}).get("default_recent", ""))
    modes = assessments.get("windows", {}).get(selected_window, {})
    assessment = modes.get(selected_key) if selected_key else None
    fit = {"mode_id": decision.get("mode_id"), "mode_key": selected_key, "window": selected_window,
           "status": "unknown", "selection_support": None, "reasons": [], "risks": [], "next_checks": [],
           "binding_basis": "explicit_research_view" if mode_key else "named_mode_correspondence" if named_mode else "unmapped",
           "adoption_verified": False,
           "execution_permission": False, "owner": "shilun.decision_research.playbook_assessment.evaluate"}
    counters = []
    if assessment is None:
        gaps.append("未绑定可比较玩法或对应窗口不存在；MA5趋势、反核等模式不自动代入首板/接力/抱团")
    else:
        for key in ("status", "status_label", "selection_support", "price_support", "change", "change_label", "reasons", "risks", "next_checks", "gaps", "diagnostic"):
            if key in assessment:
                fit[key] = deepcopy(assessment[key])
        counters.extend({"scope": "playbook", "kind": "owner_risk", "text": risk, "source_ref": current_ref}
                        for risk in assessment.get("risks", []))
        gaps.extend(assessment.get("gaps", []))
    market = current.get("market", {})
    counters.extend({"scope": "market", "kind": "owner_counter", "text": risk, "source_ref": current_ref}
                    for risk in market.get("outlook", {}).get("counter", []))
    previous_ref = None
    if previous_research is not None:
        previous = _snapshot(previous_research)
        previous_ref = reference(previous)
        if previous["analysis_date"] >= current["analysis_date"]:
            raise ResearchBridgeError("前照研究日须早于当前日；同日重算不是市场阶段转换")
        if previous["version"] != current["version"]:
            gaps.append("前后玩法规则版本不同，不比较为市场转变")
        else:
            prior_market = previous.get("market", {})
            for field, before, after in (
                ("outlook.state", prior_market.get("outlook", {}).get("state"), market.get("outlook", {}).get("state")),
                ("turnover.regime", prior_market.get("turnover", {}).get("regime"), market.get("turnover", {}).get("regime")),
                ("route", previous.get("route"), current.get("route")),
            ):
                _change(market_events, kind="market_owner_change", field=field, before=before, after=after,
                        before_ref=previous_ref, after_ref=current_ref)
            old_assessment = previous.get("assessments", {}).get("windows", {}).get(selected_window, {}).get(selected_key, {})
            if assessment is not None:
                _change(market_events, kind="playbook_feedback_change", field="assessment.status",
                        before=old_assessment.get("status"), after=assessment.get("status"),
                        before_ref=previous_ref, after_ref=current_ref)
    else:
        gaps.append("没有前一精确研究快照；不推断环境或玩法转折")
    theme_ref, roles, theme_gaps = _theme_context(theme_snapshot, decision.get("ticker"), not_after=current["analysis_date"])
    gaps.extend(theme_gaps)
    prior_theme_ref = None
    if previous_theme_snapshot is not None:
        prior_theme_ref, previous_roles, prior_gaps = _theme_context(previous_theme_snapshot, decision.get("ticker"), not_after=current["analysis_date"])
        gaps.extend(prior_gaps)
        if theme_ref and prior_theme_ref and not theme_gaps and not prior_gaps:
            if prior_theme_ref["date"] >= theme_ref["date"]:
                raise ResearchBridgeError("前题材快照须早于当前题材日")
            old, new = {r["cycle_id"]: r for r in previous_roles}, {r["cycle_id"]: r for r in roles}
            for cycle_id in sorted(set(old) | set(new)):
                role_fields = ("role", "core_ticker", "active", "stage")
                before = {key: old[cycle_id].get(key) for key in role_fields} if cycle_id in old else {"membership": "not_in_owner_scope"}
                after = {key: new[cycle_id].get(key) for key in role_fields} if cycle_id in new else {"membership": "not_in_owner_scope"}
                _change(role_events, kind="fixed_cohort_role_context_change", field=cycle_id,
                        before=before, after=after,
                        before_ref=prior_theme_ref, after_ref=theme_ref)
    elif theme_ref:
        gaps.append("没有前一题材快照；只展示当期固定队列关系")
    result = {"version": BRIDGE_VERSION, "analysis_date": current["analysis_date"],
              "decision_ref": {key: deepcopy(decision[key]) for key in ("decision_id", "revision", "observed_at", "mode_id", "ticker") if key in decision},
              "plan_fit": fit, "market": deepcopy(market), "counters": counters,
              "market_events": market_events, "roles": roles, "role_events": role_events,
              "source_refs": {"research": current_ref, "previous_research": previous_ref, "theme": theme_ref, "previous_theme": prior_theme_ref},
              "journal_qualification": deepcopy(journal_qualification), "qualification_effect": "none",
              "replay_evidence": _replay_links(replay_evidence), "gaps": list(dict.fromkeys(gaps)),
              "time_qualification": {"decision_time_verified": False, "use": "当前/事后研究上下文；不替代当时可得事实"},
              "limitations": ["适配性复用原评估状态，不生成新阈值、分数或开仓许可。",
                              "事件只是两个明确日期的所有者输出差异；未证明连续交易日、资金因果、龙头更替或首次扩散/末端补涨。",
                              "角色来自原固定队列；新核心、抗跌后领涨和盘口主动性仍须独立证据。",
                              "回放验收与交易轮资格是两类证据；本桥不授予规则回测资格，也不读取可变最新计划代替历史版本。"]}
    result["content_hash"] = digest(result)
    return result


def _load_research_pair(analysis_date, snapshot_id):
    from shilun.decision_research.service import load_mode_research
    import re
    _day(analysis_date, "显式研究日期")
    if not isinstance(snapshot_id, str) or not re.fullmatch(r"[0-9a-f]{64}", snapshot_id):
        raise ResearchBridgeError("必须提供精确研究snapshot_id；不读取latest或仅按日期回退")
    research = load_mode_research(analysis_date, snapshot_id)
    if research is None:
        raise ResearchContextMissing("missing: 指定日期/hash的owner研究缓存不存在；未回退其他日期或最新快照")
    research = _snapshot(research)
    if research["analysis_date"] != analysis_date or research["snapshot_id"] != snapshot_id:
        raise ResearchBridgeError("加载的owner研究与显式日期/hash不一致")
    return research


def _load_theme_exact(store, ref):
    if ref is None:
        return None
    if not isinstance(ref, Mapping) or not ref.get("revision") or not ref.get("sha256"):
        raise ResearchBridgeError("题材引用须含date/revision/sha256，不读取current指针")
    day = _day(ref.get("date"), "题材归档日期")
    identity = f'cycle:{ref["revision"]}:{day}'
    document = store.collection("theme_archive_files").find_one({"_id": identity})
    if not document:
        raise ResearchContextMissing("missing: 指定题材修订/日期未在当前store归档")
    raw = bytes(document.get("data", b""))
    if document.get("sha256") != ref["sha256"] or sha256(raw).hexdigest() != ref["sha256"]:
        raise ResearchBridgeError("题材归档hash与精确引用不一致")
    try:
        payload = json.loads(gzip.decompress(raw))
    except (OSError, ValueError) as error:
        raise ResearchBridgeError("题材归档内容无法解码") from error
    if not isinstance(payload, dict) or payload.get("date") != day or payload.get("revision") != ref["revision"]:
        raise ResearchBridgeError("题材原件日期/修订与引用不一致")
    return payload


def load_journal_context(store, detail, *, analysis_date, snapshot_id, mode_key=None, window=None,
                         previous_analysis_date=None, previous_snapshot_id=None, decision_id=None,
                         decision_revision=None, theme_ref=None, previous_theme_ref=None,
                         replay_refs=()) -> dict[str, Any]:
    """Resolve explicit inputs through current owners, using only narrow DB reads.

    Research comes from its owner's immutable hash-addressed local cache. This
    does not certify ECS archival. Theme/replay inputs are loaded from the same
    caller-provided store only when exact references are provided. No jobs run.
    """
    if not isinstance(detail, Mapping) or not isinstance(detail.get("episode"), Mapping):
        raise ResearchBridgeError("context需要已读取的交易轮detail")
    current = _load_research_pair(analysis_date, snapshot_id)
    previous = None
    if previous_analysis_date is not None or previous_snapshot_id is not None:
        if previous_analysis_date is None or previous_snapshot_id is None:
            raise ResearchBridgeError("前照研究必须同时给出日期和精确hash")
        previous = _load_research_pair(previous_analysis_date, previous_snapshot_id)
    if decision_revision is not None and (not decision_id or type(decision_revision) is not int or decision_revision < 1):
        raise ResearchBridgeError("指定决策版本须同时提供decision_id与正整数revision")
    decisions = list(detail.get("decisions") or [])
    if decision_id:
        decisions = [row for row in decisions if row.get("decision_id") == decision_id
                     and (decision_revision is None or row.get("revision") == decision_revision)]
        if not decisions and decision_revision is not None:
            record = store.collection("trade_journal_decisions").find_one({"decision_id": decision_id,
                "revision": decision_revision, "episode_id": detail["episode"]["episode_id"]}, {"_id": 0})
            decisions = [record] if record else []
        if not decisions:
            raise ResearchContextMissing("missing: 指定决策/版本不属于该交易轮或不存在")
    decision = deepcopy(max(decisions, key=lambda row: (row.get("recorded_at") or "", row.get("revision", 0)))) if decisions else {}
    decision["ticker"] = detail["episode"]["ticker"]
    theme = _load_theme_exact(store, theme_ref)
    previous_theme = _load_theme_exact(store, previous_theme_ref)
    replay_owners = {"execution_scenario_replays": "session_id", "trading_replay_materialization_reports": "report_id",
                     "trading_replay_gate_reports": "report_id"}
    evidence = []
    for ref in replay_refs or []:
        collection = ref.get("collection") if isinstance(ref, Mapping) else None
        identity = ref.get("id") if isinstance(ref, Mapping) else None
        if collection not in replay_owners or not isinstance(identity, str) or not identity or identity.endswith(":latest") or identity == "current":
            raise ResearchBridgeError("回放引用须为受支持collection与具体session/report ID，不读取latest")
        record = store.collection(collection).find_one({replay_owners[collection]: identity}, {"_id": 0, "frames": 0})
        if not record:
            raise ResearchContextMissing("missing: 指定回放证据未在当前store归档")
        evidence.append({"collection": collection, "record": record})
    result = build_journal_context(current, decision=decision, mode_key=mode_key, window=window,
        previous_research=previous, theme_snapshot=theme, previous_theme_snapshot=previous_theme,
        journal_qualification=detail.get("qualification") or detail["episode"].get("qualification"), replay_evidence=evidence)
    result["input_storage"] = {
        "research": {"owner": "shilun.decision_research.service.load_mode_research", "source": "owner_local_immutable_cache",
                     "ecs_archive_verified": False, "note": "精确文件缓存输入；本地存在不证明已在ECS归档"},
        "theme": {"collection": "theme_archive_files", "reference": deepcopy(theme_ref)} if theme_ref else None,
        "previous_theme": {"collection": "theme_archive_files", "reference": deepcopy(previous_theme_ref)} if previous_theme_ref else None,
        "replays": deepcopy(list(replay_refs or [])), "database_scope": "调用方当前store，未扫描全量，未启动采集或生成任务"}
    result.pop("content_hash", None)
    result["content_hash"] = digest(result)
    return result


__all__ = ["BRIDGE_VERSION", "MAX_FROZEN_COHORT_BYTES", "ResearchBridgeError", "ResearchContextMissing",
           "freeze_strategy_cohort", "build_journal_context", "load_journal_context"]
