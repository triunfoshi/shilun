import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from shilun.decision.execution_engine import ExecutionEngine


class ExecutionEngineTests(unittest.TestCase):
    def test_engine_emits_action_and_price_bands(self) -> None:
        engine = ExecutionEngine()
        decision = engine.evaluate(
            latest_close=12.5,
            atr_abs=0.4,
            snapshot={
                "p_continue_10d": 0.68,
                "p_acceptance_1d": 0.61,
                "p_fail_fast_3d": 0.14,
                "entry_probability": 0.72,
                "risk_score": 38,
                "risk_level": 0.22,
                "trend_stage": "mid",
                "trigger_state": "confirmed",
                "breakout_quality": "valid",
                "pullback_quality": "healthy",
                "opportunity_type": "trend_follow",
                "support_main": 11.9,
                "support_basis": "ma20",
                "pressure_main": 12.8,
                "invalidation_level": 11.6,
                "gentle_expand_score": 72.0,
                "pullback_shrink_score": 58.0,
                "impulsive_spike_score": 18.0,
                "distribution_score": 12.0,
                "stall_score": 10.0,
                "early_stage_score": 48.0,
                "mid_stage_score": 74.0,
                "late_stage_score": 16.0,
            },
            market_context={"market_trend_score": 62},
            sector_context={"sector_trend_score": 66, "sector_strength_score": 64},
            fundamental_context={"fundamental_score": 58},
            chip_context={
                "overhang_ratio": 0.03,
                "support_density": 0.82,
                "pressure_density": 0.25,
                "vacuum_up_ratio": 0.58,
                "winner_rate": 0.6,
                "chip_concentration": 0.62,
                "cost_15": 11.8,
                "cost_50": 12.0,
                "cost_85": 12.9,
            },
        )

        self.assertIn(decision.action_label, {"probe", "build"})
        self.assertGreater(decision.execution_score, 0)
        self.assertIsNotNone(decision.support_band_low)
        self.assertIsNotNone(decision.pressure_band_high)
        self.assertIsNotNone(decision.invalidation_price)
        self.assertGreaterEqual(decision.target_position_pct, 0)
        self.assertIn(decision.entry_style, {"breakout_only", "pullback_only", "breakout_or_pullback"})
        self.assertIn("continuation_edge", decision.weight_breakdown)


if __name__ == "__main__":
    unittest.main()
