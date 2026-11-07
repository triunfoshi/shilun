from copy import deepcopy

import pytest

from shilun.review.theme_selection import SelectionRules, ThemeSelectionReplay, reason_matches
from shilun.review.theme_selection_data import TaxonomyResearchReader


DAYS = ['2026-09-14', '2026-09-15', '2026-09-16', '2026-09-17', '2026-09-18', '2026-09-21', '2026-09-22']
CORE, FIRST, SECOND = '600001.SH', '600002.SH', '000003.SZ'


def inputs():
    market = {'sessions': DAYS[:], 'stocks': {t: {'name': name} for t, name in [(CORE, '核心'), (FIRST, '跟随甲'), (SECOND, '跟随乙')]},
              'cores': {}, 'pools': {}, 'reasons': {}, 'bars': {}, 'limits': {}}
    taxonomy = {'snapshots': {}}
    for day in DAYS:
        market['cores'][day] = [{'ticker': CORE, 'name': '核心', 'date': day, 'status': 'qualified',
            'qualified_at': DAYS[0], 'latest_limit_date': DAYS[-3] if day >= DAYS[-3] else day,
            'cycle': {'max_streak': 5, 'cumulative_return': .9, 'drawdown_warning': True}}]
        tickers = [CORE, FIRST] if day == DAYS[-3] else [FIRST, SECOND] if day == DAYS[-2] else []
        market['pools'][day] = {'date': day, 'complete': True, 'snapshot_mode': 'settled',
            'stocks': [{'ticker': t, 'name': market['stocks'][t]['name'], 'boards': 1} for t in tickers]}
        market['reasons'][day] = {'date': day, 'reasons': {t[:6]: '新零售+消费概念' for t in tickers}}
        taxonomy['snapshots'][day] = {'date': day, 'core_mapping_complete': True, 'rows': [
            {'code': 'BK0001.DC', 'name': '新零售', 'research_eligible': True, 'has_qualified_core': True,
             'membership_date': day, 'membership_complete': True, 'member_count': 300,
             'members': [{'con_code': t} for t in market['stocks']]}]}
    for ticker in (FIRST, SECOND):
        market['bars'][ticker] = {DAYS[-1]: {'open': 10, 'low': 9, 'high': 11, 'close': 9, 'pre_close': 10, 'volume': 100}}
        market['limits'][ticker] = {DAYS[-1]: {'up_limit': 11}}
    return market, taxonomy


def evaluate(market, taxonomy):
    return ThemeSelectionReplay(market, taxonomy).evaluate(DAYS[-1])


def test_diffusion_distinct_stocks_across_exchange_sessions_and_large_board_allowed():
    market, taxonomy = inputs()
    result = evaluate(market, taxonomy)
    assert len(result['directions']) == 1
    candidate = result['candidates'][0]
    assert candidate['followers'] == sorted([FIRST, SECOND])
    assert candidate['active_dates'] == DAYS[-3:-1]  # Friday and Monday, no weekend gap
    assert result['directions'][0]['core']['cycle']['drawdown_warning']
    assert candidate['feedback']['cohort_count'] == 2
    assert candidate['feedback']['negative_count'] == 2
    assert candidate['feedback']['median_return'] == pytest.approx(-.1)
    assert len(result['individual_observation']) == 1  # No limit today does not erase the core.


def test_one_follower_on_two_days_is_not_two_independent_followers():
    market, taxonomy = inputs()
    market['pools'][DAYS[-2]]['stocks'] = [market['pools'][DAYS[-2]]['stocks'][0]]
    row = evaluate(market, taxonomy)['candidates'][0]
    assert row['status'] == 'rejected'
    assert 'insufficient_followers' in row['reason_codes']


@pytest.mark.parametrize('field', ['pools', 'reasons'])
def test_missing_records_are_pending_not_observed_zero(field):
    market, taxonomy = inputs()
    market[field].pop(DAYS[-2])
    result = evaluate(market, taxonomy)
    assert not result['directions']
    assert result['candidates'][0]['status'] == 'pending'
    assert result['candidates'][0]['feedback']['status'] == 'partial'


def test_all_qualified_cores_excluded_from_follower_count():
    market, taxonomy = inputs()
    market['cores'][DAYS[-1]].append({**market['cores'][DAYS[-1]][0], 'ticker': SECOND})
    result = evaluate(market, taxonomy)
    assert not result['directions']
    assert result['candidates'][0]['followers'] == [FIRST]


def test_pending_three_or_four_board_stock_cannot_be_a_core():
    market, taxonomy = inputs()
    market['cores'][DAYS[-1]][0]['status'] = 'pending'
    result = evaluate(market, taxonomy)
    assert not result['directions']
    assert result['candidates'][0]['diffusion_evaluated'] is False


def test_evaluated_zero_is_distinct_from_skipped_diffusion():
    market, taxonomy = inputs()
    for day in DAYS[-3:]:
        market['pools'][day]['stocks'] = [r for r in market['pools'][day]['stocks'] if r['ticker'] == CORE]
    candidate = evaluate(market, taxonomy)['candidates'][0]
    assert candidate['diffusion_evaluated'] is True
    assert candidate['followers'] == []


def test_future_qualification_cannot_certify_today():
    market, taxonomy = inputs()
    market['cores'][DAYS[-1]][0]['qualified_at'] = '2026-09-23'
    assert not evaluate(market, taxonomy)['directions']


def test_old_price_cycle_attribution_does_not_certify_a_new_cycle():
    market, taxonomy = inputs()
    market['cores'][DAYS[-1]][0]['cycle']['start_date'] = DAYS[-2]
    row = evaluate(market, taxonomy)['candidates'][0]
    assert row['status'] == 'rejected'
    assert 'core_attribution_mismatch' in row['reason_codes']


def test_shared_broad_tag_without_reason_support_rejected():
    market, taxonomy = inputs()
    for snapshot in taxonomy['snapshots'].values():
        snapshot['rows'][0]['name'] = '人工智能'
    row = evaluate(market, taxonomy)['candidates'][0]
    assert row['status'] == 'rejected'
    assert 'core_attribution_mismatch' in row['reason_codes']
    assert row['membership_only_count'] == 3


@pytest.mark.parametrize('excluded', [{'name': '*ST甲'}, {'name': '退市甲'}, {'name': '甲', 'list_status': 'D'}])
def test_exclude_st_and_delisted(excluded):
    market, taxonomy = inputs()
    market['stocks'][SECOND] = excluded
    assert not evaluate(market, taxonomy)['directions']


def test_unknown_or_wrong_date_memberships_block_entry():
    market, taxonomy = inputs()
    taxonomy['snapshots'][DAYS[-2]]['rows'][0]['membership_date'] = DAYS[-1]
    row = evaluate(market, taxonomy)['candidates'][0]
    assert row['status'] == 'pending'
    assert DAYS[-2] in row['missing_membership_dates']


def test_same_identified_core_merge_deduplicates_events_and_stocks():
    market, taxonomy = inputs()
    for snapshot in taxonomy['snapshots'].values():
        snapshot['rows'].append({**snapshot['rows'][0], 'code': 'BK0002.DC', 'name': '消费概念'})
    result = evaluate(market, taxonomy)
    assert len(result['directions']) == 1
    assert len(result['directions'][0]['branches']) == 2
    assert len(result['directions'][0]['events']) == 3
    assert len(result['directions'][0]['followers']) == 2


def test_performance_screen_ignored_even_if_flagged_eligible():
    market, taxonomy = inputs()
    taxonomy['snapshots'][DAYS[-1]]['rows'][0]['name'] = '昨日连板_含一字'
    assert not evaluate(market, taxonomy)['candidates']


def test_fixed_yesterday_cohort_keeps_losers_and_reports_missing_feedback():
    market, taxonomy = inputs()
    market['bars'][SECOND] = {}
    row = evaluate(market, taxonomy)['candidates'][0]
    assert row['feedback']['cohort_count'] == 2
    assert row['feedback']['observed_count'] == 1
    assert row['feedback']['negative_count'] == 1
    assert row['feedback']['status'] == 'partial'
    assert row['feedback']['cohort'][1]['return'] is None


def test_appending_future_data_does_not_change_past_output():
    market, taxonomy = inputs()
    before = evaluate(market, taxonomy)
    future = '2026-09-23'
    market['sessions'].append(future)
    market['cores'][future] = [{**r, 'date': future} for r in market['cores'][DAYS[-1]]]
    market['pools'][future] = {'date': future, 'complete': True, 'stocks': []}
    taxonomy['snapshots'][future] = {**deepcopy(taxonomy['snapshots'][DAYS[-1]]), 'date': future}
    assert before == evaluate(market, taxonomy)


def test_reason_token_matching_is_conservative():
    assert reason_matches('粮食概念', '粮油加工+粮食+食品')
    assert reason_matches('CPO', 'cpo+光模块')
    assert not reason_matches('AI', 'AI制药')
    assert not reason_matches('创新药', '创新药业务传闻，尚未证实')


def test_four_different_main_cores_have_only_three_direction_slots():
    market, taxonomy = inputs()
    for number in range(2, 5):
        core, first, second = (f'600{number}{suffix:02d}.SH' for suffix in (1, 2, 3))
        name = f'题材{number}'
        market['stocks'].update({t: {'name': t} for t in (core, first, second)})
        for day in DAYS:
            market['cores'][day].append({**market['cores'][day][0], 'ticker': core, 'name': core})
            members = [core, first, second]
            events = [core, first] if day == DAYS[-3] else [first, second] if day == DAYS[-2] else []
            market['pools'][day]['stocks'].extend({'ticker': t, 'name': t} for t in events)
            market['reasons'][day]['reasons'].update({t[:6]: name for t in events})
            # Shared membership in a second main core does not itself merge
            # cards: only the attributed core is identified for each topic.
            taxonomy['snapshots'][day]['rows'].append({**taxonomy['snapshots'][day]['rows'][0],
                'code': f'BK000{number}.DC', 'name': name,
                'members': [{'con_code': t} for t in members + [CORE]]})
    result = evaluate(market, taxonomy)
    assert len(result['all_eligible_groups']) == 4
    assert len(result['directions']) == 3
    assert sum('outside_direction_limit' in r['reason_codes'] for r in result['candidates']) == 1


def test_core_observation_survives_entry_activity_window():
    market, taxonomy = inputs()
    old = '2026-09-11'
    market['sessions'].insert(0, old)
    market['cores'][DAYS[-1]][0]['latest_limit_date'] = old
    result = evaluate(market, taxonomy)
    assert not result['directions']
    assert len(result['individual_observation']) == 1
    assert not result['individual_observation'][0]['active_for_theme_entry']


def test_invalid_rules_rejected():
    with pytest.raises(ValueError):
        SelectionRules(minimum_active_dates=4)
    with pytest.raises(ValueError):
        SelectionRules(maximum_directions=4)


def test_research_reader_filters_calendar_weekends_and_rejects_wrong_id(tmp_path):
    reader = TaxonomyResearchReader(tmp_path)
    rows = [{'trade_date': d, 'ts_code': 'BK0001.DC', 'con_code': CORE} for d in ('20260918', '20260919', '20260921')]
    reader.fetch = lambda *args, **kwargs: {'rows': rows, 'fetched_at': '2026-09-23'}
    assert len(reader.members(['2026-09-18', '2026-09-21'], ts_code='BK0001.DC')) == 2
    with pytest.raises(ValueError):
        reader.members(['2026-09-18', '2026-09-21'], ts_code='BK0002.DC')


def test_research_reader_splits_at_provider_cap(tmp_path):
    reader = TaxonomyResearchReader(tmp_path)
    calls = []
    def fetch(api, **params):
        calls.append(params)
        row = {'trade_date': params['start_date'], 'ts_code': 'BK0001.DC', 'con_code': CORE}
        return {'rows': [row] * 5000 if params['start_date'] != params['end_date'] else [row], 'fetched_at': '2026-09-23'}
    reader.fetch = fetch
    result = reader.members(['2026-09-18', '2026-09-21'], ts_code='BK0001.DC')
    assert len(result) == 2
    assert len(calls) == 3
