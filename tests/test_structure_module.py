import unittest
from pathlib import Path
import sys

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from shilun.structure import (
    BiBuilder,
    CenterDetector,
    DivergenceDetector,
    EventEngine,
    PivotDetector,
    SegmentBuilder,
    SwingBuilder,
    ZoneDetector,
)


class StructureModuleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.df = pd.DataFrame(
            [
                {"ticker": "000001.SZ", "date": "2026-03-01", "high": 10.0, "low": 9.0, "close": 9.5, "volume": 100.0},
                {"ticker": "000001.SZ", "date": "2026-03-02", "high": 11.0, "low": 9.6, "close": 10.2, "volume": 110.0},
                {"ticker": "000001.SZ", "date": "2026-03-03", "high": 14.0, "low": 10.1, "close": 13.6, "volume": 180.0},
                {"ticker": "000001.SZ", "date": "2026-03-04", "high": 11.2, "low": 9.2, "close": 9.8, "volume": 120.0},
                {"ticker": "000001.SZ", "date": "2026-03-05", "high": 10.7, "low": 8.0, "close": 8.6, "volume": 160.0},
                {"ticker": "000001.SZ", "date": "2026-03-06", "high": 11.0, "low": 9.1, "close": 10.1, "volume": 130.0},
                {"ticker": "000001.SZ", "date": "2026-03-07", "high": 13.0, "low": 10.0, "close": 12.3, "volume": 170.0},
                {"ticker": "000001.SZ", "date": "2026-03-08", "high": 11.4, "low": 8.5, "close": 9.1, "volume": 115.0},
                {"ticker": "000001.SZ", "date": "2026-03-09", "high": 12.0, "low": 9.0, "close": 11.4, "volume": 140.0},
                {"ticker": "000001.SZ", "date": "2026-03-10", "high": 15.0, "low": 10.4, "close": 14.7, "volume": 210.0},
            ]
        )

    def test_structure_pipeline_builds_domain_objects(self) -> None:
        pivots = PivotDetector().detect(self.df, left_bars=1, right_bars=1)
        self.assertEqual(["high", "low", "high", "low"], [pivot.pivot_type for pivot in pivots])
        self.assertEqual(0.0, pivots[0].left_strength)
        self.assertFalse(pivots[0].invalidated)

        swings = SwingBuilder().build(pivots)
        self.assertEqual(3, len(swings))
        self.assertEqual("down", swings[0].direction)
        self.assertEqual(2, swings[0].start_idx)
        self.assertEqual(4, swings[0].end_idx)
        self.assertAlmostEqual((14.0 - 8.0) / 8.0, swings[0].amplitude_pct)

        zones = ZoneDetector().detect(swings)
        self.assertEqual(1, len(zones))
        self.assertEqual(3, zones[0].segment_count)
        self.assertAlmostEqual((zones[0].zone_upper + zones[0].zone_lower) / 2, zones[0].center_mid)
        self.assertAlmostEqual(zones[0].zone_upper - zones[0].zone_lower, zones[0].width_abs)

    def test_event_engine_marks_breakout_with_index(self) -> None:
        pivots = PivotDetector().detect(self.df, left_bars=1, right_bars=1)
        swings = SwingBuilder().build(pivots)
        zones = ZoneDetector().detect(swings)

        events = EventEngine().detect(self.df, zones)
        self.assertEqual(1, len(events))
        self.assertEqual("breakout_up", events[0].event_type)
        self.assertEqual(9, events[0].index)
        self.assertEqual(14.7, events[0].trigger_price)

    def test_new_structure_builders_emit_segments_centers_and_divergence(self) -> None:
        pivots = PivotDetector().detect(self.df, left_bars=1, right_bars=1)
        bis = BiBuilder().build(pivots, bars_df=self.df)
        self.assertEqual(3, len(bis))
        self.assertIsNotNone(bis[0].volume_sum)
        self.assertIsNotNone(bis[0].impulse_score)

        segments = SegmentBuilder().build(bis)
        self.assertEqual(3, len(segments))
        self.assertEqual(1, segments[0].bi_count)
        self.assertEqual("down", segments[0].direction)
        self.assertIsNotNone(segments[0].break_price)

        centers = CenterDetector().detect(segments)
        self.assertEqual(1, len(centers))
        self.assertTrue(centers[-1].active)
        self.assertGreater(centers[-1].overlap_strength, 0)

        divergence = DivergenceDetector().detect(segments)
        self.assertIn(divergence.divergence_state, {"none", "mild", "confirmed"})
        self.assertGreaterEqual(divergence.divergence_score, 0.0)
        self.assertLessEqual(divergence.divergence_score, 1.0)


if __name__ == "__main__":
    unittest.main()
