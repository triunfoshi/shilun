import unittest
from pathlib import Path
import sys

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from shilun.pipeline import PipelineConfig, ShilunPipeline


class FakeImporter:
    def import_daily(self, ts_code: str, start_date: str, end_date: str):
        dates = pd.date_range("2026-01-01", periods=100, freq="D")
        rows = []
        for i, date in enumerate(dates):
            base = 10 + i * 0.05
            close = base + (0.8 if i % 7 == 0 else -0.4 if i % 5 == 0 else 0.2)
            high = close + 0.6 + (0.2 if i % 9 == 0 else 0.0)
            low = close - 0.7 - (0.2 if i % 11 == 0 else 0.0)
            rows.append(
                {
                    "ticker": ts_code,
                    "date": date,
                    "open": close - 0.1,
                    "high": high,
                    "low": low,
                    "close": close,
                    "volume": 1000 + i * 10,
                    "amount": (1000 + i * 10) * close,
                }
            )
        return pd.DataFrame(rows), object()


class PipelineStructureContextTests(unittest.TestCase):
    def test_pipeline_emits_structure_context_snapshot(self) -> None:
        pipeline = ShilunPipeline(config=PipelineConfig(min_required_rows=80), importer=FakeImporter())
        bars = pipeline._load_daily_bars(ticker="000001.SZ", analysis_date="2026-04-10")
        payload = pipeline._build_snapshot_payload(ticker="000001.SZ", analysis_date="2026-04-10", bars=bars)
        result = pipeline.run(ticker="000001.SZ", analysis_date="2026-04-10")
        snapshot = result["snapshot"]

        self.assertIn("structure_context", payload)
        self.assertIn("feature_context", payload)
        self.assertIn("model_context", payload)
        self.assertIn("market_context", payload)
        self.assertIn("evidence_sections", payload)
        self.assertIn("structure_assessment", payload)
        self.assertIn("trigger_context", payload)
        self.assertIn("trend_stage", payload)
        self.assertIn("execution_score", payload)
        self.assertIn("action_label", payload)
        self.assertIn("target_position_pct", payload)
        self.assertIn("fundamental_context", payload)
        self.assertIn("sector_context", payload)
        self.assertIn("chip_context", payload)
        self.assertIn("pattern_context", payload)
        self.assertIn("trend_stage_context", payload)
        self.assertIn("reason_codes", payload)
        self.assertIn("segment_direction", payload)
        self.assertIn("divergence_state", payload)
        self.assertIsInstance(payload["structure_context"], dict)
        self.assertIsInstance(payload["trigger_context"], dict)
        self.assertIsInstance(payload["feature_context"], dict)
        self.assertIsInstance(payload["model_context"], dict)
        self.assertIsInstance(payload["market_context"], dict)
        self.assertIsInstance(payload["evidence_sections"], dict)
        self.assertIsInstance(payload["structure_assessment"], dict)
        self.assertGreaterEqual(payload["structure_context"]["pivot_count"], 1)
        self.assertIn("atr_pct", payload["feature_context"])
        self.assertIn("trigger_strength", payload["feature_context"])
        self.assertIn("volume_pattern", payload["feature_context"])
        self.assertIn("position_state", payload["feature_context"])
        self.assertIn("excess_return_5d", payload["feature_context"])
        self.assertIn("regime_label", snapshot)
        self.assertIn("p_continue_10d", snapshot)
        self.assertIn("trigger_score", snapshot)
        self.assertIn("trigger_state", snapshot)
        self.assertIn("opportunity_type", snapshot)
        self.assertIn("entry_probability", snapshot)
        self.assertIn("entry_zone", snapshot)
        self.assertIn("trend_stage", snapshot)
        self.assertIn("trend_stage_confidence", snapshot)
        self.assertIn("early_stage_score", snapshot)
        self.assertIn("mid_stage_score", snapshot)
        self.assertIn("late_stage_score", snapshot)
        self.assertIn("gentle_expand_score", snapshot)
        self.assertIn("pullback_shrink_score", snapshot)
        self.assertIn("distribution_score", snapshot)
        self.assertIn("stall_score", snapshot)
        self.assertIn("dominant_positive_pattern", snapshot)
        self.assertIn("dominant_risk_pattern", snapshot)
        self.assertIn("execution_score", snapshot)
        self.assertIn("action_label", snapshot)
        self.assertIn("target_position_pct", snapshot)
        self.assertIn("trigger_context", snapshot)
        self.assertIn("support_basis", snapshot)
        self.assertIn("resistance_basis", snapshot)
        self.assertIsInstance(snapshot["pattern_context"], dict)
        self.assertIsInstance(snapshot["trend_stage_context"], dict)
        self.assertIsInstance(snapshot["reason_codes"], list)
        self.assertIn("模型层判断为", "\n".join(snapshot["evidence"]))
        self.assertIn("机会类型归为", "\n".join(snapshot["evidence"]))
        self.assertIn("最近线段方向为", "\n".join(snapshot["evidence"]))
        self.assertIn("相对基准", "\n".join(snapshot["evidence"]))
        self.assertIn("阶段分 early/mid/late", "\n".join(snapshot["evidence"]))
        self.assertIn("模式分 gentle/pullback/impulse/distribution/stall", "\n".join(snapshot["evidence"]))

    def test_pipeline_can_run_directly_from_supplied_bars(self) -> None:
        pipeline = ShilunPipeline(config=PipelineConfig(min_required_rows=80), importer=FakeImporter())
        stock_bars, _ = FakeImporter().import_daily("000001.SZ", "20260101", "20260410")
        benchmark_bars, _ = FakeImporter().import_daily("000300.SH", "20260101", "20260410")

        result = pipeline.run_with_bars(
            ticker="000001.SZ",
            analysis_date="2026-04-10",
            bars=stock_bars,
            benchmark_bars=benchmark_bars,
        )

        self.assertEqual("000001.SZ", result["ticker"])
        self.assertIn("decision", result)
        self.assertIn("snapshot", result)
        self.assertIn("market_context", result["snapshot"])
        self.assertEqual("000300.SH", result["snapshot"]["market_context"]["benchmark_ticker"])


if __name__ == "__main__":
    unittest.main()
