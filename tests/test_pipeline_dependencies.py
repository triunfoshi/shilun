from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pandas as pd
import pytest

from shilun.jobs import pipeline_dependencies as dep
from shilun.market.taxonomy import VERSION

DAY, PRIOR = '2026-11-03', '2026-11-02'


def matches(row, query):
    for key, expected in query.items():
        value = row.get(key)
        if isinstance(expected, dict):
            if '$in' in expected and value not in expected['$in']:
                return False
            if '$ne' in expected and value == expected['$ne']:
                return False
        elif value != expected:
            return False
    return True


class Collection:
    def __init__(self, rows=()):
        self.rows = deepcopy(list(rows))
        self.writes = []

    def find(self, query, projection=None, **kwargs):
        rows = [deepcopy(r) for r in self.rows if matches(r, query)]
        fields = [k for k, v in (projection or {}).items() if k != '_id' and v == 1]
        return [{k: r[k] for k in fields if k in r} for r in rows] if fields else rows

    def find_one(self, query, projection=None, **kwargs):
        return next(iter(self.find(query, projection)), None)

    def count_documents(self, query):
        return len(self.find(query))

    def update_one(self, query, update, **kwargs):
        self.writes.append((query, update))


@pytest.fixture
def environment(monkeypatch):
    tickers = [f'{600000+i}.SH' for i in range(500)]
    bars = [{'ticker': t, 'date': d, 'open': 10, 'high': 11, 'low': 9, 'close': 10,
             'pre_close': 9, 'volume': 100, 'amount': 1000} for d in (PRIOR, DAY) for t in tickers]
    bars += [{'ticker': t, 'date': DAY, 'close': 3000} for t in ('000001.SH','000300.SH','399001.SZ','399006.SZ')]
    data = {'market_daily_bars': Collection(bars),
        'daily_basic': Collection({'ts_code':t, 'trade_date':DAY.replace('-',''),
                                  'turnover_rate':2, 'circ_mv':10000} for t in tickers),
        'moneyflow': Collection({'ts_code':t, 'trade_date':DAY.replace('-',''), 'net_mf_amount':20} for t in tickers),
        'daily_price_limits': Collection({'ticker':t, 'trade_date':DAY, 'up_limit':11, 'down_limit':9} for t in tickers),
        'review_board_reasons': Collection({'date':d, 'reasons': {'600001':'AI应用'}} for d in (PRIOR,DAY)),
        'cffex_positions_daily': Collection([{'analysis_date':DAY, 'payload':{'status':'ok'}}]),
        'review_previous_limit_pools': Collection()}
    store = SimpleNamespace(collection=lambda name: data.setdefault(name, Collection()))
    pools = {'up': {d: {'date':d, 'complete':True, 'snapshot_mode':'settled',
                        'stocks':[{'ticker':'600001.SH','code':'600001'}]} for d in (PRIOR,DAY)},
             'broken': {DAY:{'date':DAY,'complete':True,'stocks':[]}}}
    monkeypatch.setattr(dep, 'load_review_sessions', lambda *a: [PRIOR, DAY])
    monkeypatch.setattr(dep, '_pools', lambda _s, days, kind='up': {d:deepcopy(v) for d,v in pools[kind].items() if d in days})
    monkeypatch.setattr('shilun.market.taxonomy.classify_stocks', lambda _s,d,rows,**kw:
        [{**r,'classification_status':'ready','taxonomy_date':d,'taxonomy_version':VERSION,'industry':'元件'} for r in rows])
    monkeypatch.setattr('shilun.data.cffex_positions.assess_snapshot', lambda p,d:
        {'successful_count':4 if p.get('status') == 'ok' else 0,'expected_count':4})
    return store, data, pools


def checks(store):
    return {r['key']:r for r in dep.inspect_dependencies(store, DAY)}


def test_same_day_inputs_ready_and_verified_previous_cohort_avoids_optional_fetch(environment):
    store, _, _ = environment
    result = checks(store)
    for key in ('trade_calendar','daily_bars','previous_daily_bars','benchmark_bars','daily_basic',
                'moneyflow','price_limits','industry_taxonomy','board_pools','limit_reasons','previous_limit_pool','cffex_positions'):
        assert result[key]['ready'], (key,result[key])
        assert result[key]['date'] == DAY
        assert result[key]['revision']
    with patch.object(dep, '_prepare_source') as fetch:
        actual = dep.prepare_dependency(store, DAY, 'previous_limit_pool')
    fetch.assert_not_called()
    assert actual['cached'] is True and actual['source'] == 'dated_pool_and_daily_close'


def test_late_arriving_industry_mapping_changes_only_its_input_revision(environment, monkeypatch):
    store, _, _ = environment
    complete = checks(store)
    monkeypatch.setattr('shilun.market.taxonomy.classify_stocks', lambda _s,d,rows,**kw:
        [{**r,'classification_status':'taxonomy_missing','taxonomy_date':d,'taxonomy_version':VERSION} for r in rows])
    missing = checks(store)
    assert not missing['industry_taxonomy']['ready'] and missing['industry_taxonomy']['blocking']
    assert missing['industry_taxonomy']['revision'] != complete['industry_taxonomy']['revision']
    assert missing['daily_bars']['revision'] == complete['daily_bars']['revision']
    assert missing['industry_taxonomy']['consumers'] == ['strong_stocks','review']


def test_one_unmapped_stock_is_degraded_without_falsely_blocking_all_analysis(environment, monkeypatch):
    store, _, _ = environment
    def classify(_s, day, rows, **kwargs):
        assert kwargs == {'industry_only':True}
        return [{**r,'classification_status':'ready' if i else 'industry_missing',
                 'taxonomy_date':day,'taxonomy_version':VERSION} for i,r in enumerate(rows)]
    monkeypatch.setattr('shilun.market.taxonomy.classify_stocks', classify)
    result = checks(store)['industry_taxonomy']
    assert result['ready'] and result['status'] == 'degraded'
    assert result['count'] == 499 and result['total'] == 500


def test_wrong_day_and_legacy_industry_rows_never_count(environment, monkeypatch):
    store, _, _ = environment
    monkeypatch.setattr('shilun.market.taxonomy.classify_stocks', lambda _s,d,rows,**kw:
        [{**r,'classification_status':'ready','taxonomy_date':PRIOR,'taxonomy_version':'legacy'} for r in rows])
    assert checks(store)['industry_taxonomy']['ready'] is False


def test_missing_current_bars_or_previous_join_blocks_dependent_analysis(environment):
    store, data, _ = environment
    data['market_daily_bars'].rows = [r for r in data['market_daily_bars'].rows if r['date'] != PRIOR]
    result = checks(store)
    assert not result['previous_daily_bars']['ready'] and result['previous_daily_bars']['blocking']
    data['market_daily_bars'].rows = []
    assert checks(store)['daily_bars']['ready'] is False


def test_futures_is_optional_and_wrong_day_never_makes_it_ready(environment):
    store, data, _ = environment
    data['cffex_positions_daily'].rows = [{'analysis_date':PRIOR,'payload':{'status':'ok'}}]
    result = checks(store)['cffex_positions']
    assert result['status'] == 'missing' and not result['ready']
    assert result['blocking'] is False and result['consumers'] == ['review']


def test_event_inputs_use_reasons_not_industry_and_zero_broken_pool_is_valid(environment):
    store, data, _ = environment
    data['review_board_reasons'].rows = [{'date':PRIOR,'reasons':{'600001':'旧题材'}}]
    result = checks(store)
    assert result['board_pools']['ready']  # Explicit complete empty broken pool is known zero.
    assert not result['limit_reasons']['ready'] and result['limit_reasons']['blocking']
    assert result['limit_reasons']['consumers'] == ['theme_cycles']
    assert result['industry_taxonomy']['ready']


def test_partial_reason_coverage_keeps_gap_and_is_not_silently_complete(environment):
    store, _, pools = environment
    pools['up'][DAY]['stocks'].append({'ticker':'600002.SH','code':'600002'})
    result = checks(store)['limit_reasons']
    assert result['ready'] and result['status'] == 'degraded'
    assert result['days'][-1]['count'] == 1 and result['days'][-1]['total'] == 2


def test_unchanged_data_timestamps_do_not_trigger_rebuild_but_changed_prices_do(environment):
    store, data, pools = environment
    before = checks(store)
    data['market_daily_bars'].rows[0]['fetched_at'] = 'later'
    pools['up'][DAY]['fetched_at'] = 'later'
    assert checks(store)['board_pools']['revision'] == before['board_pools']['revision']
    assert checks(store)['previous_daily_bars']['revision'] == before['previous_daily_bars']['revision']
    data['market_daily_bars'].rows[0]['close'] = 10.5
    assert checks(store)['previous_daily_bars']['revision'] != before['previous_daily_bars']['revision']
    assert checks(store)['review_history']['revision'] != before['review_history']['revision']


def test_preparation_repairs_one_source_and_checks_stored_result_again():
    store = object()
    first={'key':'daily_basic','ready':False,'status':'missing'}
    second={'key':'daily_basic','ready':True,'status':'ready'}
    with patch.object(dep, 'inspect_dependencies', side_effect=[[first],[second]]), patch.object(dep,'_prepare_source',return_value={'row_count':500}) as source:
        result=dep.prepare_dependency(store,DAY,'daily_basic')
    source.assert_called_once_with(store,DAY,'daily_basic')
    assert result['cached'] is False and result['source_result']['row_count'] == 500


def test_fetch_claiming_success_without_same_day_evidence_remains_failure():
    state={'key':'moneyflow','ready':False,'reason':'同日缺失'}
    with patch.object(dep,'inspect_dependencies',return_value=[state]), patch.object(dep,'_prepare_source',return_value={'row_count':500}):
        with pytest.raises(ValueError,match='补数后仍未就绪'):
            dep.prepare_dependency(object(),DAY,'moneyflow')


def test_wrong_date_provider_frame_is_rejected_before_any_write():
    frame=pd.DataFrame([{'trade_date':PRIOR,'ts_code':'600001.SH','turnover_rate':1,'circ_mv':10}])
    store=Mock()
    with patch.object(dep,'load_review_sessions',return_value=[PRIOR,DAY]),patch.object(dep,'_client',return_value=SimpleNamespace(fetch_daily_basic=lambda **k:frame)):
        with pytest.raises(ValueError,match='日期不匹配'):
            dep._prepare_source(store,DAY,'daily_basic')
    store.upsert_daily_basic.assert_not_called()


def test_partial_industry_publication_reports_error_instead_of_claiming_ready():
    with patch.object(dep,'load_review_sessions',return_value=[PRIOR,DAY]),patch('shilun.market.industry_history.sync_industry_history',return_value={'pending':[DAY],'diagnostics':[{'code':'BK1234.DC','error_type':'ValueError'}]}) as repair:
        with pytest.raises(ValueError,match='三级行业未发布'):
            dep._prepare_source(object(),DAY,'industry_taxonomy')
    assert repair.call_args.args[1] == [DAY]


def test_review_source_errors_preserve_sanitized_validation_detail():
    from shilun.review.dated_pools import sync_review_history
    with patch('shilun.review.dated_pools.load_review_sessions',side_effect=ValueError('calendar mismatch token=secret-value')):
        result=sync_review_history(object(),DAY)
    assert result[0]['error_type'] == 'ValueError'
    assert 'calendar mismatch' in result[0]['error_message']
    assert 'secret-value' not in result[0]['error_message']


def test_price_history_mode_does_not_retry_unrelated_yesterday_pool():
    from shilun.review.dated_pools import sync_dated_evidence
    db=Mock()
    db.collection.return_value.find.return_value=[]
    db.collection.return_value.distinct.return_value=[f'{600000+i}.SH' for i in range(5000)]
    with patch('shilun.review.dated_pools.fetch_previous') as previous:
        assert sync_dated_evidence(db,[PRIOR,DAY],include_previous=False) == []
    previous.assert_not_called()


def test_missing_calendar_cannot_borrow_previous_session_as_today(environment, monkeypatch):
    store,_,_=environment
    monkeypatch.setattr(dep,'load_review_sessions',lambda *a:[PRIOR])
    result=checks(store)
    assert not result['trade_calendar']['ready']
    assert not result['previous_daily_bars']['ready']


def test_partial_source_still_consumable_but_is_retried_independently():
    first={'key':'cffex_positions','ready':True,'status':'degraded','complete':False,'needs_retry':True}
    second={**first,'status':'ready','complete':True,'needs_retry':False}
    with patch.object(dep,'inspect_dependencies',side_effect=[[first],[second]]),patch.object(dep,'_prepare_source',return_value={'status':'success'}) as repair:
        result=dep.prepare_dependency(object(),DAY,'cffex_positions')
    repair.assert_called_once()
    assert not result['cached'] and result['complete']


def test_frozen_previous_pool_must_agree_with_actual_current_close(environment):
    store,data,pools=environment
    pools['up'].pop(PRIOR)
    data['review_previous_limit_pools'].rows=[{'analysis_date':DAY,'previous_date':PRIOR,
        'rows':[{'ticker':'600001.SH','close':9,'return_1d':.1}]}]
    assert not checks(store)['previous_limit_pool']['ready']
    data['review_previous_limit_pools'].rows[0]['rows'][0]['close']=10
    assert checks(store)['previous_limit_pool']['ready']


def test_core_history_fingerprints_exact_old_prices_limits_and_suspensions_without_fetch(environment, monkeypatch):
    store,data,_=environment
    old='2026-10-30'
    data['stock_basic']=Collection([{'ts_code':'600001.SH','name':'元件','list_date':'20200101'}])
    data['market_daily_bars'].rows.append({'date':old,'ticker':'600001.SH','close':9,'open':9,'high':9,'low':9,'volume':100})
    data['daily_price_limits'].rows.append({'trade_date':old,'ticker':'600001.SH','up_limit':9,'pre_close':8})
    monkeypatch.setattr(dep,'load_review_sessions',lambda *a:[old,PRIOR,DAY])
    before=checks(store)
    data['market_daily_bars'].rows[-1]['close']=9.1
    price_change=checks(store)
    assert price_change['core_history']['revision'] != before['core_history']['revision']
    assert price_change['daily_bars']['revision'] == before['daily_bars']['revision']
    data['daily_price_limits'].rows[-1]['up_limit']=9.2
    limit_change=checks(store)
    assert limit_change['core_history']['revision'] != price_change['core_history']['revision']
    data['review_daily_suspensions']=Collection([{'date':old,'ticker':'600001.SH','suspend_type':'S','suspend_timing':None}])
    halt_change=checks(store)
    assert halt_change['core_history']['revision'] != limit_change['core_history']['revision']
    assert halt_change['core_history']['consumers'] == ['strong_stocks','theme_cycles']
    assert not halt_change['core_history']['needs_retry']
    assert halt_change['core_history']['status'] == 'degraded'  # Incomplete120-day history stays explicit.
    with patch.object(dep,'_prepare_source') as repair:
        result=dep.prepare_dependency(store,DAY,'core_history')
    repair.assert_not_called()
    assert result['cached']


def test_historical_reason_repair_changes_theme_only_and_not_current_or_core_evidence(environment, monkeypatch):
    store,data,pools=environment
    old='2026-10-30'
    data['review_board_reasons'].rows.append({'date':old,'reasons':{'600001':'旧标签'}})
    pools['up'][old]={'date':old,'complete':True,'stocks':[{'ticker':'600001.SH','code':'600001'}]}
    monkeypatch.setattr(dep,'load_review_sessions',lambda _s,_d,*args:[old,PRIOR,DAY] if args else [PRIOR,DAY])
    before=checks(store)
    data['review_board_reasons'].rows[-1]['reasons']['600001']='修复后的事件标签'
    after=checks(store)
    assert after['theme_history']['revision'] != before['theme_history']['revision']
    assert after['limit_reasons']['revision'] == before['limit_reasons']['revision']
    assert after['core_history']['revision'] == before['core_history']['revision']
    assert after['theme_history']['consumers'] == ['theme_cycles']
    assert not after['theme_history']['needs_retry']
    with patch.object(dep,'_prepare_source') as repair:
        dep.prepare_dependency(store,DAY,'theme_history')
    repair.assert_not_called()


def test_streamed_content_digest_is_order_independent_and_keeps_semantic_changes():
    rows=[{'ticker':'600001.SH','date':PRIOR,'close':10},{'ticker':'600002.SH','date':PRIOR,'close':20}]
    baseline=dep._stream_fingerprint(iter(rows))
    assert dep._stream_fingerprint(iter(reversed(rows))) == baseline
    rows[0]['fetched_at']='later'
    assert dep._stream_fingerprint(iter(rows)) == baseline
    rows[0]['close']=10.1
    assert dep._stream_fingerprint(iter(rows)) != baseline


def test_theme_window_uses_checkpoint_context_even_when_start_is_not_first_json_key(environment, monkeypatch, tmp_path):
    import gzip
    import json
    store,_,_=environment
    source=tmp_path/'inputs'/'snapshot'
    source.mkdir(parents=True)
    (tmp_path/'inputs.json').write_text(json.dumps({'source':'inputs/snapshot'}))
    with gzip.open(source/'market_inputs.json.gz','wt',encoding='utf8') as handle:
        json.dump({'schema':1,'start':'2026-10-01','context_days':['2026-09-22','2026-10-01']},handle)
    monkeypatch.setattr('shilun.jobs.theme_daily_job.ROOT',tmp_path)
    requested=[]
    monkeypatch.setattr(dep,'load_review_sessions',lambda _s,_d,start:requested.append(start) or ['2026-09-22',PRIOR,DAY])
    days,basis=dep._theme_window(store,DAY)
    assert days[0] == '2026-09-22' and requested == ['2026-09-22']
    assert '基线' in basis


def test_index_history_repairs_missing_old_day_even_when_latest_day_exists(environment):
    store,_,_=environment
    written=[]
    store.upsert_daily_bars=lambda frame:written.extend(frame.to_dict('records')) or len(frame)
    client=Mock()
    client.fetch_index_daily.side_effect=lambda ticker,start,end:pd.DataFrame([
        {'ticker':ticker,'date':pd.Timestamp(PRIOR),'close':3000}])
    with patch.object(dep,'_client',return_value=client):
        result=dep._prepare_source(store,DAY,'index_history')
    assert len(result['reports']) == 4 and len(written) == 4
    assert all(call.args[1:] == ('20261102','20261102') for call in client.fetch_index_daily.call_args_list)
