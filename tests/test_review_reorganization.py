"""Reorganization invariants: dated topic roles, one snapshot, one engine."""
from types import SimpleNamespace
from shilun.review.theme_evidence import front_support
from shilun.review.theme_scope import topic_scope
from shilun.review.service import DailyReviewService
from shilun.market.strategy_candidates import review_candidate_payload, review_facet
from shilun.jobs.cloud_scheduler import build_scheduler


def row(ticker, boards, industry='农化制品', day='2026-09-08'):
    return dict(ticker=ticker, name=ticker, consecutive_count=boards, sector_name=industry, trade_date=day)


def test_industry_proxy_does_not_manually_union_concept_constituents():
    for ticker, industry in [('002470.SZ','农化制品'),('000912.SZ','农化制品'),('000930.SZ','农产品加'),('600108.SH','种植业')]:
        assert topic_scope(row(ticker,1,industry))[:2] == (industry,'industry_proxy')
    r=row('000523.SZ',1,'综合Ⅱ')
    assert topic_scope(r)[:2] == ('综合Ⅱ','industry_proxy')
    assert topic_scope(dict(r,topic='其他当日题材'))[:2] == ('其他当日题材','dated_topic')


def test_four_followers_remain_back_row_when_their_today_prices_change():
    yesterday=[row('600108.SH',4,'种植业'),row('600354.SH',3,'种植业'),
               row('000523.SZ',1,'综合Ⅱ'),row('002470.SZ',2),row('000930.SZ',1,'农产品加'),row('000912.SZ',1)]
    history=[('2026-09-08',r) for r in yesterday]
    # Earlier core fails yesterday. Its past evidence must remain in today's denominator.
    history.insert(0,('2026-09-07',row('600127.SH',4,'农产品加','2026-09-07')))
    previous={r['ticker']: {'close':10,'amount':100000} for _,r in history}
    def run(close):
        current={t:dict(close=close,high=15,low=5) for t in previous}
        return front_support(yesterday,current,previous,current_rows=[],history_rows=history)
    first,second=run(5),run(15)
    assert [(r['ticker'],r['front']) for r in first['rows']] == [(r['ticker'],r['front']) for r in second['rows']]
    roster={r['ticker']:r for r in first['rows']}
    assert all(roster[t]['front'] is False for t in ['000523.SZ','002470.SZ','000930.SZ','000912.SZ'])
    assert roster['600127.SH']['front'] is True and roster['600127.SH']['boards']==0
    assert roster['600127.SH']['return_1d'] == -.5


def test_research_candidates_read_exact_day_and_do_not_recompute():
    requests=[]
    class Collection:
        def find_one(self,q,p):
            requests.append((q,p))
            return {'analysis_date':'2026-09-09','tactics':{'weekly_breakout':{'label':'周K突破','count':1,'items':[{'ticker':'A'}]}}}
    store=SimpleNamespace(collection=lambda _:Collection())
    payload=review_candidate_payload(store,'2026-09-09','weekly_breakout')
    assert payload['candidates']==[{'ticker':'A'}] and payload['research_only']
    assert requests[0][0]['analysis_date']=='2026-09-09'
    assert requests[0][1]['tactics']==1
    empty=SimpleNamespace(collection=lambda _:SimpleNamespace(find_one=lambda *_:None))
    assert review_facet(empty,'2026-09-08','expectations')['status']=='not_materialized'


def test_pattern_forecast_uses_shared_r1_engine_and_no_future_bars():
    from datetime import date,timedelta
    history=[dict(ticker='000001.SH',date=(date(2026,1,1)+timedelta(days=i)).isoformat(),open=100+i,close=101+i,high=102+i,low=99+i,amount=1e6,volume=1000) for i in range(125)]
    day=history[-1]['date']
    evidence=DailyReviewService._pattern_evidence(history,day,'000001.SH','上证指数')
    assert evidence['status']=='ready' and evidence['pattern_forecast']['primary_pattern']
    assert max(r['date'] for r in evidence['chart_data']['wave_series'])==day
    assert DailyReviewService._pattern_evidence(history[:3],history[2]['date'],'A','A')['status']=='missing'


def test_cloud_data_only_does_not_start_trading_or_message_jobs():
    scheduler=build_scheduler(data_only=True)
    ids={j.id for j in scheduler.get_jobs()}
    assert ids=={'tushare_morning_0500','tushare_morning_0700','price_limits_0845','tushare_close_1715','tushare_close_1915','minute_bars_1515','daily_review_retry'}
    assert scheduler._executors['default']._pool._max_workers==1
    for job in scheduler.get_jobs():
        assert str(job.trigger.timezone)=='Asia/Shanghai'
