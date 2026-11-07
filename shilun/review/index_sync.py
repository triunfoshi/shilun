"""Repair dated index prerequisites before an explicit review/research build."""
from datetime import date, timedelta

REVIEW_INDICES = ('000001.SH', '000300.SH', '399001.SZ', '399006.SZ')


def sync_index_history(store, target, tickers=REVIEW_INDICES, *, client=None):
    from shilun.data.providers.tushare_provider import TushareConfig, TushareDailyClient
    reports = []
    for ticker in dict.fromkeys(tickers):
        latest = store.collection('market_daily_bars').find_one(
            {'ticker': ticker, 'date': {'$lte': target}}, {'_id': 0, 'date': 1}, sort=[('date', -1)])
        if latest and latest['date'] == target:
            continue
        start = latest['date'] if latest else (date.fromisoformat(target)-timedelta(days=240)).isoformat()
        try:
            client = client or TushareDailyClient(TushareConfig.from_env())
            frame = client.fetch_index_daily(ticker, start.replace('-', ''), target.replace('-', ''))
            days = frame['date'].dt.strftime('%Y-%m-%d') if not frame.empty else []
            if frame.empty or target not in set(days) or set(frame['ticker']) != {ticker}:
                raise ValueError('所选日指数尚未发布')
            count = store.upsert_daily_bars(frame.assign(source='tushare_index_daily'))
            reports.append({'dataset': 'index_daily', 'ticker': ticker, 'date': target, 'status': 'ok', 'row_count': count})
        except Exception as error:
            reports.append({'dataset': 'index_daily', 'ticker': ticker, 'date': target,
                            'status': 'unavailable', 'error_type': type(error).__name__})
    return reports
