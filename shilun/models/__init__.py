"""Model layer for labels, fallback scores, trained estimators, and loading.

M5 simplification note:
The original model layer was split across many small files (`schema`, `labeling`,
`registry`, `predictor`, and one file per estimator). That made imports noisy
without giving the system clearer boundaries. This module keeps the same public
API, removes duplicate feature-encoding code, and aliases the old submodule
paths below so existing research scripts and tests keep working.
"""

from __future__ import annotations

import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Protocol, Sequence

import joblib
import pandas as pd
from sklearn.feature_extraction import DictVectorizer
from sklearn.linear_model import LogisticRegression


# ---------------------------------------------------------------------------
# Prediction schema
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RegimePrediction:
    regime_label: str
    regime_score: float
    regime_confidence: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class EventPrediction:
    p_continue_10d: float
    p_breakout_success: float
    p_fail_5d: float
    p_acceptance_1d: float | None = None
    p_fail_fast_3d: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class EntryCurvePrediction:
    entry_probability: float | None = None
    entry_zone: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class RiskPrediction:
    expected_return_10d: float
    expected_drawdown_10d: float
    risk_level: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ModelPredictionBundle:
    regime: RegimePrediction
    event: EventPrediction
    risk: RiskPrediction
    entry: EntryCurvePrediction | None = None
    model_version: str = "rule_fallback_v1"

    def to_dict(self) -> dict[str, Any]:
        payload = {
            **self.regime.to_dict(),
            **self.event.to_dict(),
            **self.risk.to_dict(),
            "model_version": self.model_version,
        }
        if self.entry is not None:
            payload.update(self.entry.to_dict())
        return payload


# ---------------------------------------------------------------------------
# Dataset and labels
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DatasetSplit:
    train: pd.DataFrame
    validation: pd.DataFrame
    test: pd.DataFrame


class DatasetBuilder:
    """Build supervised modeling datasets from feature and label tables."""

    def build(self, features_df: pd.DataFrame, label_df: pd.DataFrame, key_columns: Sequence[str]) -> pd.DataFrame:
        dataset = features_df.merge(label_df, on=list(key_columns), how="inner")
        return dataset.sort_values(list(key_columns)).reset_index(drop=True)

    def time_split(
        self,
        dataset: pd.DataFrame,
        date_column: str = "date",
        train_ratio: float = 0.7,
        validation_ratio: float = 0.15,
    ) -> DatasetSplit:
        ordered = dataset.sort_values(date_column).reset_index(drop=True)
        train_end = int(len(ordered) * train_ratio)
        validation_end = train_end + int(len(ordered) * validation_ratio)
        return DatasetSplit(
            train=ordered.iloc[:train_end].reset_index(drop=True),
            validation=ordered.iloc[train_end:validation_end].reset_index(drop=True),
            test=ordered.iloc[validation_end:].reset_index(drop=True),
        )


def label_continue_10d(
    df: pd.DataFrame,
    return_threshold: float = 0.0,
    max_drawdown_threshold: float = -0.06,
) -> pd.Series:
    close = df["close"]
    future_return = close.shift(-10) / close - 1.0
    future_min = _forward_window_stat(close, window=10, fn="min")
    future_drawdown = future_min / close - 1.0
    return ((future_return > return_threshold) & (future_drawdown >= max_drawdown_threshold)).astype("Int64")


def label_breakout_success(
    df: pd.DataFrame,
    breakout_flag: pd.Series | None = None,
    breakout_threshold: float = 0.03,
    failure_threshold: float = -0.03,
) -> pd.Series:
    close = df["close"]
    if breakout_flag is None:
        breakout_flag = ((close / close.rolling(20).max()) - 1.0 > -0.01).fillna(False)
    future_max = _forward_window_stat(close, window=10, fn="max")
    future_min = _forward_window_stat(close, window=10, fn="min")
    future_runup = future_max / close - 1.0
    future_drawdown = future_min / close - 1.0
    success = breakout_flag & (future_runup >= breakout_threshold) & (future_drawdown > failure_threshold)
    return success.astype("Int64")


def label_fail_5d(df: pd.DataFrame, failure_threshold: float = -0.04) -> pd.Series:
    close = df["close"]
    future_min = _forward_window_stat(close, window=5, fn="min")
    future_drawdown = future_min / close - 1.0
    return (future_drawdown <= failure_threshold).astype("Int64")


def label_fail_fast_3d(df: pd.DataFrame, failure_threshold: float = -0.03) -> pd.Series:
    close = df["close"]
    future_min = _forward_window_stat(close, window=3, fn="min")
    future_drawdown = future_min / close - 1.0
    return (future_drawdown <= failure_threshold).astype("Int64")


def label_acceptance_1d(
    df: pd.DataFrame,
    return_threshold: float = 0.0,
    allow_drawdown: float = -0.02,
) -> pd.Series:
    close = df["close"]
    next_return = df["close"].shift(-1) / close - 1.0
    next_drawdown = df["low"].shift(-1) / close - 1.0
    return ((next_return >= return_threshold) & (next_drawdown >= allow_drawdown)).astype("Int64")


def label_entry_success_3d(
    df: pd.DataFrame,
    return_threshold: float = 0.02,
    max_drawdown_threshold: float = -0.03,
) -> pd.Series:
    close = df["close"]
    future_close = close.shift(-3)
    future_min = _forward_window_stat(close, window=3, fn="min")
    future_return = future_close / close - 1.0
    future_drawdown = future_min / close - 1.0
    return ((future_return >= return_threshold) & (future_drawdown >= max_drawdown_threshold)).astype("Int64")


def label_exhaustion_5d(
    df: pd.DataFrame,
    weak_return_threshold: float = -0.02,
    recovery_cap: float = 0.02,
) -> pd.Series:
    close = df["close"]
    future_close = close.shift(-5)
    future_max = _forward_window_stat(close, window=5, fn="max")
    future_return = future_close / close - 1.0
    future_runup = future_max / close - 1.0
    return ((future_return <= weak_return_threshold) & (future_runup <= recovery_cap)).astype("Int64")


def label_return_profile(df: pd.DataFrame) -> pd.Series:
    future_return = df["close"].shift(-10) / df["close"] - 1.0
    bins = [-float("inf"), -0.05, -0.01, 0.02, 0.06, float("inf")]
    labels = ["large_loss", "small_loss", "flat", "small_gain", "large_gain"]
    return pd.cut(future_return, bins=bins, labels=labels)


def label_drawdown_bucket(df: pd.DataFrame) -> pd.Series:
    future_min = _forward_window_stat(df["close"], window=10, fn="min")
    future_drawdown = future_min / df["close"] - 1.0
    bins = [-float("inf"), -0.12, -0.08, -0.04, -0.02, float("inf")]
    labels = ["crash", "deep", "medium", "mild", "contained"]
    return pd.cut(future_drawdown, bins=bins, labels=labels)


def _forward_window_stat(series: pd.Series, window: int, fn: str) -> pd.Series:
    shifted = series.shift(-1)
    if fn == "min":
        return shifted.iloc[::-1].rolling(window, min_periods=1).min().iloc[::-1]
    if fn == "max":
        return shifted.iloc[::-1].rolling(window, min_periods=1).max().iloc[::-1]
    raise ValueError(f"unsupported forward stat: {fn}")


# ---------------------------------------------------------------------------
# Deterministic fallback
# ---------------------------------------------------------------------------


class RuleFallbackModel:
    """Deterministic scoring layer that mimics model outputs before trained models exist."""

    def predict(self, market_features: dict[str, Any], structure_features: dict[str, Any]) -> ModelPredictionBundle:
        regime = self._predict_regime(market_features, structure_features)
        event = self._predict_event(market_features, structure_features, regime)
        risk = self._predict_risk(market_features, structure_features, event)
        entry = self._predict_entry(market_features, structure_features, event, regime)
        return ModelPredictionBundle(regime=regime, event=event, risk=risk, entry=entry)

    def _predict_regime(
        self,
        market_features: dict[str, Any],
        structure_features: dict[str, Any],
    ) -> RegimePrediction:
        score = 50.0
        score += 12.0 * self._coalesce(market_features.get("price_vs_ma20_z"), 0.0, clip=(-2.0, 2.0))
        score += 10.0 * self._coalesce(market_features.get("trend_r2_20"), 0.0, clip=(0.0, 1.0))
        score += 10.0 * self._coalesce(market_features.get("efficiency_ratio_20"), 0.0, clip=(0.0, 1.0))
        score += 40.0 * self._coalesce(market_features.get("excess_return_20d"), 0.0, clip=(-0.2, 0.2))
        score += 25.0 * self._coalesce(market_features.get("excess_return_5d"), 0.0, clip=(-0.1, 0.1))
        score += 8.0 * self._coalesce(structure_features.get("leave_center_strength"), 0.0, clip=(-2.0, 2.0))
        if structure_features.get("segment_direction") == "up":
            score += 8.0
        elif structure_features.get("segment_direction") == "down":
            score -= 8.0
        if market_features.get("benchmark_weekly_trend") == "down":
            score -= 4.0
        elif market_features.get("benchmark_weekly_trend") == "up":
            score += 2.0
        if structure_features.get("divergence_state") == "confirmed":
            score -= 15.0
        elif structure_features.get("divergence_state") == "mild":
            score -= 7.0

        score = max(0.0, min(100.0, score))
        if score >= 75:
            label = "strong_up"
        elif score >= 58:
            label = "weak_up"
        elif score <= 25:
            label = "risk_reversal"
        elif score <= 42:
            label = "weak_down"
        else:
            label = "range"
        confidence = min(0.95, max(0.35, abs(score - 50.0) / 50.0 + 0.35))
        return RegimePrediction(regime_label=label, regime_score=score, regime_confidence=confidence)

    def _predict_event(
        self,
        market_features: dict[str, Any],
        structure_features: dict[str, Any],
        regime: RegimePrediction,
    ) -> EventPrediction:
        p_continue = 0.5
        p_continue += 0.12 * self._coalesce(market_features.get("trend_r2_20"), 0.0, clip=(0.0, 1.0))
        p_continue += 0.12 * self._coalesce(market_features.get("efficiency_ratio_20"), 0.0, clip=(0.0, 1.0))
        p_continue += 0.25 * self._coalesce(market_features.get("excess_return_20d"), 0.0, clip=(-0.2, 0.2))
        p_continue += 0.20 * self._coalesce(market_features.get("excess_return_5d"), 0.0, clip=(-0.1, 0.1))
        p_continue += 0.10 * self._coalesce(structure_features.get("leave_center_strength"), 0.0, clip=(-1.0, 1.5))
        p_continue -= 0.18 * self._coalesce(structure_features.get("divergence_score"), 0.0, clip=(0.0, 1.0))
        if regime.regime_label in {"strong_up", "weak_up"}:
            p_continue += 0.08
        if structure_features.get("segment_direction") == "down":
            p_continue -= 0.10
        if market_features.get("benchmark_weekly_trend") == "down":
            p_continue -= 0.03

        p_breakout = 0.45
        p_breakout += 0.20 * self._coalesce(market_features.get("breakout_volume_percentile"), 0.0, clip=(0.0, 1.0))
        p_breakout += 0.15 * self._coalesce(market_features.get("excess_return_5d"), 0.0, clip=(-0.1, 0.1))
        p_breakout += 0.10 * self._coalesce(structure_features.get("leave_center_strength"), 0.0, clip=(0.0, 1.5))
        p_breakout -= 0.10 * self._coalesce(structure_features.get("return_test_depth"), 0.0, clip=(0.0, 1.5))
        if structure_features.get("latest_event_type") == "breakout_up":
            p_breakout += 0.08

        p_fail = 0.30
        p_fail += 0.18 * self._coalesce(structure_features.get("divergence_score"), 0.0, clip=(0.0, 1.0))
        p_fail += 0.12 * self._coalesce(market_features.get("atr_pct"), 0.0, clip=(0.0, 0.15)) / 0.15
        p_fail -= 0.10 * self._coalesce(market_features.get("pullback_volume_shrink_ratio"), 0.0, clip=(0.0, 1.5))
        p_fail -= 0.15 * self._coalesce(market_features.get("excess_return_20d"), 0.0, clip=(-0.2, 0.2))
        if regime.regime_label == "risk_reversal":
            p_fail += 0.18
        if market_features.get("benchmark_weekly_trend") == "down":
            p_fail += 0.03

        p_acceptance = 0.48
        p_acceptance += 0.18 * self._coalesce(market_features.get("acceptance_strength"), 0.5, clip=(0.0, 1.0))
        p_acceptance += 0.12 * self._coalesce(market_features.get("trigger_strength"), 50.0, clip=(0.0, 100.0)) / 100.0
        p_acceptance += 0.08 * self._coalesce(market_features.get("breakout_confirm_flag"), 0.0, clip=(0.0, 1.0))
        p_acceptance -= 0.16 * self._coalesce(market_features.get("false_breakout_risk_flag"), 0.0, clip=(0.0, 1.0))
        p_acceptance -= 0.08 * self._coalesce(structure_features.get("divergence_score"), 0.0, clip=(0.0, 1.0))

        p_fail_fast = 0.20
        p_fail_fast += 0.18 * self._coalesce(market_features.get("false_breakout_risk_flag"), 0.0, clip=(0.0, 1.0))
        p_fail_fast += 0.14 * self._coalesce(market_features.get("upper_shadow_ratio"), 0.0, clip=(0.0, 1.0))
        p_fail_fast += 0.10 * self._coalesce(market_features.get("atr_pct"), 0.0, clip=(0.0, 0.15)) / 0.15
        p_fail_fast -= 0.10 * self._coalesce(market_features.get("acceptance_strength"), 0.5, clip=(0.0, 1.0))
        p_fail_fast -= 0.06 * self._coalesce(market_features.get("breakout_confirm_flag"), 0.0, clip=(0.0, 1.0))

        return EventPrediction(
            p_continue_10d=self._clip_prob(p_continue),
            p_breakout_success=self._clip_prob(p_breakout),
            p_fail_5d=self._clip_prob(p_fail),
            p_acceptance_1d=self._clip_prob(p_acceptance),
            p_fail_fast_3d=self._clip_prob(p_fail_fast),
        )

    def _predict_risk(
        self,
        market_features: dict[str, Any],
        structure_features: dict[str, Any],
        event: EventPrediction,
    ) -> RiskPrediction:
        expected_return = 0.02
        expected_return += 0.08 * (event.p_continue_10d - 0.5)
        expected_return += 0.05 * (event.p_breakout_success - 0.5)
        if event.p_acceptance_1d is not None:
            expected_return += 0.04 * (event.p_acceptance_1d - 0.5)

        expected_drawdown = 0.03
        expected_drawdown += 0.08 * event.p_fail_5d
        if event.p_fail_fast_3d is not None:
            expected_drawdown += 0.06 * event.p_fail_fast_3d
        expected_drawdown += 0.04 * self._coalesce(market_features.get("atr_pct"), 0.0, clip=(0.0, 0.15)) / 0.15
        expected_drawdown += 0.04 * self._coalesce(structure_features.get("return_test_depth"), 0.0, clip=(0.0, 2.0)) / 2.0

        risk_level = min(1.0, max(0.0, expected_drawdown / 0.15))
        return RiskPrediction(
            expected_return_10d=round(expected_return, 4),
            expected_drawdown_10d=round(expected_drawdown, 4),
            risk_level=round(risk_level, 4),
        )

    def _predict_entry(
        self,
        market_features: dict[str, Any],
        structure_features: dict[str, Any],
        event: EventPrediction,
        regime: RegimePrediction,
    ) -> EntryCurvePrediction:
        position_state = str(market_features.get("position_state") or "")
        volume_pattern = str(market_features.get("volume_pattern") or "")
        confirmation_score = float(market_features.get("structure_confirmation_score") or 0.0)
        breakout_quality = str(market_features.get("breakout_quality") or "")
        structure_stage = str(market_features.get("structure_stage") or "")

        probability = 0.45
        if regime.regime_label in {"strong_up", "weak_up"}:
            probability += 0.08
        if event.p_acceptance_1d is not None and event.p_acceptance_1d >= 0.6:
            probability += 0.1
        if event.p_fail_fast_3d is not None and event.p_fail_fast_3d >= 0.45:
            probability -= 0.16
        if breakout_quality == "valid":
            probability += 0.06
        elif breakout_quality == "suspicious":
            probability -= 0.06
        if volume_pattern in {"gentle_expand", "pullback_shrink"}:
            probability += 0.05
        if volume_pattern in {"distribution", "high_level_stall", "impulsive_spike"}:
            probability -= 0.1
        if position_state in {"downtrend", "high_zone"}:
            probability -= 0.05
        if structure_stage in {"trend_pullback", "rebound_repair", "breakout_confirmed"}:
            probability += 0.04
        probability += min(0.08, confirmation_score / 1000.0)
        probability = max(0.02, min(0.98, probability))
        if probability >= 0.7:
            zone = "ready"
        elif probability >= 0.55:
            zone = "candidate"
        elif probability >= 0.4:
            zone = "watch"
        else:
            zone = "avoid"
        return EntryCurvePrediction(entry_probability=round(probability, 4), entry_zone=zone)

    @staticmethod
    def _clip_prob(value: float) -> float:
        return round(max(0.01, min(0.99, value)), 4)

    @staticmethod
    def _coalesce(value: Any, default: float, clip: tuple[float, float] | None = None) -> float:
        if value is None:
            base = default
        else:
            try:
                base = float(value)
            except (TypeError, ValueError):
                base = default
        if clip is None:
            return base
        low, high = clip
        return max(low, min(high, base))


# ---------------------------------------------------------------------------
# Trained estimators
# ---------------------------------------------------------------------------


class LogisticRegressionEntryModel:
    """Fit an entry probability curve from semantic states and model outputs."""

    def __init__(
        self,
        feature_names: Sequence[str] | None = None,
        *,
        max_iter: int = 3000,
        C: float = 0.7,
    ) -> None:
        self.feature_names = list(feature_names or [])
        self.max_iter = max_iter
        self.C = C
        self.vectorizer = DictVectorizer(sparse=True)
        self.model = LogisticRegression(
            max_iter=max_iter,
            C=C,
            class_weight="balanced",
            solver="liblinear",
        )
        self.constant_probability: float | None = None

    def fit(self, X: pd.DataFrame, y: pd.Series) -> "LogisticRegressionEntryModel":
        self.feature_names = self.feature_names or list(X.columns)
        records = [self._record_from_mapping(row) for row in X[self.feature_names].to_dict(orient="records")]
        train_y = pd.to_numeric(y, errors="coerce").fillna(0).astype(int)
        unique = sorted(train_y.unique().tolist())
        if len(unique) < 2:
            self.constant_probability = float(train_y.mean()) if len(train_y) else 0.0
            return self

        matrix = self.vectorizer.fit_transform(records)
        self.model.fit(matrix, train_y)
        self.constant_probability = None
        return self

    def predict(self, features: dict[str, Any]) -> EntryCurvePrediction:
        record = self._record_from_mapping({name: features.get(name) for name in self.feature_names})
        if self.constant_probability is not None:
            probability = float(self.constant_probability)
        else:
            matrix = self.vectorizer.transform([record])
            probability = float(self.model.predict_proba(matrix)[0][1])
        return EntryCurvePrediction(
            entry_probability=round(probability, 4),
            entry_zone=self._zone_from_probability(probability),
        )

    def save(self, model_path: str | Path) -> str:
        path = Path(model_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "feature_names": self.feature_names,
            "vectorizer": self.vectorizer,
            "model": self.model,
            "constant_probability": self.constant_probability,
            "max_iter": self.max_iter,
            "C": self.C,
        }
        joblib.dump(payload, path)
        return str(path)

    @classmethod
    def load(cls, model_path: str | Path) -> "LogisticRegressionEntryModel":
        payload = joblib.load(model_path)
        instance = cls(
            feature_names=payload.get("feature_names"),
            max_iter=payload.get("max_iter", 3000),
            C=payload.get("C", 0.7),
        )
        instance.vectorizer = payload["vectorizer"]
        instance.model = payload["model"]
        instance.constant_probability = payload.get("constant_probability")
        return instance

    @staticmethod
    def _zone_from_probability(probability: float) -> str:
        if probability >= 0.7:
            return "ready"
        if probability >= 0.55:
            return "candidate"
        if probability >= 0.4:
            return "watch"
        return "avoid"

    @staticmethod
    def _record_from_mapping(mapping: dict[str, Any]) -> dict[str, Any]:
        record: dict[str, Any] = {}
        for key, value in mapping.items():
            if value is None:
                continue
            try:
                if pd.isna(value):
                    continue
            except TypeError:
                pass
            if isinstance(value, str):
                stripped = value.strip()
                if stripped:
                    record[key] = stripped
                continue
            if isinstance(value, bool):
                record[key] = int(value)
                continue
            try:
                record[key] = float(value)
            except (TypeError, ValueError):
                record[key] = str(value)
        return record


class _TabularEncodingMixin:
    """Shared categorical/numeric encoding for small tabular estimators."""

    feature_names: list[str]
    category_maps: dict[str, dict[str, int]]

    def _row_from_features(self, features: dict[str, Any]) -> pd.DataFrame:
        row = {name: self._encode_value(name, features.get(name)) for name in self.feature_names}
        return pd.DataFrame([row])

    def _prepare_training_frame(self, frame: pd.DataFrame) -> pd.DataFrame:
        prepared = frame.copy()
        for column in prepared.columns:
            if prepared[column].dtype == "object":
                categories = [str(value) for value in pd.Series(prepared[column]).dropna().unique()]
                self.category_maps[column] = {value: idx for idx, value in enumerate(sorted(categories), start=1)}
                prepared[column] = prepared[column].map(lambda value: self._encode_value(column, value))
            else:
                prepared[column] = pd.to_numeric(prepared[column], errors="coerce").fillna(0.0)
        return prepared

    def _encode_value(self, feature_name: str, value: Any) -> float:
        if feature_name in self.category_maps:
            if value is None:
                return 0.0
            return float(self.category_maps[feature_name].get(str(value), 0))
        return self._numeric(value)

    @staticmethod
    def _numeric(value: Any) -> float:
        if value is None:
            return 0.0
        try:
            return float(value)
        except (TypeError, ValueError):
            return 0.0


@dataclass(frozen=True)
class RegimeModelArtifact:
    feature_names: list[str]
    label_order: list[str]
    model_path: str


class LightGBMRegimeModel(_TabularEncodingMixin):
    """Multiclass regime classifier backed by LightGBM."""

    def __init__(
        self,
        feature_names: Sequence[str] | None = None,
        params: dict[str, Any] | None = None,
    ) -> None:
        self.feature_names = list(feature_names or [])
        default_params = {
            "objective": "multiclass",
            "n_estimators": 150,
            "learning_rate": 0.05,
            "num_leaves": 31,
            "subsample": 0.9,
            "colsample_bytree": 0.9,
            "random_state": 42,
            "n_jobs": 1,
            "verbosity": -1,
        }
        if params:
            default_params.update(params)
        self.model = self._build_model(default_params)
        self.label_order: list[str] = []
        self.category_maps: dict[str, dict[str, int]] = {}

    def fit(self, X: pd.DataFrame, y: pd.Series) -> "LightGBMRegimeModel":
        self.feature_names = self.feature_names or list(X.columns)
        train_X = self._prepare_training_frame(X[self.feature_names])
        y_encoded = pd.Categorical(y)
        self.label_order = list(y_encoded.categories.astype(str))
        self.model.fit(train_X, y_encoded.codes)
        return self

    def predict(self, features: dict[str, Any]) -> RegimePrediction:
        row = self._row_from_features(features)
        proba = self.model.predict_proba(row)[0]
        best_idx = int(proba.argmax())
        label = self.label_order[best_idx]
        confidence = float(proba[best_idx])
        score = confidence * 100.0
        return RegimePrediction(regime_label=label, regime_score=round(score, 4), regime_confidence=round(confidence, 4))

    def save(self, model_path: str | Path) -> RegimeModelArtifact:
        path = Path(model_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "model": self.model,
            "feature_names": self.feature_names,
            "label_order": self.label_order,
            "category_maps": self.category_maps,
        }
        joblib.dump(payload, path)
        return RegimeModelArtifact(
            feature_names=self.feature_names,
            label_order=self.label_order,
            model_path=str(path),
        )

    @classmethod
    def load(cls, model_path: str | Path) -> "LightGBMRegimeModel":
        payload = joblib.load(model_path)
        instance = cls(feature_names=payload["feature_names"])
        instance.model = payload["model"]
        instance.label_order = payload["label_order"]
        instance.category_maps = payload.get("category_maps", {})
        return instance

    @staticmethod
    def _build_model(params: dict[str, Any]):
        from lightgbm import LGBMClassifier

        return LGBMClassifier(**params)


class LightGBMEventModel(_TabularEncodingMixin):
    """Binary LightGBM models for continuation, breakout success, and failure risk."""

    TARGETS = ("continue_10d", "breakout_success", "fail_5d", "acceptance_1d", "fail_fast_3d")

    def __init__(
        self,
        feature_names: Sequence[str] | None = None,
        params: dict[str, Any] | None = None,
    ) -> None:
        self.feature_names = list(feature_names or [])
        default_params = {
            "objective": "binary",
            "n_estimators": 180,
            "learning_rate": 0.05,
            "num_leaves": 31,
            "subsample": 0.9,
            "colsample_bytree": 0.9,
            "random_state": 42,
            "n_jobs": 1,
            "verbosity": -1,
        }
        if params:
            default_params.update(params)
        self.params = default_params
        self.models = {target: self._build_model(self.params) for target in self.TARGETS}
        self.category_maps: dict[str, dict[str, int]] = {}
        self.active_targets = list(self.TARGETS)

    def fit(self, X: pd.DataFrame, labels: pd.DataFrame) -> "LightGBMEventModel":
        self.feature_names = self.feature_names or list(X.columns)
        train_X = self._prepare_training_frame(X[self.feature_names])
        self.active_targets = []
        for target, model in self.models.items():
            if target not in labels.columns:
                continue
            target_y = labels[target]
            mask = target_y.notna()
            if int(mask.sum()) == 0:
                continue
            model.fit(train_X.loc[mask], target_y.loc[mask])
            self.active_targets.append(target)
        return self

    def predict(self, features: dict[str, Any]) -> EventPrediction:
        row = self._row_from_features(features)
        probs: dict[str, float | None] = {}
        for target, model in self.models.items():
            if target not in self.active_targets:
                probs[target] = None
                continue
            probs[target] = float(model.predict_proba(row)[0][1])
        return EventPrediction(
            p_continue_10d=round(probs["continue_10d"] or 0.0, 4),
            p_breakout_success=round(probs["breakout_success"] or 0.0, 4),
            p_fail_5d=round(probs["fail_5d"] or 0.0, 4),
            p_acceptance_1d=round(probs["acceptance_1d"], 4) if probs["acceptance_1d"] is not None else None,
            p_fail_fast_3d=round(probs["fail_fast_3d"], 4) if probs["fail_fast_3d"] is not None else None,
        )

    def save(self, model_path: str | Path) -> str:
        path = Path(model_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "feature_names": self.feature_names,
            "models": self.models,
            "category_maps": self.category_maps,
            "active_targets": self.active_targets,
        }
        joblib.dump(payload, path)
        return str(path)

    @classmethod
    def load(cls, model_path: str | Path) -> "LightGBMEventModel":
        payload = joblib.load(model_path)
        instance = cls(feature_names=payload["feature_names"])
        instance.models = payload["models"]
        instance.category_maps = payload.get("category_maps", {})
        instance.active_targets = payload.get("active_targets", list(instance.models.keys()))
        return instance

    @staticmethod
    def _build_model(params: dict[str, Any]):
        from lightgbm import LGBMClassifier

        return LGBMClassifier(**params)


class LightGBMRiskModel(_TabularEncodingMixin):
    """Regression models for expected return and expected drawdown."""

    TARGETS = ("expected_return_10d", "expected_drawdown_10d")

    def __init__(
        self,
        feature_names: Sequence[str] | None = None,
        params: dict[str, Any] | None = None,
    ) -> None:
        self.feature_names = list(feature_names or [])
        default_params = {
            "objective": "regression",
            "n_estimators": 180,
            "learning_rate": 0.05,
            "num_leaves": 31,
            "subsample": 0.9,
            "colsample_bytree": 0.9,
            "random_state": 42,
            "n_jobs": 1,
            "verbosity": -1,
        }
        if params:
            default_params.update(params)
        self.params = default_params
        self.models = {target: self._build_model(self.params) for target in self.TARGETS}
        self.category_maps: dict[str, dict[str, int]] = {}

    def fit(self, X: pd.DataFrame, targets: pd.DataFrame) -> "LightGBMRiskModel":
        self.feature_names = self.feature_names or list(X.columns)
        train_X = self._prepare_training_frame(X[self.feature_names])
        for target, model in self.models.items():
            model.fit(train_X, targets[target])
        return self

    def predict(self, features: dict[str, Any]) -> RiskPrediction:
        row = self._row_from_features(features)
        expected_return = float(self.models["expected_return_10d"].predict(row)[0])
        expected_drawdown = abs(float(self.models["expected_drawdown_10d"].predict(row)[0]))
        risk_level = min(1.0, max(0.0, expected_drawdown / 0.15))
        return RiskPrediction(
            expected_return_10d=round(expected_return, 4),
            expected_drawdown_10d=round(expected_drawdown, 4),
            risk_level=round(risk_level, 4),
        )

    def save(self, model_path: str | Path) -> str:
        path = Path(model_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "feature_names": self.feature_names,
            "models": self.models,
            "category_maps": self.category_maps,
        }
        joblib.dump(payload, path)
        return str(path)

    @classmethod
    def load(cls, model_path: str | Path) -> "LightGBMRiskModel":
        payload = joblib.load(model_path)
        instance = cls(feature_names=payload["feature_names"])
        instance.models = payload["models"]
        instance.category_maps = payload.get("category_maps", {})
        return instance

    @staticmethod
    def _build_model(params: dict[str, Any]):
        from lightgbm import LGBMRegressor

        return LGBMRegressor(**params)


# ---------------------------------------------------------------------------
# Registry and prediction wrappers
# ---------------------------------------------------------------------------


class ModelRegistry:
    """Filesystem-based registry for trained LightGBM artifacts."""

    def __init__(self, base_dir: str | Path) -> None:
        self.base_dir = Path(base_dir)

    def regime_path(self) -> Path:
        return self.base_dir / "regime_model.joblib"

    def event_path(self) -> Path:
        return self.base_dir / "event_model.joblib"

    def risk_path(self) -> Path:
        return self.base_dir / "risk_model.joblib"

    def entry_path(self) -> Path:
        return self.base_dir / "entry_model.joblib"

    def has_all_models(self) -> bool:
        return self.regime_path().exists() and self.event_path().exists() and self.risk_path().exists()

    def load_regime_model(self) -> LightGBMRegimeModel:
        return LightGBMRegimeModel.load(self.regime_path())

    def load_event_model(self) -> LightGBMEventModel:
        return LightGBMEventModel.load(self.event_path())

    def load_risk_model(self) -> LightGBMRiskModel:
        return LightGBMRiskModel.load(self.risk_path())

    def has_entry_model(self) -> bool:
        return self.entry_path().exists()

    def load_entry_model(self) -> LogisticRegressionEntryModel:
        return LogisticRegressionEntryModel.load(self.entry_path())


class PredictionModel(Protocol):
    def predict(self, market_features: dict[str, Any], structure_features: dict[str, Any]) -> ModelPredictionBundle: ...


@dataclass
class TrainedModelPredictor:
    regime_model: LightGBMRegimeModel
    event_model: LightGBMEventModel
    risk_model: LightGBMRiskModel
    entry_model: LogisticRegressionEntryModel | None = None
    entry_fallback: RuleFallbackModel | None = None
    model_version: str = "lightgbm_bundle_v1"

    def predict(self, market_features: dict[str, Any], structure_features: dict[str, Any]) -> ModelPredictionBundle:
        features = {**market_features, **structure_features}
        regime = self.regime_model.predict(features)
        event = self.event_model.predict(features)
        risk = self.risk_model.predict(features)
        entry = None
        if self.entry_model is not None:
            entry_features = {
                **features,
                **regime.to_dict(),
                **event.to_dict(),
                **risk.to_dict(),
            }
            entry = self.entry_model.predict(entry_features)
        elif self.entry_fallback is not None:
            entry = self.entry_fallback.predict(market_features, structure_features).entry
        return ModelPredictionBundle(
            regime=regime,
            event=event,
            risk=risk,
            entry=entry,
            model_version=self.model_version,
        )


@dataclass
class SafePredictionModel:
    primary: PredictionModel
    fallback: RuleFallbackModel

    def predict(self, market_features: dict[str, Any], structure_features: dict[str, Any]) -> ModelPredictionBundle:
        try:
            return self.primary.predict(market_features, structure_features)
        except Exception:
            return self.fallback.predict(market_features, structure_features)


__all__ = [
    "DatasetBuilder",
    "DatasetSplit",
    "EntryCurvePrediction",
    "EventPrediction",
    "LightGBMEventModel",
    "LightGBMRegimeModel",
    "LightGBMRiskModel",
    "LogisticRegressionEntryModel",
    "ModelPredictionBundle",
    "ModelRegistry",
    "PredictionModel",
    "RegimeModelArtifact",
    "RegimePrediction",
    "RiskPrediction",
    "RuleFallbackModel",
    "SafePredictionModel",
    "TrainedModelPredictor",
    "label_acceptance_1d",
    "label_breakout_success",
    "label_continue_10d",
    "label_drawdown_bucket",
    "label_entry_success_3d",
    "label_exhaustion_5d",
    "label_fail_fast_3d",
    "label_fail_5d",
    "label_return_profile",
]


_COMPAT_SUBMODULES = (
    "dataset",
    "entry_model",
    "event_model",
    "labeling",
    "predictor",
    "regime_model",
    "registry",
    "risk_model",
    "rule_fallback",
    "schema",
)

for _module_name in _COMPAT_SUBMODULES:
    sys.modules[f"{__name__}.{_module_name}"] = sys.modules[__name__]
