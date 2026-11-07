"""Offline optional-source coverage; simulated adapters never contact providers."""
from copy import deepcopy
import json
from unittest.mock import Mock

import pytest

from shilun.jobs import pipeline_optional_sources as optional
from shilun.review.board_auction import COLLECTION as AUCTIONS, SOURCE, VERSION
from shilun.review.strength_review import LHB
from shilun.review.suspensions import COLLECTION as SUSPENSIONS


DAY, PRIOR = '2026-11-03', '2026-11-02'
DAYS = [PRIOR, DAY]
TICKER, CODE = '600001.SH', '600001'


def matches(row, query):
    for key, expected in query.items():
        value = row.get(key)
        if isinstance(expected, dict):
            if '$in' in expected and value not in expected['$in']:
                return False
            if '$ne' in expected and value == expected['$ne']:
                return False
            if '$gte' in expected and (value is None or value < expected['$gte']):
                return False
            if '$lte' in expected and (value is None or value > expected['$lte']):
                return False
        elif value != expected:
            return False
    return True


class Collection:
    def __init__(self, rows=()):
        self.rows = deepcopy(list(rows))
        self.writes = 0

    def find(self, query, projection=None):
        return [deepcopy(r) for r in self.rows if matches(r, query)]

    def find_one(self, query, projection=None):
        rows = self.find(query, projection)
        return rows[0] if rows else None

    def update_one(self, query, update, upsert=False):
        self.writes += 1
        row = next((r for r in self.rows if matches(r, query)), None)
        if row is None and upsert:
            row = deepcopy(query)
            self.rows.append(row)
        if row is not None:
            row.update(deepcopy(update.get('$set', {})))

    def replace_one(self, query, document, upsert=False):
        self.writes += 1
        existing = next((r for r in self.rows if matches(r, query)), None)
        if existing is not None:
            existing.clear()
            existing.update(deepcopy(document))
        elif upsert:
            self.rows.append(deepcopy(document))


class Store:
    def __init__(self):
        self.collections = {}

    def collection(self, name):
        return self.collections.setdefault(name, Collection())


@pytest.fixture
def setup(monkeypatch, tmp_path):
    store = Store()
    monkeypatch.setattr(optional, '_sessions', lambda *a: DAYS)
    monkeypatch.setattr(optional, '_heat_path', lambda day: tmp_path / f'{day}.json')
    pools = {kind: {day: {'date': day, 'complete': True,
                         'stocks': ([{'ticker': TICKER, 'code': CODE, 'boards': 1}] if kind == 'up' else [])}
                    for day in DAYS} for kind in ('up', 'broken')}
    monkeypatch.setattr('shilun.review.board_data.BoardResearchData.pools',
                        lambda self, days, kind='up', **kw: {d: pools[kind][d] for d in days if d in pools[kind]})
    return store, pools


def check(store, key):
    return next(c for c in optional.inspect_optional_sources(store, DAY) if c['key'] == key)


def test_optional_checks_have_independent_nonblocking_contract_and_no_writes(setup):
    store, _ = setup
    rows = optional.inspect_optional_sources(store, DAY)
    assert {c['key'] for c in rows} == set(optional.OPTIONAL_KEYS)
    assert all(c['date'] == DAY and c['blocking'] is False and c['consumers'] == ['review'] for c in rows)
    assert all(len(c['revision']) == 24 for c in rows)
    assert all(col.writes == 0 for col in store.collections.values())


def test_auction_requires_both_dates_and_keeps_real_zero_amount(setup, monkeypatch):
    store, _ = setup
    for day in DAYS:
        store.collection(AUCTIONS).rows.append({'date': day, 'source': SOURCE, 'schema': VERSION,
                                                'amounts': {CODE: 0}, 'fetched_at': 'old'})
    result = check(store, 'first_board_auction')
    assert result['ready']
    fingerprint = result['revision']
    store.collection(AUCTIONS).rows[0]['fetched_at'] = 'new'
    assert check(store, 'first_board_auction')['revision'] == fingerprint
    sync = Mock(side_effect=AssertionError('must not fetch complete input'))
    monkeypatch.setattr('shilun.review.board_auction.sync_first_board_auction', sync)
    assert optional.prepare_optional_source(store, DAY, 'first_board_auction')['cached']
    sync.assert_not_called()
    store.collection(AUCTIONS).rows[0]['date'] = '2026-10-30'
    assert not check(store, 'first_board_auction')['ready']


def test_explicit_empty_first_board_pool_is_ready_but_missing_pool_is_unknown(setup):
    store, pools = setup
    for day in DAYS:
        pools['up'][day]['stocks'] = []
    assert check(store, 'first_board_auction')['ready']
    del pools['up'][DAY]
    assert not check(store, 'first_board_auction')['ready']


def test_suspension_empty_range_requires_verified_receipt_and_then_skips_fetch(setup, monkeypatch):
    store, _ = setup
    assert not check(store, 'suspensions')['ready']  # Absence is not evidence for no suspensions.
    from shilun.review.suspensions import normalize_suspension_source
    _, report = normalize_suspension_source([], PRIOR, DAY)
    sync = Mock(return_value={**report, 'verified': True, 'readback_revision': report['source_revision']})
    monkeypatch.setattr('shilun.review.suspensions.sync_suspensions', sync)
    assert optional.prepare_optional_source(store, DAY, 'suspensions')['ready']
    assert check(store, 'suspensions')['ready']
    optional.prepare_optional_source(store, DAY, 'suspensions')
    sync.assert_called_once_with(store, PRIOR, DAY)
    store.collection(SUSPENSIONS).rows.append({'date': DAY, 'ticker': TICKER, 'suspend_type': 'S'})
    assert not check(store, 'suspensions')['ready']  # Changed facts invalidate the range receipt.


def test_suspension_provider_receipt_cannot_hide_wrong_window_or_missing_archive(setup, monkeypatch):
    store, _ = setup
    monkeypatch.setattr('shilun.review.suspensions.sync_suspensions',
                        Mock(return_value={'from': PRIOR, 'to': DAY, 'rows': 1}))
    result = optional.prepare_optional_source(store, DAY, 'suspensions')
    assert not result['ready'] and result['error_type'] == 'ValueError'
    assert '实际读回' in result['error_message']
    assert store.collection(optional.RECEIPTS).rows[0]['status'] == 'unavailable'


def test_cohort_prices_use_actual_bars_references_and_explicit_suspensions(setup):
    store, pools = setup
    for day in DAYS:
        store.collection('market_daily_bars').rows.append({'date': day, 'ticker': TICKER, 'close': 10, 'pre_close': 9})
    assert check(store, 'board_cohort_prices')['ready']
    store.collection('market_daily_bars').rows[-1]['pre_close'] = None
    result = check(store, 'board_cohort_prices')
    assert not result['ready'] and result['unresolved'][0]['reason'] == '次日收益计价基准待补齐'
    store.collection('daily_price_limits').rows.append({'trade_date': DAY.replace('-', ''), 'ticker': TICKER, 'pre_close': 9})
    assert check(store, 'board_cohort_prices')['ready']
    store.collection('market_daily_bars').rows.pop()
    store.collection(SUSPENSIONS).rows.append({'date': DAY, 'ticker': TICKER, 'suspend_type': 'S', 'suspend_timing': None})
    assert check(store, 'board_cohort_prices')['ready']
    del pools['broken'][PRIOR]
    assert not check(store, 'board_cohort_prices')['ready']  # Missing pool is never an empty cohort.


def test_successful_price_attempt_cannot_clear_unresolved_prices(setup, monkeypatch):
    store, _ = setup
    monkeypatch.setattr(optional, '_RecordingClient', lambda: Mock(errors=[]))
    sync = Mock(return_value={'date': DAY, 'status': 'ok', 'unresolved': []})
    monkeypatch.setattr('shilun.review.board_price_quality.sync_board_price_gaps', sync)
    result = optional.prepare_optional_source(store, DAY, 'board_cohort_prices')
    assert not result['ready'] and result['error_type'] == 'ValueError'
    assert result['unresolved']
    sync.assert_called_once()


def test_no_local_heat_cache_is_not_applicable_and_never_calls_source(setup, monkeypatch):
    store, _ = setup
    sync = Mock(side_effect=AssertionError('optional migration cache must not become a provider dependency'))
    monkeypatch.setattr('shilun.review.dated_pools.sync_heat_summaries', sync)
    result = optional.prepare_optional_source(store, DAY, 'heat_summary')
    assert result['ready'] and result['applicability'] == 'not_applicable' and result['cached']
    sync.assert_not_called()


def test_wrong_date_heat_cache_stays_missing_with_specific_validation_error(setup, monkeypatch):
    store, _ = setup
    optional._heat_path(DAY).write_text(json.dumps({'date': PRIOR, 'schema': 1, 'source': 'akshare_zt_pool',
                                                  'summary': {'limit_up': 0, 'highest_consec': 0}}))
    sync = Mock()
    monkeypatch.setattr('shilun.review.dated_pools.sync_heat_summaries', sync)
    result = optional.prepare_optional_source(store, DAY, 'heat_summary')
    assert not result['ready'] and result['error_type'] == 'ValueError'
    assert '日期' in result['error_message']
    sync.assert_not_called()


def test_lhb_empty_verified_lists_are_ready_and_wrong_date_does_not_count(setup, monkeypatch):
    store, _ = setup
    store.collection(LHB).rows.append({'date': DAY, 'status': 'ready', 'summaries': [], 'seats': [],
                                      'source': 'tushare_top_list_top_inst'})
    assert check(store, 'lhb')['ready']
    sync = Mock()
    monkeypatch.setattr('shilun.review.strength_review.sync_lhb', sync)
    assert optional.prepare_optional_source(store, DAY, 'lhb')['cached']
    sync.assert_not_called()
    store.collection(LHB).rows[0]['summaries'] = [{'trade_date': '20261030'}]
    assert not check(store, 'lhb')['ready']


def test_lhb_invalid_ready_archive_is_revalidated_and_repaired(setup, monkeypatch):
    import pandas as pd
    store, _ = setup
    store.collection(LHB).rows.append({'date': DAY, 'status': 'ready', 'summaries': [{'trade_date': '20261030'}],
                                      'seats': [], 'source': 'tushare_top_list_top_inst'})
    client = Mock()
    client._call_api.return_value = pd.DataFrame()
    monkeypatch.setattr('shilun.data.providers.tushare_provider.TushareConfig.from_env', lambda: None)
    monkeypatch.setattr('shilun.data.providers.tushare_provider.TushareDailyClient', lambda *a, **kw: client)

    def replace(query, doc, upsert=False):
        store.collection(LHB).rows = [doc]
    store.collection(LHB).replace_one = replace
    assert optional.prepare_optional_source(store, DAY, 'lhb')['ready']
    assert client._call_api.call_count == 2


def test_auction_provider_error_keeps_useful_sanitized_message(setup, monkeypatch):
    store, _ = setup
    client = Mock()
    client._call_api.side_effect = TimeoutError('auction timeout token=abc-private https://provider.invalid/private')
    monkeypatch.setattr('shilun.data.providers.tushare_provider.TushareConfig.from_env', lambda: None)
    monkeypatch.setattr('shilun.data.providers.tushare_provider.TushareDailyClient', lambda *a, **kw: client)
    result = optional.prepare_optional_source(store, DAY, 'first_board_auction')
    assert not result['ready'] and result['error_type'] == 'TimeoutError'
    assert 'auction timeout' in result['error_message']
    assert 'abc-private' not in str(result) and 'provider.invalid' not in str(result)


def test_wrong_calendar_day_cannot_be_treated_as_zero_sample(setup, monkeypatch):
    store, _ = setup
    monkeypatch.setattr(optional, '_sessions', lambda *a: [PRIOR])
    assert not check(store, 'first_board_auction')['ready']
    assert not check(store, 'board_cohort_prices')['ready']


def test_unknown_key_fails_without_provider_calls(setup):
    store, _ = setup
    with pytest.raises(ValueError, match='Unknown optional source'):
        optional.prepare_optional_source(store, DAY, 'retired_topic_ranker')


def test_one_unreadable_optional_archive_does_not_abort_other_checks(setup, monkeypatch):
    store, _ = setup
    monkeypatch.setattr(optional, '_heat_path', Mock(side_effect=OSError('cache permission denied token=private')))
    rows = optional.inspect_optional_sources(store, DAY)
    assert len(rows) == 5
    heat = next(r for r in rows if r['key'] == 'heat_summary')
    assert not heat['ready'] and heat['error_type'] == 'OSError'
    assert 'permission denied' in heat['reason']
    assert 'private' not in str(heat)
    assert all(r['blocking'] is False for r in rows)


def suspension_event(kind='S', timing=None, day='20261103', ticker=TICKER):
    return {'trade_date': day, 'ts_code': ticker, 'suspend_type': kind, 'suspend_timing': timing}


def suspension_client(rows):
    import pandas as pd
    client = Mock()
    client._call_api.return_value = pd.DataFrame(rows)
    return client


def test_suspension_events_are_deduplicated_and_mixed_sr_never_means_full_day_s(setup):
    from shilun.review.suspensions import sync_suspensions, load_suspensions
    store, _ = setup
    client = suspension_client([suspension_event(), suspension_event(), suspension_event('R', '13:00-15:00')])
    report = sync_suspensions(store, PRIOR, DAY, client=client)
    assert report['verified']
    assert (report['raw_count'], report['valid_row_count'], report['unique_event_count'],
            report['duplicate_count'], report['stock_day_count'], report['written_count'],
            report['readback_count']) == (3, 3, 2, 1, 1, 1, 1)
    assert report['source_revision'] == report['readback_revision']
    saved = store.collection(SUSPENSIONS).rows[0]
    assert saved['suspend_type'] == 'R' and saved['full_day_suspended'] is False
    assert len(saved['source_events']) == 2
    assert load_suspensions(store, DAYS) == {}
    client._call_api.assert_called_once_with('suspend_d', start_date='20261102', end_date='20261103')


def test_suspension_duplicates_can_be_verified_without_matching_raw_to_cached_count(setup):
    from shilun.review.suspensions import sync_suspensions, load_suspensions
    store, _ = setup
    report = sync_suspensions(store, PRIOR, DAY,
                              client=suspension_client([suspension_event(), suspension_event()]))
    assert report['verified'] and report['raw_count'] == 2 and report['readback_count'] == 1
    assert load_suspensions(store, DAYS) == {TICKER: {DAY}}


def test_suspension_extra_old_cache_is_retained_and_window_stays_unverified(setup):
    from shilun.review.suspensions import sync_suspensions, SuspensionSourceError
    store, _ = setup
    extra = {'date': PRIOR, 'ticker': '600002.SH', 'suspend_type': 'S', 'suspend_timing': None}
    store.collection(SUSPENSIONS).rows.append(deepcopy(extra))
    with pytest.raises(SuspensionSourceError) as caught:
        sync_suspensions(store, PRIOR, DAY, client=suspension_client([suspension_event()]))
    report = caught.value.report
    assert report['verified'] is False and report['extra_cached_count'] == 1
    assert report['raw_count'] == report['unique_event_count'] == report['stock_day_count'] == 1
    assert report['written_count'] == 1 and report['readback_count'] == 2
    assert report['extra_cached_examples'] == [{'date': PRIOR, 'ticker': '600002.SH'}]
    assert any('未自动失效或删除' in reason for reason in report['unconfirmed_reasons'])
    assert extra in store.collection(SUSPENSIONS).rows


@pytest.mark.parametrize('invalid', [suspension_event(day='20261104'), suspension_event(kind='X'),
                                      suspension_event(ticker='invalid'), suspension_event(day='20261340')])
def test_invalid_suspension_source_does_not_publish_partial_window(setup, invalid):
    from shilun.review.suspensions import sync_suspensions, SuspensionSourceError
    store, _ = setup
    with pytest.raises(SuspensionSourceError) as caught:
        sync_suspensions(store, PRIOR, DAY, client=suspension_client([suspension_event(), invalid]))
    report = caught.value.report
    assert report['raw_count'] == 2 and report['valid_row_count'] == report['unique_event_count'] == 1
    assert report['invalid_count'] == 1 and report['written_count'] == 0
    assert store.collection(SUSPENSIONS).rows == []


def test_suspension_truncation_limit_is_explicit_and_never_ready(setup):
    from shilun.review.suspensions import sync_suspensions, SuspensionSourceError, SOURCE_LIMIT
    store, _ = setup
    with pytest.raises(SuspensionSourceError) as caught:
        sync_suspensions(store, PRIOR, DAY, client=suspension_client([suspension_event()] * SOURCE_LIMIT))
    assert caught.value.report['raw_count'] == SOURCE_LIMIT
    assert caught.value.report['written_count'] == 0
    assert '接口上限' in str(caught.value)


def test_optional_suspension_error_preserves_normalized_source_report(setup, monkeypatch):
    from shilun.review.suspensions import sync_suspensions
    store, _ = setup
    store.collection(SUSPENSIONS).rows.append({'date': PRIOR, 'ticker': '600002.SH', 'suspend_type': 'R'})
    client = suspension_client([suspension_event(), suspension_event()])
    monkeypatch.setattr('shilun.review.suspensions.sync_suspensions',
                        lambda store, start, end: sync_suspensions(store, start, end, client=client))
    result = optional.prepare_optional_source(store, DAY, 'suspensions')
    assert not result['ready'] and '旧缓存' in result['error_message']
    assert len(result['reports']) == 1
    assert result['reports'][0]['raw_count'] == 2
    assert result['reports'][0]['unique_event_count'] == 1
    assert result['reports'][0]['extra_cached_count'] == 1
    saved = store.collection(optional.RECEIPTS).rows[0]
    assert saved['status'] == 'unavailable'
    assert saved['source_report']['raw_count'] == 2
    assert saved['source_report']['unique_event_count'] == saved['source_report']['extra_cached_count'] == 1
    assert saved['source_report']['verified'] is False
    assert saved['source_report']['adapter_verified'] is False
    assert not check(store, 'suspensions')['ready']


def test_optional_suspension_valid_normalized_report_creates_verified_window(setup, monkeypatch):
    from shilun.review.suspensions import sync_suspensions
    store, _ = setup
    client = suspension_client([suspension_event(), suspension_event()])
    monkeypatch.setattr('shilun.review.suspensions.sync_suspensions',
                        lambda store, start, end: sync_suspensions(store, start, end, client=client))
    result = optional.prepare_optional_source(store, DAY, 'suspensions')
    assert result['ready']
    assert check(store, 'suspensions')['ready']
    receipt = store.collection(optional.RECEIPTS).rows[0]
    assert receipt['source_report']['raw_count'] == 2
    assert receipt['source_report']['stock_day_count'] == receipt['row_count'] == 1


def test_suspension_failed_readback_retains_attempt_counts(setup):
    from shilun.review.suspensions import sync_suspensions, SuspensionSourceError
    store, _ = setup
    store.collection(SUSPENSIONS).find = Mock(side_effect=TimeoutError('readback timeout password=private'))
    with pytest.raises(SuspensionSourceError) as caught:
        sync_suspensions(store, PRIOR, DAY, client=suspension_client([suspension_event()]))
    report = caught.value.report
    assert report['raw_count'] == report['written_count'] == 1
    assert report['verified'] is False
    assert report['error_type'] == 'TimeoutError' and 'readback timeout' in report['error_message']
    assert 'private' not in str(report)


def test_suspension_producer_protocol_invalidates_only_its_own_check(setup, monkeypatch):
    store, _ = setup
    before = {r['key']: r['revision'] for r in optional.inspect_optional_sources(store, DAY)}
    monkeypatch.setattr(optional, 'SUSPENSION_PROTOCOL', 'verified_events_v3')
    after = {r['key']: r['revision'] for r in optional.inspect_optional_sources(store, DAY)}
    assert [key for key in before if before[key] != after[key]] == ['suspensions']
