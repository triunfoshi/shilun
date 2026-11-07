from copy import deepcopy

import pytest

from shilun.review.theme_core_tracking import post_exit_feedback
from shilun.review.theme_selection import ThemeSelectionReplay
from shilun.review.theme_cycles import ThemeCycleReplay
from test_theme_cycles import inputs, DAYS, CORE, A, B


def rebounding_after_exit():
    m, tax = inputs()
    baseline = ThemeCycleReplay(ThemeSelectionReplay(m, tax)).run(DAYS)
    exited = baseline[-1]['archived'][0]
    anchor = exited['exit_date']
    for index, day in enumerate(DAYS):
        if day <= anchor:
            continue
        for ticker in (CORE, A, B):
            previous = m['bars'][ticker][DAYS[index-1]]['close']
            close = previous * (1.06 if ticker == CORE else .98)
            m['bars'][ticker][day].update(pre_close=previous, close=close, high=max(previous,close), low=min(previous,close))
            m['limits'][ticker][day]['up_limit'] = previous*1.1
    m['benchmark'] = {'ticker': '000001.SH', 'name': '上证指数',
                      'bars': {d: {'close': 100*1.01**i} for i, d in enumerate(DAYS)}}
    return m, tax, exited


def test_core_outperformance_does_not_rewrite_theme_retreat_or_frozen_losses():
    m, tax, exited = rebounding_after_exit()
    rows = ThemeCycleReplay(ThemeSelectionReplay(m, tax)).run(DAYS)
    c = rows[-1]['archived'][0]; p = c['post_exit']
    assert c['cycle_stage'] == 'retreat' and c['exit_date'] == exited['exit_date']
    assert c['metrics'] == exited['metrics'] and c['history'] == exited['history']
    assert c['back'] == [A, B] and not rows[-1]['active']
    n = p['sessions']
    assert p['core']['return'] == pytest.approx(1.06**n-1)
    assert p['benchmark']['return'] == pytest.approx(1.01**n-1)
    assert p['core']['excess_return'] == pytest.approx(1.06**n-1.01**n)
    assert p['back_return'] < 0 and p['label'] == '核心相对强势 · 抱团观察'
    assert len(p['rows']) == 3


def test_missing_benchmark_or_core_price_stays_unknown_but_reason_gap_does_not_hide_prices():
    m, tax, exited = rebounding_after_exit()
    m['reasons'] = {}
    m['benchmark']['bars'].pop(DAYS[-1])
    p = post_exit_feedback(ThemeSelectionReplay(m,tax), exited, DAYS[-1])
    assert p['core']['return'] > 0 and p['core']['excess_return'] is None
    assert p['benchmark']['return'] is None and p['status'] == 'partial'
    m['bars'][CORE].pop(DAYS[-1])
    p = post_exit_feedback(ThemeSelectionReplay(m,tax), exited, DAYS[-1])
    assert p['core']['return'] is None and p['core']['missing_dates'] == [DAYS[-1]]


def test_tracking_uses_only_asof_prices_and_trading_days():
    m,tax,c = rebounding_after_exit(); day=DAYS[-2]
    expected=post_exit_feedback(ThemeSelectionReplay(m,tax),c,day)
    changed=deepcopy(m);changed['bars'][CORE][DAYS[-1]]['close']=999999
    changed['benchmark']['bars'][DAYS[-1]]['close']=1
    assert post_exit_feedback(ThemeSelectionReplay(changed,tax),c,day)==expected
    assert expected['sessions']==len([d for d in DAYS if c['exit_date']<d<=day])
