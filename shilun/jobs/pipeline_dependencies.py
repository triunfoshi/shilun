"""Exact-day prerequisites and explicit, independently retryable source repairs.

Inspections read archived facts only. Preparations repair one named source and
then inspect again; a successful fetch is never itself proof that a dependency
is ready. Vibe event reasons and industry classifications remain distinct.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from hashlib import sha256
import json
from math import isfinite

from shilun.market.taxonomy import VERSION as TAXONOMY_VERSION
from shilun.review.sources import load_review_sessions
from shilun.review.strong_stock_data import a_share

DEPENDENCY_KEYS = ('trade_calendar', 'daily_bars', 'previous_daily_bars', 'benchmark_bars',
    'daily_basic', 'moneyflow', 'price_limits', 'industry_taxonomy', 'board_pools',
    'limit_reasons', 'previous_limit_pool', 'cffex_positions', 'review_history', 'board_history', 'price_history', 'index_history',
    'core_history', 'theme_history')
ALL_CONSUMERS = ('strong_stocks', 'review', 'theme_cycles')
_VOLATILE = {'_id', 'fetched_at', 'updated_at', 'created_at', 'imported_at',
             'collected_at', 'checked_at', 'generated_at'}


def _day(value):
    value = str(value or '').replace('-', '')[:8]
    return f'{value[:4]}-{value[4:6]}-{value[6:]}' if len(value) == 8 and value.isdigit() else ''


def _number(value):
    try:
        value = float(value)
        return value if isfinite(value) else None
    except (TypeError, ValueError):
        return None


def _positive(value):
    value = _number(value)
    return value is not None and value > 0


def _stable(value):
    if isinstance(value, dict):
        return {k: _stable(v) for k, v in sorted(value.items()) if k not in _VOLATILE}
    if isinstance(value, (list, tuple)):
        items = [_stable(v) for v in value]
        return sorted(items, key=lambda v: json.dumps(v, ensure_ascii=False, sort_keys=True, default=str))
    if isinstance(value, float) and not isfinite(value):
        return None
    return value


def revision(value):
    return sha256(json.dumps(_stable(value), ensure_ascii=False, sort_keys=True,
                            default=str, allow_nan=False).encode()).hexdigest()


def _check(key, day, *, ready, complete=None, needs_retry=None, blocking=False, consumers=ALL_CONSUMERS,
           reason, evidence, **details):
    complete = ready if complete is None else complete
    return {'key': key, 'date': day, 'status': 'ready' if complete else 'degraded' if ready else 'missing',
        'ready': bool(ready), 'complete': bool(complete),
        'needs_retry': not ready if needs_retry is None else bool(needs_retry),
        'blocking': blocking, 'consumers': list(consumers), 'reason': reason,
        'revision': revision({'date': day, 'evidence': evidence}), **details}


def _records(store, collection, day, *, day_field='trade_date', fields=None):
    query = {day_field: {'$in': [day, day.replace('-', '')]}}
    rows = store.collection(collection).find(query, {'_id': 0, **{f: 1 for f in fields}} if fields else {'_id': 0})
    # Enforce the date after reading as well: wrong-date mocks or malformed
    # archived envelopes cannot make today's data appear available.
    return [r for r in rows if _day(r.get(day_field)) == day]


def _stock_rows(store, day):
    fields = ('ticker', 'date', 'open', 'high', 'low', 'close', 'pre_close', 'volume', 'amount')
    return [r for r in _records(store, 'market_daily_bars', day, day_field='date', fields=fields)
            if a_share(r.get('ticker', ''))]


def _pools(store, days, kind='up'):
    from shilun.review.board_data import BoardResearchData
    return {day: doc for day, doc in BoardResearchData(store).pools(list(days), kind, settled=True).items()
            if day in days and doc.get('date') == day and doc.get('complete') is True
            and doc.get('snapshot_mode') != 'live' and isinstance(doc.get('stocks'), list)}


def inspect_dependencies(store, day, *, include_history=True):
    """Return stable source checks for one exact trading session, without IO writes.

    ``blocking`` applies to the listed consumers. Optional missing sources stay
    explicit and retryable while allowing an honestly degraded review. Sparse
    per-stock gaps are not confused with an absent whole-market feed.
    """
    day = date.fromisoformat(day).isoformat()
    sessions = load_review_sessions(store, day)
    exact_calendar = bool(sessions and sessions[-1] == day)
    prior = sessions[-2] if exact_calendar and len(sessions) >= 2 else None
    current = _stock_rows(store, day)
    current_by_ticker = {r['ticker']: r for r in current if _positive(r.get('close'))}
    tickers = set(current_by_ticker)
    duplicate = len({r['ticker'] for r in current}) != len(current)
    previous = _stock_rows(store, prior) if prior else []
    prev_valid = {r['ticker']: r for r in previous if _positive(r.get('close'))}
    prior_join = tickers & prev_valid.keys()
    checks = [_check('trade_calendar', day, ready=bool(prior), blocking=True,
        reason='已核验当日与前一交易日' if prior else '同日或前一交易日历未核验', evidence=sessions),
        _check('daily_bars', day, ready=len(tickers) >= 500 and not duplicate,
            complete=len(tickers) >= 500 and not duplicate and len(tickers) == len(current), blocking=True,
            reason=f'同日有效A股日线 {len(tickers)} 只' + ('；存在重复记录' if duplicate else ''),
            evidence=current, count=len(tickers)),
        _check('previous_daily_bars', day, ready=bool(tickers) and len(prior_join)/len(tickers) >= .8,
            complete=bool(tickers) and len(prior_join) == len(tickers), blocking=True,
            reason=f'前一交易日 {prior or "未确认"} 与当日样本交集 {len(prior_join)}/{len(tickers)}',
            evidence={'previous_date': prior, 'rows': previous}, source_date=prior,
            count=len(prior_join), total=len(tickers))]
    from shilun.review.index_sync import REVIEW_INDICES
    benchmarks = [r for r in _records(store, 'market_daily_bars', day, day_field='date',
                    fields=('ticker', 'date', 'open', 'high', 'low', 'close', 'volume', 'amount'))
                  if r.get('ticker') in REVIEW_INDICES and _positive(r.get('close'))]
    indices = {r['ticker'] for r in benchmarks}
    checks.append(_check('benchmark_bars', day, ready=len(indices) == len(REVIEW_INDICES), blocking=True,
        consumers=('review', 'theme_cycles'), reason=f'同日宽基收盘 {len(indices)}/{len(REVIEW_INDICES)}',
        evidence=benchmarks, count=len(indices), total=len(REVIEW_INDICES)))
    for key, collection, required in (
            ('daily_basic', 'daily_basic', ('turnover_rate', 'circ_mv')),
            ('moneyflow', 'moneyflow', ('net_mf_amount',)),
            ('price_limits', 'daily_price_limits', ('up_limit', 'down_limit'))):
        rows = _records(store, collection, day)
        valid = {r.get('ticker') or r.get('ts_code') for r in rows
                 if all(_number(r.get(field)) is not None for field in required)} & tickers
        count, total = len(valid), len(tickers)
        enough = bool(total) and (count/total >= .8 if key == 'price_limits' else count > 0)
        checks.append(_check(key, day, ready=enough, complete=bool(total) and count == total,
            needs_retry=not total or count < total,
            blocking=key == 'price_limits', consumers=ALL_CONSUMERS if key == 'price_limits' else ('review',),
            reason=f'同日{key}有效覆盖 {count}/{total}', evidence=rows,
            count=count, total=total, coverage=count/total if total else None))
    from shilun.market.taxonomy import classify_stocks
    classified = classify_stocks(store, day, [{'ticker': t} for t in sorted(tickers)], industry_only=True)
    ready_stocks = [r for r in classified if r.get('classification_status') == 'ready'
                    and r.get('taxonomy_date') == day and r.get('taxonomy_version') == TAXONOMY_VERSION]
    count, total = len(ready_stocks), len(tickers)
    checks.append(_check('industry_taxonomy', day, ready=bool(total) and count/total >= .8,
        complete=bool(total) and count == total, blocking=True, consumers=('strong_stocks', 'review'),
        reason=f'同日东方财富唯一三级行业 {count}/{total}；零星缺口保留',
        evidence=classified, count=count, total=total, coverage=count/total if total else None))
    ups, broken = _pools(store, [day]), _pools(store, [day], 'broken')
    pools_ready = day in ups and day in broken
    checks.append(_check('board_pools', day, ready=pools_ready, blocking=True,
        consumers=('theme_cycles',), reason='同日涨停与炸板收盘池已核验' if pools_ready else '同日涨停或炸板收盘池未归档',
        evidence={'up': ups.get(day), 'broken': broken.get(day)}))
    reason_days = [d for d in (prior, day) if d]
    reason_pools = _pools(store, reason_days)
    from shilun.review.board_data import REASON_COLLECTION
    reasons = {r['date']: r for r in store.collection(REASON_COLLECTION).find(
        {'date': {'$in': reason_days}}, {'_id': 0}) if r.get('date') in reason_days}
    reason_coverage = []
    for d in reason_days:
        pool = reason_pools.get(d)
        required = {r['code'] for r in (pool or {}).get('stocks', []) if not r.get('ticker', '').endswith('.BJ')}
        labels = {str(k).split('.')[0]: v for k, v in reasons.get(d, {}).get('reasons', {}).items() if str(v or '').strip()}
        known = bool(pool) and (not required or bool(required & labels.keys()))
        reason_coverage.append({'date': d, 'ready': known, 'count': len(required & labels.keys()), 'total': len(required)})
    known_reasons = bool(reason_coverage) and all(r['ready'] for r in reason_coverage)
    complete_reasons = known_reasons and all(r['count'] == r['total'] for r in reason_coverage)
    checks.append(_check('limit_reasons', day, ready=known_reasons, complete=complete_reasons,
        needs_retry=not complete_reasons, blocking=True,
        consumers=('theme_cycles',), reason='同日及昨日事件原因覆盖已核验；缺标签不以行业替代' if known_reasons else '当日或昨日涨停原因尚未归档',
        evidence={'reasons': reasons, 'coverage': reason_coverage}, days=reason_coverage))
    # A failed optional yesterday-pool endpoint does not erase a usable frozen
    # prior-day cohort joined with the exact current and prior closes.
    prior_pool = _pools(store, [prior]).get(prior) if prior else None
    frozen = {r['ticker'] for r in (prior_pool or {}).get('stocks', [])}
    observed = {t for t in frozen if t in current_by_ticker
                and (_positive(current_by_ticker[t].get('pre_close')) or t in prev_valid)}
    fallback_ready = bool(prior_pool) and (not frozen or len(observed)/len(frozen) >= .8)
    from shilun.review.dated_pools import COLLECTION
    settled = store.collection(COLLECTION).find_one({'analysis_date': day}, {'_id': 0}) or {}
    from shilun.review.sources import at_limit
    settled_rows = settled.get('rows') if isinstance(settled.get('rows'), list) else []
    verified_settled = [r for r in settled_rows if _number(r.get('return_1d')) is not None
                       and at_limit(current_by_ticker.get(r.get('ticker'), {}).get('close'), r.get('close')) is True]
    settled_ready = (settled.get('analysis_date') == day and settled.get('previous_date') == prior
                     and bool(settled_rows) and len(verified_settled)/len(settled_rows) >= .8)
    prev_ready = fallback_ready or settled_ready
    checks.append(_check('previous_limit_pool', day, ready=prev_ready,
        complete=prev_ready and (settled_ready or len(observed) == len(frozen)), consumers=('review',),
        reason=f'已冻结昨日样本 {len(frozen)} 只，当前有效反馈 {len(observed)} 只' if fallback_ready else
               '已归档同日昨日涨停表现' if settled_ready else '昨日固定名单或当日反馈缺失',
        evidence={'prior': prior, 'pool': prior_pool, 'observed': sorted(observed), 'settled': settled},
        source='dated_pool_and_daily_close' if fallback_ready else settled.get('source'), count=len(observed), total=len(frozen)))
    future = store.collection('cffex_positions_daily').find_one({'analysis_date': day}, {'_id': 0}) or {}
    from shilun.data.cffex_positions import assess_snapshot
    assessment = assess_snapshot(future.get('payload') or {}, day)
    count, total = assessment['successful_count'], assessment['expected_count']
    checks.append(_check('cffex_positions', day, ready=count > 0, complete=count == total, needs_retry=count < total,
        consumers=('review',), reason=f'同日期货机构持仓 {count}/{total}；旁证不阻断主体',
        evidence=future.get('payload'), count=count, total=total, source_date=day if count else None))
    history_days = sessions[-21:]
    history_rows = [r for r in store.collection('market_daily_bars').find(
        {'date': {'$in': history_days}},
        {'_id': 0, 'ticker': 1, 'date': 1, 'open': 1, 'high': 1, 'low': 1,
         'close': 1, 'pre_close': 1, 'volume': 1, 'amount': 1})
        if r.get('date') in history_days and a_share(r.get('ticker', ''))]
    history_counts = {d: sum(r['date'] == d for r in history_rows) for d in history_days}
    history_missing = [d for d, n in history_counts.items() if n < 5000]
    checks.append(_check('review_history', day, ready=len(history_days) >= 21 and not history_missing,
        consumers=('review',), reason='复盘21日行情历史已覆盖' if len(history_days) >= 21 and not history_missing else f'复盘历史已核验 {len(history_days)}/21 日；行情缺口 {len(history_missing)} 日',
        evidence={'sessions': history_days, 'rows': history_rows}, missing_dates=history_missing))
    board_days = load_review_sessions(store, day, (date.fromisoformat(day)-timedelta(days=110)).isoformat())[-31:]
    history_up, history_broken = _pools(store, board_days), _pools(store, board_days, 'broken')
    missing_boards = [{'date': d, 'kind': kind} for d in board_days for kind, pool in
        (('up', history_up), ('broken', history_broken)) if d not in pool]
    checks.append(_check('board_history', day, ready=len(board_days) >= 31 and not missing_boards,
        consumers=('review',), reason='近31日涨停与炸板池已归档' if len(board_days) >= 31 and not missing_boards else f'历史板池已核验 {len(board_days)}/31 日；缺口 {len(missing_boards)} 项',
        evidence={'up': history_up, 'broken': history_broken, 'missing': missing_boards}, missing=missing_boards))
    limits = [r for r in store.collection('daily_price_limits').find(
        {'trade_date': {'$in': history_days + [d.replace('-', '') for d in history_days]}},
        {'_id': 0, 'ticker': 1, 'trade_date': 1, 'up_limit': 1, 'down_limit': 1, 'pre_close': 1, 'no_limit': 1})
        if _day(r.get('trade_date')) in history_days]
    limit_counts = {d: len({r.get('ticker') for r in limits if _day(r.get('trade_date')) == d}) for d in history_days}
    missing_prices = [d for d, n in limit_counts.items() if n < 5000]
    checks.append(_check('price_history', day, ready=len(history_days) >= 21 and not missing_prices,
        consumers=('review',), reason=f'近21日官方涨跌停价；缺口 {len(missing_prices)} 日',
        evidence={'rows': limits, 'sessions': history_days}, missing_dates=missing_prices))
    from shilun.review.index_sync import REVIEW_INDICES
    index_rows = [r for r in store.collection('market_daily_bars').find(
        {'date': {'$in': history_days}, 'ticker': {'$in': list(REVIEW_INDICES)}},
        {'_id': 0, 'date': 1, 'ticker': 1, 'close': 1, 'open': 1, 'high': 1, 'low': 1, 'amount': 1})
        if r.get('date') in history_days and r.get('ticker') in REVIEW_INDICES and _positive(r.get('close'))]
    index_missing = [{'date': d, 'ticker': t} for d in history_days for t in REVIEW_INDICES
                     if not any(r['date'] == d and r['ticker'] == t for r in index_rows)]
    checks.append(_check('index_history', day, ready=len(history_days) >= 21 and not index_missing,
        consumers=('review',), reason=f'近21日宽基历史；缺口 {len(index_missing)} 项',
        evidence={'rows': index_rows, 'sessions': history_days}, missing=index_missing))
    if include_history:
        checks.append(inspect_core_history(store, day, current_ready=len(tickers) >= 500 and not duplicate))
        checks.append(inspect_theme_history(store, day))
    return checks


def _stream_fingerprint(rows):
    """Order-independent sum of row SHA256 digests plus count, bounded memory.

    There is no lossy price averaging: every field, ticker and date contributes
    to a cryptographic digest. Including count also distinguishes duplicate and
    empty records. Source-only timestamps never invalidate an analysis.
    """
    count, value = 0, 0
    for row in rows:
        value = (value + int(revision(row), 16)) % (1 << 256)
        count += 1
    return {'count': count, 'content_sha256_sum': f'{value:064x}'}


def inspect_core_history(store, day, *, current_ready=None):
    """Fingerprint the actual strong-stock 120-session window without fetching.

    Stream projected rows rather than holding the full market history in RAM.
    History gaps remain evidence; this dependency never starts a 120-day repair.
    """
    from collections import Counter
    from shilun.market.research_scope import stock_allowed
    from shilun.review.strong_stock_data import LOOKBACK
    start = (date.fromisoformat(day)-timedelta(days=LOOKBACK*2+30)).isoformat()
    days = load_review_sessions(store, day, start)[-LOOKBACK:]
    meta = [r for r in store.collection('stock_basic').find({}, {'_id': 0, 'ts_code': 1, 'name': 1,
                'list_date': 1, 'delist_date': 1, 'list_status': 1})
            if a_share(r.get('ts_code', '')) and stock_allowed(r, day)
            and (not _day(r.get('list_date')) or _day(r['list_date']) <= day)
            and (not _day(r.get('delist_date')) or _day(r['delist_date']) > day)]
    eligible = {r['ts_code'] for r in meta}
    bar_counts, limit_counts, suspended_counts = Counter(), Counter(), Counter()
    def bars():
        for row in store.collection('market_daily_bars').find({'date': {'$in': days}},
                {'_id': 0, 'ticker': 1, 'date': 1, 'open': 1, 'close': 1, 'high': 1, 'low': 1,
                 'pre_close': 1, 'volume': 1}, batch_size=1500):
            if row.get('date') in days and row.get('ticker') in eligible:
                bar_counts[row['date']] += 1
                yield row
    def limits():
        for row in store.collection('daily_price_limits').find(
                {'trade_date': {'$in': days + [d.replace('-', '') for d in days]}},
                {'_id': 0, 'ticker': 1, 'ts_code': 1, 'trade_date': 1, 'up_limit': 1, 'pre_close': 1, 'no_limit': 1},
                batch_size=1500):
            d, ticker = _day(row.get('trade_date')), row.get('ticker') or row.get('ts_code')
            if d in days and ticker in eligible:
                limit_counts[d] += 1
                yield {**row, 'trade_date': d, 'ticker': ticker}
    bar_revision, limit_revision = _stream_fingerprint(bars()), _stream_fingerprint(limits())
    def suspended():
        for row in store.collection('review_daily_suspensions').find(
                {'date': {'$in': days}, 'suspend_type': 'S'}, {'_id': 0, 'date': 1, 'ticker': 1,
                 'suspend_type': 1, 'suspend_timing': 1}):
            if row.get('date') in days and row.get('ticker') in eligible:
                timing = row.get('suspend_timing')
                if timing is None or isinstance(timing, float) and not isfinite(timing):
                    suspended_counts[row['date']] += 1
                yield row
    suspension_revision = _stream_fingerprint(suspended())
    missing = [d for d in days if not bar_counts[d] or not limit_counts[d]]
    partial = [d for d in days if bar_counts[d] + suspended_counts[d] < sum(
        not _day(r.get('list_date')) or _day(r['list_date']) <= d for r in meta)]
    if current_ready is None:
        current_ready = len(_stock_rows(store, day)) >= 500
    complete = (len(days) == LOOKBACK and not missing and not partial and bool(eligible))
    return _check('core_history', day, ready=bool(current_ready), complete=complete, needs_retry=False,
        consumers=('strong_stocks', 'theme_cycles'),
        reason=f'核心实际{len(days)}/{LOOKBACK}交易日输入；整日行情或涨停价缺口 {len(missing)} 日、个股历史缺口 {len(partial)} 日；只读核验，不自动回补120日',
        evidence={'sessions': days, 'stocks': meta, 'bars': bar_revision, 'limits': limit_revision,
                  'suspensions': suspension_revision},
        sessions=len(days), requested_sessions=LOOKBACK, missing_dates=missing, partial_dates=partial,
        fingerprint_method='逐行SHA256之和及记录数，排除采集时间；流式读取120日实际必要字段')


def _theme_window(store, day):
    """Read small checkpoint metadata, never load the huge input archive in RAM."""
    from pathlib import Path
    import gzip
    import re
    from shilun.jobs.theme_daily_job import ROOT
    root = Path(ROOT)
    checkpoint = root/'inputs.json'
    try:
        source = root/json.loads(checkpoint.read_text())['source'] if checkpoint.exists() else root/'seed'
        with gzip.open(source/'market_inputs.json.gz', 'rt', encoding='utf8') as handle:
            # collect_market_inputs writes start/context_days before price maps.
            # If a legacy archive has another shape, disclose the bounded fallback.
            header = handle.read(262144)
        match = re.search(r'"start"\s*:\s*"(\d{4}-\d{2}-\d{2})"', header)
        context = re.search(r'"context_days"\s*:\s*(\[[^\]]*\])', header)
        if match:
            start = match[1]
            warmup = json.loads(context[1]) if context else []
            start = min([start] + [d for d in warmup if isinstance(d, str) and _day(d) == d])
            if start <= day:
                return load_review_sessions(store, day, start), '已验收题材基线及context窗口'
    except (OSError, ValueError, KeyError, TypeError):
        pass
    start = (date.fromisoformat(day)-timedelta(days=270)).isoformat()
    return load_review_sessions(store, day, start)[-120:], '未读取到基线，限定最近120个已核验交易日'


def inspect_theme_history(store, day):
    """Track historical editorial repairs independently of core certification."""
    from shilun.review.board_data import REASON_COLLECTION
    days, basis = _theme_window(store, day)
    reasons = [r for r in store.collection(REASON_COLLECTION).find({'date': {'$in': days}}, {'_id': 0})
               if r.get('date') in days]
    ups, broken = _pools(store, days), _pools(store, days, 'broken')
    reason_dates = {r['date'] for r in reasons if r.get('reasons')}
    missing = [d for d in days if d not in ups or d not in broken
               or ups[d].get('stocks') and d not in reason_dates]
    return _check('theme_history', day, ready=bool(days), complete=bool(days) and not missing, needs_retry=False,
        consumers=('theme_cycles',), reason=f'{basis}；历史原因与收盘双池只读指纹，不启动历史回补',
        evidence={'sessions': days, 'reasons': _stream_fingerprint(reasons), 'up': ups, 'broken': broken},
        from_date=days[0] if days else None, sessions=len(days), window_basis=basis, missing_dates=missing)


def _client():
    from shilun.data.providers.tushare_provider import TushareConfig, TushareDailyClient
    return TushareDailyClient(TushareConfig.from_env())


def _validate_frame(frame, day, field, *, ticker_field=None):
    if frame.empty or field not in frame or any(_day(v) != day for v in frame[field]):
        raise ValueError(f'{day} 返回日期不匹配或为空，未写入')
    if ticker_field and (ticker_field not in frame or frame[ticker_field].duplicated().any()):
        raise ValueError(f'{day} 返回证券重复或缺失，未写入')
    return frame


def _raise_unavailable(reports):
    failed = [r for r in reports if r.get('status') in ('unavailable', 'failed', 'partial')]
    if failed:
        detail = '; '.join(f'{r.get("date", "")} {r.get("dataset", "source")}: '
                           f'{r.get("error_message") or r.get("error_type") or r.get("status")}' for r in failed)
        raise ValueError(detail)


def _prepare_source(store, day, key):
    """Mutating adapters kept separately so offline tests never touch providers."""
    sessions = load_review_sessions(store, day)
    prior = sessions[-2] if len(sessions) >= 2 and sessions[-1] == day else None
    compact = day.replace('-', '')
    if key == 'trade_calendar':
        start = (date.fromisoformat(day)-timedelta(days=110)).isoformat()
        frame = _client().fetch_trade_calendar(start.replace('-', ''), compact)
        if frame.empty or any(not start <= _day(v) <= day for v in frame['cal_date']):
            raise ValueError('交易日历返回范围不匹配')
        return {'row_count': store.upsert_trade_calendar(frame)}
    if key in ('daily_bars', 'previous_daily_bars'):
        target = day if key == 'daily_bars' else prior
        if not target:
            raise ValueError('前一交易日尚未核验')
        frame = _validate_frame(_client().fetch_daily_for_trade_date(target.replace('-', '')),
                                target, 'date', ticker_field='ticker')
        return {'row_count': store.upsert_daily_bars(frame.assign(source='tushare_daily'))}
    if key == 'benchmark_bars':
        from shilun.review.index_sync import sync_index_history
        reports = sync_index_history(store, day)
        _raise_unavailable(reports)
        return {'reports': reports}
    if key == 'index_history':
        from shilun.review.index_sync import REVIEW_INDICES
        days = sessions[-21:]
        if not days:
            raise ValueError('指数历史缺少已核验交易日')
        rows = [r for r in store.collection('market_daily_bars').find(
            {'date': {'$in': days}, 'ticker': {'$in': list(REVIEW_INDICES)}}, {'_id': 0})
            if r.get('date') in days and r.get('ticker') in REVIEW_INDICES and _positive(r.get('close'))]
        existing = {(r['date'], r['ticker']) for r in rows}
        reports, client = [], None
        for ticker in REVIEW_INDICES:
            missing = [d for d in days if (d, ticker) not in existing]
            if not missing:
                continue
            client = client or _client()
            frame = client.fetch_index_daily(ticker, missing[0].replace('-', ''), missing[-1].replace('-', ''))
            if frame.empty or set(frame['ticker']) != {ticker} or any(
                    _day(d) not in days or not missing[0] <= _day(d) <= missing[-1] for d in frame['date']):
                raise ValueError(f'{ticker} 指数历史日期或证券代码校验失败')
            if not set(missing) <= {_day(d) for d in frame['date']}:
                raise ValueError(f'{ticker} 指数历史仍缺所请求交易日')
            count = store.upsert_daily_bars(frame.assign(source='tushare_index_daily'))
            reports.append({'dataset': 'index_daily', 'ticker': ticker, 'date': day, 'row_count': count})
        return {'reports': reports}
    if key in ('daily_basic', 'moneyflow', 'price_limits'):
        client = _client()
        fetcher = getattr(client, {'daily_basic': 'fetch_daily_basic', 'moneyflow': 'fetch_moneyflow',
                                  'price_limits': 'fetch_all_stock_limits'}[key])
        frame = fetcher(compact) if key == 'price_limits' else fetcher(trade_date=compact)
        frame = _validate_frame(frame, day, 'trade_date', ticker_field='ticker' if key == 'price_limits' else 'ts_code')
        writer = {'daily_basic': store.upsert_daily_basic, 'moneyflow': store.upsert_moneyflow,
                  'price_limits': store.upsert_price_limits}[key]
        return {'row_count': writer(frame)}
    if key == 'industry_taxonomy':
        from shilun.market.industry_history import sync_industry_history
        result = sync_industry_history(store, [day])
        if result.get('pending'):
            raise ValueError(f'同日三级行业未发布：{result.get("diagnostics", [])}')
        return result
    if key in ('board_pools', 'board_history'):
        from shilun.review.dated_pools import sync_review_board_history
        days = [day] if key == 'board_pools' else load_review_sessions(store, day,
            (date.fromisoformat(day)-timedelta(days=110)).isoformat())[-31:]
        reports = sync_review_board_history(store, days)
        _raise_unavailable(reports)
        return {'reports': reports}
    if key == 'limit_reasons':
        from shilun.review.board_data import sync_reasons
        reports = [sync_reasons(store, d) for d in (prior, day) if d]
        _raise_unavailable(reports)
        return {'reports': reports}
    if key == 'previous_limit_pool':
        if not prior:
            raise ValueError('昨日交易日尚未核验')
        from shilun.review.dated_pools import COLLECTION, fetch_previous
        rows = fetch_previous(day)
        store.collection(COLLECTION).update_one({'analysis_date': day}, {'$set': {
            'analysis_date': day, 'previous_date': prior, 'rows': rows,
            'source': 'eastmoney_dated_previous_pool', 'imported_at': datetime.now().astimezone().isoformat()}}, upsert=True)
        return {'row_count': len(rows)}
    if key == 'cffex_positions':
        from shilun.jobs.cffex_positions_sync_job import run
        result = run(store, day)
        if result.get('status') not in ('success', 'partial'):
            raise ValueError(result.get('error_message') or f'期货持仓任务 {result.get("status")}')
        return result
    if key == 'price_history':
        from shilun.review.dated_pools import sync_dated_evidence
        reports = sync_dated_evidence(store, sessions, include_previous=False)
        _raise_unavailable(reports)
        return {'reports': reports}
    if key == 'review_history':
        from shilun.review.dated_pools import sync_review_history
        reports = sync_review_history(store, day)
        _raise_unavailable(reports)
        return {'reports': reports}
    raise ValueError(f'未知依赖来源 {key}')


def prepare_dependency(store, day, key):
    """Repair just one prerequisite, preserving exact-day validation and retries.

    Ready feeds (including a verified yesterday-cohort fallback) are not fetched
    again. Sparse optional gaps remain visible in the returned degraded status.
    Failed repairs raise with useful details so the durable task ledger can
    schedule that source alone; a second inspection verifies a claimed repair.
    """
    if key not in DEPENDENCY_KEYS:
        raise ValueError(f'未知依赖来源 {key}')
    if key in ('core_history', 'theme_history'):
        check = inspect_core_history(store, day) if key == 'core_history' else inspect_theme_history(store, day)
        return {**check, 'cached': True, 'repair_policy': '只读指纹；不自动回补历史'}
    before = next(r for r in inspect_dependencies(store, day, include_history=False) if r['key'] == key)
    if before['ready'] and not before.get('needs_retry', False):
        return {**before, 'cached': True}
    result = _prepare_source(store, day, key)
    after = next(r for r in inspect_dependencies(store, day, include_history=False) if r['key'] == key)
    if not after['ready']:
        raise ValueError(f'{day} {key} 补数后仍未就绪：{after["reason"]}')
    return {**after, 'cached': False, 'source_result': result}
