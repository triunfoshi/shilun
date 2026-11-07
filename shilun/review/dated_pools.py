"""Explicit materialization adapters for settled previous-limit-up observations.

Vibe's dated cache is optional migration input, never a running service dependency.
Only manual/scheduled materialization writes; rendering remains cache-only.
"""
from datetime import datetime, date, timedelta
from hashlib import sha256
import json
import os
from pathlib import Path

import requests

from shilun.review.sources import finite_number, load_review_sessions
from shilun.jobs.pipeline_state import error_details

COLLECTION = 'review_previous_limit_pools'


def sync_review_board_history(store, sessions, *, client=None):
    """Fill both settled up/broken pools; a daily up list cannot supply break rates."""
    from shilun.review.board_data import BoardResearchData, POOL_COLLECTION, CHINA, fetch_pool, fetch_historical_pool, today
    from shilun.data.providers.tushare_provider import TushareConfig, TushareDailyClient
    dates = sessions[-31:]
    data = BoardResearchData(store)
    existing = {kind: data.pools(dates, kind, settled=True) for kind in ('up', 'broken')}
    results = []
    for day, kind in ((day, kind) for day in dates for kind in ('up', 'broken')):
        if day in existing[kind]:
            continue
        try:
            if day < today():
                client = client or TushareDailyClient(TushareConfig.from_env())
                document = fetch_historical_pool(client, day, kind)
            else:
                document = fetch_pool(day, kind)
            now = datetime.now(CHINA)
            document['snapshot_mode'] = 'live' if day == today() and (now.hour, now.minute) < (15, 10) else 'settled'
            store.collection(POOL_COLLECTION).update_one({'date': day, 'kind': kind}, {'$set': document}, upsert=True)
            results.append({'date': day, 'dataset': f'{kind}_pool_history', 'status': 'ok', 'row_count': len(document['stocks'])})
        except Exception as error:
            results.append({'date': day, 'dataset': f'{kind}_pool_history', 'status': 'unavailable', **error_details(error)})
    return results


def sync_review_history(store, target, *, client=None):
    """Bound manual repair to 21 verified sessions; never run on dashboard GET."""
    from shilun.data.providers.tushare_provider import TushareConfig, TushareDailyClient
    results = []
    try:
        sessions = load_review_sessions(store, target)
        if len(sessions) < 21:
            client = client or TushareDailyClient(TushareConfig.from_env())
            start = (date.fromisoformat(target) - timedelta(days=40)).strftime('%Y%m%d')
            calendar = client.fetch_trade_calendar(start, target.replace('-', ''))
            if calendar.empty: raise ValueError('Missing calendar')
            count = store.upsert_trade_calendar(calendar)
            results.append({'dataset': 'trade_calendar', 'date': target, 'status': 'ok', 'row_count': count})
            sessions = load_review_sessions(store, target)
    except Exception as error:
        return [{'dataset': 'trade_calendar', 'date': target, 'status': 'unavailable', **error_details(error)}]
    for day in sessions[-21:]:
        # Existing complete-market dates are left untouched. Individual suspensions
        # remain missing observations, not zeros or invented historical bars.
        if store.collection('market_daily_bars').count_documents({'date': day}) >= 5000:
            continue
        try:
            client = client or TushareDailyClient(TushareConfig.from_env())
            frame = client.fetch_daily_for_trade_date(day.replace('-', ''))
            if frame.empty or not all(str(d)[:10].replace('-', '') == day.replace('-', '') for d in frame['date']):
                raise ValueError('Daily history date mismatch')
            frame = frame.assign(source='tushare_daily')
            count = store.upsert_daily_bars(frame)
            results.append({'dataset': 'market_daily_bars', 'date': day, 'status': 'ok', 'row_count': count})
        except Exception as error:
            results.append({'dataset': 'market_daily_bars', 'date': day, 'status': 'unavailable', **error_details(error)})
    from shilun.review.index_sync import sync_index_history
    results.extend(sync_index_history(store, target, client=client))
    return results


def stock_code(value):
    value = str(value or '').split('.')[0]
    if len(value) != 6 or not value.isdigit():
        raise ValueError('Invalid pool ticker')
    return value + ('.BJ' if value.startswith(('4', '8', '9')) else '.SH' if value.startswith('6') else '.SZ')


def normalize_previous(rows):
    """Fail closed for incomplete rows instead of changing the denominator."""
    if not isinstance(rows, list): raise ValueError('Missing pool')
    result, seen = [], set()
    for row in rows:
        ticker = stock_code(row.get('code'))
        ret, boards, close = (finite_number(row.get(k)) for k in ('ret', 'prev_boards', 'close'))
        if ticker in seen or ret is None or boards is None or boards < 1 or boards != int(boards) or close is None or close <= 0:
            raise ValueError('Incomplete previous-pool row')
        seen.add(ticker)
        result.append({'ticker': ticker, 'name': row.get('name') or ticker, 'return_1d': ret / 100,
            'consecutive_count': int(boards), 'industry': row.get('sector') or '', 'close': close,
            'up_limit': finite_number(row.get('limit_price')), 'previous_seal_time': row.get('seal_time')})
    return result


def fetch_previous(day):
    from shilun.review.live_emotion import _POOL_UT
    with requests.Session() as session:
        session.trust_env = False
        response = session.get('https://push2ex.eastmoney.com/getYesterdayZTPool',
            params={'ut': _POOL_UT, 'dpt': 'wz.ztzt', 'Pageindex': 0, 'pagesize': 10000, 'sort': 'zs:desc', 'date': day.replace('-', '')},
            headers={'User-Agent': 'Mozilla/5.0', 'Referer': 'https://quote.eastmoney.com/'}, timeout=8)
        response.raise_for_status()
        payload = response.json()
    data = payload.get('data')
    if payload.get('rc') != 0 or not isinstance(data, dict) or str(data.get('qdate')) != day.replace('-', ''):
        raise ValueError('Upstream date mismatch')
    raw = data.get('pool')
    if not isinstance(raw, list) or data.get('tc') != len(raw): raise ValueError('Incomplete pool')
    rows = [{'code': r.get('c'), 'name': r.get('n'), 'ret': r.get('zdp'), 'prev_boards': r.get('lbc'),
             'close': (finite_number(r.get('p')) or 0) / 1000,
             'limit_price': (finite_number(r.get('ztp')) or 0) / 1000,
             'sector': r.get('hybk'), 'seal_time': r.get('fbt')} for r in raw]
    return normalize_previous(rows)


def sync_dated_evidence(store, sessions, *, cache_dir=None, price_client=None, include_previous=True):
    """Import explicitly dated settled observations and fill recent stk_limit gaps.

    Vibe files retain their checksum/provenance and are checked against the target
    day's close before being used by the calculation layer.
    """
    if len(sessions) < 2: return []
    results = []
    root = Path(cache_dir or os.environ.get('SHILUN_VIBE_CACHE_DIR') or Path.home() / '.duanxian-agents/cache/prev_pool')
    for prior, day in (list(zip(sessions, sessions[1:]))[-21:] if include_previous else []):
        if store.collection(COLLECTION).find_one({'analysis_date': day}, {'_id': 1}): continue
        try:
            path = root / f'{day}.json'
            if path.is_file():
                raw = path.read_bytes()
                rows = normalize_previous(json.loads(raw))
                if not rows: continue  # A bare local [] is not independently verified emptiness.
                source, digest = 'vibe_settled_previous_pool', sha256(raw).hexdigest()
            elif day == sessions[-1]:
                rows = fetch_previous(day)
                source, digest = 'eastmoney_dated_previous_pool', None
            else: continue
            store.collection(COLLECTION).update_one({'analysis_date': day}, {'$set': {
                'analysis_date': day, 'previous_date': prior, 'rows': rows, 'source': source,
                'checksum': digest, 'imported_at': datetime.now().isoformat(timespec='seconds')}}, upsert=True)
            results.append({'dataset': 'previous_limit_pool', 'date': day, 'status': 'ok', 'row_count': len(rows), 'source': source})
        except Exception as error:
            results.append({'dataset': 'previous_limit_pool', 'date': day, 'status': 'unavailable', **error_details(error)})
    # Use the project's authenticated provider and writer; never derive price
    # limits from stock-name/rate guesses. Bounded to the 20-day risk + current day.
    stock_universe = {r.get('ts_code') or r.get('ticker') for r in store.collection('stock_basic').find({}, {'_id': 0, 'ts_code': 1, 'ticker': 1})}
    for day in sessions[-21:]:
        compact = day.replace('-', '')
        bars = set(store.collection('market_daily_bars').distinct('ticker', {'date': day}))
        limits = set(store.collection('daily_price_limits').distinct('ticker', {'trade_date': {'$in': [day, compact]}}))
        stocks = bars & stock_universe
        if len(limits) >= 5000 and stocks <= limits: continue
        try:
            if price_client is None:
                from shilun.data.providers.tushare_provider import TushareConfig, TushareDailyClient
                price_client = TushareDailyClient(TushareConfig.from_env())
            frame = price_client.fetch_all_stock_limits(compact)
            if frame.empty or not all(str(d).replace('-', '') == compact for d in frame['trade_date']):
                raise ValueError('Price limit date mismatch')
            count = store.upsert_price_limits(frame)
            results.append({'dataset': 'daily_price_limits', 'date': day, 'status': 'ok', 'row_count': count})
        except Exception as error:
            results.append({'dataset': 'daily_price_limits', 'date': day, 'status': 'unavailable', **error_details(error)})
    return results


def sync_heat_summaries(store, sessions, *, cache_dir=None):
    """Import Vibe's explicit dated, settled summary; never execute its code."""
    root = Path(cache_dir or Path.home() / '.duanxian-agents/cache/zt_summary')
    results = []
    for day in sessions:
        path = root / f'{day}.json'
        if not path.is_file():
            continue
        try:
            raw = path.read_bytes()
            document = json.loads(raw)
            values = document.get('summary') or {}
            if document.get('schema') != 1 or document.get('date') != day or document.get('source') != 'akshare_zt_pool':
                raise ValueError('Summary date/source mismatch')
            count, height, rate = (finite_number(values.get(k)) for k in ('limit_up', 'highest_consec', 'broken_rate'))
            if count is None or count < 0 or count != int(count) or height is None or height < 0 or height != int(height):
                raise ValueError('Invalid count')
            if rate is not None and not 0 <= rate <= 1:
                raise ValueError('Invalid rate')
            store.collection('review_heat_summaries').update_one({'date': day}, {'$set': {
                'date': day, 'limit_up': int(count), 'highest_consec': int(height), 'broken_rate': rate,
                'source': 'vibe_settled_zt_summary', 'checksum': sha256(raw).hexdigest(),
                'imported_at': datetime.now().isoformat(timespec='seconds')}}, upsert=True)
            results.append({'dataset': 'heat_summary', 'date': day, 'status': 'ok'})
        except Exception as error:
            results.append({'dataset': 'heat_summary', 'date': day, 'status': 'unavailable', **error_details(error)})
    return results
