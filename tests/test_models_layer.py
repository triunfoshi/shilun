import unittest
from pathlib import Path
import sys

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from shilun.models import DatasetBuilder, LogisticRegressionEntryModel, RuleFallbackModel
from shilun.models.labeling import (
    label_acceptance_1d,
    label_breakout_success,
    label_continue_10d,
    label_drawdown_bucket,
    label_entry_success_3d,
    label_exhaustion_5d,
    label_fail_fast_3d,
    label_fail_5d,
    label_return_profile,
)


class ModelsLayerTests(unittest.TestCase):
    def setUp(self) -> None:
        dates = pd.date_range("2026-01-01", periods=40, freq="D")
        closes = pd.Series([10 + i * 0.1 + (0.2 if i % 7 == 0 else -0.1 if i % 9 == 0 else 0) for i in range(40)])
        self.df = pd.DataFrame(
            {
                "ticker": "000001.SZ",
                "date": dates,
                "open": closes - 0.05,
                "high": closes + 0.15,
                "low": closes - 0.18,
                "close": closes,
                "volume": [1000 + i * 20 for i in range(40)],
            }
        )

    def test_labeling_functions_emit_series(self) -> None:
        continue_label = label_continue_10d(self.df)
        breakout_label = label_breakout_success(self.df)
        fail_label = label_fail_5d(self.df)
        fail_fast_label = label_fail_fast_3d(self.df)
        acceptance_label = label_acceptance_1d(self.df)
        entry_success_label = label_entry_success_3d(self.df)
        exhaustion_label = label_exhaustion_5d(self.df)
        return_profile = label_return_profile(self.df)
        drawdown_bucket = label_drawdown_bucket(self.df)

        self.assertEqual(len(self.df), len(continue_label))
        self.assertEqual(len(self.df), len(breakout_label))
        self.assertEqual(len(self.df), len(fail_label))
        self.assertEqual(len(self.df), len(fail_fast_label))
        self.assertEqual(len(self.df), len(acceptance_label))
        self.assertEqual(len(self.df), len(entry_success_label))
        self.assertEqual(len(self.df), len(exhaustion_label))
        self.assertEqual(len(self.df), len(return_profile))
        self.assertEqual(len(self.df), len(drawdown_bucket))

    def test_dataset_builder_time_split(self) -> None:
        features = pd.DataFrame(
            {
                "ticker": ["000001.SZ"] * 10,
                "date": pd.date_range("2026-01-01", periods=10, freq="D"),
                "feature_a": range(10),
            }
        )
        labels = pd.DataFrame(
            {
                "ticker": ["000001.SZ"] * 10,
                "date": pd.date_range("2026-01-01", periods=10, freq="D"),
                "label_a": [0, 1] * 5,
            }
        )
        dataset = DatasetBuilder().build(features, labels, key_columns=["ticker", "date"])
        split = DatasetBuilder().time_split(dataset)
        self.assertEqual(10, len(dataset))
        self.assertGreater(len(split.train), len(split.validation))
        self.assertGreater(len(split.test), 0)

    def test_rule_fallback_model_emits_probability_bundle(self) -> None:
        prediction = RuleFallbackModel().predict(
            market_features={
                "price_vs_ma20_z": 1.2,
                "trend_r2_20": 0.7,
                "efficiency_ratio_20": 0.65,
                "breakout_volume_percentile": 0.8,
                "pullback_volume_shrink_ratio": 0.6,
                "atr_pct": 0.04,
            },
            structure_features={
                "segment_direction": "up",
                "leave_center_strength": 0.9,
                "return_test_depth": 0.2,
                "divergence_score": 0.1,
                "divergence_state": "none",
                "latest_event_type": "breakout_up",
            },
        )
        payload = prediction.to_dict()
        self.assertIn(payload["regime_label"], {"strong_up", "weak_up", "range", "weak_down", "risk_reversal"})
        self.assertGreater(payload["p_continue_10d"], 0)
        self.assertLess(payload["p_continue_10d"], 1)
        self.assertIsNotNone(payload["p_acceptance_1d"])
        self.assertIsNotNone(payload["p_fail_fast_3d"])
        self.assertIsNotNone(payload["entry_probability"])
        self.assertIn(payload["entry_zone"], {"ready", "candidate", "watch", "avoid"})
        self.assertGreater(payload["risk_level"], 0)

    def test_logistic_entry_model_can_fit_and_predict(self) -> None:
        X = pd.DataFrame(
            {
                "position_state": ["low_base", "rising", "high_zone", "downtrend"],
                "volume_pattern": ["pullback_shrink", "gentle_expand", "distribution", "high_level_stall"],
                "acceptance_strength": [0.72, 0.66, 0.28, 0.18],
                "p_fail_fast_3d": [0.14, 0.22, 0.71, 0.64],
            }
        )
        y = pd.Series([1, 1, 0, 0])
        model = LogisticRegressionEntryModel(feature_names=list(X.columns)).fit(X, y)
        prediction = model.predict(
            {
                "position_state": "rising",
                "volume_pattern": "gentle_expand",
                "acceptance_strength": 0.61,
                "p_fail_fast_3d": 0.20,
            }
        )
        self.assertIsNotNone(prediction.entry_probability)
        self.assertIn(prediction.entry_zone, {"ready", "candidate", "watch", "avoid"})


if __name__ == "__main__":
    unittest.main()
