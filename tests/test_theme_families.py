from copy import deepcopy

from shilun.review.theme_families import RETAIL
from shilun.review.theme_selection import ThemeSelectionReplay
from shilun.review.theme_observation import member_observation
from test_theme_selection import inputs, DAYS, CORE, FIRST, SECOND


def retail_inputs():
    market, taxonomy = inputs()
    second_core = '600004.SH'
    market['stocks'][second_core] = {'name': '另一零售核心'}
    for day in DAYS:
        original = taxonomy['snapshots'][day]['rows'][0]
        taxonomy['snapshots'][day]['rows'] = [
            {**deepcopy(original), 'code': 'BK0825.DC', 'name': '新零售',
             'members': [{'con_code': t} for t in (CORE, FIRST)]},
            {**deepcopy(original), 'code': 'BK1655.DC', 'name': '零售概念',
             'members': [{'con_code': t} for t in (second_core, FIRST, SECOND)]}]
        market['cores'][day].append({**deepcopy(market['cores'][day][0]), 'ticker': second_core, 'name': '另一零售核心'})
        if day == DAYS[-3]:
            market['pools'][day]['stocks'].append({'ticker': second_core, 'name': '另一零售核心', 'boards': 5})
            market['reasons'][day]['reasons'][second_core[:6]] = '商业零售'
    return market, taxonomy


def test_retail_family_merges_different_cores_with_deduplicated_events_and_sources():
    m, tax = retail_inputs(); original = deepcopy(tax)
    replay = ThemeSelectionReplay(m, tax); result = replay.evaluate(DAYS[-1])
    assert tax == original  # Never rewrite the supplier taxonomy.
    assert len(result['candidates']) == len(result['directions']) == 1
    candidate = result['candidates'][0]
    assert candidate['code'] == RETAIL and candidate['name'] == '零售主线'
    assert candidate['member_count'] == 4
    assert len(candidate['cores']) == 2
    assert candidate['followers'] == sorted([FIRST, SECOND])
    assert len(candidate['events']) == 3  # FIRST occurs twice, but is not doubled by two labels.
    assert len({(e['date'], e['ticker']) for e in candidate['events']}) == 3
    rows = {r['ticker']: r for r in member_observation(replay, DAYS[-1], candidate)['members']}
    assert {s['code'] for s in rows[FIRST]['source_topics']} == {'BK0825.DC', 'BK1655.DC'}
    assert result['directions'][0]['primary_code'] == RETAIL


def test_missing_family_branch_membership_is_not_treated_as_empty_or_separate_slot():
    m, tax = retail_inputs()
    tax['snapshots'][DAYS[-1]]['rows'][1]['membership_complete'] = False
    replay = ThemeSelectionReplay(m, tax); result = replay.evaluate(DAYS[-1])
    assert not result['directions']
    assert len(result['candidates']) == 1 and result['candidates'][0]['status'] == 'pending'
    assert (DAYS[-1], RETAIL) not in replay.members
    assert not replay.events(RETAIL, DAYS[-1])['complete']
