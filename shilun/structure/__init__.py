from __future__ import annotations

import sys
from dataclasses import asdict, dataclass, field
from typing import Any, Iterable, Sequence

import pandas as pd


@dataclass
class Pivot:
    pivot_type: str
    date: str
    price: float
    index: int
    left_strength: float = 0.0
    right_strength: float = 0.0
    confirm_bars: int = 0
    invalidated: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Bi:
    direction: str
    start_date: str
    end_date: str
    high: float
    low: float
    amplitude: float
    bars: int
    start_idx: int | None = None
    end_idx: int | None = None
    start_price: float | None = None
    end_price: float | None = None
    amplitude_pct: float | None = None
    slope: float | None = None
    volume_sum: float | None = None
    volume_mean: float | None = None
    atr_mean: float | None = None
    impulse_score: float | None = None
    retracement_ratio: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Segment:
    direction: str
    start_date: str
    end_date: str
    high: float
    low: float
    amplitude: float
    bars: int
    bi_count: int = 0
    start_idx: int | None = None
    end_idx: int | None = None
    amplitude_pct: float | None = None
    slope: float | None = None
    trend_fit_r2: float | None = None
    efficiency_ratio: float | None = None
    volume_sum: float | None = None
    impulse_score: float | None = None
    break_price: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Center:
    zone_upper: float
    zone_lower: float
    touch_count: int
    start_date: str
    end_date: str
    start_idx: int | None = None
    end_idx: int | None = None
    center_mid: float | None = None
    width_abs: float | None = None
    width_pct: float | None = None
    segment_count: int = 0
    overlap_strength: float | None = None
    shift_direction: str = "flat"
    shift_pct: float | None = None
    active: bool = True
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class StructureEvent:
    event_type: str
    date: str
    trigger_price: float
    note: str
    index: int | None = None
    source_center_id: str | None = None
    source_segment_id: str | None = None
    confirm_score: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class DivergenceSignal:
    divergence_score: float
    divergence_state: str
    note: str


@dataclass(frozen=True)
class StructureAssessment:
    trend_bias: str
    structure_stage: str
    confirmation_state: str
    strength_score: int
    confirmation_score: int
    risk_score: int
    confidence: float
    support_level: float | None
    resistance_level: float | None
    invalidation_level: float | None
    support_basis: str | None = None
    resistance_basis: str | None = None
    adaptive_thresholds: dict[str, float] = field(default_factory=dict)
    supporting_evidence: list[str] = field(default_factory=list)
    opposing_evidence: list[str] = field(default_factory=list)
    watch_items: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class PivotDetector:
    """Detect pivot highs and lows from a rolling local window."""

    def detect(self, df: pd.DataFrame, left_bars: int = 3, right_bars: int = 3) -> list[Pivot]:
        pivots: list[Pivot] = []
        highs = df["high"].tolist()
        lows = df["low"].tolist()
        dates = df["date"].astype(str).tolist()
        for i in range(left_bars, len(df) - right_bars):
            high_window = highs[i - left_bars : i + right_bars + 1]
            low_window = lows[i - left_bars : i + right_bars + 1]
            if highs[i] == max(high_window):
                pivots.append(Pivot("high", dates[i], float(highs[i]), i))
            if lows[i] == min(low_window):
                pivots.append(Pivot("low", dates[i], float(lows[i]), i))
        return pivots


class BiBuilder:
    """Build simplified Chan-style bi structures from alternating pivots."""

    def build(self, pivots: list[Pivot], bars_df: pd.DataFrame | None = None) -> list[Bi]:
        ordered = sorted(pivots, key=lambda pivot: pivot.index)
        bis: list[Bi] = []
        previous_bi: Bi | None = None
        for left, right in zip(ordered, ordered[1:]):
            if left.pivot_type == right.pivot_type:
                continue
            bi = self._build_single_bi(left, right, bars_df=bars_df, previous_bi=previous_bi)
            bis.append(bi)
            previous_bi = bi
        return bis

    def _build_single_bi(
        self,
        left: Pivot,
        right: Pivot,
        bars_df: pd.DataFrame | None,
        previous_bi: Bi | None,
    ) -> Bi:
        high = max(left.price, right.price)
        low = min(left.price, right.price)
        amplitude_abs = high - low
        amplitude_pct = (right.price / left.price - 1.0) if left.price else None
        bars = right.index - left.index
        slope = (right.price - left.price) / bars if bars else None

        window = self._slice_bars(bars_df, left.index, right.index)
        volume_sum = float(window["volume"].sum()) if window is not None and "volume" in window else None
        volume_mean = float(window["volume"].mean()) if window is not None and "volume" in window else None
        atr_mean = self._safe_mean(window, ("atr14", "atr_14"))
        impulse_score = self._compute_impulse_score(amplitude_pct, bars, volume_mean)
        retracement_ratio = None
        if previous_bi and previous_bi.amplitude not in (None, 0):
            retracement_ratio = amplitude_abs / previous_bi.amplitude

        return Bi(
            direction="up" if right.price > left.price else "down",
            start_date=left.date,
            end_date=right.date,
            high=high,
            low=low,
            amplitude=amplitude_abs,
            bars=bars,
            start_idx=left.index,
            end_idx=right.index,
            start_price=left.price,
            end_price=right.price,
            amplitude_pct=amplitude_pct,
            slope=slope,
            volume_sum=volume_sum,
            volume_mean=volume_mean,
            atr_mean=atr_mean,
            impulse_score=impulse_score,
            retracement_ratio=retracement_ratio,
        )

    @staticmethod
    def _slice_bars(bars_df: pd.DataFrame | None, start_idx: int, end_idx: int) -> pd.DataFrame | None:
        if bars_df is None or bars_df.empty:
            return None
        return bars_df.iloc[start_idx : end_idx + 1]

    @staticmethod
    def _safe_mean(window: pd.DataFrame | None, candidates: Iterable[str]) -> float | None:
        if window is None:
            return None
        for column in candidates:
            if column in window:
                value = window[column].mean()
                if pd.notna(value):
                    return float(value)
        return None

    @staticmethod
    def _compute_impulse_score(amplitude_pct: float | None, bars: int, volume_mean: float | None) -> float | None:
        if amplitude_pct is None or bars <= 0:
            return None
        pace = abs(amplitude_pct) / bars
        volume_adjust = 1.0
        if volume_mean is not None and volume_mean > 0:
            volume_adjust += min(1.0, volume_mean / 1_000_000)
        return pace * volume_adjust


class SegmentBuilder:
    """Build higher-level segments from contiguous bis with the same direction."""

    def build(self, bis: Sequence[Bi]) -> list[Segment]:
        if not bis:
            return []

        segments: list[Segment] = []
        current_group: list[Bi] = [bis[0]]
        for bi in bis[1:]:
            if bi.direction == current_group[-1].direction:
                current_group.append(bi)
                continue
            segments.append(self._build_segment(current_group))
            current_group = [bi]
        segments.append(self._build_segment(current_group))
        return segments

    @staticmethod
    def _build_segment(group: Sequence[Bi]) -> Segment:
        first = group[0]
        last = group[-1]
        highs = [item.high for item in group]
        lows = [item.low for item in group]
        amplitude = max(highs) - min(lows)
        total_bars = sum(item.bars for item in group)
        slope = None
        if first.start_price is not None and last.end_price is not None and total_bars:
            slope = (last.end_price - first.start_price) / total_bars

        volume_values = [item.volume_sum for item in group if item.volume_sum is not None]
        volume_sum = float(sum(volume_values)) if volume_values else None
        impulse_values = [item.impulse_score for item in group if item.impulse_score is not None]
        impulse_score = float(sum(impulse_values) / len(impulse_values)) if impulse_values else None
        amplitude_pct = None
        if first.start_price not in (None, 0) and last.end_price is not None:
            amplitude_pct = last.end_price / first.start_price - 1.0
        break_price = min(lows) if first.direction == "up" else max(highs)

        return Segment(
            direction=first.direction,
            start_date=first.start_date,
            end_date=last.end_date,
            high=max(highs),
            low=min(lows),
            amplitude=amplitude,
            bars=total_bars,
            bi_count=len(group),
            start_idx=first.start_idx,
            end_idx=last.end_idx,
            amplitude_pct=amplitude_pct,
            slope=slope,
            volume_sum=volume_sum,
            impulse_score=impulse_score,
            break_price=break_price,
        )


class CenterDetector:
    """Detect overlap centers from rolling segment windows."""

    def detect(self, segments: Sequence[Segment]) -> list[Center]:
        centers: list[Center] = []
        previous_center: Center | None = None
        for i in range(len(segments) - 2):
            window = segments[i : i + 3]
            upper = min(segment.high for segment in window)
            lower = max(segment.low for segment in window)
            if upper <= lower:
                continue

            width_abs = upper - lower
            center_mid = (upper + lower) / 2
            base = center_mid if center_mid else None
            width_pct = width_abs / base if base else None
            overlap_strength = width_abs / max(1e-9, max(segment.high for segment in window) - min(segment.low for segment in window))

            shift_direction = "flat"
            shift_pct = None
            if previous_center and previous_center.center_mid not in (None, 0):
                shift_pct = center_mid / previous_center.center_mid - 1.0
                if shift_pct > 0.01:
                    shift_direction = "up"
                elif shift_pct < -0.01:
                    shift_direction = "down"

            center = Center(
                zone_upper=upper,
                zone_lower=lower,
                touch_count=len(window),
                start_date=window[0].start_date,
                end_date=window[-1].end_date,
                start_idx=window[0].start_idx,
                end_idx=window[-1].end_idx,
                center_mid=center_mid,
                width_abs=width_abs,
                width_pct=width_pct,
                segment_count=len(window),
                overlap_strength=overlap_strength,
                shift_direction=shift_direction,
                shift_pct=shift_pct,
                active=True,
            )
            centers.append(center)
            previous_center = center

        if centers:
            for center in centers[:-1]:
                center.active = False
        return centers


Swing = Bi
Zone = Center


class SwingBuilder:
    """Compatibility layer; new code should use BiBuilder directly."""

    def build(self, pivots: list[Pivot]) -> list[Swing]:
        swings = BiBuilder().build(pivots)
        for swing in swings:
            swing.amplitude_pct = swing.amplitude / swing.low if swing.low else None
        return swings


class ZoneDetector:
    """Compatibility layer; new code should use CenterDetector directly."""

    def detect(self, swings: list[Swing]) -> list[Zone]:
        return CenterDetector().detect(swings)


class EventEngine:
    """Detect breakout and failure events from active centers/zones."""

    def detect(self, df: pd.DataFrame, zones: list[Zone]) -> list[StructureEvent]:
        if not zones or df.empty:
            return []

        latest_zone = zones[-1]
        latest_row = df.iloc[-1]
        events: list[StructureEvent] = []

        if latest_row["close"] > latest_zone.zone_upper:
            events.append(
                StructureEvent(
                    event_type="breakout_up",
                    date=str(latest_row["date"]),
                    trigger_price=float(latest_row["close"]),
                    note="close above latest zone upper bound",
                    index=int(latest_row.name),
                )
            )
        elif latest_row["close"] < latest_zone.zone_lower:
            events.append(
                StructureEvent(
                    event_type="breakout_down",
                    date=str(latest_row["date"]),
                    trigger_price=float(latest_row["close"]),
                    note="close below latest zone lower bound",
                    index=int(latest_row.name),
                )
            )

        return events


class DivergenceDetector:
    """Detect simple momentum divergence between latest same-direction segments."""

    def detect(self, segments: Sequence[Segment]) -> DivergenceSignal:
        comparable = self._latest_same_direction_pair(segments)
        if comparable is None:
            return DivergenceSignal(0.0, "none", "not enough comparable segments")

        previous, latest = comparable
        score = 0.0
        reasons: list[str] = []

        if self._is_weaker(latest.amplitude_pct, previous.amplitude_pct):
            score += 0.35
            reasons.append("segment amplitude weakened")
        if self._is_weaker(latest.impulse_score, previous.impulse_score):
            score += 0.35
            reasons.append("segment impulse weakened")
        if latest.bars > previous.bars:
            score += 0.15
            reasons.append("latest segment takes longer")
        if latest.volume_sum is not None and previous.volume_sum is not None and latest.volume_sum < previous.volume_sum:
            score += 0.15
            reasons.append("volume does not confirm")

        score = min(1.0, score)
        state = "confirmed" if score >= 0.65 else "mild" if score >= 0.3 else "none"
        note = ", ".join(reasons) if reasons else "no clear divergence"
        return DivergenceSignal(score, state, note)

    @staticmethod
    def _latest_same_direction_pair(segments: Sequence[Segment]) -> tuple[Segment, Segment] | None:
        if len(segments) < 2:
            return None
        latest = segments[-1]
        for candidate in reversed(segments[:-1]):
            if candidate.direction == latest.direction:
                return candidate, latest
        return None

    @staticmethod
    def _is_weaker(latest: float | None, previous: float | None) -> bool:
        if latest is None or previous is None:
            return False
        return abs(latest) < abs(previous)


class StructureAssessmentEngine:
    """Adaptive structure evaluator built on price, structure objects, and market context."""

    def assess(
        self,
        latest: pd.Series,
        structure_context: dict[str, Any],
        market_context: dict[str, Any],
    ) -> StructureAssessment:
        atr_pct = self._as_float(latest.get("atr_pct")) or 0.02
        atr_abs = self._as_float(latest.get("atr_14")) or self._as_float(latest.get("atr14"))
        close = self._as_float(latest.get("close"))
        ma20 = self._as_float(latest.get("ma20"))
        recent_high = self._as_float(latest.get("recent_high_20"))
        recent_low = self._as_float(latest.get("recent_low_20"))
        close_to_ma20 = self._as_float(latest.get("close_to_ma20")) or 0.0
        close_to_ma60 = self._as_float(latest.get("close_to_ma60")) or 0.0
        close_to_recent_high = self._as_float(latest.get("close_to_recent_high")) or 0.0
        ma20_slope = self._as_float(latest.get("ma20_slope")) or self._as_float(latest.get("ma_slope")) or 0.0
        return_1d = self._as_float(latest.get("return_1d")) or 0.0
        return_5d = self._as_float(latest.get("return_5d")) or 0.0
        return_20d = self._as_float(latest.get("return_20d")) or 0.0
        volatility_10d = self._as_float(latest.get("volatility_10d")) or 0.0
        volume_ratio = self._as_float(latest.get("volume_ratio")) or 1.0
        breakout_volume_percentile = self._as_float(latest.get("breakout_volume_percentile")) or 0.5
        pullback_volume_shrink_ratio = self._as_float(latest.get("pullback_volume_shrink_ratio")) or 1.0

        segment_direction = structure_context.get("segment_direction")
        center_shift_direction = structure_context.get("center_shift_direction")
        active_center = structure_context.get("active_center")
        active_center_upper = self._as_float(structure_context.get("active_center_upper"))
        active_center_lower = self._as_float(structure_context.get("active_center_lower"))
        leave_center_strength = self._as_float(structure_context.get("leave_center_strength")) or 0.0
        return_test_depth = self._as_float(structure_context.get("return_test_depth")) or 0.0
        divergence_state = structure_context.get("divergence_state") or "none"
        latest_event_type = structure_context.get("latest_event_type")

        benchmark_weekly_trend = market_context.get("benchmark_weekly_trend")
        excess_return_5d = self._as_float(market_context.get("excess_return_5d")) or 0.0
        excess_return_20d = self._as_float(market_context.get("excess_return_20d")) or 0.0

        near_high_pct = self._clip(max(0.015, atr_pct * 1.25), 0.015, 0.08)
        support_buffer_pct = self._clip(max(0.018, atr_pct * 1.1), 0.018, 0.06)
        volume_confirm_ratio = self._clip(1.0 + atr_pct * 2.5, 1.0, 1.45)
        pullback_shrink_threshold = self._clip(1.0 - atr_pct * 1.5, 0.78, 0.98)

        strength_score = 50.0
        strength_score += self._clip(close_to_ma20 * 120.0, -18.0, 18.0)
        strength_score += self._clip(close_to_ma60 * 80.0, -10.0, 10.0)
        strength_score += self._clip(return_20d * 100.0, -14.0, 14.0)
        strength_score += self._clip(return_5d * 80.0, -10.0, 10.0)
        strength_score += self._clip(excess_return_20d * 90.0, -12.0, 12.0)
        strength_score += self._clip(excess_return_5d * 70.0, -8.0, 8.0)
        strength_score += self._clip(leave_center_strength * 8.0, -10.0, 10.0)
        if ma20_slope > 0:
            strength_score += 6.0
        elif ma20_slope < 0:
            strength_score -= 6.0
        if segment_direction == "up":
            strength_score += 8.0
        elif segment_direction == "down":
            strength_score -= 8.0
        if center_shift_direction == "up":
            strength_score += 5.0
        elif center_shift_direction == "down":
            strength_score -= 5.0
        if latest_event_type == "breakout_up":
            strength_score += 8.0
        elif latest_event_type == "breakout_down":
            strength_score -= 10.0
        if divergence_state == "confirmed":
            strength_score -= 14.0
        elif divergence_state == "mild":
            strength_score -= 7.0
        if benchmark_weekly_trend == "down":
            strength_score -= 3.0

        confirmation_score = 42.0
        if close_to_recent_high >= -near_high_pct:
            confirmation_score += 14.0
        if latest_event_type == "breakout_up":
            confirmation_score += 16.0
        if breakout_volume_percentile >= 0.75:
            confirmation_score += 12.0
        elif breakout_volume_percentile <= 0.35:
            confirmation_score -= 10.0
        if volume_ratio >= volume_confirm_ratio:
            confirmation_score += 12.0
        elif volume_ratio < 0.85:
            confirmation_score -= 8.0
        if pullback_volume_shrink_ratio <= pullback_shrink_threshold:
            confirmation_score += 8.0
        elif pullback_volume_shrink_ratio > 1.05:
            confirmation_score -= 4.0
        if segment_direction == "up":
            confirmation_score += 6.0
        elif segment_direction == "down":
            confirmation_score -= 6.0
        if excess_return_5d > 0:
            confirmation_score += self._clip(excess_return_5d * 80.0, 0.0, 7.0)
        if divergence_state == "confirmed":
            confirmation_score -= 14.0
        elif divergence_state == "mild":
            confirmation_score -= 6.0
        if return_test_depth > 0.85:
            confirmation_score -= 8.0

        risk_score = 35.0
        risk_score += self._clip(volatility_10d * 350.0, 0.0, 22.0)
        risk_score += self._clip(max(0.0, -close_to_ma20) * 140.0, 0.0, 16.0)
        risk_score += self._clip(return_test_depth * 10.0, 0.0, 12.0)
        if latest_event_type == "breakout_down":
            risk_score += 14.0
        if divergence_state == "confirmed":
            risk_score += 12.0
        elif divergence_state == "mild":
            risk_score += 6.0
        if benchmark_weekly_trend == "down":
            risk_score += 6.0
        if excess_return_20d < -0.04:
            risk_score += 8.0

        strength = self._clip_int(strength_score)
        confirmation = self._clip_int(confirmation_score)
        risk = self._clip_int(risk_score)
        trend_bias = "bullish" if strength >= 68 else "bearish" if strength <= 34 else "neutral"
        confirmation_state = "confirmed" if confirmation >= 72 else "pending" if confirmation >= 48 else "failed"

        structure_stage = self._derive_stage(
            trend_bias=trend_bias,
            confirmation_state=confirmation_state,
            latest_event_type=latest_event_type,
            segment_direction=segment_direction,
            close_to_recent_high=close_to_recent_high,
            near_high_pct=near_high_pct,
            active_center=active_center,
            pullback_volume_shrink_ratio=pullback_volume_shrink_ratio,
            pullback_shrink_threshold=pullback_shrink_threshold,
            close_to_ma20=close_to_ma20,
            support_buffer_pct=support_buffer_pct,
            return_1d=return_1d,
        )

        supportive: list[str] = []
        opposing: list[str] = []
        watch_items: list[str] = []
        self._collect_direction_evidence(
            supportive=supportive,
            opposing=opposing,
            trend_bias=trend_bias,
            close_to_ma20=close_to_ma20,
            ma20_slope=ma20_slope,
            segment_direction=segment_direction,
            center_shift_direction=center_shift_direction,
            divergence_state=divergence_state,
        )
        self._collect_confirmation_evidence(
            supportive=supportive,
            opposing=opposing,
            watch_items=watch_items,
            close_to_recent_high=close_to_recent_high,
            near_high_pct=near_high_pct,
            latest_event_type=latest_event_type,
            breakout_volume_percentile=breakout_volume_percentile,
            volume_ratio=volume_ratio,
            volume_confirm_ratio=volume_confirm_ratio,
            pullback_volume_shrink_ratio=pullback_volume_shrink_ratio,
            pullback_shrink_threshold=pullback_shrink_threshold,
            confirmation_state=confirmation_state,
        )
        self._collect_market_evidence(
            supportive=supportive,
            opposing=opposing,
            watch_items=watch_items,
            benchmark_weekly_trend=benchmark_weekly_trend,
            excess_return_5d=excess_return_5d,
            excess_return_20d=excess_return_20d,
        )

        support_level, support_basis = self._derive_support_level(
            close=close,
            ma20=ma20,
            recent_low=recent_low,
            center_upper=active_center_upper,
            center_lower=active_center_lower,
            structure_stage=structure_stage,
        )
        resistance_level, resistance_basis = self._derive_resistance_level(
            close=close,
            recent_high=recent_high,
            center_upper=active_center_upper,
            structure_stage=structure_stage,
            latest_event_type=latest_event_type,
        )
        invalidation_level = self._derive_invalidation(
            support_level=support_level,
            atr_abs=atr_abs,
            support_buffer_pct=support_buffer_pct,
        )

        alignment_signals = (close_to_ma20 > 0, ma20_slope > 0, segment_direction == "up", excess_return_20d > 0)
        alignment_hits = sum(1 for signal in alignment_signals if signal)
        alignment_total = len(alignment_signals)
        if divergence_state != "none":
            alignment_total += 1
        confidence = self._clip(0.35 + 0.45 * (alignment_hits / max(1, alignment_total)) + abs(strength - 50) / 120.0, 0.35, 0.95)

        if not watch_items:
            watch_items.append("当前结构条件基本齐备，重点盯住确认区和失效位")

        return StructureAssessment(
            trend_bias=trend_bias,
            structure_stage=structure_stage,
            confirmation_state=confirmation_state,
            strength_score=strength,
            confirmation_score=confirmation,
            risk_score=risk,
            confidence=round(confidence, 4),
            support_level=support_level,
            resistance_level=resistance_level,
            invalidation_level=invalidation_level,
            support_basis=support_basis,
            resistance_basis=resistance_basis,
            adaptive_thresholds={
                "near_high_pct": round(near_high_pct, 4),
                "support_buffer_pct": round(support_buffer_pct, 4),
                "volume_confirm_ratio": round(volume_confirm_ratio, 4),
                "pullback_shrink_threshold": round(pullback_shrink_threshold, 4),
            },
            supporting_evidence=supportive,
            opposing_evidence=opposing,
            watch_items=watch_items,
        )

    @staticmethod
    def _derive_stage(
        trend_bias: str,
        confirmation_state: str,
        latest_event_type: str | None,
        segment_direction: str | None,
        close_to_recent_high: float,
        near_high_pct: float,
        active_center: str | None,
        pullback_volume_shrink_ratio: float,
        pullback_shrink_threshold: float,
        close_to_ma20: float,
        support_buffer_pct: float,
        return_1d: float,
    ) -> str:
        if trend_bias == "bullish":
            if latest_event_type == "breakout_up" and confirmation_state == "confirmed":
                return "breakout_confirmed"
            if latest_event_type == "breakout_up":
                return "breakout_attempt"
            if segment_direction == "up" and close_to_recent_high >= -near_high_pct:
                return "trend_pressing_high"
            if (
                segment_direction == "up"
                and close_to_ma20 >= -support_buffer_pct
                and pullback_volume_shrink_ratio <= pullback_shrink_threshold
            ):
                return "trend_pullback"
            return "trend_advancing"
        if trend_bias == "bearish":
            if latest_event_type == "breakout_down":
                return "breakdown"
            return "distribution"
        if active_center:
            return "range_rotation"
        if return_1d > 0:
            return "rebound_repair"
        return "transition"

    @staticmethod
    def _collect_direction_evidence(
        supportive: list[str],
        opposing: list[str],
        trend_bias: str,
        close_to_ma20: float,
        ma20_slope: float,
        segment_direction: str | None,
        center_shift_direction: str | None,
        divergence_state: str,
    ) -> None:
        if close_to_ma20 > 0:
            supportive.append("价格站在 20 日均线上方")
        else:
            opposing.append("价格仍处在 20 日均线下方或贴近下沿")
        if ma20_slope > 0:
            supportive.append("20 日均线仍在抬升")
        elif ma20_slope < 0:
            opposing.append("20 日均线斜率转弱")
        if segment_direction == "up":
            supportive.append("最近线段方向保持向上")
        elif segment_direction == "down":
            opposing.append("最近线段方向仍向下")
        if center_shift_direction == "up":
            supportive.append("活跃中枢有上移特征")
        elif center_shift_direction == "down":
            opposing.append("活跃中枢存在下移压力")
        if divergence_state == "confirmed":
            opposing.append("结构背驰已确认")
        elif divergence_state == "mild":
            opposing.append("结构背驰开始出现")
        if trend_bias == "neutral":
            opposing.append("结构强弱尚未形成单边优势")

    @staticmethod
    def _collect_confirmation_evidence(
        supportive: list[str],
        opposing: list[str],
        watch_items: list[str],
        close_to_recent_high: float,
        near_high_pct: float,
        latest_event_type: str | None,
        breakout_volume_percentile: float,
        volume_ratio: float,
        volume_confirm_ratio: float,
        pullback_volume_shrink_ratio: float,
        pullback_shrink_threshold: float,
        confirmation_state: str,
    ) -> None:
        if close_to_recent_high >= -near_high_pct:
            supportive.append("价格已逼近或触达近期高点区")
        else:
            opposing.append("价格离近期高点仍有距离")
            watch_items.append("需要先回到近期高点附近，确认上攻路径仍然存在")
        if latest_event_type == "breakout_up":
            supportive.append("最新结构事件给出了向上突破信号")
        if breakout_volume_percentile >= 0.75 or volume_ratio >= volume_confirm_ratio:
            supportive.append("放量条件接近或达到确认阈值")
        else:
            opposing.append("突破量能仍未达到确认要求")
            watch_items.append("需要突破时出现明显放量，而不是普通换手")
        if pullback_volume_shrink_ratio <= pullback_shrink_threshold:
            supportive.append("回踩阶段具备缩量特征")
        else:
            opposing.append("回踩缩量不明显，承接质量仍需观察")
            watch_items.append("回踩时需要成交量收敛，避免放量回落")
        if confirmation_state == "failed":
            watch_items.append("当前确认状态偏弱，先等价格和量能同步改善")

    @staticmethod
    def _collect_market_evidence(
        supportive: list[str],
        opposing: list[str],
        watch_items: list[str],
        benchmark_weekly_trend: str | None,
        excess_return_5d: float,
        excess_return_20d: float,
    ) -> None:
        if benchmark_weekly_trend is None:
            watch_items.append("当前没有可用基准或板块数据，结构判断仍缺少相对强弱坐标")
            return
        if excess_return_20d > 0.03:
            supportive.append("相对基准的中期超额收益为正")
        elif excess_return_20d < -0.03:
            opposing.append("相对基准的中期超额收益为负")
        if excess_return_5d > 0.01:
            supportive.append("最近 5 日相对基准继续走强")
        elif excess_return_5d < -0.01:
            opposing.append("最近 5 日相对基准走弱")
        if benchmark_weekly_trend == "down":
            opposing.append("当前市场基准本身处于偏弱环境")
            watch_items.append("即便个股结构改善，也要观察是否能逆势持续强于市场")

    @staticmethod
    def _derive_support_level(
        close: float | None,
        ma20: float | None,
        recent_low: float | None,
        center_upper: float | None,
        center_lower: float | None,
        structure_stage: str,
    ) -> tuple[float | None, str | None]:
        candidates: list[tuple[str, float]] = []
        if ma20 is not None:
            candidates.append(("20日均线", ma20))
        if center_lower is not None:
            candidates.append(("活跃中枢下沿", center_lower))
        if structure_stage in {"breakout_confirmed", "breakout_attempt"} and center_upper is not None:
            candidates.append(("活跃中枢上沿", center_upper))
        if recent_low is not None:
            candidates.append(("近20日低点", recent_low))
        if close is None:
            return (candidates[0][1], candidates[0][0]) if candidates else (None, None)
        below_close = [(label, value) for label, value in candidates if value <= close]
        if below_close:
            label, value = max(below_close, key=lambda item: item[1])
            return round(value, 4), label
        if candidates:
            label, value = min(candidates, key=lambda item: abs(item[1] - close))
            return round(value, 4), label
        return None, None

    @staticmethod
    def _derive_resistance_level(
        close: float | None,
        recent_high: float | None,
        center_upper: float | None,
        structure_stage: str,
        latest_event_type: str | None,
    ) -> tuple[float | None, str | None]:
        candidates: list[tuple[str, float]] = []
        if recent_high is not None:
            candidates.append(("近20日高点", recent_high))
        if center_upper is not None and structure_stage not in {"breakout_confirmed"}:
            candidates.append(("活跃中枢上沿", center_upper))
        if latest_event_type == "breakout_up" and close is not None:
            candidates.append(("突破后观察价", close))
        if close is None:
            return (candidates[0][1], candidates[0][0]) if candidates else (None, None)
        above_close = [(label, value) for label, value in candidates if value >= close]
        if above_close:
            label, value = min(above_close, key=lambda item: item[1])
            return round(value, 4), label
        if candidates:
            label, value = max(candidates, key=lambda item: item[1])
            return round(value, 4), label
        return None, None

    @staticmethod
    def _derive_invalidation(
        support_level: float | None,
        atr_abs: float | None,
        support_buffer_pct: float,
    ) -> float | None:
        if support_level is None:
            return None
        pct_gap = support_level * support_buffer_pct
        atr_gap = (atr_abs or 0.0) * 0.8
        gap = max(pct_gap, atr_gap, support_level * 0.015)
        return round(support_level - gap, 4)

    @staticmethod
    def _as_float(value: Any) -> float | None:
        if value is None or pd.isna(value):
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _clip(value: float, low: float, high: float) -> float:
        return max(low, min(high, value))

    @staticmethod
    def _clip_int(value: float) -> int:
        return int(max(0, min(100, round(value))))


__all__ = [
    "Bi",
    "BiBuilder",
    "Center",
    "CenterDetector",
    "DivergenceDetector",
    "DivergenceSignal",
    "EventEngine",
    "Pivot",
    "PivotDetector",
    "Segment",
    "SegmentBuilder",
    "StructureAssessment",
    "StructureAssessmentEngine",
    "StructureEvent",
    "Swing",
    "SwingBuilder",
    "Zone",
    "ZoneDetector",
]


for _module_name in (
    "assessment",
    "bi_builder",
    "center_detector",
    "divergence_detector",
    "domain",
    "event_engine",
    "pivot_detector",
    "segment_builder",
    "swing_builder",
    "zone_detector",
):
    sys.modules[f"{__name__}.{_module_name}"] = sys.modules[__name__]
