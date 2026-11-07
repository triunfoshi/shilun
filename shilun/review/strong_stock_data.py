"""Materialized strong-stock evidence, independent of P2 and sector rankings."""
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from uuid import uuid4

from pymongo import ReplaceOne

from shilun.market.taxonomy import classify_stocks
from shilun.review.board_data import settled_target
from shilun.review.sources import load_review_sessions
from shilun.review.strong_stock_candidates import VERSION, RULE, evaluate_stock
from shilun.review.suspensions import load_suspensions
from shilun.market.research_scope import stock_allowed

SNAPSHOTS = 'review_strong_stock_snapshots'
DETAILS = 'review_strong_stock_details'
JOBS = 'review_strong_stock_jobs'
LOOKBACK = 120


def iso(value):
    text = str(value or '').replace('-', '')[:8]
    return f'{text[:4]}-{text[4:6]}-{text[6:]}' if len(text) == 8 and text.isdigit() else ''


def a_share(ticker):
    return ((ticker.endswith('.SH') and ticker.startswith(('60', '68')))
            or (ticker.endswith('.SZ') and ticker.startswith(('00', '30')))
            or (ticker.endswith('.BJ') and ticker.startswith(('43', '83', '87', '92'))))


def listing_no_limit_days(meta, sessions):
    start = iso(meta.get('list_date'))
    if start not in sessions:
        return set()
    ticker = meta['ts_code']
    registered = (start >= '2023-04-10' or ticker.startswith('688')
                  or (ticker.startswith(('300', '301')) and start >= '2020-08-24'))
    n = 1 if ticker.endswith('.BJ') else 5 if registered else 0
    index = sessions.index(start)
    return set(sessions[index:index+n])


def resolve_date(store, requested=None):
    target = settled_target(requested)
    days = load_review_sessions(store, target)
    if not days:
        raise ValueError('所选日期缺少可核验交易日历')
    return days[-1]


def _future_sessions(store, target):
    end = date.fromisoformat(target) + timedelta(days=35)
    records = store.find_trade_calendar(start_date=target, end_date=end.isoformat())
    mapping = {iso(r.get('cal_date')): str(r.get('is_open')) for r in records}
    days, cursor = [], date.fromisoformat(target) + timedelta(days=1)
    while cursor <= end:
        state = mapping.get(cursor.isoformat())
        if state not in ('0', '1', 'True', 'False'):
            break
        if state in ('1', 'True'):
            days.append(cursor.isoformat())
        cursor += timedelta(days=1)
    return days[:10]


def _compact_cycle(cycle):
    return {k: v for k, v in (cycle or {}).items() if k not in ('series', 'end_evidence')}


def _compact_leg(leg):
    return {k: v for k, v in (leg or {}).items() if k != 'evidence'}


def load_snapshot(store, target):
    data = store.collection(SNAPSHOTS).find_one({'_id': f'{VERSION}:{target}'}, {'_id': 0})
    if data and data.get('rows'):
        from shilun.market.taxonomy import reconcile_dated_industries
        data = {**data, 'rows': reconcile_dated_industries(store, target, data['rows'])}
    job = store.collection(JOBS).find_one({'_id': f'{VERSION}:{target}'}, {'_id': 0, 'status': 1, 'error': 1, 'updated_at': 1, 'expires_at': 1})
    if job and job.get('status') in ('queued', 'running') and job.get('expires_at'):
        expires = job['expires_at'].replace(tzinfo=timezone.utc)
        if expires < datetime.now(timezone.utc):
            job = {**job, 'status': 'failed', 'error': '上次认证任务已中断或超时，可重新认证。'}
    return {**(data or {'status': 'not_materialized', 'date': target, 'version': VERSION, 'rows': [],
                       'reason': '该日尚未生成核心资格认证，可点击重新认证。'}), 'job': job}


def sync_missing_history(store, target, *, client=None):
    """Explicit one-time/recovery path. Normal GET and rebuild remain local-only."""
    from shilun.data.providers.tushare_provider import TushareConfig, TushareDailyClient
    start = (date.fromisoformat(target) - timedelta(days=LOOKBACK*2+30)).isoformat()
    sessions = load_review_sessions(store, target, start)[-LOOKBACK:]
    client = client or TushareDailyClient(TushareConfig.from_env())
    reports = []
    # Future dates are used only for published window deadlines, never prices.
    future_end = (date.fromisoformat(target) + timedelta(days=35)).isoformat()
    calendar = client.fetch_trade_calendar(target.replace('-', ''), future_end.replace('-', ''))
    if calendar.empty or any(not target <= iso(d) <= future_end for d in calendar['cal_date']):
        raise ValueError('后续交易日历未通过日期核验')
    store.upsert_trade_calendar(calendar)
    for day in sessions:
        compact = day.replace('-', '')
        bar_rows = list(store.collection('market_daily_bars').find({'date': day},
                        {'_id': 0, 'ticker': 1, 'pre_close': 1}))
        stock_bars = {r['ticker']: r for r in bar_rows if a_share(r['ticker'])}
        refs_missing = any(not isinstance(r.get('pre_close'), (float, int)) or not r['pre_close'] > 0 for r in stock_bars.values())
        if len(stock_bars) < 5000 or refs_missing:
            daily = client.fetch_daily_for_trade_date(compact, include_reference=True)
            if daily.empty or len(daily) >= 6000 or any(iso(d) != day for d in daily['date']):
                raise ValueError(f'{day} 日线补数未通过完整性或日期核验')
            if daily['ticker'].duplicated().any() or not set(stock_bars) <= set(daily['ticker']):
                raise ValueError(f'{day} 日线补数丢失已有证券，未写入')
            store.upsert_daily_bars(daily.assign(source='tushare_daily'))
            stock_bars = {t: {} for t in daily['ticker'] if a_share(t)}
            reports.append({'date': day, 'dataset': 'daily_reference', 'rows': len(daily)})
        limits = {r.get('ticker') or r.get('ts_code') for r in store.collection('daily_price_limits').find(
            {'trade_date': {'$in': [day, compact]}}, {'_id': 0, 'ticker': 1, 'ts_code': 1})}
        if not set(stock_bars) <= limits:
            frame = client.fetch_all_stock_limits(compact)
            if frame.empty or any(iso(d) != day for d in frame['trade_date']) or frame['ticker'].duplicated().any():
                raise ValueError(f'{day} 涨停价补数未通过日期或唯一性核验')
            store.upsert_price_limits(frame)
            reports.append({'date': day, 'dataset': 'official_price_limits', 'rows': len(frame)})
        print(f'strong_stock_history date={day} checked', flush=True)
    return reports


def materialize(store, target):
    # Calendar gaps shorten the verified suffix and are disclosed. Nothing
    # beyond target is read except announced exchange calendar dates.
    start = (date.fromisoformat(target) - timedelta(days=LOOKBACK*2+30)).isoformat()
    past = load_review_sessions(store, target, start)[-LOOKBACK:]
    if not past or past[-1] != target:
        raise ValueError('所选日不是已核验交易日，不能进行盘后认证')
    sessions = past + _future_sessions(store, target)
    stocks = [r for r in store.collection('stock_basic').find({}, {'_id': 0})
              if a_share(r.get('ts_code', '')) and stock_allowed(r, target) and (not iso(r.get('list_date')) or iso(r['list_date']) <= target)
              and (not iso(r.get('delist_date')) or iso(r['delist_date']) > target)]
    if not stocks:
        raise ValueError('A股证券目录缺失')
    stocks = classify_stocks(store, target, stocks, industry_only=True)
    tickers = {r['ts_code'] for r in stocks}
    bars, limits = defaultdict(dict), defaultdict(dict)
    fields = {'_id': 0, 'ticker': 1, 'date': 1, 'open': 1, 'close': 1, 'high': 1, 'low': 1, 'pre_close': 1, 'volume': 1}
    for row in store.collection('market_daily_bars').find({'date': {'$in': past}}, fields, batch_size=1500):
        if row['ticker'] in tickers:
            if row['date'] in bars[row['ticker']]:
                raise ValueError('日线出现重复股票日期')
            bars[row['ticker']][row['date']] = row
    for row in store.collection('daily_price_limits').find(
            {'trade_date': {'$in': past + [d.replace('-', '') for d in past]}}, {'_id': 0}, batch_size=1500):
        ticker, day = row.get('ticker') or row.get('ts_code'), iso(row.get('trade_date'))
        if ticker in tickers:
            existing = limits[ticker].get(day)
            if existing and any(existing.get(k) != row.get(k) for k in ('up_limit', 'pre_close')):
                raise ValueError('涨停价出现同日冲突记录')
            limits[ticker][day] = row
    suspensions = load_suspensions(store, past)
    from shilun.review.strength_review import materialize_market_stats
    materialize_market_stats(store, past, stocks, bars, limits, suspensions)
    revision = uuid4().hex
    rows, writes, scanned, latest_known = [], [], 0, 0
    state_counts = Counter()
    for stock in stocks:
        ticker = stock['ts_code']
        stock_past = [d for d in past if not iso(stock.get('list_date')) or d >= iso(stock['list_date'])]
        if not stock_past:
            continue
        result = evaluate_stock(ticker, stock_past + [d for d in sessions if d > target], target,
                                bars[ticker], limits[ticker], suspensions=suspensions.get(ticker, ()),
                                no_limit_dates=listing_no_limit_days(stock, sessions))
        scanned += 1
        if target not in result['coverage']['missing_dates']:
            latest_known += 1
        ever_qualified = [l for l in result['legs'] if l['qualified_at']]
        if not result['recent_limit_up'] and not ever_qualified and result['status'] != 'qualified':
            continue
        detail_id = f'{VERSION}:{target}:{revision}:{ticker}'
        selected_leg = result['focus_leg'] or (ever_qualified[-1] if ever_qualified else None)
        row = {k: result[k] for k in ('ticker', 'status', 'recent_limit_up', 'qualified_at', 'current_streak',
                                      'current_streak_boundary_confirmed', 'latest_limit_date', 'latest_shape')}
        row.update(name=stock.get('name') or ticker, industry=stock.get('industry'), industry_code=stock.get('industry_code'),
                   taxonomy_date=stock.get('taxonomy_date'), classification_status=stock.get('classification_status'),
                   cycle=_compact_cycle(result['current_cycle']), leg=_compact_leg(selected_leg), detail_id=detail_id,
                   historical_qualified_legs=len(ever_qualified),
                   tracking_complete=bool(result['current_cycle'] and result['current_cycle']['price_complete']))
        rows.append(row)
        state_counts[result['status']] += 1
        writes.append(ReplaceOne({'_id': detail_id}, {'_id': detail_id, 'version': VERSION, 'date': target,
                           'revision': revision, 'name': row['name'], 'industry': row['industry'], **result}, upsert=True))
        if len(writes) >= 100:
            store.collection(DETAILS).bulk_write(writes, ordered=False)
            writes = []
    if writes:
        store.collection(DETAILS).bulk_write(writes, ordered=False)
    rows.sort(key=lambda r: (r['status'] == 'qualified', r['recent_limit_up'], r['leg'].get('board_count', 0),
                             r['latest_limit_date'] or '', r['ticker']), reverse=True)
    payload = {'status': 'ready', 'date': target, 'version': VERSION, 'revision': revision, 'rule': RULE,
               'previous_date': past[-2] if len(past) > 1 else None,
               'next_date': next((d for d in sessions if d > target), None),
               'generated_at': datetime.now(timezone.utc).isoformat(), 'rows': rows, 'counts': dict(state_counts),
               'scope': '沪深北A股，剔除ST及退市整理、已退市股票；按逐日官方涨停价核验。',
               'history_from': past[0], 'history_sessions': len(past), 'history_requested_sessions': LOOKBACK,
               'scanned_stocks': scanned, 'latest_known_stocks': latest_known,
               'sources': ['market_daily_bars', 'daily_price_limits', 'trade_calendar', 'review_daily_suspensions'],
               'source_note': '按复盘日截断行情重建，使用当前可取得的日期证据；尚未验证当时的发布时点，不作为严格事前回测。',
               'cycle_rule': '逐日复权后跌破走势首板最低价，或连续超过10个交易日未涨停，结束走势；15%收盘回撤仅提醒。量能与情绪只留证，后续周期规则单独校准。'}
    store.collection(SNAPSHOTS).replace_one({'_id': f'{VERSION}:{target}'}, {'_id': f'{VERSION}:{target}', **payload}, upsert=True)
    return payload
