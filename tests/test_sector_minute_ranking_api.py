from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from shilun.api import (
    _compute_sector_trends_with_candidate_minutes,
    _sector_candidate_minute_targets,
)


class _EmptyCursor:
    def __iter__(self):
        return iter(())


class _EmptyCollection:
    def find(self, *_args, **_kwargs):
        return _EmptyCursor()


class _Store:
    def collection(self, _name: str):
        return _EmptyCollection()


class SectorMinuteRankingApiTest(unittest.TestCase):
    def test_targets_use_full_shortlist_not_visible_top_three(self) -> None:
        payload = {
            "top_sectors": [{"minute_candidate_tickers": ["VISIBLE"]}],
            "all_sectors": [
                {"minute_candidate_tickers": ["A", "B", "C", "D"]},
                {"minute_candidate_tickers": ["E", "A"]},
            ],
        }

        self.assertEqual(
            ["A", "B", "C", "D", "E"],
            _sector_candidate_minute_targets(payload, top_n=1),
        )

    def test_final_compute_always_enables_strict_minute_rank_gate(self) -> None:
        initial = {
            "top_sectors": [{"minute_candidate_tickers": ["600644.SH"]}],
            "all_sectors": [{"minute_candidate_tickers": ["600644.SH", "600900.SH"]}],
        }
        final = {
            "top_sectors": [],
            "all_sectors": [],
            "minute_ranking_gate": "strict",
        }
        config = SimpleNamespace(
            klineshare_api_key=None,
            klineshare_base_url="https://example.invalid",
            klineshare_timeout=1,
        )

        with patch(
            "shilun.market.sector_service._compute_sector_trends_full",
            side_effect=[initial, final],
        ) as compute:
            result = _compute_sector_trends_with_candidate_minutes(
                store=_Store(),
                config=config,
                target_date="2026-07-20",
                bm_ticker="000001.SH",
                lookback_days=40,
                trend_lookback_days=60,
                top_n=8,
                include_daily_leaders=False,
                include_all_sectors=True,
            )

        self.assertFalse(compute.call_args_list[0].kwargs["require_minute_data_for_ranking"])
        self.assertTrue(compute.call_args_list[1].kwargs["require_minute_data_for_ranking"])
        self.assertEqual("blocked", result["minute_sync"]["status"])
        self.assertEqual("strict", result["minute_sync"]["ranking_gate"])


if __name__ == "__main__":
    unittest.main()
