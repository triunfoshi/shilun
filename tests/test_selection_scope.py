import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from shilun.api import market_candidates
from fastapi import HTTPException
from shilun.market.ma5_liquidity import MA5_SELECTION_RULE_VERSION


class SelectionScopeTests(unittest.TestCase):
    def test_ma5_reports_full_market_scope_without_changing_candidates(self):
        store = Mock()
        candidates = [{'ticker': '000001.SZ', 'ranking_status': 'excluded'}]
        payload = {'top_sectors': [{'sector_name': '瓷砖'}, {'sector_name': '芯片'}],
                   'candidates': candidates,
                   'selection_rule_version': MA5_SELECTION_RULE_VERSION,
                   'selection_scope': {'kind': 'full_market',
                                       'independent_from_sector_ranking': True,
                                       'universe_count': 5579}}
        with patch('shilun.api.load_config', return_value=SimpleNamespace(mongo_uri='test', mongo_db='test')), \
             patch('shilun.common.db.MongoSnapshotStore', return_value=store), \
             patch('shilun.api._load_sector_cache', return_value={'payload': payload}), \
             patch('shilun.api._load_market_gate', return_value={}):
            result = market_candidates(date='2026-09-21')
        self.assertEqual(result['selection_scope']['kind'], 'full_market')
        self.assertEqual(result['selection_scope']['universe_count'], 5579)
        self.assertTrue(result['selection_scope']['independent_from_sector_ranking'])
        self.assertEqual(result['selection_rule_version'], MA5_SELECTION_RULE_VERSION)
        self.assertEqual(result['candidates'], candidates)
        store.close.assert_called_once()

    def test_ma5_rejects_cached_candidates_from_old_selection_rule(self):
        store = Mock()
        with patch('shilun.api.load_config', return_value=SimpleNamespace(mongo_uri='test', mongo_db='test')), \
             patch('shilun.common.db.MongoSnapshotStore', return_value=store), \
             patch('shilun.api._load_sector_cache', return_value={'payload': {
                 'selection_rule_version': 'ma5_selection_v1',
                 'candidates': [{'ticker': '000001.SZ'}],
             }}):
            with self.assertRaises(HTTPException) as raised:
                market_candidates(date='2026-09-21')
        self.assertEqual(raised.exception.status_code, 400)
        self.assertIn('规则已更新', raised.exception.detail)
        store.close.assert_called_once()


if __name__ == '__main__':
    unittest.main()
