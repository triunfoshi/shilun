"""Small read-only supplement for legacy index-review snapshots.

Historical turnover is aggregated in MongoDB.  Only two daily cross-sections
and dated price limits reach Python; the frozen review document is never edited.
"""
from datetime import date, datetime, timedelta, timezone
from hashlib import sha256
import json
from pathlib import Path
import uuid

from shilun.review.index_liquidity import (breadth_distribution, market_rows,
    volume_facts, THRESHOLD_RULE, number)
from shilun.review.sources import load_review_sessions

REVISION = 'index-market-facts-v1'
CACHE_ROOT = Path(__file__).resolve().parents[2] / 'runtime' / 'review_market_facts'
CACHE_SECONDS = 900


def cache_path(day):
    return CACHE_ROOT / f'{day}-{REVISION}.json'


def _read_cached(day, *, allow_stale=False):
    path = cache_path(day)
    try:
        payload = json.loads(path.read_text())
        age = (datetime.now(timezone.utc) - datetime.fromisoformat(payload['built_at'])).total_seconds()
        expected_revision = sha256(json.dumps(payload['market'], ensure_ascii=False,
            sort_keys=True, allow_nan=False).encode()).hexdigest()
        if (payload['revision'] == REVISION and payload['analysis_date'] == day
                and payload.get('source_revision') == expected_revision and age >= 0
                and (allow_stale or age < CACHE_SECONDS)):
            return payload, age
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return None


def cached(day):
    result = _read_cached(day)
    return result[0] if result is not None else None


def cached_supplement(day):
    """Attach already materialized chart facts without another database read.

    A historical cache may outlive its refresh interval.  It remains available
    as an explicitly stale same-date view while the client requests a refresh.
    The original review's market and conclusions are never replaced here.
    """
    result = _read_cached(day, allow_stale=True)
    if result is None:
        return None
    payload, age = result
    return {**payload, 'cache_status': 'fresh' if age < CACHE_SECONDS else 'stale',
            'cache_age_seconds': int(age)}


def amount_pipeline(sessions, tickers):
    return [
        {'$match': {'date': {'$in': sessions}, 'ticker': {'$in': tickers}}},
        {'$project': {'date': 1, 'market': {'$arrayElemAt': [{'$split': ['$ticker', '.']}, 1]},
            'amount': {'$convert': {'input': '$amount', 'to': 'double', 'onError': None, 'onNull': None}}}},
        {'$set': {'valid': {'$and': [{'$ne': ['$amount', None]}, {'$gte': ['$amount', 0]}, {'$lt': ['$amount', 1e100]}]}}},
        {'$group': {'_id': {'date': '$date', 'market': '$market'},
            'total_n': {'$sum': 1}, 'valid_n': {'$sum': {'$cond': ['$valid', 1, 0]}},
            'amount': {'$sum': {'$cond': ['$valid', '$amount', 0]}}}},
        {'$sort': {'_id.date': 1, '_id.market': 1}},
    ]


def build_market_facts(store, day):
    start = (date.fromisoformat(day) - timedelta(days=180)).isoformat()
    sessions = load_review_sessions(store, day, start)[-61:]
    if not sessions or sessions[-1] != day:
        raise ValueError('所选日不是已核验交易日，不能用其他日期替代')
    raw_basics = list(store.collection('stock_basic').find({}, {'_id': 0, 'ts_code': 1, 'list_date': 1, 'delist_date': 1}))
    compact = day.replace('-', '')
    tickers = sorted({r['ts_code'] for r in raw_basics if str(r.get('ts_code', '')).endswith(('.SH', '.SZ', '.BJ'))
        and (not r.get('list_date') or str(r['list_date']).replace('-', '') <= compact)
        and (not r.get('delist_date') or str(r['delist_date']).replace('-', '') > compact)})
    if not tickers:
        raise ValueError('所选日没有可核验的A股证券范围')
    aggregate = list(store.collection('market_daily_bars').aggregate(amount_pipeline(sessions, tickers), maxTimeMS=45000))
    history, group_meta = {}, {}
    for row in aggregate:
        at, market = row['_id']['date'], row['_id']['market']
        if at not in sessions or market not in ('SH', 'SZ', 'BJ'):
            continue
        amount = number(row.get('amount')) if row.get('valid_n') == row.get('total_n') else None
        history.setdefault(at, {})[f'TOTAL.{market}'] = {'amount': amount}
        group_meta.setdefault(at, []).append(row)
    previous_day = sessions[-2] if len(sessions) >= 2 else None
    rows = store.collection('market_daily_bars').find(
        {'date': {'$in': sessions[-2:]}, 'ticker': {'$in': tickers}},
        {'_id': 0, 'ticker': 1, 'date': 1, 'close': 1, 'pre_close': 1, 'amount': 1}, batch_size=10000)
    current, previous = {}, {}
    seen = set()
    for row in rows:
        key = (row['date'], row['ticker'])
        if key in seen:
            raise ValueError('同股同日行情重复，补充统计停止')
        seen.add(key)
        if row['date'] == day:
            current[row['ticker']] = row
        elif row['date'] == previous_day:
            previous[row['ticker']] = row
    if not current:
        raise ValueError('所选日个股日线尚未缓存')
    prices = list(store.collection('daily_price_limits').find(
        {'trade_date': {'$in': [day, compact]}},
        {'_id': 0, 'ticker': 1, 'ts_code': 1, 'up_limit': 1, 'down_limit': 1, 'up': 1, 'down': 1}))
    limits = {r.get('ticker') or r.get('ts_code'): r for r in prices}
    volume = volume_facts(sessions, history)
    for item in volume['series']:
        groups = group_meta.get(item['date'], [])
        item['stock_count'] = sum(r['total_n'] for r in groups)
        item['amount_valid_count'] = sum(r['valid_n'] for r in groups)
    breadth = {'distribution': breadth_distribution(current, previous, limits),
        'markets': market_rows(current, previous, sessions=sessions, bars_by_date=history),
        'expansion_rule': THRESHOLD_RULE}
    facts = {'volume': volume, 'breadth': breadth}
    revision = sha256(json.dumps(facts, ensure_ascii=False, sort_keys=True, allow_nan=False).encode()).hexdigest()
    return {'status': 'ready', 'analysis_date': day, 'previous_trade_date': previous_day,
        'revision': REVISION, 'source_revision': revision, 'built_at': datetime.now(timezone.utc).isoformat(),
        'scope': '补充重建：以读取时已缓存的所选日期行情和证券范围统计，不证明当时已发布；原复盘快照保持不变。',
        'market': facts}


def load_market_facts(store, day, *, refresh=False):
    date.fromisoformat(day)
    if not refresh and (result := cached(day)) is not None:
        return result
    result = build_market_facts(store, day)
    CACHE_ROOT.mkdir(parents=True, exist_ok=True)
    # Content-addressed evidence and a replaceable latest pointer remain separate
    # from the immutable review/decision snapshots.
    archived = CACHE_ROOT / f'{day}-{result["source_revision"]}.json'
    text = json.dumps(result, ensure_ascii=False, allow_nan=False)
    if not archived.exists():
        archived.write_text(text)
    temporary = CACHE_ROOT / f'.{uuid.uuid4().hex}.json'
    temporary.write_text(text)
    temporary.replace(cache_path(day))
    return result
