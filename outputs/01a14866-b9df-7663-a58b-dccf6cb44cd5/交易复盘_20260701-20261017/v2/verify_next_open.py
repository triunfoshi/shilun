#!/usr/bin/env python3
"""Independent, source-based arithmetic and inventory checks of gap_analysis."""
import collections
import hashlib
import json
from decimal import Decimal as D
from pathlib import Path
P=Path('/private/tmp/shilun-trade-review-202607')
R=json.loads((P/'v2/gap_analysis.json').read_text())
E={e['episode_id']:e for e in json.loads((P/'episodes.json').read_text())}
M=json.loads((P/'matched_lots.json').read_text())
B={(b['ticker'][:6],b['date']):b for b in json.loads((P/'market/daily_bars.json').read_text()) if b['ticker'] not in ['000001.SH','000300.SH','399006.SZ']}
C=sorted(f"{c['cal_date'][:4]}-{c['cal_date'][4:6]}-{c['cal_date'][6:8]}" for c in json.loads((P/'market/calendar_api.json').read_text()) if c['is_open'])
checks=[]
def check(name,truth):
    assert truth,name
    checks.append(name)
for name,sha in R['metadata']['source_sha256'].items():
    check('source sha '+name,hashlib.sha256((P/name).read_bytes()).hexdigest()==sha)
count=collections.Counter()
for r in R['episodes']:
    e=E[r['episode_id']];day=e['first_buy_date']
    buys=[x for x in e['event_details'] if x['date']==day and x['kind']=='证券买入']
    qty=sum(D(str(x['qty'])) for x in buys);cost=-sum(D(str(x['net'])) for x in buys)
    check(e['episode_id']+' first day quantity',qty==D(str(r['first_day_quantity'])))
    check(e['episode_id']+' first day cost',cost==D(str(r['first_day_cost'])))
    future=[x for x in C if x>day]
    check(e['episode_id']+' calendar',r['next_market_date']==future[0] and r['third_market_date']==future[1])
    b=B[(e['code'],future[0])];f=B[(e['code'],day)]
    value=(D(str(b['open']))*D(str(b['adj_factor']))/D(str(f['adj_factor']))*qty/cost-1)*100
    check(e['episode_id']+' gap ratio',abs(value-D(str(r['next_open_vs_first_day_cost_pct'])))<D('.000000001'))
    key='down5' if value<=-5 else 'down2to5' if value<=-2 else 'other'
    count[key]+=1
    same=sum(D(str(m['pnl'])) for m in M if m['episode_id']==e['episode_id'] and m['buy_date']==day)
    check(e['episode_id']+' same first-day lots',abs(same-D(str(r['first_day_actual_pnl'])))<=D('.005'))
    for s in r['strategies'].values():
        if 'price_benchmark_pnl' in s:
            expected=D(str(s['raw_price']))*qty-D(str(s['estimated_sell_fee']))-cost
            check(e['episode_id']+' '+s['strategy']+' cash',abs(expected-D(str(s['price_benchmark_pnl'])))<=D('.005'))
            if s['eligible']:
                check(e['episode_id']+' '+s['strategy']+' not limit queued',not s['one_price_down_day'] and not s['at_inferred_down_limit'])
    inventory=0
    for day2 in sorted({x['date'] for x in e['event_details']}):
        events=[x for x in e['event_details'] if x['date']==day2]
        # Day's cumulative sells cannot use purchases made that same day.
        check(e['episode_id']+' '+day2+' T+1',sum(x['qty'] for x in events if x['kind']=='证券卖出')<=inventory)
        inventory+=sum(x['qty']*(1 if x['kind']=='证券买入' else -1) for x in events)
    check(e['episode_id']+' closed inventory',inventory==0)
check('independent gap buckets',count=={'down5':32,'down2to5':30,'other':96})
check('full actual pnl',abs(sum(D(str(r['actual_pnl']))for r in R['episodes'])+D('48511.84'))<D('.001'))
check('all next sessions present',all(r['next_bar'] for r in R['episodes']))
check('original 13 differs from next-open group',R['original_13_uptrend_entry_down5']['next_open_groups']['次日开盘较首日含费均价跌≥5%']==7)
low=[r for r in R['episodes'] if r['next_open_vs_first_day_cost_pct']<=-5]
eligible=[r for r in low if r['strategies']['next_market_open']['eligible']]
later_pnl=sum(D(str(r['actual_pnl']))-D(str(r['first_day_actual_pnl'])) for r in eligible)
first_only_delta=sum(D(str(r['strategies']['next_market_open']['price_benchmark_pnl']))-D(str(r['first_day_actual_pnl'])) for r in eligible)
check('low5 later-lot loss',later_pnl==D('-8300.36'))
check('low5 first-lot open disadvantage',first_only_delta==D('-1241.62'))
check('low5 total scenario decomposition',first_only_delta-later_pnl==D('7058.74'))
g=next(r for r in R['episodes'] if r['code']=='601086')
check('Guofang not low5',-5<g['next_open_vs_first_day_cost_pct']<-2)
check('Guofang original-first-buy down5',g['entry_vs_prior_close_pct']<-5)
check('Guofang close near breakeven',g['strategies']['next_market_close']['price_benchmark_pnl']==39.20)
check('Guofang 10bp flips slight profit',g['strategies']['next_market_close']['pnl_after_10bp_sell_slippage']==-25.0)
result={'status':'passed','checks_passed':len(checks),'independent_bucket_count':dict(count),'low5_open_delta_decomposition':{'first_day_share_exit_difference':float(first_only_delta),'avoided_actual_later_buy_lot_pnl':float(later_pnl),'full_episode_difference':float(first_only_delta-later_pnl)},'check_names':checks}
(P/'v2/next_open_verification.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
print(json.dumps({k:v for k,v in result.items() if k!='check_names'},ensure_ascii=False,indent=2))
