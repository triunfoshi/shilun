from __future__ import annotations

import unittest
from dataclasses import replace
from datetime import datetime
from zoneinfo import ZoneInfo

from shilun.trading.contracts import ActionType, RuleState
from shilun.trading.playbooks.e8_first_board import (
    E8DecisionState,
    E8FiveMinuteBar,
    E8IndustryFacts,
    E8IndustryGatePath,
    E8Setup,
    E8StockFacts,
    IndustryPeerReturn,
    evaluate_e8_buy_point,
)


TZ = ZoneInfo("Asia/Shanghai")


def dt(hour: int, minute: int) -> datetime:
    return datetime(2026, 7, 31, hour, minute, tzinfo=TZ)


def bar(
    hour: int,
    minute: int,
    open_price: float,
    high: float,
    low: float,
    close: float,
    amount: float,
) -> E8FiveMinuteBar:
    return E8FiveMinuteBar(
        closed_at=dt(hour, minute),
        open=open_price,
        high=high,
        low=low,
        close=close,
        amount=amount,
    )


def direct_industry(**changes) -> E8IndustryFacts:
    values = {
        "target_ticker": "002131.SZ",
        "expected_member_count": 11,
        "peers": (
            IndustryPeerReturn("002131.SZ", 0.06),
            *(IndustryPeerReturn(f"PEER{i:02d}", 0.01) for i in range(9)),
            IndustryPeerReturn("PEER09", -0.01),
        ),
        "matching_index_return": 0.005,
    }
    values.update(changes)
    return E8IndustryFacts(**values)


def secondary_bars() -> tuple[E8FiveMinuteBar, ...]:
    return (
        bar(9, 45, 10.20, 10.30, 10.15, 10.25, 100.0),
        bar(9, 50, 10.25, 10.34, 10.20, 10.30, 110.0),
        bar(9, 55, 10.30, 10.36, 10.25, 10.32, 90.0),
        bar(10, 0, 10.32, 10.39, 10.28, 10.35, 100.0),
        bar(10, 5, 10.35, 10.52, 10.33, 10.50, 220.0),
    )


def opening_reclaim_bars() -> tuple[E8FiveMinuteBar, ...]:
    return (
        bar(9, 35, 10.20, 10.30, 10.15, 10.25, 500.0),
        bar(9, 40, 10.25, 10.32, 10.18, 10.28, 400.0),
        bar(9, 45, 10.28, 10.35, 10.20, 10.31, 250.0),
        bar(9, 50, 10.31, 10.38, 10.22, 10.34, 200.0),
        bar(9, 55, 10.34, 10.40, 10.24, 10.36, 180.0),
        bar(10, 0, 10.36, 10.42, 10.25, 10.39, 150.0),
    )


def stock(**changes) -> E8StockFacts:
    values = {
        "ticker": "002131.SZ",
        "computed_at": dt(10, 6),
        "previous_close": 10.0,
        "current_price": 10.60,
        "vwap": 10.35,
        "up_limit": 11.0,
        "matching_index_return": 0.005,
        "closed_bars": secondary_bars(),
        "open_price": 10.20,
        "opening_amount_ratio": None,
        "auction_only": False,
        "up_limit_sealed": False,
    }
    values.update(changes)
    return E8StockFacts(**values)


def trace_by_id(decision, rule_id: str):
    return next(trace for trace in decision.traces if trace.rule_id == rule_id)


class E8IndustryGateTests(unittest.TestCase):
    def test_direct_breadth_excludes_target_and_uses_conservative_lower_bound(self) -> None:
        decision = evaluate_e8_buy_point(direct_industry(), stock())

        self.assertEqual(decision.state, E8DecisionState.ENTRY_READY)
        self.assertEqual(decision.industry_gate_path, E8IndustryGatePath.BREADTH_85)
        metrics = decision.metrics["industry"]
        self.assertEqual(metrics["excluded_target_rows"], 1)
        self.assertEqual(metrics["expected_peer_count_excluding_target"], 10)
        self.assertEqual(metrics["valid_peer_count"], 10)
        self.assertEqual(metrics["up_peer_count"], 9)
        self.assertAlmostEqual(metrics["observed_up_ratio"], 0.9)
        self.assertAlmostEqual(metrics["conservative_up_ratio_lower_bound"], 0.9)
        self.assertEqual(
            trace_by_id(decision, "e8.industry.exclude_target").state,
            RuleState.SATISFIED,
        )

    def test_relative_path_accepts_60_percent_breadth_and_two_point_excess(self) -> None:
        peers = (
            IndustryPeerReturn("002131.SZ", 0.055),
            *(IndustryPeerReturn(f"UP{i}", 0.04) for i in range(6)),
            *(IndustryPeerReturn(f"DOWN{i}", -0.005) for i in range(4)),
        )
        industry = direct_industry(peers=peers, matching_index_return=0.005)

        decision = evaluate_e8_buy_point(industry, stock(current_price=10.60))

        self.assertEqual(decision.state, E8DecisionState.ENTRY_READY)
        self.assertEqual(
            decision.industry_gate_path,
            E8IndustryGatePath.RELATIVE_OUTPERFORMANCE,
        )
        metrics = decision.metrics["industry"]
        self.assertAlmostEqual(metrics["conservative_up_ratio_lower_bound"], 0.6)
        self.assertGreaterEqual(metrics["sector_excess_index"], 0.02)
        self.assertFalse(metrics["direct_path_passed"])
        self.assertTrue(metrics["relative_path_passed"])

    def test_low_coverage_is_data_blocked_instead_of_inflating_breadth(self) -> None:
        peers = tuple(IndustryPeerReturn(f"UP{i}", 0.03) for i in range(10))
        industry = direct_industry(expected_member_count=21, peers=peers)

        decision = evaluate_e8_buy_point(industry, stock())

        self.assertEqual(decision.state, E8DecisionState.DATA_BLOCKED)
        self.assertEqual(decision.action_type, ActionType.PAUSE_NEW_ACTION)
        metrics = decision.metrics["industry"]
        self.assertAlmostEqual(metrics["coverage"], 0.5)
        self.assertAlmostEqual(metrics["observed_up_ratio"], 1.0)
        self.assertAlmostEqual(metrics["conservative_up_ratio_lower_bound"], 0.5)
        self.assertEqual(
            trace_by_id(decision, "e8.industry.gate").state,
            RuleState.BLOCKED,
        )


class E8SetupTests(unittest.TestCase):
    def test_secondary_burst_is_entry_ready(self) -> None:
        decision = evaluate_e8_buy_point(direct_industry(), stock())

        self.assertEqual(decision.state, E8DecisionState.ENTRY_READY)
        self.assertEqual(decision.action_type, ActionType.PREPARE_ENTRY)
        self.assertEqual(decision.setup, E8Setup.SECONDARY_BURST)
        self.assertGreaterEqual(
            decision.metrics["setups"]["secondary_amount_ratio"],
            1.8,
        )
        self.assertTrue(decision.metrics["setups"]["breaks_platform"])

    def test_opening_burst_reclaim_has_priority_when_both_setup_dimensions_pass(self) -> None:
        facts = stock(
            computed_at=dt(10, 1),
            closed_bars=opening_reclaim_bars(),
            opening_amount_ratio=3.5,
            vwap=10.30,
        )

        decision = evaluate_e8_buy_point(direct_industry(), facts)

        self.assertEqual(decision.state, E8DecisionState.ENTRY_READY)
        self.assertEqual(decision.setup, E8Setup.OPENING_BURST_RECLAIM)
        setup_metrics = decision.metrics["setups"]
        self.assertTrue(setup_metrics["opening_ready"])
        self.assertLessEqual(setup_metrics["opening_contraction_ratio"], 0.70)
        self.assertLessEqual(setup_metrics["opening_max_pullback"], 0.02)

    def test_rs_vwap_pass_but_no_amount_setup_stays_confirming(self) -> None:
        quiet_bars = tuple(replace(item, amount=100.0) for item in secondary_bars())
        decision = evaluate_e8_buy_point(
            direct_industry(),
            stock(closed_bars=quiet_bars, current_price=10.40, vwap=10.30),
        )

        self.assertEqual(decision.state, E8DecisionState.CONFIRMING)
        self.assertEqual(decision.action_type, ActionType.WAIT_CONFIRMATION)
        self.assertEqual(decision.setup, E8Setup.NONE)
        self.assertEqual(
            trace_by_id(decision, "e8.setup.confirmed").state,
            RuleState.UNSATISFIED,
        )


class E8StateTests(unittest.TestCase):
    def test_gain_above_seven_percent_is_missed_and_never_chased(self) -> None:
        decision = evaluate_e8_buy_point(
            direct_industry(),
            stock(current_price=10.71, vwap=10.50),
        )

        self.assertEqual(decision.state, E8DecisionState.MISSED)
        self.assertEqual(decision.action_type, ActionType.NO_CHASE)
        self.assertEqual(
            trace_by_id(decision, "e8.chase.gain_cap").state,
            RuleState.UNSATISFIED,
        )

    def test_less_than_two_percent_to_limit_is_missed_even_below_gain_cap(self) -> None:
        decision = evaluate_e8_buy_point(
            direct_industry(),
            stock(current_price=10.40, vwap=10.25, up_limit=10.50),
        )

        self.assertEqual(decision.state, E8DecisionState.MISSED)
        self.assertEqual(
            trace_by_id(decision, "e8.chase.limit_distance").state,
            RuleState.UNSATISFIED,
        )

    def test_auction_only_is_a_distinct_non_executable_state(self) -> None:
        decision = evaluate_e8_buy_point(
            direct_industry(),
            stock(computed_at=dt(9, 31), auction_only=True),
        )

        self.assertEqual(decision.state, E8DecisionState.AUCTION_ONLY)
        self.assertEqual(decision.action_type, ActionType.OBSERVE_SETUP)
        self.assertEqual(decision.setup, E8Setup.NONE)

    def test_before_window_is_confirming_and_after_cutoff_is_missed(self) -> None:
        before = evaluate_e8_buy_point(
            direct_industry(),
            stock(computed_at=dt(9, 34), closed_bars=()),
        )
        after = evaluate_e8_buy_point(
            direct_industry(),
            stock(computed_at=dt(10, 31)),
        )

        self.assertEqual(before.state, E8DecisionState.CONFIRMING)
        self.assertEqual(after.state, E8DecisionState.MISSED)

    def test_missing_core_quote_is_data_blocked(self) -> None:
        decision = evaluate_e8_buy_point(direct_industry(), stock(vwap=None))

        self.assertEqual(decision.state, E8DecisionState.DATA_BLOCKED)
        self.assertEqual(decision.action_type, ActionType.PAUSE_NEW_ACTION)
        self.assertIn("VWAP", decision.evidence[0])

    def test_record_contains_serializable_rule_trace_and_evidence(self) -> None:
        record = evaluate_e8_buy_point(direct_industry(), stock()).to_record()

        self.assertEqual(record["scenario_id"], "E8")
        self.assertEqual(record["state"], "entry_ready")
        self.assertEqual(record["action_type"], "prepare_entry")
        self.assertTrue(record["traces"])
        self.assertTrue(record["evidence"])
        self.assertEqual(
            record["metrics"]["industry"]["gate_path"],
            "breadth_85",
        )


if __name__ == "__main__":
    unittest.main()
