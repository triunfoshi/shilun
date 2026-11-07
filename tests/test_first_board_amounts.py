import unittest
from unittest.mock import MagicMock, patch

import pandas as pd

from shilun.review.board_auction import (
    COLLECTION, SOURCE, VERSION, normalize_opening_auction, sync_first_board_auction,
)
from shilun.review.board_data import BoardResearchData, fetch_historical_pool, normalize_pool
from shilun.review.research_ai import prompt


DAY = "2026-09-21"


def auction(amount=29723219.01, **overrides):
    return {"ts_code": "000607.SZ", "trade_date": "20260921", "amount": amount, **overrides}


class FirstBoardAmountsTests(unittest.TestCase):
    def test_auction_keeps_yuan_and_genuine_zero(self):
        result = normalize_opening_auction(pd.DataFrame([
            auction(), auction(0, ts_code="600743.SH"), auction(None, ts_code="600301.SH"),
        ]), DAY)
        self.assertEqual({"000607": 29723219.01, "600743": 0}, result["amounts"])
        self.assertEqual("09:25", result["auction_time"])
        self.assertEqual("CNY", result["unit"])

    def test_auction_rejects_wrong_date_code_duplicate_or_truncation(self):
        cases = [[], [auction(trade_date="20260918")], [auction(ts_code="000607.SH")],
                 [auction(), auction()], [auction()] * 8000]
        for rows in cases:
            with self.subTest(rows=len(rows)), self.assertRaises(ValueError):
                normalize_opening_auction(pd.DataFrame(rows), DAY)

    def test_invalid_amount_is_not_zero(self):
        for value in (-1, float("nan"), float("inf"), True, "—", None):
            with self.subTest(value=value), self.assertRaises(ValueError):
                normalize_opening_auction(pd.DataFrame([auction(value)]), DAY)

    def test_mixed_gateway_universe_excludes_etfs_b_shares_and_convertibles(self):
        rows = [auction(), auction(100, ts_code="510300.SH"), auction(200, ts_code="900901.SH"),
                auction(300, ts_code="200002.SZ"), auction(400, ts_code="110001.SH"),
                auction(500, ts_code="920002.BJ")]
        result = normalize_opening_auction(pd.DataFrame(rows), DAY)
        self.assertEqual({"000607": 29723219.01, "920002": 500}, result["amounts"])

    def test_pool_seal_amount_maps_all_sources_in_yuan(self):
        east = normalize_pool([{"c": "000607", "fund": 12345678}], eastmoney=True)[0]
        local = normalize_pool([{"ticker": "000607.SZ", "limit_up_fund": 0}])[0]
        self.assertEqual(12345678, east["seal_amount"])
        self.assertEqual(0, local["seal_amount"])
        client = MagicMock()
        client._call_api.return_value = pd.DataFrame([
            {**auction(), "limit": "U", "fd_amount": 87654321, "limit_times": 1}])
        historical = fetch_historical_pool(client, DAY, "up")
        self.assertEqual(87654321, historical["stocks"][0]["seal_amount"])
        self.assertIsNone(normalize_pool([{"ticker": "000607.SZ"}])[0]["seal_amount"])

    def test_read_joins_only_selected_day_without_network(self):
        store, collection = MagicMock(), MagicMock()
        store.collection.return_value = collection
        def find(query, *args, **kwargs):
            if query.get("source") == SOURCE:
                self.assertEqual(DAY, query["date"])
                return {"amounts": {"000607": 29723219.01}, "source": SOURCE}
            return {}
        collection.find_one.side_effect = find
        store.find_daily_bars_for_trade_date.return_value = []
        store.find_price_limits.return_value = []
        stocks = normalize_pool([{"c": "000607", "n": "华媒", "lbc": 1, "fund": 1e8},
                                 {"c": "600743", "n": "甲", "lbc": 1}], eastmoney=True)
        data = BoardResearchData(store)
        with patch.object(data, "pools", return_value={DAY: {"stocks": stocks, "source": "eastmoney_dated_pool"}}), \
                patch("shilun.review.board_auction.sync_first_board_auction") as sync:
            result = data.first_board(DAY)
        sync.assert_not_called()
        self.assertEqual({"auction": 1, "seal": 1, "total": 2}, result["amount_coverage"])
        self.assertEqual(29723219.01, result["stocks"][0]["auction_amount"])
        self.assertIsNone(result["stocks"][1]["auction_amount"])
        self.assertIsNone(result["stocks"][1]["seal_amount"])

    def test_first_board_amount_priority_and_missing_zero(self):
        store, collection = MagicMock(), MagicMock()
        store.collection.return_value = collection
        collection.find.return_value = []
        specs = [
            ('600001', 5, 7, '100000'),
            ('600002', None, 1e12, '092500'),
            ('600003', 6, 0, '140000'),
            ('600004', 5, 7, '093000'),
            ('600005', 5, 8, '150000'),
            ('600006', 0, None, '092500'),
            ('600007', None, None, '092500'),
            ('600008', 5, None, '092500'),
            ('600009', 5, 0, '092500'),
        ]
        amounts = {code: amount for code, amount, _, _ in specs if amount is not None}
        collection.find_one.side_effect = lambda query, *_args, **_kwargs: (
            {'amounts': amounts, 'source': SOURCE} if query.get('source') == SOURCE else {})
        stocks = normalize_pool([{'c': code, 'n': code, 'lbc': 1, 'fund': seal, 'fbt': time}
                                 for code, _, seal, time in specs], eastmoney=True)
        data = BoardResearchData(store)
        with patch.object(data, 'pools', return_value={DAY: {'stocks': stocks, 'source': 'eastmoney_dated_pool'}}):
            result = data.first_board(DAY)
        self.assertEqual(['600003', '600005', '600004', '600001', '600009', '600008',
                          '600006', '600002', '600007'], [r['code'] for r in result['stocks']])
        self.assertEqual(['auction_amount_desc', 'seal_amount_desc', 'seal_time_asc', 'code_asc'],
                         result['sort_order'])

    def test_sync_skips_verified_cohort_and_keeps_cache_on_failure(self):
        store, client = MagicMock(), MagicMock()
        collection = store.collection.return_value
        pool = {DAY: {"stocks": [{"code": "000607", "boards": 1}]}}
        with patch.object(BoardResearchData, "pools", return_value=pool):
            collection.find_one.return_value = {"amounts": {"000607": 0}}
            self.assertEqual("ok", sync_first_board_auction(store, DAY, client=client)["status"])
            client._call_api.assert_not_called()
            collection.find_one.return_value = {"amounts": {"600743": 25}}
            client._call_api.side_effect = RuntimeError("private-key")
            report = sync_first_board_auction(store, DAY, client=client)
        self.assertEqual("unavailable", report["status"])
        self.assertNotIn("private-key", str(report))
        collection.update_one.assert_not_called()

    def test_sync_materializes_and_merges_only_validated_date(self):
        store, client = MagicMock(), MagicMock()
        collection = store.collection.return_value
        collection.find_one.return_value = {"amounts": {"600743": 25}}
        pool = {DAY: {"stocks": [{"code": "000607", "boards": 1}, {"code": "600743", "boards": 1}]}}
        client._call_api.return_value = pd.DataFrame([auction()])
        with patch.object(BoardResearchData, "pools", return_value=pool):
            report = sync_first_board_auction(store, DAY, client=client)
        self.assertEqual(2, report["count"])
        self.assertEqual("ok", report["status"])
        saved = collection.update_one.call_args.args[1]["$set"]
        self.assertEqual({"000607": 29723219.01, "600743": 25}, saved["amounts"])
        self.assertEqual(VERSION, saved["schema"])
        self.assertEqual("stk_auction", client._call_api.call_args.args[0])

    def test_research_receives_amounts_and_scope(self):
        text = prompt({"auction_amount": 1234, "seal_amount": 9876, "seal_snapshot_mode": "live"}, DAY)
        self.assertIn('"auction_amount": 1234', text)
        self.assertIn('"seal_amount": 9876', text)
        self.assertIn("涨停买入挂单资金", text)
        self.assertIn("缺失值不等于零", text)


if __name__ == "__main__":
    unittest.main()
