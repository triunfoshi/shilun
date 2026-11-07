"""Same-session return repair preserves the independently verified limit-down identity."""
from copy import deepcopy
from unittest.mock import Mock

from shilun.review.live_emotion import reconcile_settled_downside


DAY, PRIOR = '2026-11-02', '2026-10-30'


def store_for(stocks, bars):
    store = Mock()
    review, market = Mock(), Mock()
    review.find_one.return_value = {'previous_trade_date': PRIOR, 'sentiment': {'downside_risk': {
        'count_ready': True, 'limit_down_count': len(stocks), 'stocks': stocks}}}
    market.find.return_value = bars
    store.collection.side_effect = lambda name: market if name == 'market_daily_bars' else review
    return store, market


def payload(raw=None):
    return {'available': True, 'snapshot_mode': 'settled', 'snapshot_date': DAY,
            'dt_count': len(raw or []), 'raw_pool_counts': {'dt_count': len(raw or [])},
            'down_stocks': raw or []}


def test_percent_uses_same_date_reference_not_a_fixed_board_limit():
    stocks = [{'ticker': '600001.SH', 'name': 'ST甲'}, {'ticker': '688001.SH', 'name': '科创乙'},
              {'ticker': '000001.SZ', 'name': '主板丙'}]
    bars = [
        {'ticker': '600001.SH', 'date': DAY, 'close': 9.51, 'pre_close': 10},
        {'ticker': '688001.SH', 'date': DAY, 'close': 40, 'pre_close': 50},
        # Prefer the source ex-right reference over yesterday's unadjusted price.
        {'ticker': '688001.SH', 'date': PRIOR, 'close': 100},
        {'ticker': '000001.SZ', 'date': DAY, 'close': 27, 'pre_close': None},
        {'ticker': '000001.SZ', 'date': PRIOR, 'close': 30},
    ]
    store, market = store_for(stocks, bars)
    raw = payload()
    before = deepcopy(raw)
    result = reconcile_settled_downside(raw, store)
    by_code = {r['code']: r for r in result['down_stocks']}
    assert by_code['600001']['pct'] == -4.9
    assert by_code['688001']['pct'] == -20
    assert by_code['688001']['pct_source'] == 'daily_pre_close'
    assert by_code['000001']['pct'] == -10
    assert by_code['000001']['pct_source'] == 'previous_session_close'
    assert all(r['limit_status'] == 'confirmed_limit_down' for r in by_code.values())
    assert raw == before
    query = market.find.call_args.args[0]
    assert query['date']['$in'] == [DAY, PRIOR]
    assert set(query['ticker']['$in']) == {r['ticker'] for r in stocks}


def test_missing_quotes_preserve_pool_percentage_and_confirmed_identity():
    stocks = [{'ticker': '600001.SH', 'name': '甲'}, {'ticker': '000001.SZ', 'name': '乙'}]
    store, _ = store_for(stocks, [])
    result = reconcile_settled_downside(payload([{'code': '600001', 'pct': -9.96}]), store)
    by_code = {r['code']: r for r in result['down_stocks']}
    assert by_code['600001']['pct'] == -9.96
    assert by_code['600001']['pct_source'] == 'eastmoney_dated_pool'
    assert by_code['000001']['pct'] is None
    assert by_code['000001']['limit_status'] == 'confirmed_limit_down'
    assert by_code['000001']['pct_missing_reason']
    assert result['dt_count'] == 2
    assert result['raw_pool_counts']['dt_count'] == 1


def test_duplicate_or_nonfinite_prices_never_become_a_calculated_return():
    stocks = [{'ticker': '600001.SH', 'name': '甲'}, {'ticker': '000001.SZ', 'name': '乙'}]
    bars = [
        {'ticker': '600001.SH', 'date': DAY, 'close': 9, 'pre_close': 10},
        {'ticker': '600001.SH', 'date': DAY, 'close': 9.1, 'pre_close': 10},
        {'ticker': '000001.SZ', 'date': DAY, 'close': float('nan'), 'pre_close': 10},
    ]
    store, _ = store_for(stocks, bars)
    result = reconcile_settled_downside(payload(), store)
    assert all(r['pct'] is None for r in result['down_stocks'])


def test_live_period_never_reads_settled_bars():
    store = Mock()
    raw = {**payload(), 'snapshot_mode': 'live'}
    assert reconcile_settled_downside(raw, store) is raw
    store.collection.assert_not_called()


def test_quote_connection_failure_keeps_the_already_verified_roster():
    from pymongo.errors import AutoReconnect
    stocks = [{'ticker': '600001.SH', 'name': '甲'}, {'ticker': '000001.SZ', 'name': '乙'}]
    store, market = store_for(stocks, [])
    market.find.side_effect = AutoReconnect('test unavailable')
    result = reconcile_settled_downside(payload([{'code': '600001', 'pct': -10.01}]), store)
    assert result['dt_count'] == 2
    assert result['down_stocks'][0]['pct'] == -10.01
    assert result['down_stocks'][1]['pct'] is None
    assert '暂不可用' in result['down_stocks'][1]['pct_missing_reason']
