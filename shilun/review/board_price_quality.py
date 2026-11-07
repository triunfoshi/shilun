"""Explain absent next-session returns and repair only genuine price gaps."""
from shilun.review.sources import finite_number


def positive(value):
    value = finite_number(value)
    return value if value is not None and value > 0 else None


def annotate_prices(collections, bars, suspensions):
    from shilun.review.dated_pools import stock_code
    for collection in collections:
        for day, rows in collection.items():
            for row in rows:
                ticker = stock_code(row['code'])
                suspended = day in suspensions.get(ticker, set())
                if finite_number(row.get('ret')) is not None:
                    status, reason = ('conflict', '收益与全天停牌记录冲突，待核验') if suspended else ('ready', '已覆盖')
                elif suspended:
                    status, reason = 'suspended', '全天停牌，未产生次日收益'
                elif not positive(bars.get((day, row['code']), {}).get('close')):
                    status, reason = 'missing_bar', '次日收盘行情待补齐'
                else:
                    status, reason = 'missing_reference', '次日收益计价基准待补齐'
                row.update(price_status=status, price_reason=reason,
                           price_evidence_date=day,
                           price_evidence_source='tushare_suspend_d' if suspended else 'dated_pool_and_daily_bars')


def price_coverage(rows):
    missing = [r for r in rows if finite_number(r.get('ret')) is None]
    suspended = sum(r.get('price_status') == 'suspended' for r in missing)
    return {'total': len(rows), 'observed': len(rows)-len(missing),
            'suspended': suspended, 'unresolved': len(missing)-suspended,
            'conflicts': sum(r.get('price_status') == 'conflict' for r in rows)}


def price_exceptions(groups):
    unique = {}
    for group in groups:
        for row in group['rows']:
            if finite_number(row.get('ret')) is not None and row.get('price_status') != 'conflict':
                continue
            key = row['date'], row['code']
            record = unique.setdefault(key, {k: row.get(k) for k in (
                'date', 'cohort_date', 'code', 'name', 'ret', 'price_status',
                'price_reason', 'price_evidence_date', 'price_evidence_source')})
            record.setdefault('groups', []).append(group['name'])
    return sorted(unique.values(), key=lambda r: (r['date'], r['code']), reverse=True)


def sync_cohort_prices(store, needed, *, client=None):
    """One bounded fetch per date with actual gaps; repeat runs are no-ops.

    Suspension evidence is refreshed by the caller before this check. Missing
    evidence never implies a suspension; absent prices remain unresolved.
    """
    from shilun.review.suspensions import load_suspensions
    suspensions = load_suspensions(store, list(needed))
    report = {'dataset': 'board_cohort_prices', 'status': 'ok', 'checked': 0,
              'repaired': 0, 'suspended': [], 'unresolved': [], 'errors': []}

    def read(day, codes):
        bars = {r['ticker']: r for r in store.collection('market_daily_bars').find(
            {'date': day, 'ticker': {'$in': codes}},
            {'_id': 0, 'ticker': 1, 'close': 1, 'pre_close': 1})}
        refs = {r['ticker']: r for r in store.collection('daily_price_limits').find(
            {'trade_date': {'$in': [day, day.replace('-', '')]}, 'ticker': {'$in': codes}},
            {'_id': 0, 'ticker': 1, 'pre_close': 1})}
        return {ticker: 'missing_bar' if not positive(bars.get(ticker, {}).get('close'))
                else 'missing_reference' for ticker in codes
                if not (positive(bars.get(ticker, {}).get('close')) and
                        (positive(bars.get(ticker, {}).get('pre_close')) or positive(refs.get(ticker, {}).get('pre_close'))))}

    for day, tickers in sorted(needed.items()):
        codes = sorted(tickers)
        if not codes:
            continue
        report['checked'] += len(codes)
        gaps = read(day, codes)
        for ticker in list(gaps):
            if day in suspensions.get(ticker, set()):
                report['suspended'].append({'date': day, 'ticker': ticker})
                del gaps[ticker]
        if not gaps:
            continue
        try:
            if client is None:
                from shilun.data.providers.tushare_provider import TushareConfig, TushareDailyClient
                client = TushareDailyClient(TushareConfig.from_env())
            frame = client.fetch_daily_for_trade_date(day.replace('-', ''), include_reference=True)
            if frame.empty or not {'date', 'ticker', 'close', 'pre_close'} <= set(frame.columns):
                raise ValueError('日线返回为空或字段不完整')
            if any(str(d).replace('-', '') != day.replace('-', '') for d in frame['date']) or frame['ticker'].duplicated().any():
                raise ValueError('日线日期或唯一性未通过核验')
            selected = frame[frame['ticker'].isin(gaps)]
            selected = selected[selected['close'].map(positive).notna() & selected['pre_close'].map(positive).notna()]
            if not selected.empty:
                store.upsert_daily_bars(selected.assign(source='tushare_daily'))
        except Exception as error:
            report['errors'].append({'date': day, 'error_type': type(error).__name__})
        remaining = read(day, sorted(gaps))
        report['repaired'] += len(gaps)-len(remaining)
        report['unresolved'].extend({'date': day, 'ticker': ticker, 'reason': reason}
                                    for ticker, reason in remaining.items())
    if report['unresolved'] or report['errors']:
        report['status'] = 'unavailable'
    return report


def sync_board_price_gaps(store, target, *, days=30, client=None):
    from shilun.review.board_data import BoardResearchData, needed_prices
    from shilun.review.dated_pools import COLLECTION
    adapter = BoardResearchData(store)
    dates = adapter.sessions(target, days)
    previous = {r['analysis_date']: r for r in store.collection(COLLECTION).find(
        {'analysis_date': {'$in': dates}}, {'_id': 0})}
    needed = needed_prices(dates, previous, adapter.pools(dates, settled=True),
                           adapter.pools(dates, 'broken', settled=True))
    return {'date': target, **sync_cohort_prices(store, needed, client=client)}
