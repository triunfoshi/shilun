import unittest

from scripts.export_joinquant_strategy import JoinQuantStrategyExportConfig, render_joinquant_strategy


class ExportJoinQuantStrategyTests(unittest.TestCase):
    def test_render_contains_route_a_core_strategy_config(self) -> None:
        rendered = render_joinquant_strategy(
            JoinQuantStrategyExportConfig(
                stock_pool=("000001.XSHE", "600519.XSHG"),
                benchmark="000300.XSHG",
                lookback=180,
                max_positions=3,
                target_weight=0.25,
                trim_weight=0.1,
                max_candidates=120,
            )
        )
        self.assertNotIn("from shilun import", rendered)
        self.assertIn('g.benchmark = "000300.XSHG"', rendered)
        self.assertIn('set_benchmark("000300.XSHG")', rendered)
        self.assertIn('"000001.XSHE"', rendered)
        self.assertIn('"600519.XSHG"', rendered)
        self.assertIn("g.fixed_stock_pool = [", rendered)
        self.assertIn("g.max_positions = 3", rendered)
        self.assertIn("g.target_weight = 0.25", rendered)
        self.assertIn("g.max_candidates = 120", rendered)
        self.assertIn("derive_invalidation_pressure", rendered)
        self.assertIn("should_reduce_existing_position", rendered)
        self.assertIn("volatility_adjusted_weight", rendered)
        self.assertIn("average_true_range", rendered)
        self.assertIn("should_take_profit_protection", rendered)
        self.assertIn("g.profit_protect_min_gain = 0.08", rendered)

    def test_render_without_stock_pool_keeps_dynamic_selection(self) -> None:
        rendered = render_joinquant_strategy(JoinQuantStrategyExportConfig())
        self.assertIn("g.fixed_stock_pool = []", rendered)
        self.assertIn("get_candidate_stocks", rendered)


if __name__ == "__main__":
    unittest.main()
