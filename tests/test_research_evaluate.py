import unittest
from pathlib import Path
import sys

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from research.modeling import classification_metrics, decile_summary, feature_names_from_dataset, regression_metrics


class ResearchEvaluateTests(unittest.TestCase):
    def test_feature_name_filter_excludes_labels(self) -> None:
        dataset = pd.DataFrame(
            {
                "ticker": ["000001.SZ"],
                "date": ["2026-01-01"],
                "feature_a": [1.0],
                "continue_10d": [1],
                "expected_return_10d": [0.02],
            }
        )
        feature_names = feature_names_from_dataset(dataset)
        self.assertEqual(["feature_a"], feature_names)

    def test_classification_metrics_and_deciles(self) -> None:
        y_true = pd.Series([1, 1, 0, 0, 1, 0])
        y_score = pd.Series([0.9, 0.8, 0.4, 0.2, 0.7, 0.1])
        metrics = classification_metrics(y_true, y_score)
        deciles = decile_summary(y_true, y_score)
        self.assertIn("auc", metrics)
        self.assertIn("brier", metrics)
        self.assertGreater(len(deciles), 0)

    def test_regression_metrics(self) -> None:
        y_true = pd.Series([0.01, 0.03, 0.05])
        y_pred = pd.Series([0.015, 0.02, 0.055])
        metrics = regression_metrics(y_true, y_pred)
        self.assertIn("mae", metrics)
        self.assertIn("rmse", metrics)


if __name__ == "__main__":
    unittest.main()
