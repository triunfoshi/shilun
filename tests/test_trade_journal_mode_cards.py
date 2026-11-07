from copy import deepcopy
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from pymongo.errors import DuplicateKeyError

from shilun.trade_journal.mode_cards import (
    ModeCardService, ModeCardError, MODE_COLLECTION, HYPOTHESIS_COLLECTION,
)
from shilun.trade_journal.definitions import get_definition

TZ = ZoneInfo("Asia/Shanghai")


class Collection:
    def __init__(self, store, name):
        self.store, self.name, self.rows = store, name, []

    def find(self, query, projection=None):
        self.store.reads.append((self.name, deepcopy(query)))
        return [{k: deepcopy(v) for k, v in row.items() if not projection or projection.get(k, 1) != 0}
                for row in self.rows if all(row.get(k) == v for k, v in query.items())]

    def find_one(self, query, projection=None):
        return next(iter(self.find(query, projection)), None)

    def update_one(self, query, changes, upsert=False):
        self.store.writes.append(self.name)
        old = next((r for r in self.rows if all(r.get(k) == v for k, v in query.items())), None)
        if old is not None:
            old.update(deepcopy(changes.get("$set", {})))
        elif upsert:
            self.rows.append({**deepcopy(query), **deepcopy(changes.get("$setOnInsert", {})), **deepcopy(changes.get("$set", {}))})

    def insert_one(self, row):
        self.store.writes.append(self.name)
        key = "mode_id" if self.name == MODE_COLLECTION else "hypothesis_id"
        if any(r.get(key) == row.get(key) and r.get("revision") == row.get("revision") for r in self.rows):
            raise DuplicateKeyError("same immutable revision")
        self.rows.append(deepcopy(row))


class Store:
    def __init__(self):
        self.collections, self.reads, self.writes = {}, [], []

    def collection(self, name):
        return self.collections.setdefault(name, Collection(self, name))


def service():
    store = Store()
    clock = [datetime(2026, 10, 27, 10, tzinfo=TZ)]
    return store, ModeCardService(store, now_factory=lambda: clock[0]), clock


def protocol(**changes):
    version = get_definition("trigger.E7")["rule_version"]
    return {"question": "失效后扩大风险是否比原计划内加仓更差？", "status": "trial",
            "development_window": {"start": "2026-07-01", "end": "2026-10-23"},
            "validation_window": {"start": "2026-11-01", "end": "2026-12-31"},
            "universe": "指定账户全部相关库存轮，失败轮不排除", "comparison_group": "原规则保持不变的同范围样本",
            "exclusion_policy": "未知成本和不可成交单列不补零", "target_metrics": ["完整轮净收益和尾部"],
            "entry_rule_refs": [{"id": "trigger.E7", "rule_version": version}],
            "exit_rule_refs": [{"id": "action.time_exit", "rule_version": get_definition("action.time_exit")["rule_version"]}],
            "execution_constraints": {"fees": "真实费用", "t_plus_one": True, "limits": "不能成交就不模拟成交"}, **changes}


def archive_data(store):
    store.collection("trade_journal_sources").rows.append({"source_id": "fills-source", "content_hash": "source-hash"})
    store.collection("trade_journal_batches").rows.append({"batch_id": "batch", "status": "committed", "fill_ids": ["f-1"], "financial_event_ids": []})
    store.collection("trade_journal_fills").rows.append({"fill_id": "f-1", "source_ref": {"source_id": "fills-source"}})


def test_mode_versions_retire_without_deleting_and_freeze_old_definition():
    store, svc, clock = service()
    first = svc.save_card({"name": "分歧观察", "purpose": "只做已确认承接的一段", "status": "trial"})
    mode_id = first["mode_id"]
    assert mode_id.startswith("mode.user.") and first["record"]["advantage_verified"] is False
    assert first["definition_snapshot"]["rule_version"] == "mode-card-v1"
    clock[0] = datetime(2026, 10, 28, 10, tzinfo=TZ)
    second = svc.save_card({"mode_id": mode_id, "expected_revision": 1, "revision_reason": "归档，不再使用", "status": "retired"})
    assert second["revision"] == 2 and len(store.collection(MODE_COLLECTION).rows) == 2
    assert svc.get_card(mode_id, 1)["status"] == "trial"
    assert svc.get_card(mode_id, 2)["content"]["purpose"] == "只做已确认承接的一段"
    assert svc.list_cards()["items"][0]["status"] == "retired"
    assert svc.list_cards()["items"][0]["revisions"] == [1, 2]
    assert svc.get_definition_snapshot(mode_id, rule_version="mode-card-v1") == first["definition_snapshot"]
    assert all(name.startswith("trade_journal_") for name in store.writes)


def test_exact_refs_and_cas_do_not_pick_latest_or_overwrite_a_racing_edit():
    store, svc, _ = service()
    first = svc.save_card({"mode_id": "mode.user.test", "name": "原模式"})
    with pytest.raises(ModeCardError, match="确切"):
        svc.get_definition_snapshot(first["mode_id"])
    with pytest.raises(ModeCardError, match="不一致"):
        svc.get_definition_snapshot(first["mode_id"], 1, rule_version="mode-card-v2")
    svc.save_card({"mode_id": first["mode_id"], "expected_revision": 1, "revision_reason": "补条件", "risk_constraints": "待配置"})
    other = ModeCardService(store)
    with pytest.raises(ModeCardError, match="expected_revision"):
        other.save_card({"mode_id": first["mode_id"], "expected_revision": 1, "revision_reason": "旧页面提交", "name": "不应覆盖"})
    assert svc.get_card(first["mode_id"], 2)["name"] == "原模式"


def test_strategy_binding_is_explicit_and_checked_against_the_owner():
    _, svc, _ = service()
    manual = svc.save_card({"name": "人工计划", "status": "active"})
    assert not manual["record"]["content"].get("strategy_id")
    assert manual["record"]["missing_fields"] and manual["record"]["advantage_verified"] is False
    with pytest.raises(ModeCardError, match="同时"):
        svc.save_card({"name": "缺版本", "strategy_id": "ma5_v0_2"})
    with pytest.raises(ModeCardError, match="registry"):
        svc.save_card({"name": "错版本", "strategy_id": "ma5_v0_2", "strategy_version": "old"})
    bound = svc.save_card({"name": "隔夜", "strategy_id": "e7_oversold_overnight", "strategy_version": "1.0.0"})
    assert bound["definition_snapshot"]["parameters"]["card_content"]["strategy_id"] == "e7_oversold_overnight"


def test_server_capture_and_tamper_detection_protect_historical_content():
    store, svc, _ = service()
    result = svc.save_card({"name": "不倒灌", "captured_at": "2020-01-01", "recorded_at": "2020-01-01"})
    assert result["record"]["captured_at"].startswith("2026-10-27")
    store.collection(MODE_COLLECTION).rows[0]["content"]["name"] = "改写原件"
    with pytest.raises(ModeCardError, match="校验"):
        svc.get_card(result["mode_id"], 1)


@pytest.mark.parametrize("bad", ["mode.ma5_trend", "mode.user.$bad", {"$ne": None}])
def test_user_ids_cannot_replace_static_terms_or_inject_a_query(bad):
    _, svc, _ = service()
    with pytest.raises(ModeCardError):
        svc.save_card({"mode_id": bad, "name": "不合法"})


def test_registered_protocol_dates_versions_and_state_do_not_prove_an_edge():
    _, svc, _ = service()
    result = svc.save_hypothesis(protocol())
    assert result["preregistration"] == "prospective" and not result["record"]["advantage_verified"]
    assert result["record"]["rule_snapshots"]["entry_rule_refs"][0]["id"] == "trigger.E7"
    with pytest.raises(ModeCardError, match="不重叠"):
        svc.save_hypothesis(protocol(validation_window={"start": "2026-10-23", "end": "2026-12-31"}))
    with pytest.raises(ModeCardError, match="版本"):
        svc.save_hypothesis(protocol(entry_rule_refs=[{"id": "trigger.E7"}]))
    historical = svc.save_hypothesis(protocol(validation_window={"start": "2026-10-24", "end": "2026-10-26"}))
    assert historical["preregistration"] == "retrospective"


def test_protocol_amendment_resets_registration_time_without_erasing_attempts():
    _, svc, clock = service()
    first = svc.save_hypothesis(protocol(parameter_attempts=[{"attempt_id": "a-1", "parameters": {"candidate_threshold": 2.3}, "reason": "探索，尚未生效"}]))
    clock[0] = datetime(2026, 11, 2, 10, tzinfo=TZ)
    second = svc.save_hypothesis({"hypothesis_id": first["hypothesis_id"], "expected_revision": 1,
        "revision_reason": "追加新假设不能保留旧预登记时点", "question": "修改比较问题", "parameter_attempts": [{"attempt_id": "a-2", "parameters": {"candidate_threshold": 2.4}}]})
    assert second["record"]["protocol_registered_at"].startswith("2026-11-02")
    assert second["preregistration"] == "retrospective"
    assert len(second["record"]["content"]["parameter_attempts"]) == 2
    assert second["record"]["content"]["parameter_attempts"][0]["protocol_ref"]["revision"] == 1
    assert second["record"]["content"]["parameter_attempts"][1]["protocol_ref"]["revision"] == 2
    assert len(svc.get_hypothesis(first["hypothesis_id"], 1)["content"]["parameter_attempts"]) == 1
    with pytest.raises(ModeCardError, match="不可覆盖"):
        svc.save_hypothesis({"hypothesis_id": first["hypothesis_id"], "expected_revision": 2, "revision_reason": "试图改旧阈值",
                             "parameter_attempts": [{"attempt_id": "a-1", "parameters": {"candidate_threshold": 9}}]})


def test_reported_numeric_results_need_real_data_and_correct_denominators():
    store, svc, _ = service()
    archive_data(store)
    item = {"result_id": "r-1", "outcome_type": "actual_trades", "sample_count": 10, "winning_count": 6,
            "data_cutoff": "2026-10-26", "data_refs": [{"source_id": "fills-source"}], "metrics": {"win_rate": .6}}
    result = svc.save_hypothesis(protocol(validation_results=[item]))
    reported = result["record"]["content"]["validation_results"][0]["content"]
    assert reported["metrics"]["win_rate"] == .6 and reported["machine_verified"] is False
    assert result["record"]["advantage_verified"] is False
    assert result["record"]["content"]["validation_results"][0]["evaluation_scope"] == "outside_registered_validation_window"
    for bad in ({"data_refs": []}, {"sample_count": 0}, {"metrics": {"win_rate": .7}}, {"data_cutoff": "2026-11-01"}):
        with pytest.raises(ModeCardError):
            svc.save_hypothesis(protocol(validation_results=[{**item, **bad}]))


def test_observation_cannot_be_relabelled_as_real_trade_profit_and_no_data_can_be_unknown():
    store, svc, _ = service()
    store.collection("trade_journal_snapshots").rows.append({"snapshot_id": "cohort", "kind": "strategy_cohort", "content_hash": "h"})
    item = {"outcome_type": "observation", "sample_count": 5, "data_cutoff": "2026-10-26",
            "data_refs": [{"snapshot_id": "cohort"}], "metrics": {"win_rate": .6}, "winning_count": 3}
    with pytest.raises(ModeCardError, match="观察反馈"):
        svc.save_hypothesis(protocol(validation_results=[item]))
    with pytest.raises(ModeCardError, match="实际成交"):
        svc.save_hypothesis(protocol(validation_results=[{**item, "outcome_type": "actual_trades"}]))
    unknown = svc.save_hypothesis({"question": "缺数先登记", "validation_results": [{"outcome_type": "inconclusive", "notes": "行情路径待补"}]})
    assert unknown["preregistration"] == "incomplete"
    assert unknown["record"]["content"]["validation_results"][0]["content"]["outcome_type"] == "inconclusive"


def test_user_mode_rule_refs_remain_bound_to_the_exact_retired_revision():
    _, svc, _ = service()
    mode = svc.save_card({"name": "本地定义"})
    svc.save_card({"mode_id": mode["mode_id"], "expected_revision": 1, "revision_reason": "退役保留历史", "status": "retired"})
    hypo = svc.save_hypothesis(protocol(mode_ref={"mode_id": mode["mode_id"], "revision": 1},
        entry_rule_refs=[{"id": mode["mode_id"], "rule_version": "mode-card-v1"}]))
    assert hypo["record"]["mode_snapshot"]["rule_version"] == "mode-card-v1"
    assert hypo["record"]["mode_snapshot"]["parameters"]["card_content"]["status"] == "draft"
    assert len(svc.list_hypotheses()["items"]) == 1


def test_result_corrections_append_and_preserve_all_trials():
    _, svc, _ = service()
    first = svc.save_hypothesis({"question": "保留失败实验", "validation_results": [{"result_id": "r-1", "notes": "当前不支持原假设"}]})
    second = svc.save_hypothesis({"hypothesis_id": first["hypothesis_id"], "expected_revision": 1, "revision_reason": "加证据更正解释",
        "validation_results": [{"result_id": "r-2", "supersedes_result_id": "r-1", "notes": "仍缺数据，保持未知"}]})
    assert len(second["record"]["content"]["validation_results"]) == 2
    assert len(svc.get_hypothesis(first["hypothesis_id"], 1)["content"]["validation_results"]) == 1


def test_owner_upgrade_does_not_force_old_experiment_rules_to_rebind(monkeypatch):
    import shilun.trade_journal.mode_cards as module
    _, svc, _ = service()
    first = svc.save_hypothesis(protocol())
    old = deepcopy(first["record"]["rule_snapshots"])
    monkeypatch.setattr(module, "validate_definition_references", lambda *_: {"valid": False, "resolved": [], "errors": ["owner upgraded"]})
    second = svc.save_hypothesis({"hypothesis_id": first["hypothesis_id"], "expected_revision": 1,
                                 "revision_reason": "追加观察，不改变旧规则", "notes": "原版本继续验证"})
    assert second["record"]["rule_snapshots"] == old
    with pytest.raises(ModeCardError, match="版本不符"):
        svc.save_hypothesis({"hypothesis_id": first["hypothesis_id"], "expected_revision": 2,
                             "revision_reason": "不能假造未冻结旧版本", "entry_rule_refs": [{"id": "trigger.E7", "rule_version": "never-frozen"}]})


def test_changing_optional_mode_ref_starts_a_new_protocol_without_making_it_mandatory():
    _, svc, clock = service()
    mode = svc.save_card({"name": "研究范围模式"})
    svc.save_card({"mode_id": mode["mode_id"], "expected_revision": 1, "revision_reason": "改变风险前提", "risk_constraints": "另一个前提"})
    first = svc.save_hypothesis(protocol(mode_ref={"mode_id": mode["mode_id"], "revision": 1}))
    clock[0] = datetime(2026, 11, 2, 10, tzinfo=TZ)
    second = svc.save_hypothesis({"hypothesis_id": first["hypothesis_id"], "expected_revision": 1,
        "revision_reason": "改用新版模式须重新登记", "mode_ref": {"mode_id": mode["mode_id"], "revision": 2}})
    assert second["record"]["protocol_digest"] != first["record"]["protocol_digest"]
    assert second["record"]["protocol_registered_at"].startswith("2026-11-02")
    assert second["preregistration"] == "retrospective"
    third = svc.save_hypothesis({"hypothesis_id": first["hypothesis_id"], "expected_revision": 2,
        "revision_reason": "解除模式绑定也是协议更改", "mode_ref": None})
    assert third["record"]["protocol_digest"] != second["record"]["protocol_digest"]
    assert "mode_ref" not in third["missing_fields"]
