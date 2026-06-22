import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from shilun.rules import RuleContext, build_default_candidate_tag_registry


class CandidateRuleTests(unittest.TestCase):
    def test_default_registry_emits_first_phase_observation_tags(self) -> None:
        registry = build_default_candidate_tag_registry()
        snapshot = {
            "structure_score": 82,
            "entry_probability": 0.72,
            "p_breakout_success": 0.62,
            "entry_style": "breakout_only",
            "gentle_expand_score": 71.0,
            "distribution_score": 16.0,
            "stall_score": 12.0,
            "risk_score": 28,
            "mid_stage_score": 78.0,
            "market_context": {"market_trend_score": 66},
            "sector_context": {"sector_strength_score": 61},
            "event_context": {"earnings_surprise_pct": 0.18},
            "fundamental_context": {
                "fundamental_score": 72,
                "profit_growth_yoy": 0.31,
                "revenue_growth_yoy": 0.16,
                "cash_dividend_yield": 0.031,
                "dividend_years": 4,
            },
        }

        tags = registry.evaluate(RuleContext(ticker="000001.SZ", snapshot=snapshot))
        codes = [tag.code for tag in tags]

        self.assertEqual(
            [
                "rps_breakout",
                "turtle_breakout",
                "high_tight_flag",
                "earnings_surprise",
                "dividend_quality",
            ],
            codes,
        )
        self.assertTrue(all(tag.validation_path.startswith("research/validation/") for tag in tags))
        self.assertTrue(all(tag.score > 0 for tag in tags))


if __name__ == "__main__":
    unittest.main()
