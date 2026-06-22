import tempfile
import unittest
from pathlib import Path
import sys
import os

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from shilun.models import LightGBMEventModel, LightGBMRegimeModel, LightGBMRiskModel, ModelRegistry


class LightGBMModelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if os.environ.get("RUN_LIGHTGBM_TESTS") != "1":
            raise unittest.SkipTest("Set RUN_LIGHTGBM_TESTS=1 to run LightGBM integration tests.")

    def setUp(self) -> None:
        self.X = pd.DataFrame(
            {
                "feature_a": [0.1, 0.3, 0.5, 0.8, 1.0, -0.2, -0.5, -0.8],
                "feature_b": [0.2, 0.1, 0.7, 0.9, 0.6, -0.1, -0.4, -0.7],
                "feature_c": [1, 1, 1, 1, 1, 0, 0, 0],
                "segment_direction": ["up", "up", "up", "up", "up", "down", "down", "down"],
            }
        )

    def test_regime_model_fit_predict_save_load(self) -> None:
        y = pd.Series(["weak_up", "weak_up", "strong_up", "strong_up", "strong_up", "range", "risk_reversal", "risk_reversal"])
        model = LightGBMRegimeModel(feature_names=list(self.X.columns)).fit(self.X, y)
        prediction = model.predict({"feature_a": 0.9, "feature_b": 0.7, "feature_c": 1})
        self.assertIn(prediction.regime_label, {"weak_up", "strong_up", "range", "risk_reversal"})

        with tempfile.TemporaryDirectory() as tmpdir:
            artifact = model.save(Path(tmpdir) / "regime_model.joblib")
            loaded = LightGBMRegimeModel.load(artifact.model_path)
            loaded_prediction = loaded.predict({"feature_a": 0.9, "feature_b": 0.7, "feature_c": 1})
            self.assertEqual(prediction.regime_label, loaded_prediction.regime_label)

    def test_event_and_risk_models_fit_predict_and_registry_load(self) -> None:
        event_labels = pd.DataFrame(
            {
                "continue_10d": [1, 1, 1, 1, 1, 0, 0, 0],
                "breakout_success": [0, 1, 1, 1, 1, 0, 0, 0],
                "fail_5d": [0, 0, 0, 0, 0, 1, 1, 1],
                "acceptance_1d": [1, 1, 1, 1, 1, 0, 0, 0],
                "fail_fast_3d": [0, 0, 0, 0, 0, 1, 1, 1],
            }
        )
        risk_targets = pd.DataFrame(
            {
                "expected_return_10d": [0.01, 0.03, 0.05, 0.07, 0.08, -0.01, -0.03, -0.05],
                "expected_drawdown_10d": [0.03, 0.025, 0.02, 0.018, 0.015, 0.05, 0.07, 0.08],
            }
        )

        event_model = LightGBMEventModel(feature_names=list(self.X.columns)).fit(self.X, event_labels)
        risk_model = LightGBMRiskModel(feature_names=list(self.X.columns)).fit(self.X, risk_targets)
        event_prediction = event_model.predict({"feature_a": 0.9, "feature_b": 0.7, "feature_c": 1})
        risk_prediction = risk_model.predict({"feature_a": 0.9, "feature_b": 0.7, "feature_c": 1})
        self.assertGreater(event_prediction.p_continue_10d, 0)
        self.assertIsNotNone(event_prediction.p_acceptance_1d)
        self.assertIsNotNone(event_prediction.p_fail_fast_3d)
        self.assertGreaterEqual(risk_prediction.risk_level, 0)

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            regime_model = LightGBMRegimeModel(feature_names=list(self.X.columns)).fit(
                self.X,
                pd.Series(["weak_up", "weak_up", "strong_up", "strong_up", "strong_up", "range", "risk_reversal", "risk_reversal"]),
            )
            regime_model.save(tmp_path / "regime_model.joblib")
            event_model.save(tmp_path / "event_model.joblib")
            risk_model.save(tmp_path / "risk_model.joblib")
            registry = ModelRegistry(tmp_path)
            self.assertTrue(registry.has_all_models())
            self.assertIsNotNone(registry.load_regime_model())
            self.assertIsNotNone(registry.load_event_model())
            self.assertIsNotNone(registry.load_risk_model())


if __name__ == "__main__":
    unittest.main()
