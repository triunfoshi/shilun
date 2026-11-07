from datetime import date, timedelta
import pytest

from shilun.review.strong_stock_candidates import evaluate_stock, daily_evidence


def calendar(n=55):
    d, days = date(2026, 7, 1), []
    while len(days) < n:
        if d.weekday() < 5:
            days.append(d.isoformat())
        d += timedelta(days=1)
    return days


def fixture(pattern, quiet=12):
    days, bars, limits = calendar(), {}, {}
    prior = 10
    for i, hit in enumerate([False]*quiet + pattern):
        upper = round(prior * 1.1, 2)
        close = upper if hit else prior
        bars[days[i]] = dict(open=prior, low=prior, high=close, close=close, volume=100)
        limits[days[i]] = dict(pre_close=prior, up_limit=upper)
        prior = close
    return days, bars, limits


def evaluate(pattern, **kwargs):
    days, bars, limits = fixture(pattern)
    return evaluate_stock('A', days, days[11+len(pattern)], bars, limits, **kwargs)


def test_five_consecutive_and_qualification_date_never_retroactively_moves():
    days, bars, limits = fixture([True]*5)
    assert evaluate_stock('A', days, days[15], bars, limits)['status'] == 'pending'
    result = evaluate_stock('A', days, days[16], bars, limits)
    assert result['qualified_at'] == days[16]
    assert result['current_cycle']['start_date'] == days[12]
    assert result['current_cycle']['cumulative_return'] == pytest.approx(bars[days[16]]['close']/10-1)
    assert result['latest_qualified_leg']['three_board_date'] == days[14]
    assert result['latest_qualified_leg']['qualification_board_count'] == 5
    assert result['latest_qualified_leg']['qualification_sessions'] == 5


@pytest.mark.parametrize('pattern,status', [
    ([1,1,0,1,1,0,1,0,0,0], 'failed'),
    ([1,1,1,0,0,0,0,0,0,0], 'failed'),
    ([1,1,1,0,1,0,0,0,0,1], 'qualified'),
    ([1,1,1,0,1,0,0,0,0,0,1], 'failed'),
])
def test_fixed_ten_exchange_day_window(pattern, status):
    first = evaluate(pattern)['legs'][0]
    assert first['status'] == status
    assert first['board_count'] == sum(pattern[:10])
    assert len(first['evidence']) == 10


def test_another_legs_three_boards_do_not_certify_an_initial_single_board():
    result = evaluate([1,0,1,1,1,0,1,1])
    assert result['legs'][0]['qualified_at'] is None
    assert result['legs'][1]['qualified_at'] is not None


def test_pending_first_window_survives_a_later_single_board_leg_failure():
    result=evaluate([1,1,1,0,1,0])
    assert result['current_leg']['status']=='failed'
    assert result['status']=='pending'
    assert result['focus_leg']==result['legs'][0]


def test_new_leg_keeps_price_cycle_and_peak():
    days, bars, limits = fixture([1,0,0,0,0,0,0,1,1,1,1,0,0,1])
    result = evaluate_stock('A', days, days[25], bars, limits)
    assert result['current_cycle']['start_date'] == days[12]
    assert result['latest_qualified_leg']['start_date'] == days[19]
    assert result['latest_qualified_leg']['board_count'] == 5
    assert result['latest_qualified_leg']['qualified_at'] == days[25]
    assert result['latest_qualified_leg']['qualification_sessions'] == 7
    assert result['current_cycle']['cumulative_return'] == pytest.approx(bars[days[25]]['close']/10-1)


def test_missing_suspended_and_unrestricted_do_not_make_fake_streaks():
    days, bars, limits = fixture([1]*6)
    missing = dict(bars)
    missing.pop(days[14])
    result = evaluate_stock('A', days, days[17], missing, limits)
    assert not result['qualified_at']
    assert result['legs'][-1]['boundary_confirmed'] is False
    result = evaluate_stock('A', days, days[17], bars, limits, suspensions={days[14]})
    assert not result['qualified_at']
    assert result['legs'][0]['remaining_sessions'] == 4
    result = evaluate_stock('A', days, days[16], bars, limits, no_limit_dates=set(days[12:17]))
    assert result['legs'] == []


def test_data_missing_after_certification_does_not_erase_historical_qualification():
    days, bars, limits = fixture([1]*5+[0])
    bars.pop(days[17])
    result = evaluate_stock('A', days, days[17], bars, limits)
    assert result['qualified_at'] == days[16]
    assert result['status'] == 'unverified'
    assert result['latest_qualified_leg']['status'] == 'qualified'
    assert result['current_cycle']['price_complete'] is False
    assert result['current_cycle']['drawdown'] is None


def test_fifteen_percent_warning_and_new_leg_do_not_reset_price_peak():
    days, bars, limits = fixture([1]*5+[0])
    peak = bars[days[16]]['close']
    # First-board low remains intact; drawdown alone does not close the cycle.
    bars[days[17]].update(close=peak*.84, low=peak*.84, high=peak, open=peak)
    result = evaluate_stock('A', days, days[17], bars, limits)
    assert result['current_cycle']['status'] == 'active'
    assert result['current_cycle']['drawdown_warning'] is True
    assert result['current_cycle']['drawdown'] == pytest.approx(-.16)
    assert result['current_cycle']['peak_close_on_start_scale'] == pytest.approx(peak)


def test_ex_rights_reference_adjusts_returns_and_first_board_floor():
    days, bars, limits = fixture([1]*5+[0])
    prior = bars[days[16]]['close']
    limits[days[17]] = dict(pre_close=prior/2, up_limit=round(prior/2*1.1,2))
    bars[days[17]].update(open=prior/2, low=prior/2, high=prior/2, close=prior/2)
    result = evaluate_stock('A', days, days[17], bars, limits)
    assert result['current_cycle']['status'] == 'active'
    assert result['current_cycle']['drawdown'] == 0
    assert result['current_cycle']['cumulative_return'] == pytest.approx(prior/10-1)


def test_broken_first_low_closes_old_window_and_does_not_borrow_new_boards():
    days, bars, limits = fixture([1,1,1,0,1,1,1,1,1])
    bars[days[15]].update(low=9,close=10,open=10,high=10)
    result = evaluate_stock('A', days, days[20], bars, limits)
    assert result['cycles'][0]['ended_at'] == days[15]
    assert result['legs'][0]['qualified_at'] is None
    assert result['current_cycle']['start_date'] == days[16]
    assert result['latest_qualified_leg']['start_date'] == days[16]


def test_more_than_ten_quiet_sessions_archives_old_cycle():
    result = evaluate([1]*5+[0]*11)
    assert result['current_cycle'] is None
    assert result['status'] == 'inactive'
    assert result['legs'][0]['qualified_at'] is not None
    assert result['cycles'][0]['end_reason'] == '连续超过10个交易日未涨停'


def test_left_censored_streak_never_certified_and_duplicate_calendar_rejected():
    days, bars, limits = fixture([1]*5, quiet=0)
    result = evaluate_stock('A', days, days[4], bars, limits)
    assert result['status'] == 'unverified'
    assert result['current_cycle']['cumulative_return'] is None
    with pytest.raises(ValueError):
        evaluate_stock('A', [days[0]]*2, days[0], bars, limits)


def test_limit_price_conflict_does_not_become_an_ordinary_break():
    row = daily_evidence('2026-09-22', dict(close=20,low=19,high=21), dict(up_limit=11,pre_close=10))
    assert row['state'] == 'missing'
    assert row['missing_inputs'] == ['price_limit_conflict']


def test_zero_volume_cannot_repeat_a_suspended_limit_close():
    row=daily_evidence('2026-09-22',dict(close=11,low=11,high=11,volume=0),dict(up_limit=11,pre_close=10))
    assert row['state']=='missing'


def test_daily_reference_overrides_stale_enriched_limit_archive():
    row=daily_evidence('2026-08-12',dict(close=6.12,low=6.04,high=6.15,pre_close=6.15,volume=100),
                       dict(up_limit=6.77,pre_close=6.32))
    assert row['daily_return']==pytest.approx(6.12/6.15-1)
    assert row['reference_disagreement'] is True
    assert row['price_basis']=='daily_pre_close'


def test_qualified_stocks_deduplicate_legs_and_keep_real_streak_above_ten():
    result=evaluate([1]*12)
    assert result['current_streak']==12
    assert len(result['legs'])==1 and result['legs'][0]['board_count']==10
    assert len(result['legs'][0]['evidence'])==10
