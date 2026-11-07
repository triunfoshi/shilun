"""Limit-down event groups share Vibe labels; historical catalysts stay dated."""
from collections import defaultdict

from shilun.review.board_data import REASON_COLLECTION
from shilun.review.vibe_shortline import clean_reason, tags_of


def attach_event_groups(store, risk, day, sessions):
    result = dict(risk or {})
    stocks = result.get('stocks') or []
    dates = sorted({d for d in sessions if d <= day})[-20:]
    if day not in dates:
        dates = (dates + [day])[-20:]
    tickers = sorted({r['ticker'] for r in stocks if r.get('ticker')})
    projection = {'_id': 0, 'date': 1, 'source': 1, 'source_note': 1,
                  **{f'reasons.{t.split(".")[0]}': 1 for t in tickers}}
    documents = list(store.collection(REASON_COLLECTION).find(
        {'date': {'$in': dates}}, projection)) if tickers else []
    dated = {}
    for doc in documents:
        if doc.get('date') not in dates:
            continue
        # A conflicting day is unknown, never chosen by database return order.
        d = doc['date']
        value = doc.get('reasons') or {}
        if d in dated and dated[d] != value:
            raise ValueError('跌停题材同日原因归档存在冲突')
        dated[d] = value
    groups, observed, missing = defaultdict(list), [], []
    for stock in stocks:
        ticker = stock.get('ticker', '')
        code = ticker.split('.')[0]
        found = next(((d, clean_reason(dated[d].get(code))) for d in reversed(dates)
                      if d in dated and str(dated[d].get(code) or '').strip()), None)
        reason_day, reason = found if found else (None, '')
        tags = list(dict.fromkeys(tags_of(reason)))
        row = {**stock, 'event_tags': tags, 'reason_date': reason_day, 'reason': reason,
               'attribution_basis': 'same_day' if reason_day == day else 'historical' if reason_day else 'missing'}
        if tags:
            observed.append(row)
            for tag in tags:
                groups[tag].append(row)
        else:
            missing.append({**row, 'missing_reason': '最近已归档原因只有属性词，未归入事件题材' if reason else '近20个交易日没有可用的同日涨停原因'})
    clusters = [{'theme': tag, 'count': len(rows), 'share': len(rows)/len(stocks) if stocks else None,
                 'stocks': rows} for tag, rows in groups.items() if len(rows) >= 2]
    clusters.sort(key=lambda row: (-row['count'], row['theme']))
    count_ready = result.get('count_ready') is True
    total = result.get('limit_down_count') if count_ready else None
    complete = count_ready and total == len(stocks) and not missing
    result.update(event_clusters=clusters, event_attribution={
        'date': day, 'topic_schema': 'vibe-event-themes-v1', 'covered': len(observed),
        'total': total, 'historical': sum(r['attribution_basis'] == 'historical' for r in observed),
        'rows': observed, 'missing': missing, 'window': 20, 'complete': complete,
    }, theme_cluster_status='none' if total == 0 else 'ready' if complete else 'partial' if observed else 'pending',
        theme_cluster_note=('当日没有跌停股。' if total == 0 else
        '题材与事件树使用同一套涨停原因标签；优先同日原因，否则使用近20个交易日内最近一次已归档原因，并标明归因日。历史归因仅作风险观察，不代表本次下跌原因。一股可属多个题材，各组不能相加。'))
    return result
