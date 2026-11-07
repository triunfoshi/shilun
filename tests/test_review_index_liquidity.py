"""Independent arithmetic and missing-data checks for index-review chart facts."""
from datetime import date, timedelta
from unittest.mock import Mock, patch

import pytest

from shilun.review.index_liquidity import breadth_distribution, market_rows, volume_facts
from shilun.review.market_facts import build_market_facts, load_market_facts, amount_pipeline


TICKERS = ('600001.SH', '000001.SZ', '920001.BJ')


def days(n=61):
    return [(date(2026, 6, 1) + timedelta(days=i)).isoformat() for i in range(n)]


def history(dates):
    return {d: {t: dict(close=10, pre_close=10, amount=a*100_000) for t, a in zip(TICKERS, (5000, 4000, 1000))} for d in dates}


def test_distribution_is_mutually_exclusive_and_respects_actual_limit_price():
    # Different limit regimes: a 10% return can be a limit-up or a normal bar.
    samples = [(11, 11, 9), (11, 12, 8), (10.9, 12, 8), (10.1, 12, 8),
               (10, 11, 9), (9.9, 11, 9), (9.1, 11, 9), (9, 11, 9), (8.5, 12, 8)]
    current, prices = {}, {}
    for i, (close, up, down) in enumerate(samples):
        t = f'{600000+i}.SH'
        current[t] = dict(close=close, pre_close=10)
        prices[t] = dict(up_limit=up, down_limit=down)
    result = breadth_distribution(current, {}, prices)
    bins = {r['key']: r['count'] for r in result['bins']}
    assert len(result['bins']) == 23
    assert result['status'] == 'ready' and result['total'] == len(samples)
    assert sum(bins.values()) == len(samples)
    assert bins['limit_up'] == bins['limit_down'] == bins['up_gt9'] == 1
    assert bins['up_8_9'] == bins['up_0_1'] == bins['flat'] == 1
    assert bins['down_0_1'] == bins['down_8_9'] == bins['down_gt9'] == 1


def test_distribution_uses_dated_ex_right_reference_and_exposes_missing_limits():
    result = breadth_distribution({'600001.SH': {'close': 11, 'pre_close': 10},
        '600002.SH': {'close': float('nan')}}, {'600001.SH': {'close': 12}})
    assert result['status'] == 'partial' and result['missing_count'] == 1
    assert result['bins'][1]['count'] == 1
    assert result['limit_classification_ready'] is False
    assert '缺失' in result['note']


def test_exchange_thresholds_use_only_preceding_twenty_sessions_and_sum_to_1000():
    dates = days(21)
    bars = history(dates)
    current = bars[dates[-1]]
    current[TICKERS[0]]['amount'] = 200_000*100_000
    out = {r['key']: r for r in market_rows(current, bars[dates[-2]], sessions=dates, bars_by_date=bars)}
    assert [out[m]['expansion_threshold_yi'] for m in ('SH','SZ','BJ')] == [500,400,100]
    assert sum(out[m]['expansion_threshold_yi'] for m in ('SH','SZ','BJ')) == 1000
    assert out['SH']['expanded'] is True and out['SZ']['expanded'] is False
    assert out['ALL']['expanded'] is True and out['ALL']['threshold_basis_days'] == 20
    assert out['ALL']['amount_yi'] == 205000
    assert out['SH']['threshold_share'] == .5


def test_missing_history_keeps_exchange_threshold_unknown_but_not_full_market_rule():
    dates = days(21)
    bars = history(dates)
    del bars[dates[5]][TICKERS[2]]
    out = market_rows(bars[dates[-1]], bars[dates[-2]], sessions=dates, bars_by_date=bars)
    assert all(r['expansion_status'] == 'unknown' and r['expanded'] is None for r in out[:3])
    assert all(r['threshold_basis_days'] == 19 for r in out[:3])
    assert out[3]['expansion_threshold_yi'] == 1000 and out[3]['expanded'] is False


def test_invalid_current_amount_is_never_a_zero_or_a_conclusive_volume_change():
    dates = days(21)
    bars = history(dates)
    bars[dates[-1]][TICKERS[0]]['amount'] = None
    out = market_rows(bars[dates[-1]], bars[dates[-2]], sessions=dates, bars_by_date=bars)
    assert out[0]['amount_yi'] is None and out[0]['amount_change_yi'] is None
    assert out[0]['expanded'] is None and out[3]['amount_yi'] is None
    assert out[1]['amount_yi'] == 4000


def test_volume_means_require_complete_calendar_window_and_do_not_skip_missing_day():
    dates = days()
    bars = history(dates)
    assert volume_facts(dates, bars)['ma60_yi'] == 10000
    # Still 60 available observations in the 61-date series, but one of the
    # final 60 is absent, so a 60-day mean cannot be manufactured by backfilling.
    del bars[dates[4]]
    result = volume_facts(dates, bars)
    assert len(result['series']) == 61 and result['series'][4]['amount_yi'] is None
    assert result['ma60_yi'] is None and result['ma60_days'] == 59
    assert result['ma20_yi'] == 10000 and result['ma20_days'] == 20
    short = volume_facts(dates[-19:], bars)
    assert short['ma20_yi'] is None and short['ma20_days'] == 19


def test_supplement_aggregates_history_server_side_and_never_writes_review():
    dates = days(21)
    bars = history(dates)
    collections = {name: Mock() for name in ('stock_basic','market_daily_bars','daily_price_limits')}
    collections['stock_basic'].find.return_value = [{'ts_code':t} for t in TICKERS]
    aggregate = [dict(_id={'date':d, 'market':t.split('.')[1]}, total_n=1, valid_n=1, amount=row['amount'])
                 for d, rows in bars.items() for t, row in rows.items()]
    collections['market_daily_bars'].aggregate.return_value = aggregate
    collections['market_daily_bars'].find.return_value = [{**r, 'ticker':t, 'date':d} for d in dates[-2:] for t,r in bars[d].items()]
    collections['daily_price_limits'].find.return_value = [{'ticker':t, 'up_limit':11, 'down_limit':9} for t in TICKERS]
    store = Mock(); store.collection.side_effect = collections.__getitem__
    with patch('shilun.review.market_facts.load_review_sessions', return_value=dates):
        result = build_market_facts(store, dates[-1])
    assert result['market']['volume']['ma20_yi'] == 10000
    assert result['market']['breadth']['markets'][0]['expansion_threshold_yi'] == 500
    assert result['analysis_date'] == dates[-1]
    request = collections['market_daily_bars'].find.call_args.args[0]
    assert request['date']['$in'] == dates[-2:]
    assert collections['market_daily_bars'].aggregate.call_args.kwargs['maxTimeMS'] == 45000
    assert all('daily_review_snapshots' not in c.args for c in store.collection.call_args_list)
    for col in collections.values():
        col.update_one.assert_not_called(); col.replace_one.assert_not_called()


def test_market_facts_cache_skips_database_and_uses_separate_content_addressed_archive(tmp_path):
    day = '2026-10-30'
    from hashlib import sha256
    import json
    revision = sha256(json.dumps({}, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    payload = {'analysis_date':day, 'revision':'index-market-facts-v1', 'source_revision':revision,
        'built_at':'2026-11-02T00:00:00+00:00', 'market':{}}
    from datetime import datetime, timezone
    payload['built_at'] = datetime.now(timezone.utc).isoformat()
    with patch('shilun.review.market_facts.CACHE_ROOT', tmp_path), \
         patch('shilun.review.market_facts.build_market_facts', return_value=payload) as build:
        assert load_market_facts(Mock(), day) == payload
        assert (tmp_path/f'{day}-{revision}.json').is_file()
        store = Mock()
        assert load_market_facts(store, day) == payload
        store.collection.assert_not_called()
        build.assert_called_once()
        load_market_facts(store, day, refresh=True)
        assert build.call_count == 2


def test_dashboard_cache_supplement_preserves_stale_same_date_facts_and_rejects_corruption(tmp_path):
    from datetime import datetime, timezone
    from hashlib import sha256
    import json
    from shilun.review.market_facts import cached, cached_supplement, cache_path, REVISION
    market = {'volume': {'current_yi': 18000}, 'breadth': {'markets': []}}
    payload = {'status': 'ready', 'analysis_date': '2026-10-30', 'revision': REVISION,
        'built_at': (datetime.now(timezone.utc) - timedelta(minutes=20)).isoformat(),
        'market': market,
        'source_revision': sha256(json.dumps(market, ensure_ascii=False, sort_keys=True).encode()).hexdigest()}
    with patch('shilun.review.market_facts.CACHE_ROOT', tmp_path):
        path = cache_path('2026-10-30')
        path.write_text(json.dumps(payload))
        assert cached('2026-10-30') is None
        supplement = cached_supplement('2026-10-30')
        assert supplement['cache_status'] == 'stale'
        assert supplement['cache_age_seconds'] >= 1200
        assert supplement['market'] == market
        assert 'cache_status' not in json.loads(path.read_text())
        payload['analysis_date'] = '2026-10-29'
        path.write_text(json.dumps(payload))
        assert cached_supplement('2026-10-30') is None
        payload['analysis_date'] = '2026-10-30'
        payload['market']['volume']['current_yi'] = 90000
        path.write_text(json.dumps(payload))
        assert cached_supplement('2026-10-30') is None
        payload['market'] = {'volume': {'current_yi': float('nan')}}
        path.write_text(json.dumps(payload))
        assert cached_supplement('2026-10-30') is None


def test_dashboard_attaches_cached_facts_without_replacing_frozen_market():
    from fastapi import FastAPI
    from shilun.api import review
    from tests.test_review_remote import LocalASGIClient
    app = FastAPI(); app.include_router(review.router)
    frozen = {'analysis_date': '2026-10-30', 'market': {'volume': {'current_yi': 100}},
        'action': {'headline': '原冻结结论'}}
    supplement = {'analysis_date': '2026-10-30', 'cache_status': 'stale',
        'market': {'volume': {'current_yi': 18000}}}
    store, service = Mock(), Mock()
    service.get.return_value = frozen
    with patch.object(review, '_open_store', return_value=store), \
         patch.object(review, 'DailyReviewService', return_value=service), \
         patch('shilun.review.market_facts.cached_supplement', return_value=supplement) as read:
        response = LocalASGIClient(app).get('/api/v1/review/dashboard?date=2026-10-30')
    assert response.status_code == 200
    result = response.json()
    assert result['market_facts_supplement'] == supplement
    assert result['market']['volume']['current_yi'] == 100
    assert result['action'] == frozen['action']
    assert 'market_facts_supplement' not in frozen
    read.assert_called_once_with('2026-10-30')
    store.collection.assert_not_called()
    store.close.assert_called_once()


def test_market_facts_endpoint_validates_date_and_hides_database_connection_details():
    from fastapi import FastAPI
    from pymongo.errors import NetworkTimeout
    from shilun.api import review
    from tests.test_review_remote import LocalASGIClient
    app = FastAPI(); app.include_router(review.router)
    client, store = LocalASGIClient(app), Mock()
    with patch('shilun.review.market_facts.cached', return_value=None), \
         patch.object(review, '_open_store', return_value=store), \
         patch('shilun.review.market_facts.load_market_facts', side_effect=NetworkTimeout('secret-uri')):
        assert client.get('/api/v1/review/market-facts?date=bad').status_code == 422
        result = client.get('/api/v1/review/market-facts?date=2026-10-30')
    assert result.status_code == 503 and 'secret-uri' not in result.text
    store.close.assert_called_once()
