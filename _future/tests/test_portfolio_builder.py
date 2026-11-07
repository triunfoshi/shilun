import unittest

from shilun.portfolio import EqualWeightPortfolioBuilder, PortfolioConstraints, SnapshotFieldRanker


class PortfolioRankerTests(unittest.TestCase):
    def test_ranker_orders_by_score_then_weight_hint(self) -> None:
        snapshots = [
            {"ticker": "000001.SZ", "execution_score": 25.0, "target_position_pct": 10, "risk_score": 20},
            {"ticker": "600519.SH", "execution_score": 35.0, "target_position_pct": 20, "risk_score": 30},
            {"ticker": "300750.SZ", "execution_score": 35.0, "target_position_pct": 10, "risk_score": 18},
        ]
        ranked = SnapshotFieldRanker().rank(snapshots)
        self.assertEqual(["600519.SH", "300750.SZ", "000001.SZ"], [item.ticker for item in ranked])


class PortfolioBuilderTests(unittest.TestCase):
    def test_equal_weight_builder_caps_by_constraints(self) -> None:
        snapshots = [
            {"ticker": "600519.SH", "execution_score": 36.0, "target_position_pct": 20, "risk_score": 30},
            {"ticker": "600036.SH", "execution_score": 34.0, "target_position_pct": 20, "risk_score": 25},
            {"ticker": "000001.SZ", "execution_score": 28.0, "target_position_pct": 10, "risk_score": 22},
        ]
        ranked = SnapshotFieldRanker().rank(snapshots)
        builder = EqualWeightPortfolioBuilder(
            PortfolioConstraints(
                max_positions=2,
                max_single_weight=0.4,
                min_score=30.0,
                max_risk_score=40.0,
            )
        )
        plan = builder.build(ranked)
        self.assertEqual(2, len(plan.positions))
        self.assertEqual(["600519.SH", "600036.SH"], [position.ticker for position in plan.positions])
        self.assertAlmostEqual(0.8, sum(position.weight for position in plan.positions), places=6)
        self.assertAlmostEqual(0.2, plan.cash_weight, places=6)

    def test_builder_returns_all_cash_when_no_candidate_passes(self) -> None:
        ranked = SnapshotFieldRanker().rank(
            [
                {"ticker": "600519.SH", "execution_score": 18.0, "target_position_pct": 10, "risk_score": 85},
            ]
        )
        builder = EqualWeightPortfolioBuilder(
            PortfolioConstraints(
                max_positions=3,
                max_single_weight=0.5,
                min_score=20.0,
                max_risk_score=60.0,
            )
        )
        plan = builder.build(ranked)
        self.assertEqual(0, len(plan.positions))
        self.assertAlmostEqual(1.0, plan.cash_weight)


if __name__ == "__main__":
    unittest.main()
