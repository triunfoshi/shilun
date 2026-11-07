import pandas as pd
import pytest
from shilun.jobs.daily_basic_backfill_job import repair_day


class Store:
    def __init__(self):
        self.rows={'stock_basic':[{'ts_code':'a'},{'ts_code':'b'},{'ts_code':'paused'}],
            'market_daily_bars':[{'date':'2026-09-10','ticker':t,'volume':v} for t,v in [('a',10),('b',20),('paused',0),('index',30)]],
            'daily_basic':[{'trade_date':'20260910','ts_code':'a','turnover_rate':2}]}
        self.writes=[]
    def collection(self,name):
        class Collection:
            def find(_,query,projection):
                return [r for r in self.rows[name] if all(r.get(k)==v for k,v in query.items())]
        return Collection()
    def upsert_daily_basic(self,rows):
        self.writes+=rows
        for row in rows:
            self.rows['daily_basic'].append({**row,'trade_date':pd.Timestamp(row['trade_date']).strftime('%Y%m%d')})
        return len(rows)


class Client:
    def __init__(self,rows):self.rows=rows;self.calls=0
    def fetch_daily_basic(self,trade_date):
        self.calls+=1
        return pd.DataFrame(self.rows)


def test_repair_only_missing_active_stocks_and_second_run_does_not_fetch():
    store=Store();client=Client([{'trade_date':'20260910','ts_code':t,'turnover_rate':v} for t,v in [('a',99),('b',3)]])
    before=repair_day(store,client,'2026-09-10')
    assert before['missing_before']==1 and client.calls==0 and store.writes==[]
    result=repair_day(store,client,'2026-09-10',apply=True)
    assert result['stored_n']==1 and result['remaining_n']==0
    assert store.rows['daily_basic'][0]['turnover_rate']==2
    assert store.writes[0]['ts_code']=='b'
    assert repair_day(store,client,'2026-09-10',apply=True)['stored_n']==0 and client.calls==1


@pytest.mark.parametrize('rows',[
    [{'trade_date':'20260911','ts_code':'b','turnover_rate':3}],
    [{'trade_date':'20260910','ts_code':'b','turnover_rate':3}]*2,
])
def test_wrong_date_or_duplicate_source_rejected_before_write(rows):
    store=Store()
    with pytest.raises(ValueError):repair_day(store,Client(rows),'2026-09-10',apply=True)
    assert store.writes==[]


def test_missing_source_turnover_remains_missing_not_synthesized():
    store=Store()
    result=repair_day(store,Client([{'trade_date':'20260910','ts_code':'b','turnover_rate':None}]),'2026-09-10',apply=True)
    assert result['remaining_tickers']==['b'] and result['stored_n']==0
