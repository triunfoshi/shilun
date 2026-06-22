import unittest
from pathlib import Path
import sys

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from shilun.features import compute_entry_features
from shilun.indicators import compute_trend_features


class EntryFeatureTests(unittest.TestCase):
    def _build_bars(self, periods: int = 40) -> pd.DataFrame:
        dates = pd.date_range("2026-01-01", periods=periods, freq="D")
        rows = []
        for i, date in enumerate(dates):
            close = 10 + i * 0.08 + (0.25 if i % 6 == 0 else -0.12 if i % 7 == 0 else 0.05)
            high = close + 0.35 + (0.18 if i % 9 == 0 else 0.0)
            low = close - 0.30 - (0.12 if i % 8 == 0 else 0.0)
            open_price = close - 0.08 + (0.05 if i % 5 == 0 else 0.0)
            volume = 1000 + i * 25 + (220 if i % 10 == 0 else 0)
            rows.append(
                {
                    "ticker": "000001.SZ",
                    "date": date,
                    "open": open_price,
                    "high": high,
                    "low": low,
                    "close": close,
                    "volume": volume,
                    "amount": volume * close,
                }
            )
        return pd.DataFrame(rows)

    def test_entry_features_do_not_change_past_rows_when_future_rows_are_appended(self) -> None:
        short_bars = self._build_bars(32)
        long_bars = self._build_bars(40)

        short_features = compute_entry_features(compute_trend_features(short_bars))
        long_features = compute_entry_features(compute_trend_features(long_bars))

        compare_columns = [
            "close_near_high_pct",
            "upper_shadow_ratio",
            "body_ratio",
            "volume_spike_ratio",
            "ma20_slope_5",
            "acceleration_score",
            "breakout_confirm_flag",
            "false_breakout_risk_flag",
            "trend_truth_score",
            "buy_readiness_score",
            "acceptance_strength",
            "trigger_strength",
        ]
        left = short_features.loc[:, compare_columns].reset_index(drop=True)
        right = long_features.loc[: len(short_features) - 1, compare_columns].reset_index(drop=True)
        pd.testing.assert_frame_equal(left, right)

    def test_entry_features_emit_trigger_columns(self) -> None:
        features = compute_entry_features(compute_trend_features(self._build_bars()))
        latest = features.iloc[-1]

        self.assertIn("trigger_strength", features.columns)
        self.assertIn("volume_pattern", features.columns)
        self.assertIn("position_state", features.columns)
        self.assertIn("trend_truth_score", features.columns)
        self.assertIn("buy_readiness_score", features.columns)
        self.assertIn("false_breakout_risk_flag", features.columns)
        self.assertIn("acceptance_strength", features.columns)
        self.assertGreaterEqual(float(latest["trigger_strength"]), 0.0)
        self.assertLessEqual(float(latest["trigger_strength"]), 100.0)
        self.assertGreaterEqual(float(latest["trend_truth_score"]), 0.0)
        self.assertLessEqual(float(latest["buy_readiness_score"]), 100.0)
        self.assertIn(int(latest["breakout_confirm_flag"]), {0, 1})
        self.assertIn(int(latest["false_breakout_risk_flag"]), {0, 1})
        self.assertIn(str(latest["volume_pattern"]), {"neutral", "gentle_expand", "impulsive_spike", "pullback_shrink", "high_level_stall", "down_shrink", "distribution"})

    def test_entry_features_emit_pattern_and_stage_columns(self) -> None:
        features = compute_entry_features(compute_trend_features(self._build_bars(80)))
        latest = features.iloc[-1]

        required_columns = [
            "vol_pct_60",
            "gentle_expand_score",
            "pullback_shrink_score",
            "impulsive_spike_score",
            "distribution_score",
            "stall_score",
            "early_stage_score_base",
            "mid_stage_score_base",
            "late_stage_score_base",
        ]
        for column in required_columns:
            self.assertIn(column, features.columns)
            self.assertGreaterEqual(float(latest[column]), 0.0)
            self.assertLessEqual(float(latest[column]), 100.0 if column != "vol_pct_60" else 1.0)


if __name__ == "__main__":
    unittest.main()
