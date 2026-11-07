from __future__ import annotations

import unittest

from shilun.trading.risk import (
    AccountRiskProfile,
    DynamicRiskInput,
    RiskConfigurationError,
    calculate_dynamic_target,
)


class TradingRiskTests(unittest.TestCase):
    def test_dynamic_target_selects_largest_lot_within_absolute_loss_budget(self) -> None:
        profile = AccountRiskProfile(
            total_assets=100_000,
            max_loss_per_trade=7_000,
            conservative_fee_buffer_rate=0,
        )
        result = calculate_dynamic_target(
            DynamicRiskInput(
                current_quantity=1_000,
                cost_basis=100,
                current_price=95,
                effective_stop=90,
            ),
            profile,
        )
        self.assertEqual(result.status, "reduce_required")
        self.assertEqual(result.target_quantity, 400)
        self.assertEqual(result.change_quantity, -600)
        self.assertEqual(result.target_worst_loss, 7_000)

    def test_budget_breach_is_explicit_when_even_exit_cannot_recover_it(self) -> None:
        profile = AccountRiskProfile(
            total_assets=100_000,
            max_loss_per_trade=7_000,
            conservative_fee_buffer_rate=0,
        )
        result = calculate_dynamic_target(
            DynamicRiskInput(
                current_quantity=1_000,
                cost_basis=100,
                current_price=95,
                effective_stop=90,
                realized_loss_amount=3_000,
            ),
            profile,
        )
        self.assertEqual(result.status, "budget_breached")
        self.assertEqual(result.target_quantity, 0)
        self.assertTrue(result.budget_breached_even_after_exit)

    def test_stop_above_market_does_not_assume_rebound(self) -> None:
        profile = AccountRiskProfile(
            total_assets=100_000,
            max_loss_per_trade=20_000,
            conservative_fee_buffer_rate=0,
        )
        result = calculate_dynamic_target(
            DynamicRiskInput(1_000, 100, 90, 95),
            profile,
        )
        self.assertEqual(result.effective_stop, 90)
        self.assertEqual(result.current_exit_loss, result.full_hold_worst_loss)

    def test_auto_trade_cannot_be_enabled(self) -> None:
        with self.assertRaises(RiskConfigurationError):
            AccountRiskProfile(auto_trade_enabled=True)


if __name__ == "__main__":
    unittest.main()
