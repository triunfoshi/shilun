"""Range-return research with dated market, stock and exchange evidence."""
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
import json
import math

from pymongo import ReplaceOne

from shilun.market.research_scope import stock_allowed
from shilun.review.sources import at_limit, finite_number as num, load_review_sessions

MARKET = 'review_strength_market_days'
LHB = 'review_strength_lhb_days'
VERSION = 'strength-review-v1'


def selected_sessions(store, end, start=None, window=20):
    history_start = (date.fromisoformat(end) - timedelta(days=270)).isoformat()
    days = load_review_sessions(store, end, history_start)
    if not days or days[-1] != end:
        raise ValueError('缺少所选日交易日历')
    if start:
        if start > end:
            raise ValueError('开始日期不能晚于结束日期')
        chosen = [d for d in days if start <= d <= end]
    else:
        chosen = days[-window:]
    if not chosen or len(chosen) > 120:
        raise ValueError('请选择1至120个交易日的区间')
    return chosen


def market_day(day, stocks, bars, limits, suspensions):
    counts = {m: dict(up=0, broken=0, down=0, covered=0, expected=0) for m in ('SH', 'SZ', 'BJ')}
    for meta in stocks:
        t = meta['ts_code']
        if str(meta.get('list_date') or '').replace('-', '') > day.replace('-', ''):
            continue
        c, bar, limit = counts[t[-2:]], bars[t].get(day, {}), limits[t].get(day, {})
        if day in suspensions.get(t, ()):
            continue
        c['expected'] += 1
        close, high, low, up, down = [num(v) for v in (bar.get('close'), bar.get('high'), bar.get('low'), limit.get('up_limit'), limit.get('down_limit'))]
        if any(v is None or v <= 0 for v in (close, high, low, up, down)) or (num(bar.get('volume')) or 0) <= 0 or not low <= close <= high or close > up+.005 or close < down-.005:
            continue
        c['covered'] += 1
        c['up'] += int(bool(at_limit(close, up)))
        c['down'] += int(bool(at_limit(close, down)))
        c['broken'] += int(high >= up-.005 and not at_limit(close, up))
    counts['ALL'] = {k: sum(c[k] for c in counts.values()) for k in ('up','broken','down','covered','expected')}
    for c in counts.values():
        c['seal_rate'] = c['up']/(c['up']+c['broken']) if c['up']+c['broken'] else None
        c['complete'] = c['covered'] == c['expected']
    return {'date': day, 'version': VERSION, 'markets': counts,
            'scope': '沪深京A股，剔除当前ST和退市股；按当日官方涨跌停价及日线核验。',
            'note': '炸板=日内触及涨停、收盘未封住；封板率=涨停/(涨停+炸板)。数字为已核验样本计数，覆盖不足时不是全市场精确总数。'}


def materialize_market_stats(store, days, stocks, bars, limits, suspensions):
    writes = [ReplaceOne({'_id': f'{VERSION}:{d}'}, {'_id': f'{VERSION}:{d}', **market_day(d, stocks, bars, limits, suspensions)}, upsert=True) for d in days]
    if writes:
        store.collection(MARKET).bulk_write(writes, ordered=False)


def rank_range(store, end, start=None, window=20, limit=50):
    from shilun.review.strong_stock_data import a_share, load_snapshot, _future_sessions
    from shilun.market.taxonomy import classify_stocks
    days = selected_sessions(store, end, start, window)
    basics = {r['ts_code']: r for r in store.collection('stock_basic').find({}, {'_id': 0})
              if a_share(r.get('ts_code','')) and stock_allowed(r, end)}
    # Aggregate next to the archive: returning a ranked cross-section avoids
    # streaming 120 days of all-market prices through the desktop tunnel.
    valid = {'$and': [{'$gt':['$close',0]}, {'$gt':['$pre_close',0]}, {'$gt':['$volume',0]}]}
    pipeline = [
        {'$match': {'date': {'$in': days}, 'ticker': {'$in': list(basics)}}},
        {'$sort': {'date': 1}},
        {'$group': {'_id': '$ticker', 'observed': {'$sum': 1}, 'valid': {'$sum': {'$cond':[valid,1,0]}},
            'log_return': {'$sum': {'$cond':[valid, {'$ln':{'$divide':['$close','$pre_close']}}, 0]}},
            'close': {'$last':'$close'}, 'pre_close': {'$last':'$pre_close'}, 'start':{'$first':'$date'}, 'end':{'$last':'$date'}}},
        {'$match': {'observed': len(days), 'valid': len(days), 'start': days[0], 'end': end}},
        {'$sort': {'log_return': -1, '_id': 1}}, {'$limit': limit}]
    raw = list(store.collection('market_daily_bars').aggregate(pipeline, maxTimeMS=60000, allowDiskUse=True))
    # This table only displays industries. A complete same-day L3 projection
    # is sufficient; it must not depend on the separate concept catalog.
    classified = {r['ts_code']: r for r in classify_stocks(
        store, end, [basics[r['_id']] for r in raw], industry_only=True)}
    cores = {r['ticker']: r for r in load_snapshot(store, end).get('rows', [])}
    rows = []
    for rank, r in enumerate(raw, 1):
        t, gain = r['_id'], math.expm1(r['log_return'])
        if not math.isfinite(gain):
            continue
        core = cores.get(t, {})
        industry_fields = {k: classified[t].get(k) for k in ('industry', 'industry_code', 'industry_source',
            'taxonomy_version', 'taxonomy_date', 'taxonomy_scope', 'classification_status')}
        rows.append({'ticker':t,'name':basics[t].get('name') or t,**industry_fields,
            'rank':rank,'return':gain,'close':r['close'],'daily_return':r['close']/r['pre_close']-1,
            'start':days[0],'end':end,'sessions':len(days),'core_status':core.get('status'),
            'core_qualified_at':core.get('qualified_at'),'core_boards':(core.get('leg') or {}).get('qualification_board_count'),
            'core_detail_id':core.get('detail_id')})
    calendar = load_review_sessions(store, end)
    from shilun.review.board_data import settled_target
    future = [d for d in _future_sessions(store, end) if d <= settled_target()]
    return {'date':end,'start':days[0],'days':days,'rows':rows,'limit':limit,'version':VERSION,
        'previous_date':calendar[-2] if len(calendar)>1 else None, 'next_date':future[0] if future else None,
        'rule':'按所选区间累计涨幅从高到低排列，包含开始日涨幅。强势股不要求五板；核心资格另按三连板、十日内五板判断。剔除ST、退市股及区间行情不完整的股票。',
        'scope':'沪深京A股；区间内日线及有效昨收完整。历史筛选使用当前证券状态，非严格事前回测。'}


def stock_detail(store, ticker, end, start=None, window=20):
    from shilun.review.strong_stock_data import a_share, iso
    from shilun.review.strong_stock_candidates import daily_evidence
    from shilun.review.board_data import REASON_COLLECTION
    meta = store.collection('stock_basic').find_one({'ts_code':ticker}, {'_id':0})
    if not meta or not a_share(ticker) or not stock_allowed(meta, end):
        raise ValueError('该股票不在当前研究范围（已剔除ST及退市股）')
    days = selected_sessions(store, end, start, window)
    bars = {r['date']: r for r in store.collection('market_daily_bars').find({'ticker':ticker,'date':{'$in':days}}, {'_id':0})}
    limits = {iso(r['trade_date']): r for r in store.collection('daily_price_limits').find(
        {'ticker':ticker,'trade_date':{'$in':days+[d.replace('-','') for d in days]}}, {'_id':0})}
    reasons = {r['date']:r for r in store.collection(REASON_COLLECTION).find({'date':{'$in':days}}, {'_id':0})}
    market = {r['date']:r for r in store.collection(MARKET).find({'_id':{'$in':[f'{VERSION}:{d}' for d in days]}}, {'_id':0})}
    benchmark = {r['date']:r for r in store.collection('market_daily_bars').find({'ticker':'000001.SH','date':{'$in':days}}, {'_id':0})}
    rows, factor = [], 1.0
    for day in days:
        b = bars.get(day,{})
        e = daily_evidence(day,b,limits.get(day,{}))
        reason_doc = reasons.get(day,{})
        reason = reason_doc.get('reasons',{}).get(ticker.split('.')[0])
        p = num(b.get('pre_close'))
        # Relative adjusted OHLC for chart continuity; a gap stays a gap.
        chart = {k: factor*b[k]/p if factor is not None and p and num(b.get(k)) is not None else None for k in ('open','high','low','close')}
        factor = chart['close']
        bm = benchmark.get(day,{})
        bm_pre = num(bm.get('pre_close'))
        rows.append({**e,'amount':num(b.get('amount'))*1000 if num(b.get('amount')) is not None else None,
            'reason':reason if e['state']=='sealed' else None,'reason_source':reason_doc.get('source'),
            'market':market.get(day), 'benchmark_return':bm['close']/bm_pre-1 if bm_pre and num(bm.get('close')) is not None else None,
            'chart':chart})
    anchor = num(bars.get(end, {}).get('close'))
    scale = anchor / factor if anchor and factor else None
    for row in rows:
        row['chart'] = {k: v * scale if v is not None and scale is not None else None for k, v in row['chart'].items()}
    lhb = list(store.collection(LHB).find({'date':{'$in':days},'status':'ready'}, {'_id':0}))
    events = []
    for doc in lhb:
        for summary in doc.get('summaries',[]):
            if summary.get('ts_code') != ticker:
                continue
            seats = [r for r in doc.get('seats',[]) if r.get('ts_code')==ticker and r.get('reason')==summary.get('reason')]
            events.append({'date':doc['date'],'summary':summary,'seats':seats})
    events.sort(key=lambda r:r['date'],reverse=True)
    return {'ticker':ticker,'name':meta.get('name'),'date':end,'start':days[0],'rows':rows,
        'limit_dates':[r['date'] for r in rows if r['state']=='sealed'], 'lhb':events,
        'lhb_missing_dates':[d for d in days if d not in {r['date'] for r in lhb}],
        'reason_note':'涨停原因来自当日归因归档，未归档时留空；它不是统一概念目录，也不是公司确认。',
        'chart_note':'日K按有效昨收连乘复权，以区间末日收盘价为锚；悬停查看当日原始价格。缺行情不补零。'}


def sync_lhb(store, days, client=None):
    from shilun.data.providers.tushare_provider import TushareConfig, TushareDailyClient
    from shilun.review.strong_stock_data import iso
    client = client or TushareDailyClient(TushareConfig.from_env())
    reports=[]
    for day in days:
        if store.collection(LHB).find_one({'date':day,'status':'ready'}, {'_id':1}):
            continue
        try:
            groups=[]
            for api in ('top_list','top_inst'):
                frame=client._call_api(api, trade_date=day.replace('-',''))
                rows=json.loads(frame.to_json(orient='records',force_ascii=False))
                if len(rows)>=10000 or any(iso(r.get('trade_date'))!=day for r in rows):
                    raise ValueError('龙虎榜日期或完整性核验失败')
                groups.append(rows)
            store.collection(LHB).replace_one({'date':day},{'date':day,'status':'ready','summaries':groups[0],
                'seats':groups[1],'source':'tushare_top_list_top_inst','fetched_at':datetime.now(timezone.utc).isoformat()},upsert=True)
            reports.append({'date':day,'status':'ready','events':len(groups[0])})
        except Exception as error:
            reports.append({'date':day,'status':'unavailable','error_type':type(error).__name__})
    return reports


def minutes(store, ticker, day, refresh=False):
    from shilun.market.minute_bars_store import COLLECTION_NAME, upsert_minute_bars
    from shilun.data.providers.minute_provider import MinuteClient
    col=store.collection(COLLECTION_NAME)
    compact=day.replace('-','')
    docs=list(col.find({'ticker':ticker,'trade_date':compact,'interval':{'$in':['1','5']}},{'_id':0}))
    doc=next((r for r in docs if r['interval']=='1'),docs[0] if docs else {})
    if refresh:
        frame=MinuteClient().fetch_minute_bars(ticker,period='1',trade_date=day)
        rows=json.loads(frame.to_json(orient='records',date_format='iso',force_ascii=False))
        rows=[r for r in rows if str(r.get('datetime',''))[:10]==day]
        if not rows:
            raise ValueError('分钟源没有返回所选日期，未使用其他日期替代')
        upsert_minute_bars(col,ticker=ticker,trade_date=compact,interval='1',bars=rows,source=frame.attrs.get('source','minute_provider'))
        doc={'bars':rows,'interval':'1','source':frame.attrs.get('source','minute_provider')}
    bars=[r for r in doc.get('bars',[]) if str(r.get('datetime',''))[:10]==day]
    return {'ticker':ticker,'date':day,'status':'ready' if bars else 'missing','bars':bars,'interval':doc.get('interval'),
            'source':doc.get('source'),'reason':None if bars else '所选日分时尚未归档，可点击补齐分时。'}
