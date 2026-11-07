"""Read-only compatibility must never turn cache misses into data writes."""
from copy import deepcopy
from datetime import date
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from fastapi import HTTPException
from shilun.services import MongoFirstAnalysisService

with patch.object(MongoFirstAnalysisService, '_build_mongo_store', return_value=None):
    from shilun import api
from shilun.api import review


DAY = '2026-11-02'
TICKER = '600001.SH'
BENCHMARK = '000001.SH'


class Cursor(list):
    def limit(self, limit):
        return Cursor(self[:limit])


class ReadOnlyCollection:
    def __init__(self, store, name):
        self.store, self.name = store, name

    def find_one(self, query, *args, **kwargs):
        self.store.reads.append((self.name, query))
        return deepcopy(self.store.documents.get(self.name))

    def find(self, query, *args, **kwargs):
        self.store.reads.append((self.name, query))
        return Cursor(deepcopy(self.store.rows.get(self.name, [])))

    def __getattr__(self, name):
        raise AssertionError('Unexpected collection operation: ' + self.name + '.' + name)


class ReadOnlyStore:
    def __init__(self):
        self.rows, self.documents, self.reads = {}, {}, []
        self.closed = False
        self.raw_market = SimpleNamespace(
            find_daily_bars=Mock(return_value=[{'date': '2026-10-30', 'close': 100}]),
            find_stock_basic=Mock(return_value=[{'ts_code': TICKER, 'name': '测试股'}]),
        )

    def collection(self, name):
        return ReadOnlyCollection(self, name)

    def close(self):
        self.closed = True


class ReadOnlyMarketTests(unittest.TestCase):
    def setUp(self):
        self.store = ReadOnlyStore()
        self.config = SimpleNamespace(mongo_uri='mongodb://offline', mongo_db='offline',
                                      klineshare_api_key='synthetic')
        self.config_patch = patch.object(api, 'load_config', return_value=self.config)
        self.store_patch = patch('shilun.common.db.MongoSnapshotStore', return_value=self.store)
        self.config_patch.start()
        self.store_factory = self.store_patch.start()
        self.addCleanup(self.config_patch.stop)
        self.addCleanup(self.store_patch.stop)

    def assert_no_indexes(self):
        self.assertIs(self.store_factory.call_args.kwargs.get('initialize_indexes'), False)
        self.assertTrue(self.store.closed)

    def test_permission_cache_miss_does_not_compute_or_write(self):
        with patch.object(api, 'evaluate_market_permission', side_effect=AssertionError('computed')):
            with self.assertRaises(HTTPException) as result:
                api.market_permission(date=DAY, read_only=True)
        self.assertEqual(404, result.exception.status_code)
        self.assert_no_indexes()
        self.assertEqual(1, self.store.raw_market.find_daily_bars.call_count)

    def test_permission_cache_hit_returns_archive(self):
        self.store.documents['market_part1_cache'] = {
            'engine_version': api.PART1_ENGINE_VERSION,
            'payload': {'analysis_date': '2026-10-30', 'market_gate': {'label': '观察'}},
            'cached_at': '2026-10-30T18:00:00',
        }
        with patch.object(api, 'evaluate_market_permission', side_effect=AssertionError('computed')):
            result = api.market_permission(date=DAY, read_only=True)
        self.assertEqual('观察', result['market_gate']['label'])
        self.assertTrue(result['_from_cache'])
        self.assert_no_indexes()

    def test_permission_rejects_force_in_read_only_mode(self):
        with self.assertRaises(HTTPException) as result:
            api.market_permission(date=DAY, force=True, read_only=True)
        self.assertEqual(400, result.exception.status_code)
        self.store_factory.assert_not_called()

    def test_leaders_cache_miss_never_recalculates(self):
        with (patch.object(api, '_load_sector_cache', return_value=None),
              patch.object(api, 'evaluate_daily_leaders', side_effect=AssertionError('computed'))):
            with self.assertRaises(HTTPException) as result:
                api.market_daily_leaders(date=DAY, read_only=True)
        self.assertEqual(404, result.exception.status_code)
        self.assert_no_indexes()

    def test_leaders_verified_empty_cache_is_returned(self):
        with (patch.object(api, '_load_sector_cache', return_value={'payload': {'daily_leaders': []}}),
              patch.object(api, 'evaluate_daily_leaders', side_effect=AssertionError('computed'))):
            result = api.market_daily_leaders(date=DAY, read_only=True)
        self.assertEqual([], result['daily_leaders'])
        self.assertTrue(result['_from_cache'])
        self.assert_no_indexes()

    def test_stock_search_uses_store_without_index_initialization(self):
        self.store.rows['stock_basic'] = [{'ts_code': TICKER, 'symbol': '600001', 'name': '测试股'}]
        with (patch('shilun.market.taxonomy.classify_stocks', side_effect=lambda store, day, rows: rows),
              patch('shilun.review.service.DailyReviewService.latest_date', return_value=DAY)):
            result = api.search_stocks('600001', read_only=True)
        self.assertEqual(TICKER, result['items'][0]['ticker'])
        self.assert_no_indexes()

    def test_archived_quotes_use_provider_date_not_fetch_date(self):
        self.store.rows['trading_realtime_quotes'] = [
            {'ticker': TICKER, 'quote_at': DAY+'T10:00:00+08:00', 'current': 10},
            {'ticker': TICKER, 'quote_at': DAY+'T11:00:00+08:00', 'current': 11},
            {'ticker': TICKER, 'quote_at': '2026-11-03T10:00:00+08:00', 'current': 99},
            {'ticker': BENCHMARK, 'fetched_at': DAY+'T11:00:00+08:00', 'current': 100},
        ]
        result = api._read_archived_quotes(self.store, [TICKER, BENCHMARK], DAY)
        self.assertEqual(11, result[TICKER]['current'])
        self.assertNotIn(BENCHMARK, result)

    def test_stock_panel_reads_only_archived_minutes_and_quotes(self):
        self.store.rows['trading_realtime_quotes'] = [
            {'ticker': TICKER, 'quote_at': DAY+'T10:00:00+08:00', 'current': 10, 'pct_chg': 1},
            {'ticker': BENCHMARK, 'quote_at': DAY+'T10:00:00+08:00', 'current': 100, 'pct_chg': 0.5},
        ]
        self.store.documents['minute_bars_daily'] = {'bars': [
            {'datetime': DAY+'T09:35:00', 'open': 10, 'high': 10, 'low': 10, 'close': 10, 'volume': 1, 'amount': 10},
            {'datetime': '2026-11-03T09:35:00', 'open': 99, 'high': 99, 'low': 99, 'close': 99, 'volume': 1, 'amount': 99},
        ]}
        with (patch('shilun.data.providers.minute_provider.MinuteClient', side_effect=AssertionError('network client')),
              patch('shilun.data.providers.klineshare_provider.KlineShareClient', side_effect=AssertionError('network client')),
              patch('shilun.data.providers.tushare_realtime.TushareRealtimeClient', side_effect=AssertionError('network client')),
              patch('shilun.market.taxonomy.classify_stocks', side_effect=lambda store, day, rows: rows)):
            result = api.stock_panel(TICKER, date=DAY, intraday_only=True, read_only=True)
        self.assertEqual('cached_only', result['source_mode'])
        self.assertEqual(10, result['realtime']['current'])
        self.assertEqual(1, len(result['minute_bars']))
        self.assertEqual('mongo_archive', result['minute_source'])
        self.store.raw_market.find_daily_bars.assert_not_called()
        self.assert_no_indexes()

    def test_intraday_reads_cache_without_fetching_or_recording(self):
        self.store.rows['trading_realtime_quotes'] = [
            {'ticker': BENCHMARK, 'quote_at': DAY+'T10:00:00+08:00', 'current': 100, 'pct_chg': 0.5},
        ]
        with (patch.object(api, 'market_permission', return_value={'market_gate': {}, 'levels': {}}) as permission,
              patch.object(api, '_fetch_realtime_via_klineshare', side_effect=AssertionError('fetch')),
              patch.object(api, '_fetch_realtime_via_tushare', side_effect=AssertionError('fetch')),
              patch.object(api, '_fetch_realtime_via_eastmoney', side_effect=AssertionError('fetch')),
              patch.object(api, '_persist_klineshare_realtime_snapshot', side_effect=AssertionError('write')),
              patch.object(api, '_sync_intraday_minutes_via_klineshare', side_effect=AssertionError('write'))):
            result = api.market_intraday(date=DAY, benchmark_ticker=BENCHMARK, include_candidates=False, read_only=True)
        self.assertTrue(permission.call_args.kwargs['read_only'])
        self.assertEqual('cached_only', result['source_mode'])
        self.assertEqual('mongo_archive', result['realtime_source'])
        self.assertEqual(0, result['recorded_decision_count'])
        self.assert_no_indexes()

    def test_intraday_rejects_decision_recording(self):
        with self.assertRaises(HTTPException) as result:
            api.market_intraday(date=DAY, record_decisions=True, read_only=True)
        self.assertEqual(400, result.exception.status_code)
        self.store_factory.assert_not_called()

    def test_intraday_candidates_use_archived_minutes_without_outbox(self):
        self.store.rows['trading_realtime_quotes'] = [
            {'ticker': BENCHMARK, 'quote_at': DAY+'T10:00:00+08:00', 'current': 100},
            {'ticker': TICKER, 'quote_at': DAY+'T10:00:00+08:00', 'current': 10},
        ]
        candidates = [{'ticker': TICKER}]
        minutes = [{'datetime': DAY+'T09:35:00', 'close': 10}]
        with (patch.object(api, 'market_permission', return_value={'market_gate': {}, 'levels': {}}),
              patch.object(api, '_extract_candidates_from_cache', return_value=candidates),
              patch.object(api, '_enrich_intraday_candidate_plans', return_value=candidates),
              patch.object(api, '_merge_candidates_with_realtime', return_value=deepcopy(candidates)),
              patch.object(api, '_fetch_realtime_via_klineshare', side_effect=AssertionError('fetch')),
              patch.object(api, '_fetch_realtime_via_tushare', side_effect=AssertionError('fetch')),
              patch.object(api, '_fetch_realtime_via_eastmoney', side_effect=AssertionError('fetch')),
              patch.object(api, '_persist_klineshare_realtime_snapshot', side_effect=AssertionError('write')),
              patch.object(api, '_sync_intraday_minutes_via_klineshare', side_effect=AssertionError('write')),
              patch('shilun.market.minute_bars_store.bulk_load_lookup', return_value={TICKER: minutes}) as load,
              patch('shilun.trading.intraday_alerts.list_watchlist', return_value=[]),
              patch('shilun.trading.intraday_alerts.list_candidate_timeline', return_value=[]),
              patch('shilun.trading.intraday_alerts.record_candidate_decisions', side_effect=AssertionError('outbox'))):
            result = api.market_intraday(date=DAY, benchmark_ticker=BENCHMARK, read_only=True)
        self.assertEqual('20261102', load.call_args.args[2])
        self.assertEqual(minutes, result['candidates_realtime'][0]['minute_bars'])
        self.assertEqual('mongo', result['candidates_realtime'][0]['minute_bar_source'])
        self.assertEqual(0, result['recorded_decision_count'])
        self.assert_no_indexes()


class ReadOnlyMarketFactsTests(unittest.TestCase):
    def test_stale_archive_is_read_without_build_or_filesystem_mutation(self):
        archived = {'status': 'ready', 'analysis_date': DAY, 'market': {'volume': {}}, 'cache_status': 'stale'}
        with (patch('shilun.review.market_facts.cached_supplement', return_value=archived),
              patch('shilun.review.market_facts.load_market_facts', side_effect=AssertionError('write')),
              patch.object(review, '_open_store', side_effect=AssertionError('database'))):
            result = review.get_market_facts(date.fromisoformat(DAY), read_only=True)
        self.assertTrue(result['read_only'])
        self.assertEqual('stale', result['cache_status'])

    def test_missing_archive_does_not_build(self):
        with (patch('shilun.review.market_facts.cached_supplement', return_value=None),
              patch('shilun.review.market_facts.load_market_facts', side_effect=AssertionError('write')),
              patch.object(review, '_open_store', side_effect=AssertionError('database'))):
            result = review.get_market_facts(date.fromisoformat(DAY), read_only=True)
        self.assertEqual('not_materialized', result['status'])

    def test_read_only_refresh_is_rejected(self):
        with (patch('shilun.review.market_facts.cached_supplement') as cached,
              self.assertRaises(HTTPException) as result):
            review.get_market_facts(date.fromisoformat(DAY), refresh=True, read_only=True)
        self.assertEqual(400, result.exception.status_code)
        cached.assert_not_called()


if __name__ == '__main__':
    unittest.main()
