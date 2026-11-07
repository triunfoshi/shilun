"""Verified full-day suspensions; never fill missing OHLC with synthetic bars."""
from collections import defaultdict
from datetime import date, datetime
from hashlib import sha256
import json
from math import isnan
import re
from shilun.review.board_data import CHINA

COLLECTION = 'review_daily_suspensions'
SOURCE_SCHEMA = 2
SOURCE_LIMIT = 5000


class SuspensionSourceError(ValueError):
    """A bounded source/readback report accompanies every validation failure."""
    def __init__(self, report):
        self.report = report
        super().__init__('；'.join(report.get('unconfirmed_reasons', [])) or '停复牌来源未通过核验')


def _timing(value):
    return None if value is None or isinstance(value, float) and isnan(value) else value


def load_suspensions(store, sessions):
    if not sessions:
        return {}
    result = {}
    for row in store.collection(COLLECTION).find({'date': {'$in': sessions}, 'suspend_type': 'S'}, {'_id': 0}):
        if not _timing(row.get('suspend_timing')):
            result.setdefault(row['ticker'], set()).add(row['date'])
    return result


def _day(value):
    compact = str(value or '').replace('-', '')
    if len(compact) != 8 or not compact.isdigit():
        raise ValueError('日期格式无效')
    return date(int(compact[:4]), int(compact[4:6]), int(compact[6:])).isoformat()


def _public_fact(row):
    return {key: row.get(key) for key in ('date', 'ticker', 'suspend_type', 'suspend_timing')}


def facts_revision(facts):
    return sha256(json.dumps(facts, sort_keys=True, ensure_ascii=False,
                             default=str, separators=(',', ':')).encode()).hexdigest()[:24]


def normalize_suspension_source(rows, start, end):
    """Validate the whole response, then group distinct events into stock days.

    The endpoint can return repeated events and same-day S/R transitions. Those
    are not additional stock days. Mixed S/R never establishes full-day S.
    """
    if date.fromisoformat(start).isoformat() != start or date.fromisoformat(end).isoformat() != end or start > end:
        raise ValueError('停复牌请求日期范围无效')
    report = {'dataset': 'suspensions', 'source_schema': SOURCE_SCHEMA,
              'requested_start': start, 'requested_end': end, 'from': start, 'to': end,
              'rows': len(rows), 'raw_count': len(rows), 'valid_row_count': 0,
              'normalization_complete': False, 'examined_count': 0,
              'unique_event_count': 0, 'duplicate_count': 0, 'stock_day_count': 0,
              'written_count': 0, 'readback_count': 0, 'extra_cached_count': 0,
              'missing_count': 0, 'invalid_count': 0, 'invalid_examples': [],
              'verified': False, 'unconfirmed_reasons': []}
    if len(rows) >= SOURCE_LIMIT:
        report['unconfirmed_reasons'].append(f'原始记录 {len(rows)} 达到接口上限 {SOURCE_LIMIT}，不能确认窗口完整')
        raise SuspensionSourceError(report)
    events = {}
    for index, row in enumerate(rows):
        report['examined_count'] += 1
        try:
            day = _day(row.get('trade_date'))
            ticker = str(row.get('ts_code') or '')
            kind = str(row.get('suspend_type') or '').upper()
            timing = _timing(row.get('suspend_timing'))
            timing = str(timing).strip() if timing is not None else None
            timing = timing or None
            if not start <= day <= end:
                raise ValueError('返回日期超出请求范围')
            if not re.fullmatch(r'\d{6}\.(SH|SZ|BJ)', ticker):
                raise ValueError('股票代码无效')
            if kind not in ('S', 'R'):
                raise ValueError('停复牌类型不是S/R')
            if timing and len(timing) > 120:
                raise ValueError('停复牌时段字段过长')
            event = {'date': day, 'ticker': ticker, 'suspend_type': kind, 'suspend_timing': timing}
            events[(day, ticker, kind, timing or '')] = event
            report['valid_row_count'] += 1
        except (AttributeError, TypeError, ValueError) as error:
            report['invalid_count'] += 1
            if len(report['invalid_examples']) < 10:
                report['invalid_examples'].append({'row_index': index, 'reason': str(error)})
    report['unique_event_count'] = len(events)
    report['duplicate_count'] = report['valid_row_count'] - len(events)
    if report['invalid_count']:
        report['unconfirmed_reasons'].append(f"原始记录 {report['raw_count']} 中有 {report['invalid_count']} 条日期、代码或类型无效，未将部分响应写成完整窗口")
        raise SuspensionSourceError(report)
    grouped = defaultdict(list)
    for key in sorted(events):
        grouped[key[:2]].append(events[key])
    documents, mixed = [], []
    for (day, ticker), rows in sorted(grouped.items()):
        kinds = {r['suspend_type'] for r in rows}
        full_day = kinds == {'S'} and any(r['suspend_timing'] is None for r in rows)
        # A same-day resumption prevents treating any S event as all-day S.
        kind = 'R' if 'R' in kinds else 'S'
        times = sorted({r['suspend_timing'] for r in rows if r['suspend_timing']})
        timing = None if full_day else '；'.join(times) or ('同日停复牌，不能确认全天停牌' if len(kinds) > 1 else None)
        documents.append({'date': day, 'ticker': ticker, 'suspend_type': kind,
            'suspend_timing': timing, 'full_day_suspended': full_day,
            'source_events': rows, 'source_schema': SOURCE_SCHEMA})
        if len(kinds) > 1:
            mixed.append({'date': day, 'ticker': ticker, 'reason': '同日有S及R事件，未按全天停牌处理'})
    facts = [_public_fact(r) for r in documents]
    report.update(stock_day_count=len(documents), mixed_stock_day_count=len(mixed),
                  mixed_stock_day_examples=mixed[:10], normalization_complete=True,
                  source_revision=facts_revision(facts))
    return documents, report


def _verify_archive(collection, documents, report, start, end):
    cached = list(collection.find({'date': {'$gte': start, '$lte': end}}, {'_id': 0}))
    observed, invalid = defaultdict(list), 0
    for row in cached:
        if start <= str(row.get('date', '')) <= end:
            if (not re.fullmatch(r'\d{6}\.(SH|SZ|BJ)', str(row.get('ticker') or ''))
                    or row.get('suspend_type') not in ('S', 'R')):
                invalid += 1
                continue
            observed[(row.get('date'), row.get('ticker'))].append(_public_fact(row))
    expected = {(r['date'], r['ticker']): _public_fact(r) for r in documents}
    extra = sorted(set(observed) - set(expected))
    missing = sorted(set(expected) - set(observed))
    mismatched = [key for key in sorted(set(expected) & set(observed))
                  if any(row != expected[key] for row in observed[key])]
    facts = [observed[key][0] for key in sorted(observed)]
    report.update(readback_count=len(observed), readback_raw_count=len(cached),
                  invalid_cached_count=invalid,
                  extra_cached_count=len(extra), missing_count=len(missing), mismatch_count=len(mismatched),
                  extra_cached_examples=[{'date': d, 'ticker': t} for d, t in extra[:10]],
                  missing_examples=[{'date': d, 'ticker': t} for d, t in missing[:10]],
                  mismatch_examples=[{'date': d, 'ticker': t} for d, t in mismatched[:10]],
                  readback_revision=facts_revision(facts))
    if extra:
        report['unconfirmed_reasons'].append(f'完整性待核查：保留了 {len(extra)} 个本次响应未返回的旧缓存股票日，未自动失效或删除')
    if missing or mismatched:
        report['unconfirmed_reasons'].append(f'读回缺失 {len(missing)} 个股票日、内容不一致 {len(mismatched)} 个股票日')
    if invalid:
        report['unconfirmed_reasons'].append(f'旧缓存有 {invalid} 条无效股票代码或类型，不能确认完整窗口')
    report['verified'] = not report['unconfirmed_reasons'] and report['source_revision'] == report['readback_revision']
    if not report['verified']:
        raise SuspensionSourceError(report)
    report['status'] = 'ready'
    return report


def sync_suspensions(store, start, end, *, client=None):
    from shilun.data.providers.tushare_provider import TushareConfig, TushareDailyClient
    client = client or TushareDailyClient(TushareConfig.from_env())
    rows = client._call_api('suspend_d', start_date=start.replace('-', ''), end_date=end.replace('-', '')).to_dict('records')
    documents, report = normalize_suspension_source(rows, start, end)
    collection = store.collection(COLLECTION)
    try:
        for document in documents:
            collection.replace_one({key: document[key] for key in ('date', 'ticker')},
                {**document, 'source_window': {'from': start, 'to': end},
                 'source_revision': report['source_revision'], 'fetched_at': datetime.now(CHINA).isoformat()}, upsert=True)
            report['written_count'] += 1
        return _verify_archive(collection, documents, report, start, end)
    except SuspensionSourceError:
        raise
    except Exception as error:
        from shilun.jobs.pipeline_state import error_details
        detail = error_details(error)
        report.update(detail)
        report['unconfirmed_reasons'].append(f"停复牌写入或读回失败：{detail['error_type']}：{detail['error_message']}")
        raise SuspensionSourceError(report) from error
