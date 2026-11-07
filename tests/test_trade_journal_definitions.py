"""Vocabulary integration boundaries; these tests do not backtest a strategy."""
import ast
from dataclasses import asdict
import json
from pathlib import Path

import pytest

from shilun.review.engine import classify_market_posture
from shilun.review.market_mood import MOOD_RULE_VERSION, _market_mood_state
from shilun.review.strong_stock_candidates import RULE, VERSION as CORE_VERSION
from shilun.review.theme_rules import rule_config
from shilun.review.theme_cycles import LABELS as THEME_CYCLE_LABELS, VERSION as THEME_CYCLE_VERSION
from shilun.decision_research.playbook_assessment import POLICY as PLAYBOOK_POLICY
from shilun.strategies.registry import list_strategies
from shilun.trade_journal.definitions import (
    DEFINITIONS_VERSION, P2_VERSION, get_definition, get_definitions,
    validate_definition_references,
)
from shilun.trading.config import DEFAULT_EXECUTION_SCENARIO_RULES
from shilun.trading.playbooks.e8_first_board import DEFAULT_E8_RULES


def test_catalogue_has_unique_ids_complete_provenance_and_no_shared_mutability():
    payload = get_definitions()
    rows = payload["definitions"]
    ids = [row["id"] for row in rows]
    assert len(ids) == len(set(ids))
    assert payload["version"] == DEFINITIONS_VERSION
    json.dumps(payload, ensure_ascii=False, allow_nan=False)
    root = Path(__file__).resolve().parents[1]
    required = {"id", "label", "category", "axis", "definition", "status", "rule_version", "scope",
                "formula", "numerator", "denominator", "unit", "window", "time_basis",
                "missing_policy", "sources", "limitations", "parameters"}
    for row in rows:
        assert required <= row.keys()
        assert row["category"] == row["id"].split(".", 1)[0]
        assert row["sources"] and row["missing_policy"] and row["limitations"]
        for source in row["sources"]:
            assert source["symbol"] and source["version"]
            assert not Path(source["path"]).is_absolute()
            assert (root / source["path"]).is_file(), source
    original = get_definition("mode.ma5_trend")
    original["sources"][0]["path"] = "mutated"
    assert get_definition("mode.ma5_trend")["sources"][0]["path"] != "mutated"
    assert all(row["category"] == "mode" for row in get_definitions("mode")["definitions"])


def test_unknown_terms_are_not_coerced_to_manual_or_resolved_from_a_label():
    assert get_definition("missing_mode") is None
    assert get_definition("MA5趋势") is None
    assert get_definition("mode.unknown")["id"] != get_definition("mode.manual")["id"]
    checked = validate_definition_references(["mode.not_a_real_mode"])
    assert not checked["valid"] and not checked["resolved"]
    assert checked["errors"][0]["code"] == "unknown_definition"
    assert get_definition("mode.leader_first_negative")["status"] == "manual_definition"
    assert get_definition("mode.leader_first_negative")["parameters"] == {}


def test_references_reject_axis_and_version_changes_instead_of_rebinding_history():
    mode = get_definition("mode.ma5_trend")
    good = {"id": mode["id"], "rule_version": mode["rule_version"]}
    assert validate_definition_references([good], expected_category="mode")["valid"]
    bad_axis = validate_definition_references(["action.intraday_t"], expected_category="mode")
    assert bad_axis["errors"][0]["code"] == "category_mismatch"
    bad_version = validate_definition_references([{**good, "rule_version": "historical-v0"}])
    assert bad_version["errors"][0]["code"] == "version_mismatch"
    assert not bad_version["resolved"]
    # An empty link set is only structurally valid, never an eligibility result.
    assert "eligible" not in validate_definition_references([])


def test_registered_strategy_identity_comes_from_existing_registry():
    strategies = list_strategies()
    by_id = {row["id"]: row for row in get_definitions("strategy")["definitions"]}
    assert set(by_id) == {"strategy." + row["strategy_id"] for row in strategies}
    for row in strategies:
        definition = by_id["strategy." + row["strategy_id"]]
        assert definition["rule_version"] == row["version"]
        assert definition["parameters"]["strategy_id"] == row["strategy_id"]
        assert definition["parameters"]["strategy_version"] == row["version"]
    assert get_definition("strategy.e7_oversold_overnight")["rule_version"] == "1.0.0"
    assert get_definition("strategy.ma5_v0_2")["rule_version"] == "0.2.0"


def test_core_cycle_and_scenario_parameters_use_their_rule_owners():
    core = get_definition("role.core_candidate")
    assert core["rule_version"] == CORE_VERSION
    assert core["definition"] == RULE
    assert core["parameters"] == rule_config()["core"]
    assert get_definition("cycle.theme_recovery")["parameters"] == rule_config()
    assert get_definition("cycle.theme_recovery")["rule_version"] == THEME_CYCLE_VERSION
    assert get_definition("cycle.theme_recovery")["label"].startswith(THEME_CYCLE_LABELS["recovery"])
    assert get_definition("metric.playbook_comparison")["parameters"] == PLAYBOOK_POLICY
    scenario = get_definition("trigger.E7")
    assert scenario["rule_version"] == DEFAULT_EXECUTION_SCENARIO_RULES.version
    assert scenario["parameters"]["e7_exit_cutoff"] == DEFAULT_EXECUTION_SCENARIO_RULES.e7_exit_cutoff.isoformat()
    e8 = get_definition("trigger.E8")
    assert e8["rule_version"] == DEFAULT_E8_RULES.version
    assert e8["parameters"]["minimum_valid_peers"] == asdict(DEFAULT_E8_RULES)["minimum_valid_peers"]
    assert e8["parameters"]["entry_start"] == DEFAULT_E8_RULES.entry_start.isoformat()


@pytest.mark.parametrize("score,code", [(29, "cold"), (30, "normal"), (59, "normal"),
                                        (60, "active"), (79, "active"), (80, "hot")])
def test_mood_boundary_matches_existing_engine_without_turning_it_into_a_phase(score, code):
    actual = _market_mood_state(
        current_count=100, previous_count=50, max_board=5, lianban_count=10,
        up_ratio=.8, market_amount_yi=120, amount_ma20_yi=100,
        feedback_median=.05, relimit_rate=.5, limit_down_count=0,
        data_ready=True, downside_penalty_override=100-score,
    )
    assert actual["score"] == score and actual["code"] == code
    definition = get_definition("emotion." + code)
    assert definition["parameters"]["owner_code"] == code
    assert definition["rule_version"] == MOOD_RULE_VERSION
    assert definition["axis"] != get_definition("cycle.main_uptrend")["axis"]
    assert get_definition("cycle.main_uptrend")["status"] == "manual_definition"


def test_1000_yi_is_a_full_day_delta_marker_and_not_an_intraday_permission():
    at_boundary = classify_market_posture([], market_amount_yi=21000, previous_amount_yi=20000)
    below = classify_market_posture([], market_amount_yi=21000, previous_amount_yi=20000.01)
    assert at_boundary["expanded_by_1000"] is True
    assert below["expanded_by_1000"] is False
    definition = get_definition("metric.market_expansion_1000_eod")
    assert "全日" in definition["formula"]
    assert "1000" in definition["formula"]
    assert "收盘" in definition["time_basis"]
    assert definition["rule_version"] == P2_VERSION
    assert get_definition("metric.intraday_amount_ratio")["denominator"] != definition["denominator"]


def test_qualification_and_evidence_are_independent_dimensions():
    assert {r["id"] for r in get_definitions("qualification")["definitions"]} == {
        "qualification.accounting", "qualification.decision_review",
        "qualification.ex_ante", "qualification.rule_backtest",
    }
    assert {r["id"] for r in get_definitions("evidence")["definitions"]} == {
        "evidence.contemporaneous", "evidence.historical_reconstruction",
        "evidence.retrospective_recall", "evidence.unknown",
    }
    assert "事后回忆不足" in get_definition("qualification.ex_ante")["definition"]
    assert get_definition("metric.round_return")["denominator"] == "累计含费买入成本"
    assert "最大资金占用" in get_definition("metric.round_return")["formula"]
    assert {r["id"] for r in get_definitions("expectation_check")["definitions"]} == {
        "expectation_check.supported", "expectation_check.refuted", "expectation_check.unknown",
    }
    assert get_definition("expectation_check.supported")["status"] == "review_outcome"
    assert "evaluation_class" in get_definition("expectation_check.supported")["limitations"][0]
    assert get_definition("expectation_check.supported")["axis"] != get_definition("expectation.above_expectation")["axis"]
    assert {r["id"].split(".", 1)[1] for r in get_definitions("observation_state")["definitions"]} == {
        "watching", "not_bought", "not_triggered", "unfilled", "abandoned", "removed",
    }
    assert get_definition("scope.strategy_cohort")["sources"][0]["symbol"] == "freeze_strategy_cohort"


def test_p2_source_version_is_not_silently_detached_from_current_code():
    path = Path(__file__).resolve().parents[1] / "shilun/review/service.py"
    constants = {}
    for node in ast.parse(path.read_text()).body:
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    constants[target.id] = node.value.value
    assert P2_VERSION == constants["REVIEW_VERSION"]


def test_new_journal_terms_keep_report_types_sample_units_and_owner_versions_distinct():
    assert {row["id"] for row in get_definitions("result_type")["definitions"]} == {
        "result_type.actual_trades", "result_type.simulation", "result_type.observation", "result_type.inconclusive",
    }
    assert get_definition("state.method_active")["axis"] != get_definition("cycle.main_uptrend")["axis"]
    assert "不" in get_definition("state.method_active")["definition"]
    assert get_definition("metric.group_win_rate")["denominator"] != get_definition("metric.win_rate")["denominator"]
    assert "切片" in get_definition("scope.mode_slice")["definition"]
    assert "不是过去" in get_definition("scope.current_portfolio_snapshot")["definition"]
    assert get_definition("mode.unallocated") is None  # A statistical residual is not a selectable user intent.
    root = Path(__file__).resolve().parents[1]
    for identifier, relative in (
        ("scope.quantity_allocation", "shilun/trade_journal/allocations.py"),
        ("metric.group_win_rate", "shilun/trade_journal/analytics.py"),
        ("state.method_active", "shilun/trade_journal/mode_cards.py"),
    ):
        constants = {target.id: node.value.value for node in ast.parse((root / relative).read_text()).body
                     if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant)
                     for target in node.targets if isinstance(target, ast.Name)}
        assert get_definition(identifier)["rule_version"] == constants["VERSION"]
