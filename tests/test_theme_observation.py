from copy import deepcopy

import pytest

from shilun.review.theme_observation import member_observation
from shilun.review.theme_selection import ThemeSelectionReplay
from test_theme_selection import inputs, DAYS, CORE, FIRST, SECOND


def observe(market, taxonomy):
    replay = ThemeSelectionReplay(market, taxonomy)
    candidate = replay.evaluate(DAYS[-1])['candidates'][0]
    result = member_observation(replay, DAYS[-1], candidate)
    return candidate, result, {r['ticker']: r for r in result['members']}


@pytest.mark.parametrize('reason,status', [('大消费+低温乳品','ambiguous'), ('粮油加工+食品加工','unmapped'), ('','missing')])
def test_members_remain_observed_without_promoting_their_reason(reason, status):
    market, taxonomy = inputs()
    for d in DAYS:
        taxonomy['snapshots'][d]['rows'][0]['code'] = 'BK0825.DC'
        if FIRST[:6] in market['reasons'][d]['reasons']:
            market['reasons'][d]['reasons'][FIRST[:6]] = reason
    candidate, observation, rows = observe(market, taxonomy)
    assert rows[FIRST]['membership_status'] == 'confirmed'
    assert rows[FIRST]['attribution']['status'] == status
    assert not rows[FIRST]['counted_follower']
    assert FIRST not in candidate['followers']
    assert len([r for r in observation['members'] if r['ticker'] == FIRST]) == 1


def test_qualified_member_with_unclear_reason_is_a_core_candidate_not_a_follower():
    market, taxonomy = inputs()
    market['cores'][DAYS[-1]].append({**market['cores'][DAYS[-1]][0], 'ticker': FIRST, 'name': '第二核心'})
    for d in DAYS:
        if FIRST[:6] in market['reasons'][d]['reasons']:
            market['reasons'][d]['reasons'][FIRST[:6]] = '低温乳品'
    candidate, _, rows = observe(market, taxonomy)
    assert rows[FIRST]['role'] == 'core_candidate'
    assert rows[FIRST]['qualified_at'] == DAYS[0]
    assert not rows[FIRST]['counted_follower']
    assert FIRST not in candidate['followers']


def test_active_core_is_observed_beyond_three_day_window_without_reason():
    market, taxonomy = inputs()
    for d in DAYS:
        market['pools'][d]['stocks'] = [r for r in market['pools'][d]['stocks'] if r['ticker'] != CORE]
    market['cores'][DAYS[-1]][0]['latest_limit_date'] = DAYS[0]
    _, _, rows = observe(market, taxonomy)
    assert rows[CORE]['role'] == 'core_candidate'
    assert rows[CORE]['latest_limit_date'] == DAYS[0]
    assert rows[CORE]['attribution']['status'] == 'missing'


def test_member_observation_survives_theme_entry_rejection():
    market, taxonomy = inputs()
    taxonomy['snapshots'][DAYS[-1]]['rows'][0]['has_qualified_core'] = False
    market['cores'][DAYS[-1]] = []
    candidate, _, rows = observe(market, taxonomy)
    assert not candidate['diffusion_evaluated']
    assert FIRST in rows and SECOND in rows
    assert rows[FIRST]['linkage']['status'] == 'no_reference'
    assert not rows[FIRST]['counted_follower']


def test_no_future_qualification_and_no_future_price_read():
    market, taxonomy = inputs()
    market['cores'][DAYS[-1]][0]['qualified_at'] = '2026-09-23'
    market['bars'][FIRST] = {'2026-09-23': {'close': 100, 'pre_close': 10}}
    _, _, rows = observe(market, taxonomy)
    assert rows[CORE]['role'] == 'member'
    assert rows[CORE]['qualified_at'] is None
    assert rows[FIRST]['linkage']['stock_return'] is None


def test_co_movement_does_not_confirm_attribution_and_missing_is_not_zero():
    market, taxonomy = inputs()
    market['bars'][FIRST][DAYS[-1]] = {'close': 11, 'pre_close': 10}
    market['bars'][CORE] = {DAYS[-1]: {'close': 12, 'pre_close': 10}}
    for d in DAYS:
        if FIRST[:6] in market['reasons'][d]['reasons']:
            market['reasons'][d]['reasons'][FIRST[:6]] = '低温乳品'
    candidate, _, rows = observe(market, taxonomy)
    assert rows[FIRST]['linkage']['status'] == 'both_up'
    assert rows[FIRST]['linkage']['stock_return'] == pytest.approx(.1)
    assert rows[FIRST]['attribution']['status'] == 'unmapped'
    assert not rows[FIRST]['counted_follower']
    assert rows[CORE]['linkage']['status'] == 'self_reference'
    assert rows[CORE]['linkage']['reference_core'] is None
    del market['bars'][CORE][DAYS[-1]]
    _, _, rows = observe(market, taxonomy)
    assert rows[FIRST]['linkage']['status'] == 'missing_prices'
    assert rows[FIRST]['linkage']['reference_core']['return'] is None


@pytest.mark.parametrize('excluded', [{'name':'*ST甲'}, {'name':'退市甲'}, {'name':'甲','list_status':'D'}])
def test_excluded_stocks_cannot_enter_observation(excluded):
    market, taxonomy = inputs()
    market['stocks'][FIRST] = excluded
    assert FIRST not in observe(market, taxonomy)[2]


def test_missing_or_outdated_membership_is_explicit_and_removals_respected():
    market, taxonomy = inputs()
    row = taxonomy['snapshots'][DAYS[-1]]['rows'][0]
    row['membership_date'] = DAYS[-2]
    _, result, rows = observe(market, taxonomy)
    assert result['status'] == 'membership_missing' and not rows
    assert result['member_limit_count'] is None
    row['membership_date'] = DAYS[-1]
    row['members'] = [r for r in row['members'] if r['con_code'] != FIRST]
    assert FIRST not in observe(market, taxonomy)[2]


def test_observation_does_not_mutate_entry_results_and_unknown_pool_stays_unknown():
    market, taxonomy = inputs()
    market['pools'].pop(DAYS[-1])
    replay = ThemeSelectionReplay(market, taxonomy)
    candidate = replay.evaluate(DAYS[-1])['candidates'][0]
    frozen = deepcopy(candidate)
    result = member_observation(replay, DAYS[-1], candidate)
    assert candidate == frozen
    assert result['member_limit_count'] is None
    assert result['status'] == 'partial'
    assert DAYS[-1] in result['missing_dates']


def extended_observation_inputs():
    market, taxonomy = inputs()
    added_days = ['2026-09-08', '2026-09-09', '2026-09-10', '2026-09-11']
    market['sessions'] = added_days + DAYS[:]
    for day in added_days:
        market['pools'][day] = {'date': day, 'complete': True, 'snapshot_mode': 'settled', 'stocks': []}
        market['reasons'][day] = {'date': day, 'reasons': {}}
        snapshot = deepcopy(taxonomy['snapshots'][DAYS[0]])
        snapshot['date'] = snapshot['rows'][0]['membership_date'] = day
        taxonomy['snapshots'][day] = snapshot
    # Only one old limit-up for FIRST; the active core and recent SECOND stay unchanged.
    for pool in market['pools'].values():
        pool['stocks'] = [r for r in pool['stocks'] if r['ticker'] != FIRST]
    return market, taxonomy


@pytest.mark.parametrize('limit_day,visible', [('2026-09-09', True), ('2026-09-08', False)])
def test_ten_exchange_session_boundary_does_not_expand_diffusion(limit_day, visible):
    market, taxonomy = extended_observation_inputs()
    market['pools'][limit_day]['stocks'].append({'ticker': FIRST, 'name': '跟随甲', 'boards': 1})
    market['reasons'][limit_day]['reasons'][FIRST[:6]] = '新零售'
    candidate, result, rows = observe(market, taxonomy)
    assert result['status'] == 'ready'
    assert result['lookback_sessions'] == 10  # Includes review day; skips the intervening weekends.
    assert (FIRST in rows) is visible
    assert FIRST not in candidate['followers']
    assert candidate['status'] == 'rejected'
    if visible:
        assert rows[FIRST]['latest_limit_date'] == limit_day
        assert rows[FIRST]['role'] == 'member'
        assert not rows[FIRST]['counted_follower']
        assert '不计入近3日跟随' in rows[FIRST]['counting_note']


def test_old_window_gap_is_reported_and_future_limit_cannot_backfill_member():
    market, taxonomy = extended_observation_inputs()
    market['pools'].pop('2026-09-09')
    future = '2026-09-23'
    market['sessions'].append(future)
    market['pools'][future] = {'date': future, 'complete': True, 'snapshot_mode': 'settled',
                             'stocks': [{'ticker': FIRST, 'name': '跟随甲', 'boards': 1}]}
    _, result, rows = observe(market, taxonomy)
    assert FIRST not in rows
    assert result['status'] == 'partial'
    assert '2026-09-09' in result['missing_dates']
    assert future not in result['missing_dates']
