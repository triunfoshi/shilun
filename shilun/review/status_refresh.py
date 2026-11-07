"""Refresh same-day industry availability without rewriting review facts.

Review snapshots capture the state at generation time. A classification that
finishes later may repair the current read view, but must not erase the source
attempt history or change certification, prices, returns or trading readiness.
"""
from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
import re

from shilun.market.taxonomy import VERSION, classify_stocks


_INDUSTRY_ALERT = re.compile(r'^东方财富行业映射缺口 \d+ 只；未使用旧来源补齐。$')


def refresh_taxonomy_status(store, snapshot):
    """Return a cache-only status overlay for the exact archived review day.

    The cohort is the same-day A-share daily-bar cross-section, not the larger
    taxonomy directory. Index bars, duplicate stock bars and other dates cannot
    inflate coverage. This GET helper never materializes or updates a record.
    """
    from shilun.review.strong_stock_data import a_share

    result = deepcopy(snapshot)
    day = result.get('analysis_date')
    if not day or result.get('status') == 'not_materialized':
        return result
    rows = store.collection('market_daily_bars').find(
        {'date': day}, {'_id': 0, 'date': 1, 'ticker': 1})
    tickers = sorted({row['ticker'] for row in rows
                      if row.get('date') == day and a_share(row.get('ticker', ''))})
    checked_at = datetime.now(timezone.utc).isoformat()
    if not tickers:
        result['taxonomy_status'] = {'date': day, 'available': False,
            'checked_at': checked_at, 'reason': '同日个股日线横截面缺失，不能重判分类覆盖'}
        return result
    classified = classify_stocks(store, day, [{'ticker': ticker} for ticker in tickers], industry_only=True)
    counts, missing = Counter(), []
    for row in classified:
        status = row.get('classification_status') or 'stock_unmapped'
        if status == 'ready' and row.get('taxonomy_date') != day:
            status = 'taxonomy_date_mismatch'
        elif status == 'ready' and row.get('taxonomy_version') != VERSION:
            status = 'taxonomy_version_mismatch'
        counts[status] += 1
        if status != 'ready':
            missing.append({'ticker': row['ticker'], 'status': status})
    total, ready = len(tickers), counts.get('ready', 0)
    result['taxonomy_coverage'] = dict(counts)
    result['taxonomy_status'] = {'date': day, 'available': True, 'checked_at': checked_at,
        'scope': '同日已归档A股日线横截面', 'total': total, 'ready': ready,
        'coverage': round(ready/total, 4), 'missing': missing,
        'snapshot_coverage': deepcopy(snapshot.get('taxonomy_coverage', {}))}
    freshness = result.setdefault('freshness', {})
    alerts = [text for text in freshness.get('alerts', []) if not _INDUSTRY_ALERT.fullmatch(str(text))]
    if ready < total:
        alerts.append(f'东方财富行业映射缺口 {total-ready} 只；未使用旧来源补齐。')
    freshness['alerts'] = alerts
    quality = result.setdefault('data_quality', {})
    checks = quality.get('checks', [])
    check = {'id': 'unified_taxonomy', 'label': '东方财富统一分类',
        'status': 'pass' if ready == total else 'warn', 'observed': ready,
        'coverage': round(ready/total, 4),
        'impact': f'唯一三级行业覆盖 {ready}/{total}；分类缺口单独保留。'}
    quality['checks'] = [check if row.get('id') == 'unified_taxonomy' else row for row in checks]
    if not any(row.get('id') == 'unified_taxonomy' for row in checks):
        quality['checks'].append(check)
    # Appending a warning after the original reducer otherwise leaves a green
    # summary above a warning row. Preserve stronger states and execution flags.
    if quality.get('status') not in ('blocked', 'historical_only'):
        degraded = any(row.get('status') in ('warn', 'fail') for row in quality['checks'])
        quality.update(status='degraded' if degraded else 'ready',
                       label='部分模块降级' if degraded else '数据可执行')
    return result
