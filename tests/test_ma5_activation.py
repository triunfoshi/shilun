"""Production handoff: versioned selection overrides legacy MA5 observations."""
from types import SimpleNamespace
from unittest.mock import patch
from shilun.market import SECTOR_ENGINE_VERSION
from shilun.market.ma5_liquidity import MA5_SELECTION_RULE_VERSION
from shilun.trading.market_facts import _latest_selection_pool_rows, _sector_cache
from shilun.api import _sector_candidate_minute_targets, _extract_candidates_from_cache


class Cursor(list):
    def sort(self, key, direction):
        return Cursor(sorted(self, key=lambda x: x.get(key, ''), reverse=direction < 0))
    def limit(self, n):
        return Cursor(self[:n])


class Collection:
    def __init__(self, rows):
        self.rows = rows
    def find(self, query, projection=None):
        def matches(row):
            for key, expected in query.items():
                actual = row
                for part in key.split('.'):
                    actual = actual.get(part) if isinstance(actual, dict) else None
                if isinstance(expected, dict):
                    if '$lte' in expected and (actual is None or actual > expected['$lte']):
                        return False
                elif actual != expected:
                    return False
            return True
        return Cursor([r for r in self.rows if matches(r)])
    def find_one(self, query, projection=None, sort=None):
        rows = self.find(query)
        for key, direction in reversed(sort or []):
            rows = rows.sort(key, direction)
        return rows[0] if rows else None


def store_for(candidates):
    cache = {'analysis_date':'2026-09-21', 'computed_at':'2026-09-22',
             'benchmark_ticker':'000001.SH', 'trend_lookback_days':60,
             'engine_version': SECTOR_ENGINE_VERSION,
             'payload':{'selection_rule_version':MA5_SELECTION_RULE_VERSION, 'candidates':candidates}}
    future = {**cache, 'analysis_date':'2026-09-23'}
    legacy = {**cache, 'engine_version':'old', 'computed_at':'2026-09-24', 'payload':{}}
    pools = [{'ticker':'OLD', 'analysis_date':'2026-09-21', 'strategy_ids':'ma5_v0_2'},
             {'ticker':'OTHER', 'analysis_date':'2026-09-21', 'strategy_ids':'weekly_breakout'}]
    collections = {'sector_trends_cache':Collection([legacy, future, cache]),
                   'candidate_pool_states':Collection(pools)}
    return SimpleNamespace(collection=lambda name: collections.get(name, Collection([])))


@patch('shilun.trading.market_facts.find_latest_strategy_candidates', return_value=('', []))
def test_current_selection_replaces_old_pool_and_api_uses_same_rows(_):
    rows = [{'ticker':'NEW', 'selection_gate':{'passed':True}, 'ranking_status':'eligible'},
            {'ticker':'OVER_CAP', 'selection_gate':{'passed':False}, 'ranking_status':'excluded'}]
    store = store_for(rows)
    observed = _latest_selection_pool_rows(store, '20260922')
    assert {r['ticker'] for r in observed} == {'NEW', 'OTHER'}
    assert {r['ticker'] for r in _extract_candidates_from_cache(store, '2026-09-22', '000001.SH', {}, 30)} == {'NEW', 'OTHER'}
    cache = _sector_cache(store, '20260922')
    assert cache['analysis_date'] == '2026-09-21'
    assert cache['engine_version'] == SECTOR_ENGINE_VERSION


@patch('shilun.trading.market_facts.find_latest_strategy_candidates', return_value=('', []))
def test_empty_current_selection_does_not_restore_old_ma5(_):
    assert [r['ticker'] for r in _latest_selection_pool_rows(store_for([]), '20260922')] == ['OTHER']


def test_full_market_minute_targets_keep_priority_and_capacity():
    rows = [{'ticker':str(i), 'selection_gate':{'passed':True}} for i in range(198)]
    selected = _sector_candidate_minute_targets({'candidates':rows, 'top_sectors':[{'minute_candidate_tickers':['EXTRA']}]}, top_n=8)
    assert selected == [str(i) for i in range(198)]
