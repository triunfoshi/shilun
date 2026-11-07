import unittest

from shilun.backtest import (
    JoinQuantSignal,
    JoinQuantSignalPolicyConfig,
    build_target_weights,
    build_trade_signal,
    to_joinquant_symbol,
    to_tushare_symbol,
)


def _analysis_payload(
    *,
    ticker: str = "600519.SH",
    conclusion_label: str = "high_quality_continuation",
    structure_score: int = 78,
    entry_probability: float = 0.68,
    entry_zone: str = "candidate",
    p_continue_10d: float = 0.67,
    p_fail_fast_3d: float = 0.18,
    risk_score: int = 32,
    opportunity_type: str = "trend_follow",
) -> dict:
    return {
        "ticker": ticker,
        "snapshot": {
            "ticker": ticker,
            "structure_score": structure_score,
            "entry_probability": entry_probability,
            "entry_zone": entry_zone,
            "p_continue_10d": p_continue_10d,
            "p_fail_fast_3d": p_fail_fast_3d,
            "risk_score": risk_score,
            "opportunity_type": opportunity_type,
            "invalidation_level": 1200.0,
        },
        "decision": {
            "conclusion_label": conclusion_label,
        },
    }


class JoinQuantSymbolTests(unittest.TestCase):
    def test_symbol_conversion_round_trip(self) -> None:
        self.assertEqual("600519.XSHG", to_joinquant_symbol("600519.SH"))
        self.assertEqual("000001.SZ", to_tushare_symbol("000001.XSHE"))


class JoinQuantSignalPolicyTests(unittest.TestCase):
    def test_high_quality_signal_maps_to_full_weight(self) -> None:
        signal = build_trade_signal(
            _analysis_payload(),
            is_holding=False,
            policy=JoinQuantSignalPolicyConfig(max_positions=5, target_weight=0.2, trim_weight=0.1),
        )
        self.assertEqual("600519.XSHG", signal.jq_symbol)
        self.assertAlmostEqual(0.2, signal.target_weight)

    def test_confirmation_signal_keeps_trimmed_weight_only_when_holding(self) -> None:
        signal = build_trade_signal(
            _analysis_payload(conclusion_label="confirmation_needed", p_continue_10d=0.52),
            is_holding=True,
            policy=JoinQuantSignalPolicyConfig(max_positions=5, target_weight=0.2, trim_weight=0.1),
        )
        self.assertAlmostEqual(0.1, signal.target_weight)

    def test_defense_signal_liquidates(self) -> None:
        signal = build_trade_signal(
            _analysis_payload(conclusion_label="defense_first", risk_score=88),
            is_holding=True,
        )
        self.assertEqual(0.0, signal.target_weight)

    def test_target_weight_selection_caps_position_count(self) -> None:
        policy = JoinQuantSignalPolicyConfig(max_positions=2, target_weight=0.2, trim_weight=0.1)
        signals = [
            JoinQuantSignal("600519.SH", "600519.XSHG", "high_quality_continuation", "trend_follow", "ready", 0.2, 90, 20, 1000.0, ""),
            JoinQuantSignal("600036.SH", "600036.XSHG", "high_quality_continuation", "trend_follow", "candidate", 0.2, 88, 22, 40.0, ""),
            JoinQuantSignal("000001.SZ", "000001.XSHE", "high_quality_continuation", "trend_follow", "watch", 0.2, 70, 30, 10.0, ""),
        ]
        weights = build_target_weights(signals, policy=policy)
        self.assertEqual({"600519.XSHG": 0.2, "600036.XSHG": 0.2}, weights)


if __name__ == "__main__":
    unittest.main()
