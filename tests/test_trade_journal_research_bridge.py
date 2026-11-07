from copy import deepcopy
import gzip
import json
from hashlib import sha256

import pytest

from shilun.decision_research.mode_research import VERSION, reference, snapshot_id
from shilun.trade_journal.research_bridge import (
    ResearchBridgeError, ResearchContextMissing, freeze_strategy_cohort, build_journal_context, load_journal_context,
)
from shilun.review.theme_cycles import VERSION as THEME_VERSION
from shilun.review.theme_rules import rule_config


def research():
    rows = [{"ticker": "600001.SH", "name": "正常样本", "close_return": 3.0},
            {"ticker": "000001.SZ", "name": "失败样本", "close_return": -9.0,
             "price_gap_reason": "保留反证，不因失败从群体移除"}]
    daily = {"formation_date": "2026-10-22", "date": "2026-10-23", "cohort_n": 2,
             "members": rows, "failed_members": [{"ticker": "000002.SZ", "close_return": -7}],
             "metrics": {"primary": {"value": -3.0, "n": 2, "total": 2}}}
    data = {"analysis_date": "2026-10-23", "built_at": "2026-10-23T10:00:00+00:00", "version": VERSION,
            "assessments": {"windows": {"5": {"relay": {"daily": [daily], "cohort": "完整换手群体"}}}},
            "provenance": {"note": "历史重建"}, "method": {"cohorts": "冻结后失败不删除"}}
    data["snapshot_id"] = snapshot_id(data)
    return data


def arguments(data):
    return {"mode_research_ref": reference(data),
            "source_ref": {"snapshot_id": data["snapshot_id"], "mode_key": "relay", "window": 5,
                           "formation_date": "2026-10-22", "feedback_date": "2026-10-23"},
            "members": deepcopy(data["assessments"]["windows"]["5"]["relay"]["daily"][0]["members"])}


def test_full_cohort_keeps_failures_auxiliary_denominators_and_immutable_content():
    data = research()
    args = arguments(data)
    args["members"].reverse()
    result = freeze_strategy_cohort(data, **args)
    assert result["valid"] and result["verified_complete"] and result["member_count"] == 2
    assert result["members"][1]["close_return"] == -9
    assert result["content"]["daily"]["failed_members"][0]["close_return"] == -7
    assert "assessments" not in result["snapshot"] and result["snapshot"]["daily"]["cohort_n"] == 2
    first_hash = result["content_hash"]
    args["members"][0]["name"] = "修改请求不影响冻结结果"
    data["assessments"]["windows"]["5"]["relay"]["daily"][0]["members"].clear()
    assert len(result["members"]) == 2 and result["content_hash"] == first_hash
    assert result["scope_metadata"]["evidence_class"] == "historical_reconstruction"


@pytest.mark.parametrize("mutation", ["drop_loser", "duplicate", "edit_fact", "extra_member", "missing_selector",
                                     "different_window", "different_date", "different_version", "different_built_at", "bad_hash"])
def test_a_filtered_edited_or_wrong_snapshot_cannot_claim_the_full_denominator(mutation):
    data = research()
    args = arguments(data)
    if mutation == "drop_loser": args["members"].pop()
    elif mutation == "duplicate": args["members"].append(deepcopy(args["members"][0]))
    elif mutation == "edit_fact": args["members"][1]["close_return"] = 0
    elif mutation == "extra_member": args["members"].append({"ticker": "600002.SH"})
    elif mutation == "missing_selector": args["source_ref"].pop("formation_date")
    elif mutation == "different_window": args["source_ref"]["window"] = 3
    elif mutation == "different_date": args["mode_research_ref"]["analysis_date"] = "2026-10-22"
    elif mutation == "different_version": args["mode_research_ref"]["version"] = "old"
    elif mutation == "different_built_at": args["mode_research_ref"]["built_at"] = "2026-10-24T10:00:00+00:00"
    elif mutation == "bad_hash": data["provenance"]["note"] = "被改过"
    with pytest.raises(ResearchBridgeError):
        freeze_strategy_cohort(data, **args)


def test_an_exact_empty_cohort_is_not_eligibility_or_zero_return():
    data = research()
    daily = data["assessments"]["windows"]["5"]["relay"]["daily"][0]
    daily.update(members=[], cohort_n=0)
    data["snapshot_id"] = snapshot_id(data)
    result = freeze_strategy_cohort(data, **arguments(data))
    assert result["verified_complete"] and result["scope_metadata"]["empty_cohort"]
    assert result["member_count"] == 0 and "eligible" not in result


def test_bad_source_count_and_duplicate_source_cohort_are_not_trusted():
    data = research()
    daily = data["assessments"]["windows"]["5"]["relay"]["daily"][0]
    daily["cohort_n"] = 100
    data["snapshot_id"] = snapshot_id(data)
    with pytest.raises(ResearchBridgeError, match="计数"):
        freeze_strategy_cohort(data, **arguments(data))
    daily["cohort_n"] = 3
    daily["members"].append(deepcopy(daily["members"][0]))
    data["snapshot_id"] = snapshot_id(data)
    with pytest.raises(ResearchBridgeError, match="重复证券"):
        freeze_strategy_cohort(data, **arguments(data))


def test_oversized_cohort_is_rejected_rather_than_truncated(monkeypatch):
    import shilun.trade_journal.research_bridge as bridge
    monkeypatch.setattr(bridge, "MAX_FROZEN_COHORT_BYTES", 1)
    data = research()
    with pytest.raises(ResearchBridgeError, match="禁止裁断"):
        freeze_strategy_cohort(data, **arguments(data))


def context_research(day="2026-10-23", status="conflicted", market_state="down"):
    data = research()
    data["analysis_date"] = day
    data["assessments"]["policy"] = {"default_recent": 5}
    data["assessments"]["windows"]["5"]["relay"].update(
        status=status, selection_support=status == "conditional", price_support=True,
        risks=["昨日断板样本负反馈"], reasons=["原所有者逐项评估"],
        next_checks=["核对开盘和承接"], gaps=["逐笔资料未接入"])
    data["market"] = {"date": day, "turnover": {"regime": "low"},
                      "outlook": {"state": market_state, "counter": ["均线结构未修复"]}}
    data["route"] = {"mode": None, "status": "waiting"}
    data["snapshot_id"] = snapshot_id(data)
    return data


def theme(day, *, stage="disagreement", active=True, state="ready"):
    cycle = {"id": "fixed-1", "name": "题材甲", "core": {"ticker": "000001.SZ"},
             "front": ["000001.SZ"], "back": ["600001.SH"], "cycle_stage": stage,
             "last_confirmed_date": day, "post_exit": {"return": 1 if day == "2026-10-22" else 2}}
    return {"date": day, "version": THEME_VERSION, "revision": "frozen-owner-revision",
            "rules": rule_config(), "status": state,
            "active": [cycle] if active else [], "archived": [] if active else [cycle]}


def test_context_reuses_owner_counters_without_creating_a_score_or_permission():
    data = context_research()
    q = {"rule_backtest": {"eligible": False, "reasons": ["历史输入不齐"]}}
    report = build_journal_context(data, decision={"mode_id": "mode.relay", "ticker": "000001.SZ"}, journal_qualification=q)
    assert report["plan_fit"]["status"] == "conflicted"
    assert report["plan_fit"]["selection_support"] is False
    assert report["plan_fit"]["execution_permission"] is False
    assert report["counters"][0]["text"] == "昨日断板样本负反馈"
    assert report["journal_qualification"] == q and report["qualification_effect"] == "none"
    assert report["time_qualification"]["decision_time_verified"] is False
    assert "score" not in report["plan_fit"] and not report["market_events"]


def test_unknown_or_ma5_intent_does_not_silently_become_first_board_mode():
    report = build_journal_context(context_research(), decision={"mode_id": "mode.ma5_trend"})
    assert report["plan_fit"]["mode_key"] is None and report["plan_fit"]["status"] == "unknown"
    with pytest.raises(ResearchBridgeError, match="静默改绑"):
        build_journal_context(context_research(), decision={"mode_id": "mode.relay"}, mode_key="trend")


def test_market_changes_keep_exact_before_after_refs_and_require_earlier_date():
    old = context_research("2026-10-22", "conditional", "up")
    current = context_research()
    report = build_journal_context(current, decision={"mode_id": "mode.relay"}, previous_research=old)
    assert [(r["field"], r["before"], r["after"]) for r in report["market_events"]] == [
        ("outlook.state", "up", "down"), ("assessment.status", "conditional", "conflicted")]
    assert report["market_events"][0]["before_ref"]["snapshot_id"] == old["snapshot_id"]
    with pytest.raises(ResearchBridgeError, match="同日重算"):
        build_journal_context(current, previous_research=current)
    old["version"] = "mode-research-v4"
    old["snapshot_id"] = snapshot_id(old)
    report = build_journal_context(current, previous_research=old)
    assert not report["market_events"] and any("版本不同" in gap for gap in report["gaps"])


def test_role_prices_and_new_confirmation_dates_are_not_role_transitions():
    report = build_journal_context(context_research(), decision={"ticker": "000001.SZ"},
        theme_snapshot=theme("2026-10-23"), previous_theme_snapshot=theme("2026-10-22"))
    assert report["roles"][0]["role"] == "core" and not report["role_events"]
    report = build_journal_context(context_research(), decision={"ticker": "000001.SZ"},
        theme_snapshot=theme("2026-10-23", stage="retreat", active=False), previous_theme_snapshot=theme("2026-10-22"))
    assert len(report["role_events"]) == 1
    assert report["role_events"][0]["before"]["active"] is True
    assert report["role_events"][0]["after"]["active"] is False
    assert report["roles"][0]["scope"].endswith("核心资格不等于真龙头")


def test_partial_or_future_theme_cannot_turn_into_a_confirmed_transition():
    report = build_journal_context(context_research(), decision={"ticker": "000001.SZ"},
        theme_snapshot=theme("2026-10-23", stage="retreat", state="partial"), previous_theme_snapshot=theme("2026-10-22"))
    assert not report["role_events"] and any("仅展示" in gap for gap in report["gaps"])
    with pytest.raises(ResearchBridgeError, match="未来关系"):
        build_journal_context(context_research(), theme_snapshot=theme("2026-10-26"))


def test_replay_owner_pass_does_not_grant_journal_backtest_qualification():
    item = {"collection": "trading_replay_gate_reports", "record": {"report_id": "job12h:content-hash",
            "ready_for_launch": True, "decision": "launch", "blockers": [], "frames": ["not copied"]}}
    report = build_journal_context(context_research(), replay_evidence=[item],
        journal_qualification={"rule_backtest": {"eligible": False, "reasons": ["本交易轮缺完整历史输入"]}})
    link = report["replay_evidence"][0]
    assert link["content"]["ready_for_launch"] is True and "frames" not in link["content"]
    assert link["journal_qualification_effect"] == "none"
    assert report["journal_qualification"]["rule_backtest"]["eligible"] is False
    with pytest.raises(ResearchBridgeError, match="所有者"):
        build_journal_context(context_research(), replay_evidence=[{"collection": "my_win_rate", "record": {"passed": True}}])


class ExactStore:
    def __init__(self, rows=None):
        self.rows, self.calls = rows or {}, []

    def collection(self, name):
        parent = self
        class Collection:
            def find_one(self, query, projection=None):
                parent.calls.append((name, query))
                for row in parent.rows.get(name, []):
                    if all(row.get(key) == value for key, value in query.items()):
                        return deepcopy(row)
                return None
        return Collection()


def test_provider_uses_explicit_owner_cache_and_does_not_claim_ecs_archive(monkeypatch):
    import shilun.decision_research.service as owner
    current = context_research()
    calls = []
    def load(day, sid):
        calls.append((day, sid))
        return deepcopy(current)
    monkeypatch.setattr(owner, "load_mode_research", load)
    store = ExactStore()
    detail = {"episode": {"episode_id": "ep-1", "ticker": "000001.SZ"},
              "decisions": [{"decision_id": "d-1", "revision": 1, "mode_id": "mode.relay"}],
              "qualification": {"rule_backtest": {"eligible": False}}}
    result = load_journal_context(store, detail, analysis_date=current["analysis_date"], snapshot_id=current["snapshot_id"])
    assert calls == [(current["analysis_date"], current["snapshot_id"])]
    assert not store.calls
    assert result["input_storage"]["research"]["ecs_archive_verified"] is False
    assert result["plan_fit"]["mode_key"] == "relay" and result["decision_ref"]["ticker"] == "000001.SZ"
    monkeypatch.setattr(owner, "load_mode_research", lambda *_: None)
    with pytest.raises(ResearchContextMissing, match="未回退"):
        load_journal_context(store, detail, analysis_date=current["analysis_date"], snapshot_id=current["snapshot_id"])
    with pytest.raises(ResearchBridgeError, match="latest"):
        load_journal_context(store, detail, analysis_date=current["analysis_date"], snapshot_id="latest")


def test_provider_loads_only_exact_theme_and_replay_records_from_the_supplied_store(monkeypatch):
    import shilun.decision_research.service as owner
    current = context_research()
    monkeypatch.setattr(owner, "load_mode_research", lambda *_: deepcopy(current))
    payload = theme("2026-10-23")
    raw = gzip.compress(json.dumps(payload).encode())
    h = sha256(raw).hexdigest()
    ref = {"date": payload["date"], "revision": payload["revision"], "sha256": h}
    store = ExactStore({"theme_archive_files": [{"_id": f'cycle:{ref["revision"]}:{ref["date"]}', "sha256": h, "data": raw}],
                        "trading_replay_gate_reports": [{"report_id": "job12h:abc", "ready_for_launch": True}]})
    detail = {"episode": {"episode_id": "ep-1", "ticker": "000001.SZ"}, "decisions": []}
    result = load_journal_context(store, detail, analysis_date=current["analysis_date"], snapshot_id=current["snapshot_id"],
        theme_ref=ref, replay_refs=[{"collection": "trading_replay_gate_reports", "id": "job12h:abc"}])
    assert result["roles"][0]["role"] == "core" and result["replay_evidence"][0]["id"] == "job12h:abc"
    assert store.calls == [("theme_archive_files", {"_id": f'cycle:{ref["revision"]}:{ref["date"]}'}),
                           ("trading_replay_gate_reports", {"report_id": "job12h:abc"})]
    with pytest.raises(ResearchContextMissing, match="当前store"):
        load_journal_context(ExactStore(), detail, analysis_date=current["analysis_date"], snapshot_id=current["snapshot_id"], theme_ref=ref)
    with pytest.raises(ResearchBridgeError, match="latest"):
        load_journal_context(store, detail, analysis_date=current["analysis_date"], snapshot_id=current["snapshot_id"],
                             replay_refs=[{"collection": "trading_replay_gate_reports", "id": "job12h:latest"}])


def test_provider_historical_revision_is_narrowly_bound_to_this_episode(monkeypatch):
    import shilun.decision_research.service as owner
    current = context_research()
    monkeypatch.setattr(owner, "load_mode_research", lambda *_: deepcopy(current))
    detail = {"episode": {"episode_id": "ep-1", "ticker": "000001.SZ"}, "decisions": []}
    store = ExactStore({"trade_journal_decisions": [{"decision_id": "old", "revision": 1, "episode_id": "ep-other", "mode_id": "mode.relay"}]})
    with pytest.raises(ResearchContextMissing, match="该交易轮"):
        load_journal_context(store, detail, analysis_date=current["analysis_date"], snapshot_id=current["snapshot_id"],
                             decision_id="old", decision_revision=1)
    assert store.calls[0][1] == {"decision_id": "old", "revision": 1, "episode_id": "ep-1"}
