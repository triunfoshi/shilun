"""Synthetic fixtures: dated cohorts, missingness and risk denominators."""
from datetime import date, timedelta

import pytest

from shilun.review.dated_pools import normalize_previous, fetch_previous, sync_review_history
from shilun.review.theme_evidence import front_support, theme_evidence, downside_risk, limit_up_resonance, price_limit_hit


def bar(close=10, high=11, low=9, amount=100, volume=100):
    return dict(close=close, high=high, low=low, amount=amount, volume=volume)


def pool(ticker, level=1, theme='方向A', **kwargs):
    return dict(ticker=ticker, name=ticker, consecutive_count=level, sector_name=theme, **kwargs)


def risk(**kwargs):
    args = dict(analysis_date='2026-09-09', limit_down_tickers=set(), current_bars={}, previous_bars={},
                bars_by_ticker={}, bars_by_date={}, price_limits_by_date={}, basic={}, indices=[])
    return downside_risk(**(args | kwargs))


def test_missing_count_is_not_zero_or_safe():
    result = risk(count_ready=False)
    assert result['limit_down_count'] is None
    assert result['risk_type'] == 'pending'
    assert result['penalty'] is None
    assert all(v is None for v in result['position_counts'].values())


def test_verified_zero_has_zero_positions_and_inapplicable_concentration():
    result = risk(count_ready=True)
    assert result['limit_down_count'] == 0
    assert result['penalty'] == 0
    assert result['position_counts'] == dict(high=0, middle=0, low=0, unknown=0)
    assert result['top1_share'] is None


def test_missing_taxonomy_is_not_one_concentrated_industry():
    result = risk(limit_down_tickers={'a', 'b', 'c'},
        basic={t: {'sector': '分类待补齐', 'classification_status': 'taxonomy_missing'}
               for t in ('a', 'b', 'c')},
        current_bars={'up': bar(11)}, previous_bars={'up': bar(10)})
    assert result['top1_share'] is None
    assert result['industry_clusters'] == []
    assert result['risk_type'] == 'pending'
    assert result['penalty'] is None


def test_explicit_zero_bound_is_not_a_missing_price():
    assert price_limit_hit(26.88, {'up_limit': 99999.99, 'down_limit': 0}) is False
    assert price_limit_hit(26.88, {'up_limit': 99999.99}) is None
    assert price_limit_hit(None, {'up_limit': 99999.99, 'down_limit': 0}) is None
    assert price_limit_hit(9, {'up_limit': 11, 'down_limit': 9}) is True


def test_fixed_yesterday_cohort_keeps_failed_leader_and_excludes_new_winner():
    result = front_support([pool('a', 4), pool('b', 1)], {'a': bar(9, 10, 8), 'b': bar(11), 'new': bar(11)},
                           {'a': bar(), 'b': bar()}, current_rows=[pool('new', 1), pool('b', 2)], theme='方向A')
    assert {r['ticker'] for r in result['rows']} == {'a', 'b'}
    assert result['rows'][0]['front'] is True
    assert result['front_median_return'] == -.1
    assert result['back_median_return'] == .1
    assert result['front_back_spread'] == -.2


def test_one_price_and_missing_bars_never_count_as_weak_support():
    result = front_support([pool('a', 2), pool('b', 2), pool('c', 2)],
        {'a': bar(11, 11, 11), 'b': bar(10.5)}, {'a': bar(), 'b': bar(), 'c': bar()},
        current_rows=[pool('a', 3)])
    assert result['locked_count'] == 1
    assert result['missing_count'] == 1
    assert result['observable_count'] == 1
    assert result['support_rate'] == 1


def test_promotion_separates_levels_and_excludes_previous_one_price():
    result = front_support([pool('a', 1), pool('b', 1), pool('c', 2)],
        {'a': bar(11), 'b': bar(11), 'c': bar(9)}, {'a': bar(), 'b': bar(10, 10, 10), 'c': bar()},
        current_rows=[pool('a', 2), pool('b', 2)])
    assert result['promotions'][0]['sample'] == 1
    assert result['promotions'][0]['rate'] == 1
    assert result['promotions'][1]['rate'] == 0
    pending = front_support([pool('a')], {'a': bar()}, {'a': bar()}, current_rows=None)
    assert pending['promotions'][0]['rate'] is None


def test_null_preclose_falls_back_to_previous_close():
    result = front_support([pool('a')], {'a': bar(11) | {'pre_close': None}}, {'a': bar()}, current_rows=[])
    assert result['unranked_median_return'] == .1


def test_equal_first_boards_are_unranked_not_all_front_or_arbitrary_back():
    result = front_support([pool('a'), pool('b')], {'a':bar(11), 'b':bar(9)}, {'a':bar(), 'b':bar()}, current_rows=[])
    assert result['front_count'] == result['back_count'] == 0
    assert result['unranked_count'] == 2
    assert result['front_median_return'] is None and result['back_median_return'] is None
    assert result['unranked_median_return'] == 0
    assert all(r['front'] is None for r in result['rows'])


def test_open_close_limit_and_zero_breaks_prove_lock_without_high_low():
    result = front_support([pool('a',2)], {'a':{'open':11,'close':11,'pre_close':10}}, {},
        current_rows=[pool('a',3,broken_count=0)], current_price_limits={'a':{'up_limit':11}})
    assert result['locked_count'] == 1 and result['missing_count'] == 0
    assert result['observable_count'] == 0  # A lock never proves tradable support.
    for breaks in [None,1]:
        pending = front_support([pool('a',2)], {'a':{'open':11,'close':11,'pre_close':10}}, {},
            current_rows=[pool('a',3,broken_count=breaks)], current_price_limits={'a':{'up_limit':11}})
        assert pending['locked_count'] == 0 and pending['missing_count'] == 1


def test_seven_sessions_not_seven_available_pools_and_proxy_cannot_prove_capital():
    sessions = [f'2026-09-{i:02d}' for i in range(1, 8)]
    pools = {'2026-07-01': [pool('old', 9)], sessions[-1]: [pool('a', 1, broken_count=0)]}
    result = theme_evidence(analysis_date=sessions[-1], limit_rows_by_date=pools, sessions=sessions,
        current_bars={'a': bar(amount=200)}, previous_bars={'a': bar(amount=100)},
        bars_by_ticker={}, moneyflow={}, basic={'a': {'sector': '不同分类'}})
    assert result['themes'] == []  # A stale nine-board leader cannot qualify the current window.


def test_three_parallel_directions_without_a_unique_main_theme():
    today = [pool(f'{i}-{j}', level, f'方向{i}', broken_count=0) for i in range(1, 5) for j, level in enumerate((3, 2))]
    result = theme_evidence(analysis_date='2026-09-09', limit_rows_by_date={'2026-09-09': today},
        current_bars={}, previous_bars={}, bars_by_ticker={}, moneyflow={})
    assert len(result['active_themes']) == 3
    assert len(result['themes']) == 4
    assert 'primary_theme' not in result
    assert all(t['front_support']['theme'] == t['name'] for t in result['themes'])


def test_twenty_session_baseline_never_uses_old_dates_to_fill_holes():
    sessions = [(date(2026, 8, 1) + timedelta(days=i)).isoformat() for i in range(21)]
    pools = {d: [] for d in sessions}
    pools.pop(sessions[3])
    pools['2026-07-01'] = [pool('a')]
    result = limit_up_resonance(analysis_date=sessions[-1], previous_date=sessions[-2], current_count=5,
        previous_count=3, limit_rows_by_date=pools, indices=[], sessions=sessions)
    assert result['baseline_20d'] is None
    assert result['sample_days'] == 19


def test_daily_same_rise_remains_visible_when_index_is_below_ma20():
    sessions = [(date(2026, 10, 1) + timedelta(days=i)).isoformat() for i in range(21)]
    result = limit_up_resonance(analysis_date=sessions[-1], previous_date=sessions[-2],
        current_count=125, previous_count=47, limit_rows_by_date={},
        summaries={d:{'limit_up':48+(i % 2)} for i,d in enumerate(sessions[:-1])},
        indices=[{'pct_chg':.013431,'index_vs_ma20':-.06058475} for _ in range(4)], sessions=sessions)
    assert result['relation'] == 'mixed'
    assert result['daily_relation'] == 'same_up'
    assert result['daily_label'] == '当日同涨'
    assert result['structure_label'] == '20日结构分歧'
    assert result['baseline_20d'] == 48.5
    assert result['supply_change'] > 0 and result['index_change'] > 0
    assert result['index_vs_ma20'] < 0


def test_daily_comparison_does_not_require_a_complete_twenty_day_baseline():
    result = limit_up_resonance(analysis_date='2026-11-02', previous_date='2026-10-30',
        current_count=125, previous_count=47, limit_rows_by_date={},
        indices=[{'pct_chg':.01,'index_vs_ma20':None} for _ in range(4)])
    assert result['relation'] == 'pending'
    assert result['daily_relation'] == 'same_up'
    missing = limit_up_resonance(analysis_date='2026-11-02', previous_date='2026-10-30',
        current_count=125, previous_count=None, limit_rows_by_date={}, indices=[])
    assert missing['daily_relation'] == 'pending'


def test_risk_position_is_prior_close_in_twenty_session_range():
    sessions = [(date(2026, 8, 1) + timedelta(days=i)).isoformat() for i in range(21)]
    histories = {d: {'a': bar(10, 20, 10), 'b': bar(10, 20, 10), 'c': bar(10, 20, 10)} for d in sessions[:-1]}
    result = risk(analysis_date=sessions[-1], sessions=sessions, limit_down_tickers={'a', 'b', 'c'},
        previous_bars={'a': bar(18), 'b': bar(15), 'c': bar(11)}, bars_by_date=histories)
    assert result['position_counts'] == dict(high=1, middle=1, low=1, unknown=0)
    histories.pop(sessions[4])
    result = risk(analysis_date=sessions[-1], sessions=sessions, limit_down_tickers={'a'},
        previous_bars={'a': bar(18)}, bars_by_date=histories)
    assert result['position_counts']['unknown'] == 1


def test_moderated_downside_penalty_and_industry_proxy_are_explicit():
    sessions = [(date(2026, 8, 1) + timedelta(days=i)).isoformat() for i in range(21)]
    tickers = {'a', 'b', 'c'}
    result = risk(analysis_date=sessions[-1], sessions=sessions, limit_down_tickers=tickers,
        previous_bars={t: bar(18) for t in tickers},
        bars_by_date={d:{t:bar(10,20,10) for t in tickers} for d in sessions[:-1]},
        basic={t:{'sector':'同一行业','name':t} for t in tickers})
    assert result['penalty'] == 12
    assert result['risk_type'] == 'high_level_collapse'
    assert [r['penalty'] for r in result['state_rules']] == [18,12,6]
    assert result['theme_cluster_status'] == 'pending'
    assert result['industry_clusters'][0]['count'] == 3
    assert '不等同题材' in result['theme_cluster_note']


def test_vibe_single_day_returns_are_normalized_not_cumulative():
    rows = normalize_previous([dict(code='000001', name='测试', ret=-.6, prev_boards=2, close=10, limit_price=11)])
    assert rows[0]['return_1d'] == -.006
    assert rows[0]['ticker'] == '000001.SZ'


@pytest.mark.parametrize('ret', [None, 'NaN', 'Infinity'])
def test_bad_return_is_not_silently_removed_from_denominator(ret):
    with pytest.raises(ValueError):
        normalize_previous([dict(code='000001', ret=ret, prev_boards=1, close=10)])


def test_stale_eastmoney_envelope_cannot_prove_empty_pool(monkeypatch):
    class Response:
        def raise_for_status(self): pass
        def json(self): return {'rc': 0, 'data': {'qdate': 20260911, 'tc': 73, 'pool': []}}
    class Session:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def get(self, *args, **kwargs): return Response()
    monkeypatch.setattr('shilun.review.dated_pools.requests.Session', Session)
    with pytest.raises(ValueError, match='date mismatch'): fetch_previous('2026-09-09')


def test_history_sync_accepts_provider_timestamp_dates_and_skips_complete_days(monkeypatch):
    import pandas as pd
    days = [(date(2026, 8, 1) + timedelta(days=i)).isoformat() for i in range(21)]
    monkeypatch.setattr('shilun.review.dated_pools.load_review_sessions', lambda *args: days)
    class Store:
        written = None
        def collection(self, name): return self
        def count_documents(self, query): return 0 if query['date'] == days[-1] else 5500
        def find_one(self, query, *args, **kwargs): return {'date': days[-1]}
        def upsert_daily_bars(self, frame): self.written = frame; return len(frame)
    class Client:
        def fetch_daily_for_trade_date(self, day):
            assert day == days[-1].replace('-', '')
            return pd.DataFrame([dict(ticker='000001.SZ', date=pd.Timestamp(days[-1]), close=10)])
    store = Store()
    result = sync_review_history(store, days[-1], client=Client())
    assert len(result) == 1
    assert result[0]['status'] == 'ok'
    assert store.written.iloc[0]['source'] == 'tushare_daily'
