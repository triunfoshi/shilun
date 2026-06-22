import tempfile
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from shilun.models.schema import EventPrediction, ModelPredictionBundle, RegimePrediction, RiskPrediction
from shilun.pipeline import PipelineConfig, ShilunPipeline


class FakeImporter:
    def import_daily(self, ts_code: str, start_date: str, end_date: str):
        raise NotImplementedError


class DummyPredictor:
    def predict(self, market_features, structure_features):
        return ModelPredictionBundle(
            regime=RegimePrediction(regime_label="strong_up", regime_score=88.0, regime_confidence=0.81),
            event=EventPrediction(p_continue_10d=0.73, p_breakout_success=0.66, p_fail_5d=0.19),
            risk=RiskPrediction(expected_return_10d=0.061, expected_drawdown_10d=0.028, risk_level=0.19),
            model_version="dummy_bundle_v1",
        )


class ExplodingPredictor:
    def predict(self, market_features, structure_features):
        raise RuntimeError("boom")


class PipelineModelLoadingTests(unittest.TestCase):
    def test_pipeline_uses_explicit_predictor_when_provided(self) -> None:
        pipeline = ShilunPipeline(config=PipelineConfig(), importer=FakeImporter(), model_predictor=DummyPredictor())
        self.assertEqual("DummyPredictor", pipeline.model.__class__.__name__)

    def test_pipeline_falls_back_when_model_dir_missing(self) -> None:
        pipeline = ShilunPipeline(
            config=PipelineConfig(model_dir="/tmp/definitely_missing_model_dir_for_shilun"),
            importer=FakeImporter(),
        )
        self.assertEqual("RuleFallbackModel", pipeline.model.__class__.__name__)

    def test_pipeline_wraps_trained_model_with_safe_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            pipeline = ShilunPipeline(config=PipelineConfig(model_dir=tmpdir), importer=FakeImporter(), model_predictor=None)
            self.assertEqual("RuleFallbackModel", pipeline.model.__class__.__name__)

    def test_safe_prediction_path_can_be_injected(self) -> None:
        from shilun.models import RuleFallbackModel, SafePredictionModel

        safe_model = SafePredictionModel(primary=ExplodingPredictor(), fallback=RuleFallbackModel())
        result = safe_model.predict(
            market_features={"price_vs_ma20_z": 1.0, "trend_r2_20": 0.5, "efficiency_ratio_20": 0.4},
            structure_features={"segment_direction": "up", "leave_center_strength": 0.3, "divergence_score": 0.1},
        )
        self.assertEqual("rule_fallback_v1", result.model_version)


if __name__ == "__main__":
    unittest.main()
