import unittest

from shilun.review.quality import assess_review_data_quality


class ReviewDataQualityTests(unittest.TestCase):
    def test_stale_optional_feeds_keep_historical_review_but_block_current_execution(self) -> None:
        quality = assess_review_data_quality(
            analysis_date="2026-08-12",
            generated_date="2026-09-01",
            stock_master_count=5600,
            current_bar_count=5532,
            previous_bar_count=5500,
            daily_basic_count=5532,
            moneyflow_count=5400,
            price_limit_count=5532,
            limit_pool_date="2026-08-07",
            futures_date="2026-08-05",
        )

        self.assertEqual("historical_only", quality["status"])
        self.assertTrue(quality["historical_review_ready"])
        self.assertFalse(quality["current_execution_ready"])

    def test_missing_cross_section_blocks_review(self) -> None:
        quality = assess_review_data_quality(
            analysis_date="2026-09-01",
            generated_date="2026-09-01",
            stock_master_count=5600,
            current_bar_count=100,
            previous_bar_count=0,
            daily_basic_count=100,
            moneyflow_count=100,
            price_limit_count=100,
            limit_pool_date="2026-09-01",
            futures_date="2026-09-01",
        )

        self.assertEqual("blocked", quality["status"])
        self.assertFalse(quality["historical_review_ready"])

    def test_coverage_never_exceeds_one_for_broader_source_universe(self) -> None:
        quality = assess_review_data_quality(
            analysis_date="2026-09-01",
            generated_date="2026-09-01",
            stock_master_count=100,
            current_bar_count=90,
            previous_bar_count=90,
            daily_basic_count=120,
            moneyflow_count=120,
            price_limit_count=140,
            limit_pool_date="2026-09-01",
            futures_date="2026-09-01",
        )

        coverages = [
            item["coverage"]
            for item in quality["checks"]
            if item.get("coverage") is not None
        ]
        self.assertTrue(all(0.0 <= value <= 1.0 for value in coverages))


if __name__ == "__main__":
    unittest.main()
