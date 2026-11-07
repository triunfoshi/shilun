from shilun.review.emotion_history import five_day_heat, round_evidence, return_path
from shilun.review.theme_evidence import limit_up_resonance


def pool(t, n=1):
    return {'ticker': t, 'name': t, 'sector_name': '农业', 'consecutive_count': n}


def test_active_seven_day_stock_retains_earlier_round_peak():
    dates = [f'2026-08-{i:02d}' for i in range(1, 31)]
    pools = {d: [pool('companion')] for d in dates}
    pools[dates[16]].append(pool('a', 4))
    pools[dates[-1]].append(pool('a'))
    result = round_evidence('农业', {'a', 'companion'}, pools, dates, dates[-1], {})
    assert result['max_board'] == 4
    assert result['leaders'][0]['ticker'] == 'a'
    assert result['boundary_confirmed'] is False
    # A stock outside the active cohort must not inflate the current round.
    pools[dates[17]].append(pool('inactive', 9))
    assert round_evidence('农业', {'a'}, pools, dates, dates[-1], {})['max_board'] == 4


def test_verified_quiet_boundary_and_censored_history_are_distinguished():
    dates = [f'2026-08-{i:02d}' for i in range(1, 21)]
    pools = {d: [] for d in dates}
    pools[dates[0]], pools[dates[-1]] = [pool('a', 8)], [pool('a', 1)]
    assert round_evidence('农业', {'a'}, pools, dates, dates[-1], {})['max_board'] == 1
    for d in dates[1:-1:4]:
        del pools[d]
    result = round_evidence('农业', {'a'}, pools, dates, dates[-1], {})
    assert result['max_board'] == 1  # Old peak cannot be attributed across holes.
    assert result['boundary_confirmed'] is False
    assert result['complete'] is False
    assert result['history_gap_dates']


def test_return_startup_and_crown_are_distinct_and_future_excluded():
    dates = ['2026-09-07', '2026-09-08', '2026-09-09', '2026-09-10']
    bars = {'a': [{'date': d, 'pre_close': p, 'close': c} for d, p, c in zip(dates, [10, 11, 12, 12], [11, 12, 11, 100])]}
    result = return_path('a', dates[0], dates[2], dates, bars)
    assert round(result['return'], 8) == 0
    assert round(result['drawdown'], 6) == round(11 / 12 - 1, 6)
    assert round(return_path('a', dates[0], dates[2], dates, bars, include_start=True)['return'], 8) == .1
    bars['a'].pop(1)
    assert return_path('a', dates[0], dates[2], dates, bars)['return'] is None


def test_startup_uses_prior_global_session_and_discloses_unadjusted_prices():
    dates = ['2026-09-07', '2026-09-08', '2026-09-09']
    bars = {'a': [{'date': dates[0], 'close': 10}, {'date': dates[1], 'close': 11},
                  {'date': dates[2], 'close': 12.1, 'pre_close': 11}]}
    result = return_path('a', dates[1], dates[2], dates, bars, include_start=True)
    assert round(result['return'], 8) == .21
    assert result['price_basis'] == 'raw_close_fallback'
    bars['a'].pop(0)
    assert return_path('a', dates[1], dates[2], dates, bars, include_start=True)['return'] is None


def test_heat_retains_tied_leaders_unknown_rate_and_asof_date():
    dates = ['2026-09-07', '2026-09-08', '2026-09-09']
    result = five_day_heat(dates[1], dates, {dates[0]: [pool('a', 3), pool('b', 3)], dates[1]: [], dates[2]: [pool('future', 8)]}, {},
        {dates[0]: {'limit_up': 99, 'highest_consec': 3, 'broken_rate': .2}})
    assert [r['date'] for r in result['days']] == dates[:2]
    assert len(result['days'][0]['leaders']) == 2
    assert result['days'][0]['broken_rate'] is None
    assert result['days'][1]['limit_up'] == 0
    assert not any(r['is_current_top'] for r in result['leader_lineage'])


def test_baseline_missing_dates_are_not_relabelled_twenty_day_average():
    dates = [f'2026-08-{i:02d}' for i in range(1, 22)]
    pools = {d: [pool('a')] for d in dates[2:-1]}
    result = limit_up_resonance(analysis_date=dates[-1], previous_date=dates[-2], current_count=2,
        previous_count=1, limit_rows_by_date=pools, indices=[], sessions=dates)
    assert result['sample_days'] == 18
    assert result['baseline_20d'] is None
    assert result['missing_dates'] == dates[:2]
    assert result['available_sample_median'] == 1
