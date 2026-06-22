from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import pandas as pd
from sklearn.metrics import accuracy_score, brier_score_loss, mean_absolute_error, mean_squared_error, roc_auc_score

from shilun.features import StructureFeatureBuilder, compute_entry_features
from shilun.indicators import compute_trend_features, compute_volatility_features, compute_volume_features
from shilun.models import (
    DatasetBuilder,
    LightGBMEventModel,
    LightGBMRegimeModel,
    LightGBMRiskModel,
    LogisticRegressionEntryModel,
    ModelRegistry,
    label_acceptance_1d,
    label_breakout_success,
    label_continue_10d,
    label_drawdown_bucket,
    label_entry_success_3d,
    label_exhaustion_5d,
    label_fail_5d,
    label_fail_fast_3d,
    label_return_profile,
)
from shilun.structure import BiBuilder, CenterDetector, DivergenceDetector, EventEngine, PivotDetector, SegmentBuilder


# P4: 模型研究入口收敛到本文件，避免 build/train/evaluate 六个薄脚本继续分散研究层边界。
EXCLUDED_COLUMNS = {
    "ticker",
    "date",
    "continue_10d",
    "breakout_success",
    "fail_5d",
    "fail_fast_3d",
    "acceptance_1d",
    "entry_success_3d",
    "exhaustion_5d",
    "return_profile",
    "drawdown_bucket",
    "expected_return_10d",
    "expected_drawdown_10d",
}

EVENT_LABEL_COLUMNS = ["continue_10d", "breakout_success", "fail_5d", "acceptance_1d", "fail_fast_3d"]
RISK_TARGET_COLUMNS = ["expected_return_10d", "expected_drawdown_10d"]


def load_dataset(dataset_path: str | Path) -> pd.DataFrame:
    path = Path(dataset_path)
    if path.suffix == ".parquet":
        return pd.read_parquet(path)
    return pd.read_csv(path)


def save_dataset(dataset: pd.DataFrame, output_path: str | Path) -> Path:
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.suffix == ".parquet":
        dataset.to_parquet(path, index=False)
    else:
        dataset.to_csv(path, index=False)
    return path


def feature_names_from_dataset(dataset: pd.DataFrame) -> list[str]:
    return [column for column in dataset.columns if column not in EXCLUDED_COLUMNS]


def build_feature_table(df: pd.DataFrame, sample_step: int = 1) -> pd.DataFrame:
    featured = compute_trend_features(df)
    featured = compute_volatility_features(featured)
    featured = compute_volume_features(featured)
    featured = compute_entry_features(featured)

    rows: list[dict[str, Any]] = []
    for ticker, frame in featured.groupby("ticker"):
        frame = frame.reset_index(drop=True)
        sample_indices = list(range(80, len(frame), max(1, sample_step)))
        if len(frame) - 1 not in sample_indices:
            sample_indices.append(len(frame) - 1)
        for end_idx in sample_indices:
            window = frame.iloc[: end_idx + 1].copy()
            pivots = PivotDetector().detect(window)
            bis = BiBuilder().build(pivots, bars_df=window)
            segments = SegmentBuilder().build(bis)
            centers = CenterDetector().detect(segments)
            divergence = DivergenceDetector().detect(segments)
            events = EventEngine().detect(window, centers)
            structure_features = StructureFeatureBuilder().build(bis, segments, centers, divergence, events)
            latest = window.iloc[-1]

            row = {"ticker": ticker, "date": latest["date"]}
            for column in [
                "return_3d",
                "return_10d",
                "return_20d",
                "price_center_shift_5",
                "price_center_shift_10",
                "ma20_slope",
                "ma60_slope",
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
                "acceleration_score",
                "acceptance_strength",
                "trigger_strength",
                "trend_truth_score",
                "buy_readiness_score",
                "breakout_confirm_flag",
                "false_breakout_risk_flag",
                "distribution_risk_flag",
                "stall_risk_flag",
                "position_state",
                "volume_pattern",
            ]:
                row[column] = latest.get(column)
            row.update(structure_features)
            rows.append(row)
    return pd.DataFrame(rows)


def build_label_table(df: pd.DataFrame) -> pd.DataFrame:
    label_frames: list[pd.DataFrame] = []
    for ticker, frame in df.groupby("ticker"):
        frame = frame.sort_values("date").reset_index(drop=True)
        labels = frame[["date"]].copy()
        labels["ticker"] = ticker
        labels["continue_10d"] = label_continue_10d(frame)
        labels["breakout_success"] = label_breakout_success(frame)
        labels["fail_5d"] = label_fail_5d(frame)
        labels["fail_fast_3d"] = label_fail_fast_3d(frame)
        labels["acceptance_1d"] = label_acceptance_1d(frame)
        labels["entry_success_3d"] = label_entry_success_3d(frame)
        labels["exhaustion_5d"] = label_exhaustion_5d(frame)
        labels["return_profile"] = label_return_profile(frame)
        labels["drawdown_bucket"] = label_drawdown_bucket(frame)
        labels["expected_return_10d"] = frame["close"].shift(-10) / frame["close"] - 1.0
        future_min = frame["close"].shift(-1).iloc[::-1].rolling(10, min_periods=1).min().iloc[::-1]
        labels["expected_drawdown_10d"] = (future_min / frame["close"] - 1.0).abs()
        label_frames.append(labels[["ticker", "date", *sorted(EXCLUDED_COLUMNS - {"ticker", "date"})]])
    return pd.concat(label_frames, ignore_index=True)


def build_modeling_dataset(df: pd.DataFrame, sample_step: int = 1) -> pd.DataFrame:
    frame = df.copy()
    frame["date"] = pd.to_datetime(frame["date"])
    frame = frame.sort_values(["ticker", "date"]).reset_index(drop=True)
    feature_table = build_feature_table(frame, sample_step=max(1, sample_step))
    label_table = build_label_table(frame)
    return DatasetBuilder().build(feature_table, label_table, key_columns=["ticker", "date"])


def derive_regime_label(dataset: pd.DataFrame) -> pd.Series:
    labels: list[str] = []
    for _, row in dataset.iterrows():
        if row["continue_10d"] == 1 and row["fail_5d"] == 0:
            labels.append("strong_up" if row.get("return_profile") == "large_gain" else "weak_up")
        elif row["fail_5d"] == 1:
            labels.append("risk_reversal")
        else:
            labels.append("range")
    return pd.Series(labels, index=dataset.index)


def _split_training_frame(dataset: pd.DataFrame, feature_names: list[str], target_columns: list[str]) -> Any:
    X = dataset[feature_names].fillna(0)
    targets = dataset[target_columns].fillna(0)
    return DatasetBuilder().time_split(pd.concat([dataset[["date"]], X, targets], axis=1))


def train_regime_model(dataset: pd.DataFrame, output_dir: str | Path) -> Path:
    feature_names = feature_names_from_dataset(dataset)
    X = dataset[feature_names].fillna(0)
    y = derive_regime_label(dataset)
    split = DatasetBuilder().time_split(pd.concat([dataset[["date"]], X, y.rename("target")], axis=1))
    model = LightGBMRegimeModel(feature_names=feature_names).fit(split.train[feature_names], split.train["target"])
    path = Path(output_dir) / "regime_model.joblib"
    path.parent.mkdir(parents=True, exist_ok=True)
    model.save(path)
    return path


def train_event_model(dataset: pd.DataFrame, output_dir: str | Path) -> Path:
    feature_names = feature_names_from_dataset(dataset)
    split = _split_training_frame(dataset, feature_names, EVENT_LABEL_COLUMNS)
    model = LightGBMEventModel(feature_names=feature_names).fit(
        split.train[feature_names],
        split.train[EVENT_LABEL_COLUMNS].astype(int),
    )
    path = Path(output_dir) / "event_model.joblib"
    path.parent.mkdir(parents=True, exist_ok=True)
    model.save(path)
    return path


def train_risk_model(dataset: pd.DataFrame, output_dir: str | Path) -> Path:
    feature_names = feature_names_from_dataset(dataset)
    split = _split_training_frame(dataset, feature_names, RISK_TARGET_COLUMNS)
    model = LightGBMRiskModel(feature_names=feature_names).fit(split.train[feature_names], split.train[RISK_TARGET_COLUMNS])
    path = Path(output_dir) / "risk_model.joblib"
    path.parent.mkdir(parents=True, exist_ok=True)
    model.save(path)
    return path


def train_entry_model(dataset: pd.DataFrame, output_dir: str | Path) -> Path:
    feature_names = feature_names_from_dataset(dataset)
    target = pd.to_numeric(dataset["entry_success_3d"], errors="coerce").fillna(0).astype(int)
    split = DatasetBuilder().time_split(pd.concat([dataset[["date"]], dataset[feature_names].fillna(0), target.rename("entry_success_3d")], axis=1))
    model = LogisticRegressionEntryModel(feature_names=feature_names).fit(
        split.train[feature_names],
        split.train["entry_success_3d"],
    )
    path = Path(output_dir) / "entry_model.joblib"
    path.parent.mkdir(parents=True, exist_ok=True)
    model.save(path)
    return path


def classification_metrics(y_true: pd.Series, y_score: pd.Series) -> dict[str, Any]:
    metrics: dict[str, Any] = {
        "mean_score": round(float(y_score.mean()), 4),
        "positive_rate": round(float(y_true.mean()), 4),
    }
    unique_values = set(y_true.dropna().unique().tolist())
    if len(unique_values) > 1:
        metrics["auc"] = round(float(roc_auc_score(y_true, y_score)), 4)
        metrics["brier"] = round(float(brier_score_loss(y_true, y_score)), 4)
        predicted_label = (y_score >= 0.5).astype(int)
        metrics["accuracy_at_0_5"] = round(float(accuracy_score(y_true, predicted_label)), 4)
    return metrics


def regression_metrics(y_true: pd.Series, y_pred: pd.Series) -> dict[str, Any]:
    mse = mean_squared_error(y_true, y_pred)
    return {
        "mae": round(float(mean_absolute_error(y_true, y_pred)), 6),
        "rmse": round(float(mse**0.5), 6),
        "pred_mean": round(float(y_pred.mean()), 6),
        "true_mean": round(float(y_true.mean()), 6),
    }


def decile_summary(y_true: pd.Series, y_score: pd.Series) -> list[dict[str, Any]]:
    ordered = pd.DataFrame({"y_true": y_true, "y_score": y_score}).sort_values("y_score", ascending=False).reset_index(drop=True)
    if ordered.empty:
        return []
    bucket_count = min(10, len(ordered))
    ordered["bucket"] = pd.qcut(ordered.index + 1, q=bucket_count, labels=False, duplicates="drop")
    summary: list[dict[str, Any]] = []
    for bucket, frame in ordered.groupby("bucket"):
        summary.append(
            {
                "bucket": int(bucket),
                "count": int(len(frame)),
                "mean_score": round(float(frame["y_score"].mean()), 4),
                "positive_rate": round(float(frame["y_true"].mean()), 4),
            }
        )
    return summary


def evaluate_regime_model(model: Any, X: pd.DataFrame, y_true: pd.Series) -> dict[str, Any]:
    predictions = [model.predict(record).regime_label for record in X.to_dict(orient="records")]
    return {
        "accuracy": round(float(accuracy_score(y_true, predictions)), 4),
        "sample_count": int(len(y_true)),
    }


def evaluate_event_model(model: Any, X: pd.DataFrame, labels: pd.DataFrame) -> dict[str, Any]:
    predictions = [model.predict(record) for record in X.to_dict(orient="records")]
    pred_df = pd.DataFrame(
        [
            {
                "continue_10d": prediction.p_continue_10d,
                "breakout_success": prediction.p_breakout_success,
                "fail_5d": prediction.p_fail_5d,
                "acceptance_1d": prediction.p_acceptance_1d,
                "fail_fast_3d": prediction.p_fail_fast_3d,
            }
            for prediction in predictions
        ]
    )
    report: dict[str, Any] = {}
    for target in EVENT_LABEL_COLUMNS:
        y_true = labels[target].astype(int)
        y_score = pred_df[target].astype(float)
        report[target] = classification_metrics(y_true, y_score)
        report[target]["deciles"] = decile_summary(y_true, y_score)
    return report


def evaluate_entry_model(model: Any, X: pd.DataFrame, labels: pd.Series) -> dict[str, Any]:
    scores = [model.predict(record).entry_probability or 0.0 for record in X.to_dict(orient="records")]
    y_true = labels.astype(int)
    y_score = pd.Series(scores, index=labels.index, dtype=float)
    report = classification_metrics(y_true, y_score)
    report["deciles"] = decile_summary(y_true, y_score)
    return report


def evaluate_risk_model(model: Any, X: pd.DataFrame, targets: pd.DataFrame) -> dict[str, Any]:
    predictions = [model.predict(record) for record in X.to_dict(orient="records")]
    pred_df = pd.DataFrame([{target: getattr(prediction, target) for target in RISK_TARGET_COLUMNS} for prediction in predictions])
    return {
        target: regression_metrics(targets[target].astype(float), pred_df[target].astype(float))
        for target in RISK_TARGET_COLUMNS
    }


def feature_importance_report(model: Any) -> list[dict[str, Any]]:
    if hasattr(model, "feature_names") and hasattr(model, "model"):
        booster = model.model
        return sorted(
            [
                {"feature": feature, "importance": int(importance)}
                for feature, importance in zip(model.feature_names, booster.feature_importances_.tolist())
            ],
            key=lambda item: item["importance"],
            reverse=True,
        )[:20]

    if hasattr(model, "models") and hasattr(model, "feature_names"):
        aggregate: dict[str, int] = {name: 0 for name in model.feature_names}
        for estimator in model.models.values():
            for feature, importance in zip(model.feature_names, estimator.feature_importances_.tolist()):
                aggregate[feature] += int(importance)
        return sorted(
            [{"feature": feature, "importance": importance} for feature, importance in aggregate.items()],
            key=lambda item: item["importance"],
            reverse=True,
        )[:20]
    return []


def build_evaluation_report(dataset: pd.DataFrame, model_dir: str | Path) -> dict[str, Any]:
    feature_names = feature_names_from_dataset(dataset)
    X = dataset[feature_names].fillna(0)
    split_source = pd.concat([dataset[["date"]], X, dataset.drop(columns=feature_names + ["date"])], axis=1)
    split = DatasetBuilder().time_split(split_source)
    test = split.test.copy()
    test_X = test[feature_names]

    registry = ModelRegistry(model_dir)
    regime_model = registry.load_regime_model()
    event_model = registry.load_event_model()
    risk_model = registry.load_risk_model()
    entry_model = registry.load_entry_model() if registry.has_entry_model() else None

    report: dict[str, Any] = {
        "sample_counts": {
            "train": int(len(split.train)),
            "validation": int(len(split.validation)),
            "test": int(len(split.test)),
        },
        "regime": evaluate_regime_model(regime_model, test_X, derive_regime_label(test)),
        "event": evaluate_event_model(event_model, test_X, test[EVENT_LABEL_COLUMNS]),
        "risk": evaluate_risk_model(risk_model, test_X, test[RISK_TARGET_COLUMNS].fillna(0)),
        "feature_importance": {
            "regime": feature_importance_report(regime_model),
            "event": feature_importance_report(event_model),
            "risk": feature_importance_report(risk_model),
        },
    }
    if entry_model is not None:
        report["entry"] = evaluate_entry_model(entry_model, test_X, test["entry_success_3d"].fillna(0))
    return report


def _build_dataset_command(args: argparse.Namespace) -> None:
    dataset = build_modeling_dataset(pd.read_csv(args.input), sample_step=args.sample_step)
    output_path = save_dataset(dataset, args.output)
    print(f"dataset saved to {output_path}")


def _train_command(args: argparse.Namespace) -> None:
    dataset = load_dataset(args.dataset)
    trainers = {
        "train-regime": train_regime_model,
        "train-event": train_event_model,
        "train-risk": train_risk_model,
        "train-entry": train_entry_model,
    }
    output_path = trainers[args.command](dataset, args.output_dir)
    print(f"saved {args.command.replace('train-', '')} model to {output_path}")


def _evaluate_command(args: argparse.Namespace) -> None:
    report = build_evaluation_report(load_dataset(args.dataset), args.model_dir)
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(rendered, encoding="utf-8")
        print(f"evaluation report saved to {output_path}")
    else:
        print(rendered)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Unified research modeling entry for Shilun.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    build_parser_ = subparsers.add_parser("build-dataset", help="Build feature/label dataset.")
    build_parser_.add_argument("--input", required=True, help="CSV file with ticker,date,open,high,low,close,volume,amount")
    build_parser_.add_argument("--output", required=True, help="Output parquet or csv path")
    build_parser_.add_argument("--sample-step", type=int, default=1, help="Sample every N eligible bars for structure snapshots.")
    build_parser_.set_defaults(func=_build_dataset_command)

    for command, description in [
        ("train-regime", "Train LightGBM regime model."),
        ("train-event", "Train LightGBM event model."),
        ("train-risk", "Train LightGBM risk model."),
        ("train-entry", "Train logistic entry curve model."),
    ]:
        train_parser = subparsers.add_parser(command, help=description)
        train_parser.add_argument("--dataset", required=True)
        train_parser.add_argument("--output-dir", required=True)
        train_parser.set_defaults(func=_train_command)

    evaluate_parser = subparsers.add_parser("evaluate", help="Evaluate trained Shilun models on a dataset.")
    evaluate_parser.add_argument("--dataset", required=True, help="Dataset produced by build-dataset")
    evaluate_parser.add_argument("--model-dir", required=True, help="Directory containing regime/event/risk joblib files")
    evaluate_parser.add_argument("--output", required=False, help="Optional path to save JSON report")
    evaluate_parser.set_defaults(func=_evaluate_command)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
