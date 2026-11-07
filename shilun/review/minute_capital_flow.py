"""Eastmoney cumulative intraday flow; aggregate identical minutes and members."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import requests
from shilun.review.board_data import CHINA, today
from shilun.review.sources import finite_number as num
from shilun.market.taxonomy import MEMBERS, VERSION, SNAPSHOTS

COLLECTION = 'review_topic_minute_flow'
FIELDS = ('main', 'small', 'medium', 'large', 'super_large')


def parse_flow(payload, ticker, target):
    data = payload.get('data') or {}
    if data.get('code') != ticker.split('.')[0]:
        return []
    rows = {}
    for text in data.get('klines', []):
        values = text.split(',')
        if len(values) != 6 or not values[0].startswith(target + ' '):
            continue
        stamp = values[0]
        clock = stamp[11:16]
        if not ('09:30' <= clock <= '11:30' or '13:00' <= clock <= '15:00'):
            continue
        amounts = [num(v) for v in values[1:]]
        if any(v is None for v in amounts):
            continue
        # main = large + super_large; allow vendor rounding to whole yuan.
        if abs(amounts[0] - amounts[3] - amounts[4]) > max(10, abs(amounts[0]) * .00001):
            continue
        rows[stamp] = dict(time=stamp, **dict(zip(FIELDS, amounts)))
    return [rows[t] for t in sorted(rows)]


def fetch_stock(ticker, target):
    code, exchange = ticker.split('.')
    if exchange not in ('SH', 'SZ') or not code.isdigit() or len(code) != 6:
        return []
    with requests.Session() as session:
        session.trust_env = False  # Match the existing Eastmoney pool adapter.
        response = session.get('https://push2.eastmoney.com/api/qt/stock/fflow/kline/get',
            params={'secid': f'{1 if exchange == "SH" else 0}.{code}', 'klt': 1, 'lmt': 0,
                    'fields1': 'f1,f2,f3,f7', 'fields2': 'f51,f52,f53,f54,f55,f56'},
            headers={'User-Agent': 'Mozilla/5.0', 'Referer': 'https://quote.eastmoney.com/'}, timeout=10)
        response.raise_for_status()
        return parse_flow(response.json(), ticker, target)


def aggregate_flow(series, members):
    members = set(members)
    indexed = {t: {r['time']: r for r in series.get(t, [])} for t in members}
    stamps = sorted({s for stock in indexed.values() for s in stock})
    rows = []
    previous = None
    for stamp in stamps:
        observed = [stock[stamp] for stock in indexed.values() if stamp in stock]
        # Strict common-minute total: never forward-fill a stale member or
        # mistake missing data for zero. Coverage is visible even for gaps.
        ready = len(observed) == len(members) and bool(members)
        total = sum(r['main'] for r in observed) if ready else None
        change = total - previous if total is not None and previous is not None else None
        rows.append({'time': stamp, 'main_net_yuan': total, 'minute_change_yuan': change,
                     'coverage': len(observed), 'expected': len(members)})
        previous = total
    return rows


def load_flow(store, code, target):
    return store.collection(COLLECTION).find_one({'code': code, 'date': target}, {'_id': 0}) or {
        'available': False, 'code': code, 'date': target, 'rows': [],
        'reason': '尚未采集该日分钟资金流；源端只提供当日，历史缺口不能由日净流入还原。'}


def refresh_flow(store, code, target):
    if target != today():
        return load_flow(store, code, target)
    manifest = store.collection(SNAPSHOTS).find_one({'_id': f'{VERSION}:{target}', 'status': 'ready'}, {'member_refs': 1})
    membership = store.collection(MEMBERS).find_one({'code': code, 'date': target, 'version': VERSION,
        '_id': {'$in': (manifest or {}).get('member_refs', [])}}, {'_id': 0})
    if not membership:
        raise ValueError('该视角没有当天成分归档，请先生成所选日复盘')
    members = sorted({r['con_code'] for r in membership['members']})
    def fetch(ticker):
        try:
            return ticker, fetch_stock(ticker, target)
        except (requests.RequestException, ValueError):
            return ticker, []
    with ThreadPoolExecutor(max_workers=4) as pool:
        series = dict(pool.map(fetch, members))
    rows = aggregate_flow(series, members)
    result = {'code': code, 'date': target, 'membership_date': target,
        'available': any(r['main_net_yuan'] is not None for r in rows), 'rows': rows,
        'fetched_at': datetime.now(CHINA).isoformat(), 'source': '东方财富个股分钟资金流按统一东方财富成分汇总',
        'members': len(members), 'observed_members': sum(bool(r) for r in series.values()),
        'reason': '部分分钟成分未齐时曲线留空，未将缺失当作零。',
        'unit': 'CNY', 'semantics': '自开盘累计主力净流入；相邻完整分钟作差才是区间变化，不跨时点重复求和。主力为源站大单+超大单分类，不代表已识别机构或净新增现金。'}
    if result['available']:
        store.collection(COLLECTION).replace_one({'code': code, 'date': target}, result, upsert=True)
    else:
        result['reason'] = f"本次可用成分{result['observed_members']}/{len(members)}，没有同一分钟成分齐全的数据；不生成全板块曲线。"
        prior = load_flow(store, code, target)
        if prior.get('available'):
            return {**prior, 'refresh_status': 'unavailable', 'refresh_note': result['reason'] + ' 保留同日上次归档。'}
    return result
