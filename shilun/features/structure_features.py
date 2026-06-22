from __future__ import annotations

from typing import Any, Sequence

from shilun.structure import Bi, Center, DivergenceSignal, Segment, StructureEvent


class StructureFeatureBuilder:
    """Flatten structure objects into model-friendly feature dictionaries."""

    def build(
        self,
        bis: Sequence[Bi],
        segments: Sequence[Segment],
        centers: Sequence[Center],
        divergence: DivergenceSignal,
        events: Sequence[StructureEvent],
    ) -> dict[str, Any]:
        latest_bi = bis[-1] if bis else None
        latest_segment = segments[-1] if segments else None
        latest_center = centers[-1] if centers else None
        latest_event = events[-1] if events else None

        return {
            "bi_count": len(bis),
            "segment_count": len(segments),
            "center_count": len(centers),
            "last_bi_direction": latest_bi.direction if latest_bi else None,
            "last_bi_amplitude_pct": latest_bi.amplitude_pct if latest_bi else None,
            "last_bi_bars": latest_bi.bars if latest_bi else None,
            "last_bi_impulse_score": latest_bi.impulse_score if latest_bi else None,
            "last_3_bi_up_ratio": self._last_3_bi_up_ratio(bis),
            "segment_direction": latest_segment.direction if latest_segment else None,
            "segment_amplitude_pct": latest_segment.amplitude_pct if latest_segment else None,
            "segment_bi_count": latest_segment.bi_count if latest_segment else None,
            "segment_impulse_score": latest_segment.impulse_score if latest_segment else None,
            "active_center_flag": int(latest_center is not None),
            "center_width_pct": latest_center.width_pct if latest_center else None,
            "center_touch_count": latest_center.touch_count if latest_center else None,
            "center_shift_direction": latest_center.shift_direction if latest_center else None,
            "center_shift_pct": latest_center.shift_pct if latest_center else None,
            "distance_to_center_upper": latest_segment.high - latest_center.zone_upper if latest_segment and latest_center else None,
            "distance_to_center_lower": latest_segment.low - latest_center.zone_lower if latest_segment and latest_center else None,
            "leave_center_strength": self._leave_center_strength(latest_segment, latest_center),
            "return_test_depth": self._return_test_depth(latest_segment, latest_center),
            "latest_event_type": latest_event.event_type if latest_event else None,
            "divergence_score": divergence.divergence_score,
            "divergence_state": divergence.divergence_state,
        }

    @staticmethod
    def _last_3_bi_up_ratio(bis: Sequence[Bi]) -> float | None:
        if not bis:
            return None
        window = list(bis[-3:])
        return sum(1 for bi in window if bi.direction == "up") / len(window)

    @staticmethod
    def _leave_center_strength(segment: Segment | None, center: Center | None) -> float | None:
        if segment is None or center is None or center.width_abs in (None, 0):
            return None
        if segment.direction == "up":
            return (segment.high - center.zone_upper) / center.width_abs
        return (center.zone_lower - segment.low) / center.width_abs

    @staticmethod
    def _return_test_depth(segment: Segment | None, center: Center | None) -> float | None:
        if segment is None or center is None or center.width_abs in (None, 0):
            return None
        if segment.direction == "up":
            return max(0.0, center.zone_upper - segment.low) / center.width_abs
        return max(0.0, segment.high - center.zone_lower) / center.width_abs
