from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd

from shilun.data import TushareConfig, TushareDailyClient, TushareDailyImporter
from shilun.decision import ActionMapper, ExecutionEngine, snapshot_from_dict
from shilun.features import StructureFeatureBuilder, compute_entry_features
from shilun.indicators import build_chip_context, compute_trend_features, compute_volatility_features, compute_volume_features
from shilun.llm_bridge import LLMPayload, PromptRenderer
from shilun.models import RuleFallbackModel
from shilun.models import ModelRegistry, SafePredictionModel, TrainedModelPredictor
from shilun.structure import (
    BiBuilder,
    CenterDetector,
    DivergenceDetector,
    EventEngine,
    PivotDetector,
    SegmentBuilder,
    StructureAssessmentEngine,
)


@dataclass(frozen=True)
class PipelineConfig:
    lookback_days: int = 360
    min_required_rows: int = 80
    model_dir: str | None = None
    benchmark_ticker: str | None = "000300.SH"


class ShilunPipeline:
    """Single-entry analysis pipeline for data-science-friendly usage."""

    def __init__(
        self,
        config: PipelineConfig | None = None,
        importer: TushareDailyImporter | None = None,
        mapper: ActionMapper | None = None,
        renderer: PromptRenderer | None = None,
        model_predictor: Any | None = None,
    ) -> None:
        self.config = config or PipelineConfig()
        self.importer = importer or self._build_default_importer()
        self.mapper = mapper or ActionMapper()
        self.renderer = renderer or PromptRenderer()
        self.model = model_predictor or self._build_model_predictor()
        self.structure_assessor = StructureAssessmentEngine()
        self.execution_engine = ExecutionEngine()
        self._benchmark_context_cache: dict[tuple[str, str], dict[str, Any]] = {}
        self._stock_basic_cache: pd.DataFrame | None = None

    @staticmethod
    def _build_default_importer() -> TushareDailyImporter:
        tushare_config = TushareConfig.from_env()
        client = TushareDailyClient(tushare_config)
        return TushareDailyImporter(client)

    def _build_model_predictor(self) -> Any:
        fallback = RuleFallbackModel()
        model_dir = self.config.model_dir
        if not model_dir:
            return fallback

        registry = ModelRegistry(Path(model_dir))
        if not registry.has_all_models():
            return fallback

        trained = TrainedModelPredictor(
            regime_model=registry.load_regime_model(),
            event_model=registry.load_event_model(),
            risk_model=registry.load_risk_model(),
            entry_model=registry.load_entry_model() if registry.has_entry_model() else None,
            entry_fallback=fallback,
            model_version=f"trained::{Path(model_dir).name}",
        )
        return SafePredictionModel(primary=trained, fallback=fallback)

    def run(self, ticker: str, analysis_date: str) -> dict[str, Any]:
        bars = self._load_daily_bars(ticker=ticker, analysis_date=analysis_date)
        external_context = self._fetch_external_contexts(ticker=ticker, analysis_date=analysis_date)
        return self._build_result(
            ticker=ticker,
            analysis_date=analysis_date,
            bars=bars,
            **external_context,
        )

    def run_with_bars(
        self,
        ticker: str,
        analysis_date: str,
        bars: pd.DataFrame,
        benchmark_bars: pd.DataFrame | None = None,
        sector_context: dict[str, Any] | None = None,
        fundamental_context: dict[str, Any] | None = None,
        daily_basic_context: dict[str, Any] | None = None,
        chip_perf_context: dict[str, Any] | None = None,
        fina_indicator_context: dict[str, Any] | None = None,
        metadata_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        prepared_bars = self._prepare_bars(ticker=ticker, analysis_date=analysis_date, bars=bars)
        prepared_benchmark = None
        if benchmark_bars is not None and self.config.benchmark_ticker:
            prepared_benchmark = self._prepare_bars(
                ticker=self.config.benchmark_ticker,
                analysis_date=analysis_date,
                bars=benchmark_bars,
            )
        return self._build_result(
            ticker=ticker,
            analysis_date=analysis_date,
            bars=prepared_bars,
            benchmark_bars=prepared_benchmark,
            sector_context=sector_context,
            fundamental_context=fundamental_context,
            daily_basic_context=daily_basic_context,
            chip_perf_context=chip_perf_context,
            fina_indicator_context=fina_indicator_context,
            metadata_context=metadata_context,
        )

    def _build_result(
        self,
        *,
        ticker: str,
        analysis_date: str,
        bars: pd.DataFrame,
        benchmark_bars: pd.DataFrame | None = None,
        sector_context: dict[str, Any] | None = None,
        fundamental_context: dict[str, Any] | None = None,
        daily_basic_context: dict[str, Any] | None = None,
        chip_perf_context: dict[str, Any] | None = None,
        fina_indicator_context: dict[str, Any] | None = None,
        metadata_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Shared final assembly for Tushare-loaded and Mongo-first analyses."""
        snapshot_payload = self._build_snapshot_payload(
            ticker=ticker,
            analysis_date=analysis_date,
            bars=bars,
            benchmark_bars=benchmark_bars,
            sector_context=sector_context,
            fundamental_context=fundamental_context,
            daily_basic_context=daily_basic_context,
            chip_perf_context=chip_perf_context,
            fina_indicator_context=fina_indicator_context,
            metadata_context=metadata_context,
        )
        snapshot = snapshot_from_dict(snapshot_payload)
        decision = self.mapper.map_actions(snapshot)
        llm_payload = LLMPayload.from_snapshot(snapshot, decision)
        explanation = self.renderer.render(llm_payload)
        snapshot_dict = {**snapshot.__dict__, **{k: v for k, v in snapshot_payload.items() if k not in snapshot.__dict__}}
        return {
            "ticker": ticker,
            "date": analysis_date,
            "snapshot": snapshot_dict,
            "decision": decision.__dict__,
            "explanation": explanation,
        }

    def _load_daily_bars(self, ticker: str, analysis_date: str) -> pd.DataFrame:
        end_date = datetime.strptime(analysis_date, "%Y-%m-%d")
        start_date = end_date - timedelta(days=self.config.lookback_days)
        if hasattr(self.importer, "client") and ticker == self.config.benchmark_ticker:
            try:
                raw_df = self.importer.client.pro_client.index_daily(
                    ts_code=ticker,
                    start_date=start_date.strftime("%Y%m%d"),
                    end_date=end_date.strftime("%Y%m%d"),
                )
                bars = self.importer.client._normalize_daily_frame(raw_df)
                if not bars.empty:
                    return bars.sort_values("date").reset_index(drop=True)
            except Exception:
                pass
        bars, _ = self.importer.import_daily(
            ts_code=ticker,
            start_date=start_date.strftime("%Y%m%d"),
            end_date=end_date.strftime("%Y%m%d"),
        )
        if bars.empty:
            raise ValueError(f"No market data found for {ticker} before {analysis_date}.")
        if len(bars) < self.config.min_required_rows:
            raise ValueError(
                f"Not enough bars for analysis. Required={self.config.min_required_rows}, got={len(bars)}."
            )
        return bars.sort_values("date").reset_index(drop=True)

    def _prepare_bars(self, ticker: str, analysis_date: str, bars: pd.DataFrame) -> pd.DataFrame:
        if bars is None or bars.empty:
            raise ValueError(f"No market data found for {ticker} before {analysis_date}.")

        prepared = bars.copy()
        if "ticker" not in prepared.columns:
            prepared["ticker"] = ticker

        required_columns = ["ticker", "date", "open", "high", "low", "close", "volume", "amount"]
        for column in required_columns:
            if column not in prepared.columns:
                prepared[column] = pd.NA

        prepared = prepared[required_columns]
        prepared["date"] = pd.to_datetime(prepared["date"], errors="coerce")
        for column in ["open", "high", "low", "close", "volume", "amount"]:
            prepared[column] = pd.to_numeric(prepared[column], errors="coerce")

        analysis_ts = pd.Timestamp(analysis_date)
        prepared = prepared.loc[prepared["date"].notna() & (prepared["date"] <= analysis_ts)]
        prepared = prepared.sort_values("date").reset_index(drop=True)
        if prepared.empty:
            raise ValueError(f"No market data found for {ticker} before {analysis_date}.")
        if len(prepared) < self.config.min_required_rows:
            raise ValueError(
                f"Not enough bars for analysis. Required={self.config.min_required_rows}, got={len(prepared)}."
            )
        return prepared

    def _build_snapshot_payload(
        self,
        ticker: str,
        analysis_date: str,
        bars: pd.DataFrame,
        benchmark_bars: pd.DataFrame | None = None,
        sector_context: dict[str, Any] | None = None,
        fundamental_context: dict[str, Any] | None = None,
        daily_basic_context: dict[str, Any] | None = None,
        chip_perf_context: dict[str, Any] | None = None,
        fina_indicator_context: dict[str, Any] | None = None,
        metadata_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        # M5 P1: keep the pipeline as orchestration only; each helper below owns
        # one stage of the analysis instead of growing this method into a wall.
        contexts = self._build_pipeline_contexts(
            ticker=ticker,
            analysis_date=analysis_date,
            bars=bars,
            benchmark_bars=benchmark_bars,
            sector_context=sector_context,
            fundamental_context=fundamental_context,
            daily_basic_context=daily_basic_context,
            chip_perf_context=chip_perf_context,
            fina_indicator_context=fina_indicator_context,
            metadata_context=metadata_context,
        )
        signals = self._derive_pipeline_signals(contexts)
        model_state = self._build_model_state(contexts, signals)
        execution = self._build_execution_state(contexts, signals, model_state["model_context"])
        evidence_sections = self._build_pipeline_evidence(contexts, signals, model_state["model_context"], execution)
        return self._assemble_snapshot_payload(
            ticker=ticker,
            analysis_date=analysis_date,
            contexts=contexts,
            signals=signals,
            model_state=model_state,
            execution=execution,
            evidence_sections=evidence_sections,
        )

    def _build_pipeline_contexts(
        self,
        *,
        ticker: str,
        analysis_date: str,
        bars: pd.DataFrame,
        benchmark_bars: pd.DataFrame | None,
        sector_context: dict[str, Any] | None,
        fundamental_context: dict[str, Any] | None,
        daily_basic_context: dict[str, Any] | None,
        chip_perf_context: dict[str, Any] | None,
        fina_indicator_context: dict[str, Any] | None,
        metadata_context: dict[str, Any] | None,
    ) -> dict[str, Any]:
        features = compute_trend_features(bars)
        features = compute_volatility_features(features)
        features = compute_volume_features(features)
        features = compute_entry_features(features)
        latest = features.iloc[-1]
        structure_context = self._build_structure_context(features)
        market_context = self._build_market_context(
            ticker=ticker,
            analysis_date=analysis_date,
            latest=latest,
            benchmark_bars=benchmark_bars,
        )
        fundamental_context = self._build_fundamental_context(
            ticker=ticker,
            latest=latest,
            daily_basic_context=daily_basic_context,
            fundamental_context=fundamental_context,
            fina_indicator_context=fina_indicator_context,
        )
        sector_context = self._build_sector_context(
            market_context=market_context,
            sector_context=sector_context,
            metadata_context=metadata_context,
        )
        structure_assessment = self.structure_assessor.assess(
            latest=latest,
            structure_context=structure_context,
            market_context=market_context,
        )
        structure_assessment_dict = structure_assessment.to_dict()
        trigger_context = self._build_trigger_context(latest)
        chip_context = build_chip_context(
            latest_close=self._as_float(latest.get("close")),
            support_main=structure_assessment.support_level,
            pressure_main=structure_assessment.resistance_level,
            daily_basic=daily_basic_context,
            chip_perf=chip_perf_context,
        )
        pattern_context = self._build_pattern_context(latest)
        trend_stage_context = self._build_trend_stage_context(
            latest=latest,
            structure_assessment=structure_assessment_dict,
            trigger_context=trigger_context,
            chip_context=chip_context,
            market_context=market_context,
            sector_context=sector_context,
        )
        return {
            "features": features,
            "latest": latest,
            "structure_context": structure_context,
            "market_context": market_context,
            "fundamental_context": fundamental_context,
            "sector_context": sector_context,
            "structure_assessment": structure_assessment,
            "structure_assessment_dict": structure_assessment_dict,
            "trigger_context": trigger_context,
            "chip_context": chip_context,
            "pattern_context": pattern_context,
            "trend_stage_context": trend_stage_context,
            "metadata_context": metadata_context or {},
        }

    def _derive_pipeline_signals(self, contexts: dict[str, Any]) -> dict[str, Any]:
        latest = contexts["latest"]
        structure_assessment = contexts["structure_assessment"]
        structure_assessment_dict = contexts["structure_assessment_dict"]
        trigger_context = contexts["trigger_context"]
        pattern_context = contexts["pattern_context"]
        trend_stage_context = contexts["trend_stage_context"]
        chip_context = contexts["chip_context"]

        close_to_ma20 = self._as_float(latest.get("close_to_ma20"))
        close_to_recent_high = self._as_float(latest.get("close_to_recent_high"))
        close_to_recent_low = self._as_float(latest.get("close_to_recent_low"))
        return_1d = self._as_float(latest.get("return_1d"))
        volume_ratio = self._as_float(latest.get("volume_ratio"))
        volatility_10d = self._as_float(latest.get("volatility_10d"))

        weekly_trend = self._map_trend_bias_to_weekly_trend(structure_assessment.trend_bias)
        daily_state = self._map_structure_stage_to_daily_state(structure_assessment.structure_stage, return_1d)
        structure_type = self._map_structure_stage_to_type(structure_assessment.structure_stage)
        volume_state = self._derive_volume_state(volume_ratio)
        breakout_quality = self._derive_breakout_quality_from_assessment(
            structure_assessment.confirmation_state,
            close_to_recent_high,
            volume_state,
        )
        pullback_quality = self._derive_pullback_quality_from_assessment(
            structure_assessment.structure_stage,
            close_to_ma20,
        )
        price_action_quality = self._derive_price_action_quality(return_1d, volatility_10d)

        chip_pressure = self._derive_chip_pressure(close_to_recent_high)
        chip_support = self._derive_chip_support(close_to_recent_low)
        chip_vacuum = self._derive_chip_vacuum(chip_pressure, breakout_quality)

        structure_score = structure_assessment.strength_score
        trigger_state = self._derive_trigger_state(
            trigger_context=trigger_context,
            breakout_quality=breakout_quality,
            structure_assessment=structure_assessment_dict,
        )
        opportunity_type = self._derive_opportunity_type(
            structure_assessment=structure_assessment_dict,
            breakout_quality=breakout_quality,
            pullback_quality=pullback_quality,
            trigger_state=trigger_state,
            trigger_context=trigger_context,
        )
        trend_stage = str(trend_stage_context.get("trend_stage") or "transition")
        quant_reason_codes = self._build_quant_reason_codes(
            pattern_context=pattern_context,
            trend_stage_context=trend_stage_context,
            chip_context=chip_context,
        )
        volume_score = self._derive_volume_score(volume_ratio, pattern_context)
        chip_score = self._derive_chip_score(chip_pressure, chip_support, chip_vacuum, chip_context)
        risk_score = max(
            structure_assessment.risk_score,
            self._derive_risk_score(volatility_10d, close_to_ma20, breakout_quality, pattern_context, trend_stage_context),
        )
        return {
            "weekly_trend": weekly_trend,
            "daily_state": daily_state,
            "structure_type": structure_type,
            "structure_score": structure_score,
            "volume_state": volume_state,
            "breakout_quality": breakout_quality,
            "pullback_quality": pullback_quality,
            "price_action_quality": price_action_quality,
            "chip_pressure": chip_pressure,
            "chip_support": chip_support,
            "chip_vacuum": chip_vacuum,
            "trigger_state": trigger_state,
            "opportunity_type": opportunity_type,
            "trend_stage": trend_stage,
            "volume_score": volume_score,
            "chip_score": chip_score,
            "risk_score": risk_score,
            "risk_tags": self._derive_risk_tags(risk_score, breakout_quality, volume_state),
            "reason_codes": quant_reason_codes,
            "support_main": structure_assessment.support_level,
            "pressure_main": structure_assessment.resistance_level,
            "invalidation_level": structure_assessment.invalidation_level,
        }

    def _build_model_state(self, contexts: dict[str, Any], signals: dict[str, Any]) -> dict[str, Any]:
        latest = contexts["latest"]
        structure_context = contexts["structure_context"]
        market_context = contexts["market_context"]
        structure_assessment = contexts["structure_assessment_dict"]
        trigger_context = contexts["trigger_context"]
        sector_context = contexts["sector_context"]
        fundamental_context = contexts["fundamental_context"]
        pattern_context = contexts["pattern_context"]
        trend_stage_context = contexts["trend_stage_context"]
        chip_context = contexts["chip_context"]
        feature_context = self._build_feature_context(
            latest,
            structure_context,
            market_context,
            structure_assessment,
            trigger_context,
            sector_context,
            fundamental_context,
            signals["trend_stage"],
            weekly_trend=signals["weekly_trend"],
            daily_state=signals["daily_state"],
            breakout_quality=signals["breakout_quality"],
            pullback_quality=signals["pullback_quality"],
            price_action_quality=signals["price_action_quality"],
            trigger_state=signals["trigger_state"],
            opportunity_type=signals["opportunity_type"],
            chip_pressure=signals["chip_pressure"],
            chip_support=signals["chip_support"],
            chip_vacuum=signals["chip_vacuum"],
            pattern_context=pattern_context,
            trend_stage_context=trend_stage_context,
            chip_context=chip_context,
        )
        model_context = self.model.predict(feature_context, structure_context).to_dict()
        return {"feature_context": feature_context, "model_context": model_context}

    def _build_execution_state(
        self,
        contexts: dict[str, Any],
        signals: dict[str, Any],
        model_context: dict[str, Any],
    ) -> dict[str, Any]:
        latest = contexts["latest"]
        market_context = contexts["market_context"]
        sector_context = contexts["sector_context"]
        fundamental_context = contexts["fundamental_context"]
        chip_context = contexts["chip_context"]
        pattern_context = contexts["pattern_context"]
        trend_stage_context = contexts["trend_stage_context"]
        structure_assessment = contexts["structure_assessment"]
        execution_snapshot = {
            "p_continue_10d": model_context.get("p_continue_10d"),
            "p_acceptance_1d": model_context.get("p_acceptance_1d"),
            "p_fail_fast_3d": model_context.get("p_fail_fast_3d"),
            "entry_probability": model_context.get("entry_probability"),
            "risk_score": signals["risk_score"],
            "risk_level": model_context.get("risk_level"),
            "trend_stage": signals["trend_stage"],
            "trigger_state": signals["trigger_state"],
            "breakout_quality": signals["breakout_quality"],
            "pullback_quality": signals["pullback_quality"],
            "opportunity_type": signals["opportunity_type"],
            "support_main": signals["support_main"],
            "support_basis": structure_assessment.support_basis,
            "pressure_main": signals["pressure_main"],
            "invalidation_level": signals["invalidation_level"],
            "gentle_expand_score": pattern_context.get("gentle_expand_score"),
            "pullback_shrink_score": pattern_context.get("pullback_shrink_score"),
            "impulsive_spike_score": pattern_context.get("impulsive_spike_score"),
            "distribution_score": pattern_context.get("distribution_score"),
            "stall_score": pattern_context.get("stall_score"),
            "early_stage_score": trend_stage_context.get("early_stage_score"),
            "mid_stage_score": trend_stage_context.get("mid_stage_score"),
            "late_stage_score": trend_stage_context.get("late_stage_score"),
        }
        return self.execution_engine.evaluate(
            latest_close=self._as_float(latest.get("close")),
            atr_abs=self._as_float(latest.get("atr_14")) or self._as_float(latest.get("atr14")),
            snapshot=execution_snapshot,
            market_context=market_context,
            sector_context=sector_context,
            fundamental_context=fundamental_context,
            chip_context=chip_context,
        ).to_dict()

    def _build_pipeline_evidence(
        self,
        contexts: dict[str, Any],
        signals: dict[str, Any],
        model_context: dict[str, Any],
        execution: dict[str, Any],
    ) -> dict[str, list[str]]:
        return self._build_evidence_sections(
            weekly_trend=signals["weekly_trend"],
            daily_state=signals["daily_state"],
            structure_type=signals["structure_type"],
            structure_assessment=contexts["structure_assessment_dict"],
            trigger_context=contexts["trigger_context"],
            trigger_state=signals["trigger_state"],
            opportunity_type=signals["opportunity_type"],
            volume_state=signals["volume_state"],
            breakout_quality=signals["breakout_quality"],
            pullback_quality=signals["pullback_quality"],
            chip_pressure=signals["chip_pressure"],
            risk_score=signals["risk_score"],
            structure_context=contexts["structure_context"],
            market_context=contexts["market_context"],
            model_context=model_context,
            fundamental_context=contexts["fundamental_context"],
            sector_context=contexts["sector_context"],
            trend_stage=signals["trend_stage"],
            execution=execution,
            pattern_context=contexts["pattern_context"],
            trend_stage_context=contexts["trend_stage_context"],
        )

    def _assemble_snapshot_payload(
        self,
        *,
        ticker: str,
        analysis_date: str,
        contexts: dict[str, Any],
        signals: dict[str, Any],
        model_state: dict[str, Any],
        execution: dict[str, Any],
        evidence_sections: dict[str, list[str]],
    ) -> dict[str, Any]:
        latest = contexts["latest"]
        structure_context = contexts["structure_context"]
        structure_assessment = contexts["structure_assessment"]
        model_context = model_state["model_context"]
        feature_context = model_state["feature_context"]
        pattern_context = contexts["pattern_context"]
        trend_stage_context = contexts["trend_stage_context"]
        evidence = self._flatten_evidence_sections(evidence_sections)

        return {
            "ticker": ticker,
            "analysis_date": analysis_date,
            "weekly_trend": signals["weekly_trend"],
            "daily_state": signals["daily_state"],
            "structure_type": signals["structure_type"],
            "structure_score": signals["structure_score"],
            "structure_bias": structure_assessment.trend_bias,
            "structure_stage": structure_assessment.structure_stage,
            "confirmation_state": structure_assessment.confirmation_state,
            "confirmation_score": structure_assessment.confirmation_score,
            "trigger_score": None,
            "trigger_state": signals["trigger_state"],
            "opportunity_type": signals["opportunity_type"],
            "structure_confidence": structure_assessment.confidence,
            "trend_stage": signals["trend_stage"],
            "trend_stage_confidence": trend_stage_context.get("trend_stage_confidence"),
            "early_stage_score": trend_stage_context.get("early_stage_score"),
            "mid_stage_score": trend_stage_context.get("mid_stage_score"),
            "late_stage_score": trend_stage_context.get("late_stage_score"),
            "volume_state": signals["volume_state"],
            "breakout_quality": signals["breakout_quality"],
            "pullback_quality": signals["pullback_quality"],
            "price_action_quality": signals["price_action_quality"],
            "volume_score": signals["volume_score"],
            "gentle_expand_score": pattern_context.get("gentle_expand_score"),
            "pullback_shrink_score": pattern_context.get("pullback_shrink_score"),
            "impulsive_spike_score": pattern_context.get("impulsive_spike_score"),
            "distribution_score": pattern_context.get("distribution_score"),
            "stall_score": pattern_context.get("stall_score"),
            "dominant_positive_pattern": pattern_context.get("dominant_positive_pattern"),
            "dominant_risk_pattern": pattern_context.get("dominant_risk_pattern"),
            "chip_pressure": signals["chip_pressure"],
            "chip_support": signals["chip_support"],
            "chip_vacuum": signals["chip_vacuum"],
            "chip_score": signals["chip_score"],
            "support_main": signals["support_main"],
            "support_basis": structure_assessment.support_basis,
            "pressure_main": signals["pressure_main"],
            "resistance_basis": structure_assessment.resistance_basis,
            "invalidation_level": signals["invalidation_level"],
            "risk_score": signals["risk_score"],
            "regime_label": model_context["regime_label"],
            "regime_score": model_context["regime_score"],
            "regime_confidence": model_context["regime_confidence"],
            "p_continue_10d": model_context["p_continue_10d"],
            "p_breakout_success": model_context["p_breakout_success"],
            "p_fail_5d": model_context["p_fail_5d"],
            "p_acceptance_1d": model_context.get("p_acceptance_1d"),
            "p_fail_fast_3d": model_context.get("p_fail_fast_3d"),
            "entry_probability": model_context.get("entry_probability"),
            "entry_zone": model_context.get("entry_zone"),
            "expected_return_10d": model_context["expected_return_10d"],
            "expected_drawdown_10d": model_context["expected_drawdown_10d"],
            "risk_level": model_context["risk_level"],
            "model_version": model_context["model_version"],
            "risk_tags": signals["risk_tags"],
            "reason_codes": signals["reason_codes"],
            "evidence": evidence,
            "market_context": contexts["market_context"],
            "sector_context": contexts["sector_context"],
            "fundamental_context": contexts["fundamental_context"],
            "chip_context": contexts["chip_context"],
            "pattern_context": pattern_context,
            "trend_stage_context": trend_stage_context,
            "metadata_context": contexts["metadata_context"],
            "evidence_sections": evidence_sections,
            "structure_assessment": structure_assessment.to_dict(),
            "structure_context": structure_context,
            "trigger_context": contexts["trigger_context"],
            "feature_context": feature_context,
            "model_context": model_context,
            **execution,
            "latest_pivot_type": structure_context.get("latest_pivot_type"),
            "bi_direction": structure_context.get("bi_direction"),
            "segment_direction": structure_context.get("segment_direction"),
            "active_center": structure_context.get("active_center"),
            "divergence_state": structure_context.get("divergence_state"),
            "latest_event_type": structure_context.get("latest_event_type"),
        }

    @staticmethod
    def _as_float(value: Any) -> float | None:
        if value is None or pd.isna(value):
            return None
        return float(value)

    @staticmethod
    def _clip_score(raw_score: float) -> int:
        return int(max(0, min(100, round(raw_score))))

    def _derive_weekly_trend(self, close_to_ma20: float | None, ma_slope: float | None) -> str:
        if close_to_ma20 is None or ma_slope is None:
            return "range"
        if close_to_ma20 > 0 and ma_slope > 0:
            return "up"
        if close_to_ma20 < 0 and ma_slope < 0:
            return "down"
        return "range"

    def _derive_daily_state(self, return_1d: float | None, return_5d: float | None, close_to_ma20: float | None) -> str:
        if return_5d is None or close_to_ma20 is None:
            return "transition"
        if return_5d > 0.03 and close_to_ma20 > 0:
            return "trend"
        if return_5d < -0.03:
            return "exhaustion"
        if return_1d is not None and return_1d > 0:
            return "rebound"
        return "consolidation"

    def _derive_volume_state(self, volume_ratio: float | None) -> str:
        if volume_ratio is None:
            return "neutral"
        if volume_ratio > 1.2:
            return "expand"
        if volume_ratio < 0.8:
            return "contract"
        return "neutral"

    def _derive_price_action_quality(self, return_1d: float | None, volatility_10d: float | None) -> str:
        if return_1d is None or volatility_10d is None:
            return "unknown"
        if return_1d > 0 and volatility_10d < 0.05:
            return "healthy"
        if return_1d < 0 and volatility_10d > 0.06:
            return "exhausting"
        return "divergent"

    def _derive_chip_pressure(self, close_to_recent_high: float | None) -> str:
        if close_to_recent_high is None:
            return "unknown"
        if close_to_recent_high > -0.02:
            return "low"
        if close_to_recent_high > -0.08:
            return "mid"
        return "high"

    def _derive_chip_support(self, close_to_recent_low: float | None) -> str:
        if close_to_recent_low is None:
            return "unknown"
        if close_to_recent_low > 0.12:
            return "high"
        if close_to_recent_low > 0.05:
            return "mid"
        return "low"

    def _derive_chip_vacuum(self, chip_pressure: str, breakout_quality: str) -> str:
        if chip_pressure == "low" and breakout_quality == "valid":
            return "open"
        if chip_pressure == "high":
            return "blocked"
        return "mixed"

    def _derive_volume_score(self, volume_ratio: float | None, pattern_context: dict[str, Any]) -> int:
        score = 50.0 if volume_ratio is None else 50.0 + (volume_ratio - 1.0) * 60.0
        score += 0.18 * float(pattern_context.get("gentle_expand_score") or 0.0)
        score += 0.15 * float(pattern_context.get("pullback_shrink_score") or 0.0)
        score -= 0.16 * float(pattern_context.get("distribution_score") or 0.0)
        score -= 0.12 * float(pattern_context.get("stall_score") or 0.0)
        score -= 0.10 * float(pattern_context.get("impulsive_spike_score") or 0.0)
        return self._clip_score(score)

    def _derive_chip_score(
        self,
        chip_pressure: str,
        chip_support: str,
        chip_vacuum: str,
        chip_context: dict[str, Any],
    ) -> int:
        score = 50
        pressure_bonus = {"low": 15, "mid": 0, "high": -15, "unknown": 0}
        support_bonus = {"high": 15, "mid": 5, "low": -10, "unknown": 0}
        vacuum_bonus = {"open": 10, "mixed": 0, "blocked": -10, "unknown": 0}
        score += pressure_bonus.get(chip_pressure, 0)
        score += support_bonus.get(chip_support, 0)
        score += vacuum_bonus.get(chip_vacuum, 0)
        score += 12.0 * float(chip_context.get("support_density") or 0.0)
        score += 10.0 * float(chip_context.get("vacuum_up_ratio") or 0.0)
        score += 8.0 * float(chip_context.get("chip_concentration") or 0.0)
        score -= 14.0 * min(1.0, float(chip_context.get("overhang_ratio") or 0.0) / 0.12)
        score -= 10.0 * float(chip_context.get("pressure_density") or 0.0)
        return self._clip_score(score)

    def _derive_risk_score(
        self,
        volatility_10d: float | None,
        close_to_ma20: float | None,
        breakout_quality: str,
        pattern_context: dict[str, Any],
        trend_stage_context: dict[str, Any],
    ) -> int:
        score = 40.0
        if volatility_10d is not None:
            score += min(35.0, volatility_10d * 400)
        if close_to_ma20 is not None and close_to_ma20 < 0:
            score += min(20.0, abs(close_to_ma20) * 150)
        if breakout_quality == "invalid":
            score += 15.0
        score += 0.16 * float(pattern_context.get("distribution_score") or 0.0)
        score += 0.14 * float(pattern_context.get("stall_score") or 0.0)
        score += 0.10 * float(pattern_context.get("impulsive_spike_score") or 0.0)
        score -= 0.10 * float(pattern_context.get("pullback_shrink_score") or 0.0)
        score -= 0.06 * float(pattern_context.get("gentle_expand_score") or 0.0)
        score += 0.12 * float(trend_stage_context.get("late_stage_score") or 0.0)
        score -= 0.05 * float(trend_stage_context.get("early_stage_score") or 0.0)
        return self._clip_score(score)

    @staticmethod
    def _derive_risk_tags(risk_score: int, breakout_quality: str, volume_state: str) -> list[str]:
        tags: list[str] = []
        if risk_score >= 70:
            tags.append("high_risk")
        if breakout_quality != "valid":
            tags.append("watch_breakout_quality")
        if volume_state == "contract":
            tags.append("watch_volume_shrink")
        return tags

    @staticmethod
    def _build_trigger_context(latest: pd.Series) -> dict[str, Any]:
        trigger_keys = [
            "close_near_high_pct",
            "upper_shadow_ratio",
            "lower_shadow_ratio",
            "body_ratio",
            "gap_pct",
            "volume_spike_ratio",
            "ma20_slope_5",
            "ma20_slope_delta",
            "return_3d",
            "acceleration_score",
            "breakout_confirm_flag",
            "false_breakout_risk_flag",
            "distribution_risk_flag",
            "stall_risk_flag",
            "acceptance_strength",
            "trigger_strength",
            "price_center_shift_5",
            "price_center_shift_10",
            "trend_truth_score",
            "buy_readiness_score",
            "position_state",
            "volume_pattern",
            "close_near_high_inv",
            "body_ratio_inv",
            "efficiency_ratio_20_inv",
            "vol_pct_60",
            "low_base_flag",
            "high_zone_flag",
            "breakout_recently_flag",
            "trend_alignment_flag",
            "peak_shift_up_flag",
            "gentle_expand_score",
            "pullback_shrink_score",
            "impulsive_spike_score",
            "distribution_score",
            "stall_score",
            "early_stage_score_base",
            "mid_stage_score_base",
            "late_stage_score_base",
        ]
        context: dict[str, Any] = {}
        for key in trigger_keys:
            if key not in latest.index or pd.isna(latest.get(key)):
                context[key] = None
                continue
            value = latest.get(key)
            if key.endswith("_flag"):
                context[key] = int(value)
            elif key in {"position_state", "volume_pattern"}:
                context[key] = str(value)
            else:
                context[key] = float(value)
        return context

    @staticmethod
    def _derive_trigger_state(
        *,
        trigger_context: dict[str, Any],
        breakout_quality: str,
        structure_assessment: dict[str, Any],
    ) -> str:
        acceptance_strength = float(trigger_context.get("acceptance_strength") or 0.0)
        trigger_strength = float(trigger_context.get("trigger_strength") or 0.0)
        volume_pattern = trigger_context.get("volume_pattern")
        position_state = trigger_context.get("position_state")
        if (
            int(trigger_context.get("false_breakout_risk_flag") or 0) == 1
            or int(trigger_context.get("distribution_risk_flag") or 0) == 1
            or volume_pattern == "distribution"
        ):
            return "exhausted"
        if (
            breakout_quality == "valid"
            and int(trigger_context.get("breakout_confirm_flag") or 0) == 1
            and structure_assessment.get("confirmation_state") == "confirmed"
            and acceptance_strength >= 0.5
            and trigger_strength >= 56.0
            and volume_pattern not in {"impulsive_spike", "high_level_stall"}
            and position_state != "high_zone"
        ):
            return "confirmed"
        return "watch"

    @staticmethod
    def _derive_opportunity_type(
        *,
        structure_assessment: dict[str, Any],
        breakout_quality: str,
        pullback_quality: str,
        trigger_state: str,
        trigger_context: dict[str, Any],
    ) -> str:
        structure_stage = structure_assessment.get("structure_stage")
        position_state = trigger_context.get("position_state")
        volume_pattern = trigger_context.get("volume_pattern")
        acceptance_strength = float(trigger_context.get("acceptance_strength") or 0.0)
        trigger_strength = float(trigger_context.get("trigger_strength") or 0.0)
        if trigger_state == "exhausted":
            return "reject"
        if (
            structure_stage in {"trend_pullback", "rebound_repair", "range_rotation"}
            and pullback_quality in {"healthy", "neutral"}
            and position_state in {"low_base", "rising"}
            and volume_pattern in {"pullback_shrink", "gentle_expand", "neutral"}
            and acceptance_strength >= 0.42
            and int(trigger_context.get("false_breakout_risk_flag") or 0) == 0
        ):
            return "first_buy"
        if (
            breakout_quality == "valid"
            and trigger_state == "confirmed"
            and volume_pattern in {"gentle_expand", "neutral"}
            and position_state in {"low_base", "rising"}
            and trigger_strength >= 56.0
        ):
            return "trend_follow"
        if (
            breakout_quality in {"valid", "suspicious"}
            and int(trigger_context.get("false_breakout_risk_flag") or 0) == 0
            and int(trigger_context.get("distribution_risk_flag") or 0) == 0
            and volume_pattern not in {"distribution", "high_level_stall"}
        ):
            return "observe"
        return "reject"

    @staticmethod
    def _flatten_evidence_sections(evidence_sections: dict[str, list[str]]) -> list[str]:
        ordered_keys = ["structure", "trigger", "market", "fundamentals", "model", "conflict"]
        flattened: list[str] = []
        for key in ordered_keys:
            flattened.extend(evidence_sections.get(key, []))
        return flattened

    def _build_evidence_sections(
        self,
        weekly_trend: str,
        daily_state: str,
        structure_type: str,
        structure_assessment: dict[str, Any],
        trigger_context: dict[str, Any],
        trigger_state: str,
        opportunity_type: str,
        volume_state: str,
        breakout_quality: str,
        pullback_quality: str,
        chip_pressure: str,
        risk_score: int,
        structure_context: dict[str, Any],
        market_context: dict[str, Any],
        model_context: dict[str, Any],
        fundamental_context: dict[str, Any],
        sector_context: dict[str, Any],
        trend_stage: str,
        execution: dict[str, Any],
        pattern_context: dict[str, Any],
        trend_stage_context: dict[str, Any],
    ) -> dict[str, list[str]]:
        structure_score = int(structure_assessment.get("strength_score", 0))
        confirmation_state = structure_assessment.get("confirmation_state")
        confirmation_score = int(structure_assessment.get("confirmation_score", 0))
        structure_stage = structure_assessment.get("structure_stage")
        structure_confidence = float(structure_assessment.get("confidence", 0.0) or 0.0)
        structure = [
            f"周线趋势判断为 {self._text(weekly_trend)}，日线状态为 {self._text(daily_state)}",
            f"结构类型归为 {self._text(structure_type)}，当前阶段为 {self._text(structure_stage)}，趋势位置处于 {self._text(trend_stage)}，结构分 {structure_score}",
            (
                f"阶段分 early/mid/late = "
                f"{float(trend_stage_context.get('early_stage_score') or 0.0):.1f}/"
                f"{float(trend_stage_context.get('mid_stage_score') or 0.0):.1f}/"
                f"{float(trend_stage_context.get('late_stage_score') or 0.0):.1f}"
            ),
            f"结构确认状态为 {self._text(confirmation_state)}，确认分 {confirmation_score}，结构置信度 {structure_confidence:.2f}",
            f"量能状态为 {self._text(volume_state)}，突破质量为 {self._text(breakout_quality)}，回踩质量为 {self._text(pullback_quality)}",
            f"筹码压力评估为 {self._text(chip_pressure)}",
        ]
        structure.extend(structure_assessment.get("supporting_evidence", []))
        if structure_context.get("bi_direction"):
            structure.append(f"最近一笔方向为 {self._text(structure_context['bi_direction'])}")
        if structure_context.get("segment_direction"):
            structure.append(f"最近线段方向为 {self._text(structure_context['segment_direction'])}")
        if structure_context.get("active_center"):
            structure.append(f"活跃中枢区间为 {structure_context['active_center']}")
        if structure_context.get("divergence_state") and structure_context["divergence_state"] != "none":
            structure.append(f"结构背驰状态为 {self._text(structure_context['divergence_state'])}")
        if structure_context.get("latest_event_type"):
            structure.append(f"最新结构事件为 {self._text(structure_context['latest_event_type'])}")

        trigger = [
            f"机会类型归为 {self._text(opportunity_type)}，当日触发状态为 {self._text(trigger_state)}",
            f"位置 {self._text(trigger_context.get('position_state'))}，量价类型 {self._text(trigger_context.get('volume_pattern'))}，承接强度 {(trigger_context.get('acceptance_strength', 0.0) or 0.0):.2f}，触发强度 {(trigger_context.get('trigger_strength', 0.0) or 0.0):.0f}",
            f"收盘强度 {(trigger_context.get('close_near_high_pct', 0.0) or 0.0):.2f}，量能脉冲 {(trigger_context.get('volume_spike_ratio', 0.0) or 0.0):.2f}，加速度 {(trigger_context.get('acceleration_score', 0.0) or 0.0):.2f}",
            (
                f"模式分 gentle/pullback/impulse/distribution/stall = "
                f"{float(pattern_context.get('gentle_expand_score') or 0.0):.1f}/"
                f"{float(pattern_context.get('pullback_shrink_score') or 0.0):.1f}/"
                f"{float(pattern_context.get('impulsive_spike_score') or 0.0):.1f}/"
                f"{float(pattern_context.get('distribution_score') or 0.0):.1f}/"
                f"{float(pattern_context.get('stall_score') or 0.0):.1f}"
            ),
        ]
        if int(trigger_context.get("breakout_confirm_flag") or 0) == 1:
            trigger.append("当日突破确认条件已触发")
        if int(trigger_context.get("false_breakout_risk_flag") or 0) == 1:
            trigger.append("当日量价更像冲高回落或假突破，不能直接当作确认")
        if int(trigger_context.get("distribution_risk_flag") or 0) == 1:
            trigger.append("量价组合更接近高位分布或出货，不适合继续按趋势票理解")
        if int(trigger_context.get("stall_risk_flag") or 0) == 1:
            trigger.append("高位滞涨缩量，重心未继续抬升，趋势延续质量存疑")
        if (trigger_context.get("acceptance_strength") or 0.0) < 0.45:
            trigger.append("当日承接偏弱，次日需要重新验证")

        market = []
        if market_context:
            market.append(
                f"相对基准 {market_context['benchmark_ticker']} 的 5 日超额收益为 "
                f"{market_context['excess_return_5d']:.2%}，20 日超额收益为 {market_context['excess_return_20d']:.2%}"
            )
            market.append(
                f"基准当前偏 {self._text(market_context['benchmark_weekly_trend'])}，本票相对强弱归类为 "
                f"{market_context['relative_strength_label']}"
            )
            market.append(
                f"市场趋势分 {float(market_context.get('market_trend_score') or 0.0):.1f}，"
                f"市场阶段为 {self._text(market_context.get('market_stage'))}"
            )
        else:
            market.append("当前数据源未返回可用基准行情，因此相对强弱仍按个股自身特征解释")
        if sector_context:
            market.append(
                f"行业 {self._text(sector_context.get('industry'))} 的趋势分 {float(sector_context.get('sector_trend_score') or 0.0):.1f}，"
                f"强度分 {float(sector_context.get('sector_strength_score') or 0.0):.1f}"
            )

        fundamentals = []
        if fundamental_context:
            fundamentals.append(
                f"基本面分 {float(fundamental_context.get('fundamental_score') or 0.0):.1f}，"
                f"质量标签 {self._text(fundamental_context.get('fundamental_label'))}"
            )
            if fundamental_context.get("valuation_label"):
                fundamentals.append(f"估值状态 {self._text(fundamental_context.get('valuation_label'))}")
            if fundamental_context.get("growth_label"):
                fundamentals.append(f"成长状态 {self._text(fundamental_context.get('growth_label'))}")
            if fundamental_context.get("quality_label"):
                fundamentals.append(f"财务质量 {self._text(fundamental_context.get('quality_label'))}")
        else:
            fundamentals.append("当前没有可用财务因子，基本面判断暂按中性处理")

        model = [
            "模型层判断为 "
            f"{self._text(model_context['regime_label'])}，延续概率 {model_context['p_continue_10d']:.2f}，"
            f"突破成功率 {model_context['p_breakout_success']:.2f}，失效概率 {model_context['p_fail_5d']:.2f}",
            f"regime 置信度 {model_context['regime_confidence']:.2f}，模型版本 {self._text(model_context['model_version'])}",
        ]
        if model_context.get("entry_probability") is not None:
            model.append(
                f"入场概率曲线给出 {model_context['entry_probability']:.2f}，入场分区为 {self._text(model_context.get('entry_zone'))}"
            )
        if model_context.get("expected_return_10d") is not None and model_context.get("expected_drawdown_10d") is not None:
            model.append(
                f"模型预期 10 日收益 {model_context['expected_return_10d']:.2%}，"
                f"预期回撤 {model_context['expected_drawdown_10d']:.2%}"
            )
        model.append(
            f"执行层给出 {self._text(execution.get('action_label'))}，目标仓位 {int(execution.get('target_position_pct') or 0)}%，"
            f"执行分 {float(execution.get('execution_score') or 0.0):.1f}"
        )

        conflict: list[str] = []
        if structure_type in {"weak_rebound", "range_pivot"} and model_context.get("regime_label") in {"strong_up", "weak_up"}:
            conflict.append("结构层尚未给出强趋势确认，但模型层已偏向上涨延续")
        if breakout_quality != "valid":
            conflict.append("突破质量尚未转为有效，说明价格位置改善快于量价确认")
        if structure_context.get("segment_direction") == "down" and model_context.get("regime_label") in {"strong_up", "weak_up"}:
            conflict.append("最近线段方向仍偏下，与模型的上涨判断存在方向冲突")
        if risk_score >= 60 and (model_context.get("p_continue_10d") or 0.0) >= 0.6:
            conflict.append("模型延续概率不低，但风险分仍偏高，说明赔率和波动没有同步改善")
        conflict.extend(structure_assessment.get("opposing_evidence", []))
        if not conflict:
            conflict.append("当前结构、相对强弱和模型判断没有明显对冲项")

        return {
            "structure": structure,
            "trigger": trigger,
            "market": market,
            "fundamentals": fundamentals,
            "model": model,
            "conflict": conflict,
        }

    def _build_market_context(
        self,
        ticker: str,
        analysis_date: str,
        latest: pd.Series,
        benchmark_bars: pd.DataFrame | None = None,
    ) -> dict[str, Any]:
        benchmark_ticker = self.config.benchmark_ticker
        if not benchmark_ticker or benchmark_ticker == ticker:
            return {}

        stock_return_5d = self._as_float(latest.get("return_5d")) or 0.0
        stock_return_20d = self._as_float(latest.get("return_20d")) or 0.0
        benchmark_context = self._resolve_benchmark_context(
            analysis_date=analysis_date,
            benchmark_ticker=benchmark_ticker,
            benchmark_bars=benchmark_bars,
        )
        if not benchmark_context:
            return {}

        benchmark_return_5d = float(benchmark_context["benchmark_return_5d"])
        benchmark_return_20d = float(benchmark_context["benchmark_return_20d"])
        excess_return_5d = stock_return_5d - benchmark_return_5d
        excess_return_20d = stock_return_20d - benchmark_return_20d
        return {
            "benchmark_ticker": benchmark_ticker,
            "benchmark_weekly_trend": benchmark_context["benchmark_weekly_trend"],
            "benchmark_daily_state": benchmark_context["benchmark_daily_state"],
            "benchmark_return_5d": benchmark_return_5d,
            "benchmark_return_20d": benchmark_return_20d,
            "excess_return_5d": excess_return_5d,
            "excess_return_20d": excess_return_20d,
            "relative_strength_label": self._derive_relative_strength_label(excess_return_20d, excess_return_5d),
            "market_trend_score": self._derive_market_trend_score(
                benchmark_context["benchmark_weekly_trend"],
                benchmark_context["benchmark_daily_state"],
                benchmark_return_5d,
                benchmark_return_20d,
            ),
            "market_stage": self._derive_market_stage(benchmark_return_5d, benchmark_return_20d),
        }

    def _build_sector_context(
        self,
        *,
        market_context: dict[str, Any],
        sector_context: dict[str, Any] | None,
        metadata_context: dict[str, Any] | None,
    ) -> dict[str, Any]:
        metadata_context = metadata_context or {}
        sector_context = dict(sector_context or {})
        if "industry" not in sector_context:
            sector_context["industry"] = metadata_context.get("industry")
        if "market" not in sector_context:
            sector_context["market"] = metadata_context.get("market")
        sector_context.setdefault("sector_trend_score", market_context.get("market_trend_score", 50.0))
        sector_context.setdefault("sector_strength_score", 50.0)
        sector_context.setdefault("sector_stage", market_context.get("market_stage", "range"))
        return sector_context

    def _build_fundamental_context(
        self,
        *,
        ticker: str,
        latest: pd.Series,
        daily_basic_context: dict[str, Any] | None,
        fundamental_context: dict[str, Any] | None,
        fina_indicator_context: dict[str, Any] | None,
    ) -> dict[str, Any]:
        if fundamental_context:
            return dict(fundamental_context)
        daily_basic_context = daily_basic_context or {}
        fina_indicator_context = fina_indicator_context or {}
        pe = self._as_float(daily_basic_context.get("pe"))
        pb = self._as_float(daily_basic_context.get("pb"))
        ps_ttm = self._as_float(daily_basic_context.get("ps_ttm"))
        turnover_rate = self._as_float(daily_basic_context.get("turnover_rate_f")) or self._as_float(daily_basic_context.get("turnover_rate"))
        total_mv = self._as_float(daily_basic_context.get("total_mv"))
        circ_mv = self._as_float(daily_basic_context.get("circ_mv"))
        return_20d = self._as_float(latest.get("return_20d")) or 0.0
        roe_dt = self._as_float(fina_indicator_context.get("roe_dt"))
        or_yoy = self._as_float(fina_indicator_context.get("or_yoy"))
        op_yoy = self._as_float(fina_indicator_context.get("op_yoy"))
        q_sales_yoy = self._as_float(fina_indicator_context.get("q_sales_yoy"))
        q_dtprofit_yoy = self._as_float(fina_indicator_context.get("q_dtprofit_yoy"))
        grossprofit_margin = self._as_float(fina_indicator_context.get("grossprofit_margin"))
        debt_to_assets = self._as_float(fina_indicator_context.get("debt_to_assets"))
        ocf_to_or = self._as_float(fina_indicator_context.get("ocf_to_or"))
        current_ratio = self._as_float(fina_indicator_context.get("current_ratio"))
        quick_ratio = self._as_float(fina_indicator_context.get("quick_ratio"))

        valuation_score = 55.0
        if pe is not None:
            valuation_score += 8.0 if 0 < pe <= 30 else -8.0 if pe > 60 else 0.0
        if pb is not None:
            valuation_score += 6.0 if pb <= 3 else -6.0 if pb > 8 else 0.0
        if ps_ttm is not None:
            valuation_score += 4.0 if ps_ttm <= 4 else -4.0 if ps_ttm > 10 else 0.0

        quality_score = 50.0
        if total_mv is not None and circ_mv is not None and total_mv > 0:
            quality_score += 6.0 if circ_mv / total_mv >= 0.45 else 0.0
        if turnover_rate is not None:
            quality_score += 5.0 if turnover_rate <= 6 else -5.0 if turnover_rate > 18 else 0.0
        quality_score += 6.0 if return_20d > 0 else -4.0
        if roe_dt is not None:
            quality_score += 8.0 if roe_dt >= 10 else -6.0 if roe_dt < 4 else 0.0
        if grossprofit_margin is not None:
            quality_score += 6.0 if grossprofit_margin >= 20 else -4.0 if grossprofit_margin < 10 else 0.0
        if ocf_to_or is not None:
            quality_score += 6.0 if ocf_to_or >= 0.08 else -5.0 if ocf_to_or < 0 else 0.0

        growth_score = 50.0
        growth_candidates = [value for value in [or_yoy, op_yoy, q_sales_yoy, q_dtprofit_yoy] if value is not None]
        if growth_candidates:
            growth_mean = sum(growth_candidates) / len(growth_candidates)
            growth_score += 8.0 if growth_mean >= 15 else -8.0 if growth_mean < 0 else 0.0
        growth_score += 6.0 if return_20d > 0 else -3.0

        balance_sheet_score = 50.0
        if debt_to_assets is not None:
            balance_sheet_score += 6.0 if debt_to_assets <= 45 else -8.0 if debt_to_assets >= 70 else 0.0
        if current_ratio is not None:
            balance_sheet_score += 4.0 if current_ratio >= 1.3 else -4.0 if current_ratio < 0.9 else 0.0
        if quick_ratio is not None:
            balance_sheet_score += 4.0 if quick_ratio >= 0.9 else -4.0 if quick_ratio < 0.6 else 0.0

        valuation_score = max(0.0, min(100.0, valuation_score))
        quality_score = max(0.0, min(100.0, quality_score))
        growth_score = max(0.0, min(100.0, growth_score))
        balance_sheet_score = max(0.0, min(100.0, balance_sheet_score))
        fundamental_score = max(
            0.0,
            min(
                100.0,
                valuation_score * 0.20
                + quality_score * 0.35
                + growth_score * 0.25
                + balance_sheet_score * 0.20,
            ),
        )
        return {
            "ticker": ticker,
            "pe": pe,
            "pb": pb,
            "ps_ttm": ps_ttm,
            "turnover_rate_f": turnover_rate,
            "total_mv": total_mv,
            "circ_mv": circ_mv,
            "roe_dt": roe_dt,
            "or_yoy": or_yoy,
            "op_yoy": op_yoy,
            "q_sales_yoy": q_sales_yoy,
            "q_dtprofit_yoy": q_dtprofit_yoy,
            "grossprofit_margin": grossprofit_margin,
            "debt_to_assets": debt_to_assets,
            "ocf_to_or": ocf_to_or,
            "current_ratio": current_ratio,
            "quick_ratio": quick_ratio,
            "valuation_score": round(valuation_score, 4),
            "quality_score": round(quality_score, 4),
            "growth_score": round(growth_score, 4),
            "balance_sheet_score": round(balance_sheet_score, 4),
            "fundamental_score": round(fundamental_score, 4),
            "fundamental_label": "strong" if fundamental_score >= 65 else "neutral" if fundamental_score >= 45 else "weak",
            "valuation_label": "reasonable" if (pe or 0) <= 30 and (pb or 0) <= 3 else "expensive" if (pe or 0) > 60 or (pb or 0) > 8 else "neutral",
            "growth_label": "positive" if growth_score >= 60 else "neutral" if growth_score >= 45 else "weak",
            "quality_label": "stable" if quality_score >= 60 else "fragile" if quality_score < 45 else "neutral",
        }

    def _fetch_external_contexts(self, *, ticker: str, analysis_date: str) -> dict[str, Any]:
        if not hasattr(self.importer, "client"):
            return {
                "metadata_context": {},
                "sector_context": {},
                "fundamental_context": None,
                "daily_basic_context": {},
                "chip_perf_context": {},
                "fina_indicator_context": {},
            }
        trade_date = pd.Timestamp(analysis_date).strftime("%Y%m%d")
        metadata = self._metadata_for_ticker(ticker)
        daily_basic_context: dict[str, Any] = {}
        chip_perf_context: dict[str, Any] = {}
        fina_indicator_context: dict[str, Any] = {}
        try:
            daily_basic_df = self.importer.client.fetch_daily_basic(ts_code=ticker, trade_date=trade_date)
            if not daily_basic_df.empty:
                daily_basic_context = daily_basic_df.iloc[0].to_dict()
        except Exception:
            daily_basic_context = {}
        try:
            fina_indicator_df = self.importer.client.fetch_fina_indicator(ts_code=ticker, limit=1)
            if not fina_indicator_df.empty:
                fina_indicator_context = fina_indicator_df.iloc[0].to_dict()
        except Exception:
            fina_indicator_context = {}
        try:
            chip_perf_df = self.importer.client.fetch_chip_perf(ts_code=ticker, trade_date=trade_date)
            if not chip_perf_df.empty:
                chip_perf_context = chip_perf_df.iloc[0].to_dict()
        except Exception:
            chip_perf_context = {}
        return {
            "metadata_context": metadata,
            "sector_context": {
                "industry": metadata.get("industry"),
                "market": metadata.get("market"),
            },
            "fundamental_context": None,
            "daily_basic_context": daily_basic_context,
            "chip_perf_context": chip_perf_context,
            "fina_indicator_context": fina_indicator_context,
        }

    def _metadata_for_ticker(self, ticker: str) -> dict[str, Any]:
        if not hasattr(self.importer, "client"):
            return {}
        if self._stock_basic_cache is None:
            try:
                self._stock_basic_cache = self.importer.client.fetch_stock_basic(fields="ts_code,name,industry,market")
            except Exception:
                self._stock_basic_cache = pd.DataFrame(columns=["ts_code", "name", "industry", "market"])
        matched = self._stock_basic_cache.loc[self._stock_basic_cache["ts_code"] == ticker]
        if matched.empty:
            return {}
        return matched.iloc[0].to_dict()

    def _resolve_benchmark_context(
        self,
        analysis_date: str,
        benchmark_ticker: str,
        benchmark_bars: pd.DataFrame | None,
    ) -> dict[str, Any]:
        cache_key = (analysis_date, benchmark_ticker)
        cached = self._benchmark_context_cache.get(cache_key)
        if cached is not None:
            return cached

        try:
            if benchmark_bars is None:
                benchmark_bars = self._load_daily_bars(ticker=benchmark_ticker, analysis_date=analysis_date)
        except Exception:
            self._benchmark_context_cache[cache_key] = {}
            return {}

        benchmark_features = compute_trend_features(benchmark_bars)
        benchmark_features = compute_volatility_features(benchmark_features)
        benchmark_features = compute_volume_features(benchmark_features)
        benchmark_latest = benchmark_features.iloc[-1]
        benchmark_close_to_ma20 = self._as_float(benchmark_latest.get("close_to_ma20"))
        benchmark_ma_slope = self._as_float(benchmark_latest.get("ma_slope"))
        benchmark_return_5d = self._as_float(benchmark_latest.get("return_5d")) or 0.0

        context = {
            "benchmark_ticker": benchmark_ticker,
            "benchmark_weekly_trend": self._derive_weekly_trend(benchmark_close_to_ma20, benchmark_ma_slope),
            "benchmark_daily_state": self._derive_daily_state(
                self._as_float(benchmark_latest.get("return_1d")),
                benchmark_return_5d,
                benchmark_close_to_ma20,
            ),
            "benchmark_return_5d": benchmark_return_5d,
            "benchmark_return_20d": self._as_float(benchmark_latest.get("return_20d")) or 0.0,
        }
        self._benchmark_context_cache[cache_key] = context
        return context

    @staticmethod
    def _derive_relative_strength_label(excess_return_20d: float, excess_return_5d: float) -> str:
        if excess_return_20d > 0.08 and excess_return_5d > 0.03:
            return "显著强于基准"
        if excess_return_20d > 0.02 and excess_return_5d > 0:
            return "略强于基准"
        if excess_return_20d < -0.05 and excess_return_5d < 0:
            return "明显弱于基准"
        if excess_return_20d < -0.01:
            return "略弱于基准"
        return "与基准大体同步"

    @staticmethod
    def _build_pattern_context(latest: pd.Series) -> dict[str, Any]:
        fields = [
            "gentle_expand_score",
            "pullback_shrink_score",
            "impulsive_spike_score",
            "distribution_score",
            "stall_score",
            "early_stage_score_base",
            "mid_stage_score_base",
            "late_stage_score_base",
            "low_base_flag",
            "high_zone_flag",
            "breakout_recently_flag",
            "trend_alignment_flag",
        ]
        context: dict[str, Any] = {}
        for field in fields:
            value = latest.get(field)
            if pd.isna(value):
                context[field] = 0.0 if field.endswith("_score") or field.endswith("_base") else 0
            elif field.endswith("_flag"):
                context[field] = int(value)
            else:
                context[field] = float(value)
        positive_patterns = {
            "gentle_expand": float(context.get("gentle_expand_score") or 0.0),
            "pullback_shrink": float(context.get("pullback_shrink_score") or 0.0),
        }
        risk_patterns = {
            "impulsive_spike": float(context.get("impulsive_spike_score") or 0.0),
            "distribution": float(context.get("distribution_score") or 0.0),
            "high_level_stall": float(context.get("stall_score") or 0.0),
        }
        dominant_positive = max(positive_patterns, key=positive_patterns.get)
        dominant_risk = max(risk_patterns, key=risk_patterns.get)
        context["dominant_positive_pattern"] = dominant_positive if positive_patterns[dominant_positive] >= 55 else "neutral"
        context["dominant_risk_pattern"] = dominant_risk if risk_patterns[dominant_risk] >= 55 else "none"
        return context

    def _build_trend_stage_context(
        self,
        *,
        latest: pd.Series,
        structure_assessment: dict[str, Any],
        trigger_context: dict[str, Any],
        chip_context: dict[str, Any],
        market_context: dict[str, Any],
        sector_context: dict[str, Any],
    ) -> dict[str, Any]:
        del structure_assessment

        return_20d = self._as_float(latest.get("return_20d")) or 0.0
        early = float(trigger_context.get("early_stage_score_base") or 0.0)
        mid = float(trigger_context.get("mid_stage_score_base") or 0.0)
        late = float(trigger_context.get("late_stage_score_base") or 0.0)

        winner_ratio = float(chip_context.get("winner_ratio") or chip_context.get("winner_rate") or 0.0)
        overhang_ratio = float(chip_context.get("overhang_ratio") or 0.0)
        support_density = float(chip_context.get("support_density") or 0.0)
        pressure_density = float(chip_context.get("pressure_density") or 0.0)
        vacuum_up_ratio = float(chip_context.get("vacuum_up_ratio") or 0.0)
        chip_concentration = float(chip_context.get("chip_concentration") or 0.0)
        peak_shift_5d = float(chip_context.get("peak_shift_5d") or 0.0)

        market_trend_score = float(market_context.get("market_trend_score") or 50.0)
        sector_trend_score = float(sector_context.get("sector_trend_score") or 50.0)
        sector_stage = str(sector_context.get("sector_stage") or "range")

        early += 10.0 * self._trap_value(winner_ratio, 0.25, 0.35, 0.70, 0.82)
        early += 8.0 * self._trap_value(1.0 - min(1.0, overhang_ratio / 0.12), 0.45, 0.60, 1.00, 1.00)
        early += 6.0 if peak_shift_5d > 0 else 0.0
        early += 4.0 * self._trap_value(vacuum_up_ratio, 0.25, 0.40, 1.00, 1.00)
        early += 4.0 * self._trap_value(market_trend_score / 100.0, 0.45, 0.55, 0.75, 0.95)

        mid += 10.0 * self._trap_value(support_density, 0.60, 0.75, 1.00, 1.00)
        mid += 8.0 * self._trap_value(1.0 - min(1.0, overhang_ratio / 0.12), 0.45, 0.60, 1.00, 1.00)
        mid += 6.0 * self._trap_value(chip_concentration, 0.30, 0.50, 0.90, 1.00)
        mid += 4.0 * self._trap_value(sector_trend_score / 100.0, 0.45, 0.55, 0.80, 1.00)
        mid += 4.0 if sector_stage == "mid" else 0.0

        late += 10.0 * self._trap_value(winner_ratio, 0.75, 0.85, 1.00, 1.00)
        late += 8.0 * self._trap_value(pressure_density, 0.55, 0.70, 1.00, 1.00)
        late += 8.0 * self._trap_value(overhang_ratio, 0.04, 0.08, 0.20, 0.30)
        late += 4.0 if sector_stage == "late" else 0.0
        late += 4.0 * self._trap_value(float(trigger_context.get("distribution_score") or 0.0) / 100.0, 0.40, 0.55, 1.00, 1.00)

        early = max(0.0, min(100.0, early))
        mid = max(0.0, min(100.0, mid))
        late = max(0.0, min(100.0, late))

        ordered = sorted(
            [("early", early), ("mid", mid), ("late", late)],
            key=lambda item: item[1],
            reverse=True,
        )
        stage, top_score = ordered[0]
        second_score = ordered[1][1]
        confidence = max(0.0, min(1.0, (top_score - second_score) / 35.0))
        if return_20d <= 0 or top_score < 55.0:
            stage = "transition"
            confidence = max(0.0, min(1.0, top_score / 100.0))
        reason_codes: list[str] = []
        if stage == "early":
            if int(trigger_context.get("low_base_flag") or 0) == 1:
                reason_codes.append("STAGE_LOW_BASE")
            if int(trigger_context.get("breakout_recently_flag") or 0) == 1:
                reason_codes.append("STAGE_BREAKOUT_RECENT")
            if float(trigger_context.get("gentle_expand_score") or 0.0) >= 60.0:
                reason_codes.append("PATTERN_GENTLE_EXPAND")
            if peak_shift_5d > 0:
                reason_codes.append("CHIP_PEAK_SHIFT_UP")
        elif stage == "mid":
            if int(trigger_context.get("trend_alignment_flag") or 0) == 1:
                reason_codes.append("STAGE_TREND_ALIGNMENT")
            if float(trigger_context.get("pullback_shrink_score") or 0.0) >= 60.0:
                reason_codes.append("PATTERN_PULLBACK_SHRINK")
            if support_density >= 0.7:
                reason_codes.append("CHIP_SUPPORT_STRONG")
            if overhang_ratio <= 0.05:
                reason_codes.append("CHIP_OVERHANG_LIGHT")
        elif stage == "late":
            if int(trigger_context.get("high_zone_flag") or 0) == 1:
                reason_codes.append("STAGE_HIGH_ZONE")
            if float(trigger_context.get("distribution_score") or 0.0) >= 58.0:
                reason_codes.append("PATTERN_DISTRIBUTION")
            if float(trigger_context.get("stall_score") or 0.0) >= 58.0:
                reason_codes.append("PATTERN_STALL")
            if winner_ratio >= 0.85:
                reason_codes.append("CHIP_PROFIT_CROWDED")
            if pressure_density >= 0.7 or overhang_ratio >= 0.08:
                reason_codes.append("CHIP_PRESSURE_NEAR")
        return {
            "trend_stage": stage,
            "trend_stage_confidence": round(confidence, 4),
            "early_stage_score": round(early, 4),
            "mid_stage_score": round(mid, 4),
            "late_stage_score": round(late, 4),
            "stage_reason_codes": reason_codes,
        }

    @staticmethod
    def _derive_market_trend_score(
        benchmark_weekly_trend: str,
        benchmark_daily_state: str,
        benchmark_return_5d: float,
        benchmark_return_20d: float,
    ) -> float:
        score = 50.0
        score += 8.0 if benchmark_weekly_trend == "up" else -8.0 if benchmark_weekly_trend == "down" else 0.0
        score += 5.0 if benchmark_daily_state == "trend" else -5.0 if benchmark_daily_state == "exhaustion" else 0.0
        score += max(-10.0, min(10.0, benchmark_return_20d * 120.0))
        score += max(-8.0, min(8.0, benchmark_return_5d * 100.0))
        return round(max(0.0, min(100.0, score)), 4)

    @staticmethod
    def _derive_market_stage(benchmark_return_5d: float, benchmark_return_20d: float) -> str:
        if benchmark_return_20d <= 0:
            return "range"
        if benchmark_return_20d < 0.05 and benchmark_return_5d > 0:
            return "early"
        if benchmark_return_20d < 0.12:
            return "mid"
        return "late"

    @staticmethod
    def _trap_value(value: float, a: float, b: float, c: float, d: float) -> float:
        if value <= a or value >= d:
            return 0.0
        if a < value < b and b > a:
            return max(0.0, min(1.0, (value - a) / (b - a)))
        if b <= value <= c:
            return 1.0
        if c < value < d and d > c:
            return max(0.0, min(1.0, (d - value) / (d - c)))
        return 0.0

    @staticmethod
    def _build_quant_reason_codes(
        *,
        pattern_context: dict[str, Any],
        trend_stage_context: dict[str, Any],
        chip_context: dict[str, Any],
    ) -> list[str]:
        codes = list(trend_stage_context.get("stage_reason_codes", []))
        if float(pattern_context.get("gentle_expand_score") or 0.0) >= 60.0:
            codes.append("PATTERN_GENTLE_EXPAND")
        if float(pattern_context.get("pullback_shrink_score") or 0.0) >= 60.0:
            codes.append("PATTERN_PULLBACK_SHRINK")
        if float(pattern_context.get("impulsive_spike_score") or 0.0) >= 60.0:
            codes.append("PATTERN_IMPULSIVE_SPIKE")
        if float(pattern_context.get("distribution_score") or 0.0) >= 58.0:
            codes.append("PATTERN_DISTRIBUTION")
        if float(pattern_context.get("stall_score") or 0.0) >= 58.0:
            codes.append("PATTERN_STALL")
        if float(chip_context.get("support_density") or 0.0) >= 0.7:
            codes.append("CHIP_SUPPORT_DENSITY_HIGH")
        if float(chip_context.get("pressure_density") or 0.0) >= 0.7:
            codes.append("CHIP_PRESSURE_DENSITY_HIGH")
        if float(chip_context.get("overhang_ratio") or 0.0) >= 0.08:
            codes.append("CHIP_OVERHANG_HEAVY")
        if float(chip_context.get("vacuum_up_ratio") or 0.0) >= 0.55:
            codes.append("CHIP_VACUUM_UP")
        return list(dict.fromkeys(codes))

    @staticmethod
    def _text(value: str | None) -> str:
        mapping = {
            "up": "上涨",
            "down": "下行",
            "range": "震荡",
            "trend": "趋势推进",
            "rebound": "反弹修复",
            "consolidation": "整理",
            "transition": "过渡",
            "exhaustion": "衰竭",
            "trend_continue": "趋势延续",
            "breakout_pullback": "突破后回踩",
            "weak_rebound": "弱反弹",
            "range_pivot": "区间枢轴",
            "neutral": "中性",
            "expand": "放量",
            "contract": "缩量",
            "valid": "有效",
            "suspicious": "可疑",
            "invalid": "无效",
            "healthy": "健康",
            "damaged": "受损",
            "divergent": "背离",
            "exhausting": "衰竭式波动",
            "low": "低",
            "mid": "中",
            "high": "高",
            "mixed": "混合",
            "open": "打开",
            "blocked": "受阻",
            "none": "无",
            "mild": "轻微",
            "confirmed": "确认",
            "breakout_up": "向上突破",
            "risk_reversal": "风险反转",
            "weak_up": "偏弱上涨",
            "strong_up": "强势上涨",
            "weak_down": "偏弱下行",
            "rule_fallback_v1": "规则回退模型 v1",
            "bullish": "偏多",
            "bearish": "偏空",
            "early": "趋势初期",
            "mid": "趋势中期",
            "late": "趋势末期",
            "transition": "过渡期",
            "reasonable": "估值合理",
            "expensive": "估值偏贵",
            "positive": "成长向上",
            "strong": "基本面较强",
            "weak": "基本面偏弱",
            "stable": "财务较稳",
            "fragile": "财务偏脆弱",
            "probe": "试仓",
            "build": "建立仓位",
            "watch": "观察",
            "stand_aside": "空仓观望",
            "breakout_only": "只做突破确认",
            "pullback_only": "只做回踩承接",
            "breakout_or_pullback": "突破或回踩均可",
            "no_chase": "不追高",
            "first_buy": "第一类买点",
            "trend_follow": "趋势确认跟随",
            "observe": "继续观察",
            "reject": "放弃",
            "ready": "可执行",
            "candidate": "候选",
            "avoid": "回避",
            "low_base": "低位蓄势",
            "rising": "上升途中",
            "high_zone": "高位区",
            "downtrend": "下跌途中",
            "gentle_expand": "温和放量",
            "impulsive_spike": "脉冲放量",
            "pullback_shrink": "回调缩量",
            "high_level_stall": "高位滞涨缩量",
            "down_shrink": "跌途缩量",
            "distribution": "高位分布",
            "breakout_confirmed": "突破确认",
            "breakout_attempt": "突破尝试",
            "trend_pressing_high": "趋势逼近前高",
            "trend_pullback": "趋势回踩",
            "trend_advancing": "趋势推进",
            "distribution": "分配转弱",
            "breakdown": "向下破位",
            "confirmed": "已确认",
            "watch": "观察中",
            "exhausted": "衰竭",
            "pending": "待确认",
            "failed": "确认失败",
        }
        return mapping.get(value or "", value or "未知")

    @staticmethod
    def _build_structure_context(features: pd.DataFrame) -> dict[str, Any]:
        pivots = PivotDetector().detect(features)
        bis = BiBuilder().build(pivots, bars_df=features)
        segments = SegmentBuilder().build(bis)
        centers = CenterDetector().detect(segments)
        divergence = DivergenceDetector().detect(segments)
        events = EventEngine().detect(features, centers)
        structure_features = StructureFeatureBuilder().build(bis, segments, centers, divergence, events)

        latest_pivot = pivots[-1] if pivots else None
        latest_bi = bis[-1] if bis else None
        latest_segment = segments[-1] if segments else None
        active_center = centers[-1] if centers else None
        latest_event = events[-1] if events else None

        active_center_text = None
        if active_center is not None:
            active_center_text = f"{active_center.zone_lower:.4f}-{active_center.zone_upper:.4f}"

        return {
            "pivot_count": len(pivots),
            "latest_pivot_type": latest_pivot.pivot_type if latest_pivot else None,
            "bi_direction": latest_bi.direction if latest_bi else None,
            "last_bi_direction": latest_bi.direction if latest_bi else None,
            "segment_direction": latest_segment.direction if latest_segment else None,
            "active_center": active_center_text,
            "active_center_upper": active_center.zone_upper if active_center else None,
            "active_center_lower": active_center.zone_lower if active_center else None,
            "center_shift_direction": active_center.shift_direction if active_center else None,
            "latest_event_type": latest_event.event_type if latest_event else None,
            **structure_features,
        }

    @staticmethod
    def _build_feature_context(
        latest: pd.Series,
        structure_context: dict[str, Any],
        market_context: dict[str, Any],
        structure_assessment: dict[str, Any],
        trigger_context: dict[str, Any],
        sector_context: dict[str, Any],
        fundamental_context: dict[str, Any],
        trend_stage: str,
        *,
        weekly_trend: str,
        daily_state: str,
        breakout_quality: str,
        pullback_quality: str,
        price_action_quality: str,
        trigger_state: str,
        opportunity_type: str,
        chip_pressure: str,
        chip_support: str,
        chip_vacuum: str,
        pattern_context: dict[str, Any],
        trend_stage_context: dict[str, Any],
        chip_context: dict[str, Any],
    ) -> dict[str, Any]:
        feature_keys = [
            "return_5d",
            "return_10d",
            "return_20d",
            "ma20_slope",
            "ma60_slope",
            "atr_14",
            "atr_pct",
            "realized_vol_20",
            "price_vs_ma20_z",
            "price_vs_ma60_z",
            "trend_r2_20",
            "trend_r2_60",
            "efficiency_ratio_20",
            "efficiency_ratio_60",
            "obv_slope_10",
            "vwap_distance",
            "breakout_volume_percentile",
            "pullback_volume_shrink_ratio",
            "close_near_high_pct",
            "upper_shadow_ratio",
            "lower_shadow_ratio",
            "body_ratio",
            "gap_pct",
            "volume_spike_ratio",
            "ma20_slope_5",
            "ma20_slope_delta",
            "return_3d",
            "acceleration_score",
            "acceptance_strength",
            "trigger_strength",
            "price_center_shift_5",
            "price_center_shift_10",
            "trend_truth_score",
            "buy_readiness_score",
            "close_near_high_inv",
            "body_ratio_inv",
            "efficiency_ratio_20_inv",
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
        feature_context = {
            key: (None if pd.isna(latest.get(key)) else float(latest.get(key))) for key in feature_keys if key in latest.index
        }
        feature_context.update(
            {
                "last_bi_direction": structure_context.get("last_bi_direction"),
                "segment_direction": structure_context.get("segment_direction"),
                "center_shift_direction": structure_context.get("center_shift_direction"),
                "divergence_state": structure_context.get("divergence_state"),
                "benchmark_weekly_trend": market_context.get("benchmark_weekly_trend"),
                "benchmark_daily_state": market_context.get("benchmark_daily_state"),
                "benchmark_return_5d": market_context.get("benchmark_return_5d"),
                "benchmark_return_20d": market_context.get("benchmark_return_20d"),
                "excess_return_5d": market_context.get("excess_return_5d"),
                "excess_return_20d": market_context.get("excess_return_20d"),
                "market_trend_score": market_context.get("market_trend_score"),
                "sector_trend_score": sector_context.get("sector_trend_score"),
                "sector_strength_score": sector_context.get("sector_strength_score"),
                "fundamental_score": fundamental_context.get("fundamental_score"),
                "valuation_score": fundamental_context.get("valuation_score"),
                "quality_score": fundamental_context.get("quality_score"),
                "weekly_trend": weekly_trend,
                "daily_state": daily_state,
                "trend_stage": trend_stage,
                "structure_stage": structure_assessment.get("structure_stage"),
                "confirmation_state": structure_assessment.get("confirmation_state"),
                "structure_strength_score": structure_assessment.get("strength_score"),
                "structure_confirmation_score": structure_assessment.get("confirmation_score"),
                "structure_risk_score": structure_assessment.get("risk_score"),
                "structure_confidence": structure_assessment.get("confidence"),
                "breakout_quality": breakout_quality,
                "pullback_quality": pullback_quality,
                "price_action_quality": price_action_quality,
                "breakout_confirm_flag": trigger_context.get("breakout_confirm_flag"),
                "false_breakout_risk_flag": trigger_context.get("false_breakout_risk_flag"),
                "distribution_risk_flag": trigger_context.get("distribution_risk_flag"),
                "stall_risk_flag": trigger_context.get("stall_risk_flag"),
                "trigger_state": trigger_state,
                "opportunity_type": opportunity_type,
                "position_state": trigger_context.get("position_state"),
                "volume_pattern": trigger_context.get("volume_pattern"),
                "gentle_expand_score": pattern_context.get("gentle_expand_score"),
                "pullback_shrink_score": pattern_context.get("pullback_shrink_score"),
                "impulsive_spike_score": pattern_context.get("impulsive_spike_score"),
                "distribution_score": pattern_context.get("distribution_score"),
                "stall_score": pattern_context.get("stall_score"),
                "early_stage_score": trend_stage_context.get("early_stage_score"),
                "mid_stage_score": trend_stage_context.get("mid_stage_score"),
                "late_stage_score": trend_stage_context.get("late_stage_score"),
                "trend_stage_confidence": trend_stage_context.get("trend_stage_confidence"),
                "chip_pressure": chip_pressure,
                "chip_support": chip_support,
                "chip_vacuum": chip_vacuum,
                "winner_ratio": chip_context.get("winner_ratio"),
                "overhang_ratio": chip_context.get("overhang_ratio"),
                "support_density": chip_context.get("support_density"),
                "pressure_density": chip_context.get("pressure_density"),
                "vacuum_up_ratio": chip_context.get("vacuum_up_ratio"),
                "chip_concentration": chip_context.get("chip_concentration"),
            }
        )
        return feature_context

    @staticmethod
    def _map_trend_bias_to_weekly_trend(trend_bias: str) -> str:
        mapping = {
            "bullish": "up",
            "bearish": "down",
            "neutral": "range",
        }
        return mapping.get(trend_bias, "range")

    @staticmethod
    def _map_structure_stage_to_daily_state(structure_stage: str, return_1d: float | None) -> str:
        mapping = {
            "breakout_confirmed": "trend",
            "breakout_attempt": "trend",
            "trend_pressing_high": "trend",
            "trend_advancing": "trend",
            "trend_pullback": "rebound",
            "rebound_repair": "rebound",
            "range_rotation": "consolidation",
            "transition": "transition",
            "distribution": "exhaustion",
            "breakdown": "exhaustion",
        }
        if structure_stage in mapping:
            return mapping[structure_stage]
        if return_1d is not None and return_1d > 0:
            return "rebound"
        return "consolidation"

    @staticmethod
    def _map_structure_stage_to_type(structure_stage: str) -> str:
        mapping = {
            "breakout_confirmed": "trend_continue",
            "breakout_attempt": "breakout_pullback",
            "trend_pressing_high": "trend_continue",
            "trend_advancing": "trend_continue",
            "trend_pullback": "breakout_pullback",
            "rebound_repair": "weak_rebound",
            "range_rotation": "range_pivot",
            "transition": "range_pivot",
            "distribution": "range_pivot",
            "breakdown": "range_pivot",
        }
        return mapping.get(structure_stage, "range_pivot")

    @staticmethod
    def _derive_breakout_quality_from_assessment(
        confirmation_state: str,
        close_to_recent_high: float | None,
        volume_state: str,
    ) -> str:
        if confirmation_state == "confirmed":
            return "valid"
        if close_to_recent_high is None:
            return "unknown"
        if confirmation_state == "pending" or close_to_recent_high > -0.03:
            return "suspicious"
        if volume_state == "contract":
            return "invalid"
        return "invalid"

    @staticmethod
    def _derive_pullback_quality_from_assessment(structure_stage: str, close_to_ma20: float | None) -> str:
        if structure_stage in {"trend_pullback", "rebound_repair"} and close_to_ma20 is not None:
            if close_to_ma20 > -0.015:
                return "healthy"
            if close_to_ma20 > -0.04:
                return "neutral"
            return "damaged"
        if close_to_ma20 is None:
            return "unknown"
        if close_to_ma20 > -0.01:
            return "healthy"
        if close_to_ma20 > -0.03:
            return "neutral"
        return "damaged"
