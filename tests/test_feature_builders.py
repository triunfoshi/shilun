import unittest
from pathlib import Path
import sys

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from shilun.features import StructureFeatureBuilder
from shilun.indicators import compute_trend_features, compute_volatility_features, compute_volume_features
from shilun.structure import BiBuilder, CenterDetector, DivergenceDetector, PivotDetector, SegmentBuilder


class FeatureBuilderTests(unittest.TestCase):
    def setUp(self) -> None:
        dates = pd.date_range("2026-01-01", periods=140, freq="D")
        rows = []
        for i, date in enumerate(dates):
            base = 10 + i * 0.04
            close = base + (0.9 if i % 13 == 0 else -0.7 if i % 8 == 0 else 0.15)
            rows.append(
                {
                    "ticker": "000001.SZ",
                    "date": date,
                    "open": close - 0.1,
                    "high": close + 0.5,
                    "low": close - 0.6,
                    "close": close,
                    "volume": 1200 + i * 12,
                    "amount": (1200 + i * 12) * close,
                }
            )
        self.df = pd.DataFrame(rows)

    def test_indicator_stack_emits_extended_features(self) -> None:
        features = compute_trend_features(self.df)
        features = compute_volatility_features(features)
        features = compute_volume_features(features)
        latest = features.iloc[-1]

        for key in [
            "return_10d",
            "return_20d",
            "ma60_slope",
            "atr_14",
            "atr_pct",
            "realized_vol_20",
            "price_vs_ma20_z",
            "trend_r2_20",
            "efficiency_ratio_20",
            "obv_slope_10",
            "vwap_distance",
            "breakout_volume_percentile",
        ]:
            self.assertIn(key, features.columns)
            self.assertFalse(pd.isna(latest[key]), key)

    def test_structure_feature_builder_flattens_structure_objects(self) -> None:
        features = compute_trend_features(self.df)
        features = compute_volatility_features(features)
        pivots = PivotDetector().detect(features, left_bars=1, right_bars=1)
        bis = BiBuilder().build(pivots, bars_df=features)
        segments = SegmentBuilder().build(bis)
        centers = CenterDetector().detect(segments)
        divergence = DivergenceDetector().detect(segments)

        structure_features = StructureFeatureBuilder().build(bis, segments, centers, divergence, [])
        self.assertIn("last_bi_direction", structure_features)
        self.assertIn("segment_direction", structure_features)
        self.assertIn("divergence_score", structure_features)
        self.assertIn("leave_center_strength", structure_features)


if __name__ == "__main__":
    unittest.main()
