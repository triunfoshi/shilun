import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from shilun.strategies import StrategyContext, build_default_strategy_registry


class StrategyRegistryTests(unittest.TestCase):
    def test_default_registry_emits_baseline_and_matching_test_strategies(self) -> None:
        registry = build_default_strategy_registry()
        context = StrategyContext(
            ticker="000001.SZ",
            record={"candidate_tags": "rps_breakout,turtle_breakout,high_tight_flag"},
        )

        signals = registry.evaluate(context)
        strategy_ids = [signal.strategy_id for signal in signals]

        self.assertEqual(
            [
                "shilun_v1",
                "shilun_v1_rps_breakout_test",
                "shilun_v1_turtle_breakout_test",
                "shilun_v1_high_tight_test",
            ],
            strategy_ids,
        )
        self.assertTrue(all(signal.strategy_version == "2026.05.12" for signal in signals))
        self.assertEqual("baseline", signals[0].status)
        self.assertTrue(all(signal.validation_report_path.startswith("research/validation/strategies/") for signal in signals))

    def test_event_quality_strategy_matches_any_event_or_quality_tag(self) -> None:
        registry = build_default_strategy_registry()
        context = StrategyContext(ticker="000001.SZ", record={"candidate_tags": "dividend_quality"})

        signals = registry.evaluate(context)
        strategy_ids = [signal.strategy_id for signal in signals]

        self.assertEqual(["shilun_v1", "shilun_v1_event_quality_test"], strategy_ids)
        self.assertEqual(("dividend_quality",), signals[1].matched_candidate_tags)


if __name__ == "__main__":
    unittest.main()
