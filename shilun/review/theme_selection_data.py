"""Read-only inputs for historical theme selection; never publish partial taxonomy.

Research provider responses are resumably cached on disk, separately from the
production classification center. All rows still use its DC IDs/normalizers.
"""
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
import gzip
import json
from pathlib import Path

from shilun.market.research_scope import stock_allowed
from shilun.market.taxonomy import VERSION as TAXONOMY_VERSION, normalize_catalog, normalize_members
from shilun.review.board_data import BoardResearchData, REASON_COLLECTION
from shilun.review.sources import load_review_sessions
from shilun.review.strong_stock_candidates import VERSION as CORE_VERSION, evaluate_stock, daily_evidence
from shilun.review.strong_stock_data import a_share, iso, listing_no_limit_days
from shilun.review.suspensions import load_suspensions

WATCH_TICKERS = ('601086.SH', '600127.SH', '000993.SZ', '600664.SH', '600721.SH')


def write_gzip(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + '.tmp')
    with gzip.open(temp, 'wt', encoding='utf8') as handle:
        json.dump(data, handle, ensure_ascii=False, allow_nan=False, default=str)
    temp.replace(path)


def read_gzip(path):
    with gzip.open(path, 'rt', encoding='utf8') as handle:
        return json.load(handle)


def unique_map(rows, ticker_key, day_key, fields=None):
    result = defaultdict(dict)
    for row in rows:
        ticker, day = row[ticker_key], iso(row[day_key])
        previous = result[ticker].get(day)
        if previous is not None and any(previous.get(k) != row.get(k) for k in (fields or row)):
            raise ValueError(f'{ticker}/{day} 存在冲突记录')
        result[ticker][day] = row
    return dict(result)


def collect_benchmark_inputs(store, sessions):
    rows = list(store.collection('market_daily_bars').find(
        {'ticker': '000001.SH', 'date': {'$in': sessions}}, {'_id': 0, 'ticker': 1, 'date': 1, 'close': 1, 'source': 1}))
    bars = unique_map(rows, 'ticker', 'date', ('close',)).get('000001.SH', {})
    return {'ticker': '000001.SH', 'name': '上证指数', 'bars': bars,
            'source': 'market_daily_bars', 'collected_at': datetime.now(timezone.utc).isoformat()}


def collect_market_inputs(store, start, end):
    history_start = (date.fromisoformat(start) - timedelta(days=300)).isoformat()
    sessions = load_review_sessions(store, end, history_start)
    days = [d for d in sessions if start <= d <= end]
    if not days or days[-1] != end:
        raise ValueError('回放结束日必须是已核验交易日')
    first = sessions.index(days[0])
    if first < 20:
        raise ValueError('至少需要20个已核验交易日作为资格边界预热')
    sessions = sessions[max(0, first - 120):]
    first = sessions.index(days[0])
    context = sessions[max(0, first - 7):]
    stocks = {r['ts_code']: r for r in store.collection('stock_basic').find({}, {
        '_id': 0, 'ts_code': 1, 'name': 1, 'list_date': 1, 'delist_date': 1, 'list_status': 1,
    }) if a_share(r.get('ts_code', '')) and stock_allowed(r, end)}
    pools = BoardResearchData(store).pools(context, settled=True)
    candidates = sorted({r['ticker'] for pool in pools.values() for r in pool.get('stocks', [])
                         if r['ticker'] in stocks} | (set(WATCH_TICKERS) & stocks.keys()))
    if not candidates:
        raise ValueError('涨停池没有可复核的A股样本')
    print(f'market candidates={len(candidates)} sessions={len(sessions)} pools={len(pools)}/{len(context)}', flush=True)
    bars = unique_map(store.collection('market_daily_bars').find(
        {'ticker': {'$in': candidates}, 'date': {'$in': sessions}},
        {'_id': 0, **{k: 1 for k in ('ticker', 'date', 'open', 'high', 'low', 'close', 'pre_close', 'volume', 'amount')}},
        batch_size=1000), 'ticker', 'date')
    limits = unique_map(store.collection('daily_price_limits').find(
        {'ticker': {'$in': candidates}, 'trade_date': {'$in': sessions + [d.replace('-', '') for d in sessions]}},
        {'_id': 0, 'ticker': 1, 'trade_date': 1, 'up_limit': 1, 'pre_close': 1, 'no_limit': 1}, batch_size=1000),
        'ticker', 'trade_date', ('up_limit',))
    suspensions = load_suspensions(store, sessions)
    reasons = {r['date']: r for r in store.collection(REASON_COLLECTION).find({'date': {'$in': context}}, {'_id': 0})}
    # Resource pruning only: each day's qualification is recomputed from its own
    # prefix. Stocks can only be cores if five closes were sealed somewhere.
    potential = [t for t in candidates if sum(daily_evidence(d, bars.get(t, {}).get(d), limits.get(t, {}).get(d),
        suspended=d in suspensions.get(t, ()))['state'] == 'sealed' for d in sessions) >= 5 or t in WATCH_TICKERS]
    cores = {}
    for day in context:
        rows = []
        for ticker in potential:
            meta = stocks[ticker]
            past = [d for d in sessions if not iso(meta.get('list_date')) or d >= iso(meta['list_date'])]
            if day not in past:
                continue
            result = evaluate_stock(ticker, past, day, bars.get(ticker, {}), limits.get(ticker, {}),
                                    suspensions=suspensions.get(ticker, ()), no_limit_dates=listing_no_limit_days(meta, sessions))
            cycle = result['current_cycle'] or {}
            leg = result['focus_leg'] or {}
            rows.append({'ticker': ticker, 'name': meta.get('name') or ticker, 'date': day, 'status': result['status'],
                'qualified_at': result['qualified_at'], 'latest_limit_date': result['latest_limit_date'],
                'cycle': {k: cycle.get(k) for k in ('cycle_id', 'start_date', 'status', 'max_streak', 'cumulative_return', 'drawdown', 'drawdown_warning', 'price_complete')},
                'leg': {k: leg.get(k) for k in ('leg_id', 'start_date', 'three_board_date', 'qualified_at', 'qualification_board_count', 'qualification_sessions', 'boundary_confirmed', 'reasons')},
                'missing_dates': result['coverage']['missing_dates']})
        cores[day] = rows
        print(f'core replay {day} qualified={sum(r["status"] == "qualified" for r in rows)}', flush=True)
    return {'start': start, 'end': end, 'days': days, 'sessions': sessions, 'context_days': context,
            'stocks': stocks, 'bars': bars, 'limits': limits, 'pools': pools, 'reasons': reasons, 'cores': cores,
            'suspensions': {k: sorted(v) for k, v in suspensions.items()}, 'core_version': CORE_VERSION,
            'benchmark': collect_benchmark_inputs(store, sessions),
            'collected_at': datetime.now(timezone.utc).isoformat(),
            'scope_note': '按当前非ST、非退市A股过滤，候选实际范围以各日涨停池scope为准；按日期截断行情重建。当前证券状态过滤存在存续偏差，历史数据采集时间不等于当时发布时间。',
            'candidate_scope_note': '对回放区间及其前7交易日出现过涨停的股票重建资格；固定关注样本只用于核验，不绕过准入条件。'}


class TaxonomyResearchReader:
    def __init__(self, cache_dir, client=None):
        self.cache_dir, self.client = Path(cache_dir), client

    def fetch(self, api, **params):
        from shilun.market.taxonomy import digest
        path = self.cache_dir / f'{api}-{digest(params)}.json.gz'
        if path.exists():
            cached = read_gzip(path)
            if cached.get('rows') or self.client is None:
                return cached
        if self.client is None:
            raise ValueError('该请求尚未归档，需显式启用历史分类采集')
        frame = self.client._call_api(api, **params)
        rows = json.loads(frame.to_json(orient='records', force_ascii=False))
        doc = {'api': api, 'params': params, 'rows': rows, 'fetched_at': datetime.now(timezone.utc).isoformat()}
        write_gzip(path, doc)
        return doc

    def members(self, days, **identity):
        if not days:
            return []
        doc = self.fetch('dc_member', start_date=days[0].replace('-', ''), end_date=days[-1].replace('-', ''), **identity)
        rows = doc['rows']
        if len(rows) >= 5000:
            if len(days) == 1:
                raise ValueError('单日成分达到5000条上限，完整性无法确认')
            mid = len(days) // 2
            return self.members(days[:mid], **identity) + self.members(days[mid:], **identity)
        if any(not days[0] <= iso(r.get('trade_date')) <= days[-1] or any(r.get(k) != v for k, v in identity.items()) for r in rows):
            raise ValueError('分类接口返回的日期或代码与请求不符')
        # The dated component API also returns calendar-day snapshots. Keep
        # only the verified exchange sessions; weekends never extend a window.
        rows = [r for r in rows if iso(r.get('trade_date')) in days]
        keys = [(r.get('ts_code'), r.get('con_code'), iso(r.get('trade_date'))) for r in rows]
        if len(keys) != len(set(keys)):
            raise ValueError('历史分类成分出现重复')
        return [{**r, 'fetched_at': doc['fetched_at']} for r in rows]

    def collect(self, market, *, retain_codes=()):
        days, catalogs, diagnostics = market['context_days'], {}, []
        for day in days:
            try:
                doc = self.fetch('dc_index', trade_date=day.replace('-', ''))
                catalogs[day] = {'date': day, 'fetched_at': doc['fetched_at'],
                                 'rows': normalize_catalog(doc['rows'], day)}
            except Exception as error:
                diagnostics.append({'date': day, 'dataset': 'catalog', 'error': type(error).__name__})
            print(f'taxonomy catalog {day}', flush=True)
        codes_by_day = defaultdict(set)
        reverse_coverage = {day: True for day in days}
        core_tickers = sorted({r['ticker'] for day in days for r in market['cores'][day] if r['status'] == 'qualified'})
        for ticker in core_tickers:
            try:
                rows = self.members(days, con_code=ticker)
                for day in days:
                    qualified = any(r['ticker'] == ticker and r['status'] == 'qualified' for r in market['cores'][day])
                    found = [r for r in rows if iso(r['trade_date']) == day]
                    if qualified and not found:
                        reverse_coverage[day] = False
                        diagnostics.append({'date': day, 'ticker': ticker, 'dataset': 'core_memberships', 'error': 'empty_memberships'})
                    if qualified:
                        codes_by_day[day].update(r['ts_code'] for r in found)
                        known = {r['code'] for r in catalogs.get(day, {}).get('rows', [])}
                        if any(r['ts_code'] not in known for r in found):
                            reverse_coverage[day] = False
                            diagnostics.append({'date': day, 'ticker': ticker, 'dataset': 'core_memberships', 'error': 'unknown_catalog_code'})
            except Exception as error:
                for day in days:
                    if any(r['ticker'] == ticker and r['status'] == 'qualified' for r in market['cores'][day]):
                        reverse_coverage[day] = False
                        diagnostics.append({'date': day, 'ticker': ticker, 'dataset': 'core_memberships', 'error': type(error).__name__})
        eligible = {day: {r['code']: r for r in catalog['rows'] if r['research_eligible']} for day, catalog in catalogs.items()}
        codes = {code for day, values in codes_by_day.items() for code in values if code in eligible.get(day, {})}
        codes.update(code for code in retain_codes if any(code in rows for rows in eligible.values()))
        from shilun.review.theme_families import FAMILIES
        for family in FAMILIES.values():
            if codes.intersection(family['codes']):
                codes.update(family['codes'])
        codes = sorted(codes)
        memberships = defaultdict(dict)
        for index, code in enumerate(codes):
            try:
                rows = self.members(days, ts_code=code)
                for day in days:
                    raw = [r for r in rows if iso(r['trade_date']) == day]
                    entry = eligible.get(day, {}).get(code)
                    if not entry:
                        continue
                    members = normalize_members(raw, code, day)
                    if not members:
                        diagnostics.append({'date': day, 'code': code, 'dataset': 'members', 'error': 'empty_members'})
                        continue
                    memberships[day][code] = {'members': members, 'member_count': len(members),
                        'membership_date': day, 'membership_complete': True,
                        'membership_fetched_at': max(r['fetched_at'] for r in raw)}
            except Exception as error:
                diagnostics.append({'code': code, 'dataset': 'members', 'error': type(error).__name__})
            if index % 10 == 0 or index == len(codes)-1:
                print(f'taxonomy members {index+1}/{len(codes)}', flush=True)
        snapshots = {}
        for day, catalog in catalogs.items():
            snapshots[day] = {'date': day, 'version': TAXONOMY_VERSION, 'fetched_at': catalog['fetched_at'],
                'scope': 'qualified_core_candidates', 'core_mapping_complete': reverse_coverage[day],
                'rows': [{**row, **memberships[day].get(row['code'], {}),
                          'has_qualified_core': row['code'] in codes_by_day[day]}
                         for row in catalog['rows'] if row['research_eligible']]}
        return {'snapshots': snapshots, 'diagnostics': diagnostics, 'provider_docs': 'https://tushare.pro/document/2?doc_id=363',
                'note': '分类身份和成分仅用东方财富日期数据；复用统一分类校验。研究缓存不发布为全站完整分类快照。'}
