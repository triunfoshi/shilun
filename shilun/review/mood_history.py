"""Materialize missing P2 daily moods in one shared data scan for board effects."""
from statistics import mean
from datetime import date, datetime, timedelta, timezone
from shilun.review.market_mood import MOOD_COLLECTION, load_market_moods, mood_ready
from shilun.review.sources import at_limit, load_review_sessions


def backfill_moods(store, target, days=31, *, force=False, progress=None):
    from shilun.review.service import DailyReviewService, REVIEW_VERSION, REVIEW_COLLECTION, PREVIOUS_POOL_COLLECTION
    svc = DailyReviewService(store)
    start_date = (date.fromisoformat(target)-timedelta(days=(days+50)*2)).isoformat()
    sessions = load_review_sessions(store, target, start_date)
    wanted = sessions[-days:]
    existing = load_market_moods(store, wanted)
    known = {d for d, mood in existing.items() if mood_ready(mood)}
    missing = [d for d in wanted if force or d not in known]
    if not missing: return {'built': 0, 'pending': [], 'diagnostics': [], 'dates': wanted}
    start = max(0, sessions.index(missing[0])-40)
    dates = sessions[start:]
    # Industry migration made the analysis date mandatory. Never classify every
    # historical day with the final day's membership just to satisfy the API.
    basic = svc._stock_basic(missing[0], industry_only=True)
    if progress: progress({'stage': 'loading_bars', 'from': dates[0], 'to': dates[-1], 'missing_days': len(missing)})
    history, dated = svc._load_bars(dates, set(basic))
    if progress: progress({'stage': 'loading_price_limits', 'bar_days': len(dated)})
    prices = svc._price_limits_history(dates)
    pools, _ = svc._limit_rows(dates, basic)
    settled = {}
    for doc in store.collection(PREVIOUS_POOL_COLLECTION).find({'analysis_date': {'$in': dates}}, {'_id': 0}):
        day, prior = doc.get('analysis_date'), doc.get('previous_date')
        if day not in dates or dates.index(day)==0 or dates[dates.index(day)-1] != prior: continue
        rows = doc.get('rows') or []
        observed = [r for r in rows if at_limit(dated.get(day,{}).get(r['ticker'],{}).get('close'), r.get('close')) is True]
        if rows and len(observed)/len(rows) < .8: continue
        settled[day] = {**doc, 'verified_rows': observed}
        if prior not in pools:
            pools[prior] = [{**r, 'trade_date': prior, 'source': doc.get('source'),
                'sector_name': r.get('industry') or basic.get(r['ticker'],{}).get('sector') or '未分类'} for r in rows]
    index_rows = list(store.collection('market_daily_bars').find({'date': {'$in': dates},
        'ticker': {'$in': ['000001.SH','000300.SH','399001.SZ','399006.SZ']}}, {'_id':0,'date':1,'ticker':1,'close':1}))
    indices = {(r['date'],r['ticker']): r for r in index_rows}
    pending, diagnostics, built = [], [], 0
    for day in missing:
        i = dates.index(day)
        if i == 0:
            pending.append(day)
            diagnostics.append({'date': day, 'reason': '上一交易日历缺失', 'missing_inputs': ['上一交易日历']})
            continue
        prior = dates[i-1]; current = dated.get(day,{}); previous = dated.get(prior,{})
        if day != missing[0]: basic = svc._stock_basic(day, industry_only=True)
        amounts = svc._amount_series(dates[max(0,i-20):i+1],dated)
        market_indices = []
        for ticker in ['000001.SH','000300.SH','399001.SZ','399006.SZ']:
            close = indices.get((day,ticker),{}).get('close'); base = indices.get((prior,ticker),{}).get('close')
            market_indices.append({'ticker':ticker,'pct_chg':close/base-1 if close is not None and base else None})
        result = svc._sentiment(analysis_date=day, previous_date=prior, current_bars=current, previous_bars=previous,
            current_limits=pools.get(day,[]), previous_limits=pools.get(prior,[]), price_limits=prices.get(day,{}),
            bars_by_ticker=history, indices=market_indices, basic=basic,
            market_amount_yi=amounts[-1]['amount_yi'] if amounts else None,
            amount_ma20_yi=mean(a['amount_yi'] for a in amounts[-20:]) if amounts else None,
            up_ratio=svc._breadth(current,previous)['up_ratio'], limit_rows_by_date=pools,
            bars_by_date=dated, price_limits_by_date=prices, moneyflow={}, sessions=dates[:i+1],
            settled_pool=settled.get(day), mood_only=True)
        mood = result['market_mood']
        doc = {'date': day, 'previous_date': prior, 'mood': mood, 'engine_version': REVIEW_VERSION,
            'source': 'P2 shared sentiment engine', 'computed_at': datetime.now(timezone.utc).isoformat(),
            'historical_reconstruction': True}
        if mood['code']=='pending':
            pending.append(day)
            diagnostics.append({'date': day, 'reason': mood['reason'],
                'missing_inputs': mood.get('missing_inputs', []), 'input_coverage': mood.get('input_coverage', {})})
            # Retain an already verified result if an explicit forced retry
            # temporarily lacks inputs; otherwise archive the diagnostic.
            if day not in known:
                store.collection(MOOD_COLLECTION).replace_one({'date':day}, doc, upsert=True)
        else:
            risk = result['downside_risk']
            store.collection(REVIEW_COLLECTION).update_many({'analysis_date': day, 'engine_version': REVIEW_VERSION},
                {'$set': {'sentiment.market_mood': mood, 'sentiment.downside_risk': risk,
                          'sentiment.limit_down_wave.counts': risk['position_counts'],
                          'sentiment.limit_down_wave.stocks': risk['stocks']}})
            store.collection(MOOD_COLLECTION).replace_one({'date':day}, doc, upsert=True)
            built += 1
        if progress:
            progress({'stage': 'day', 'date': day, 'code': mood['code'], 'score': mood.get('score'),
                      'done': built + len(pending), 'total': len(missing), 'missing_inputs': mood.get('missing_inputs', [])})
    return {'built': built, 'pending': pending, 'diagnostics': diagnostics, 'dates': wanted}
