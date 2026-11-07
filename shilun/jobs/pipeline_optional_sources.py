"""Independently verified optional review inputs, with no network on inspection.

Success receipts do not stand in for actual auction or price coverage.  Only a
verified full-range suspension response can establish an explicitly empty range.
The optional Vibe heat cache is migration input, not a remote dependency.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from hashlib import sha256
import json
from math import isfinite
from pathlib import Path

from shilun.jobs.pipeline_state import error_details
from shilun.review.sources import finite_number


OPTIONAL_KEYS = ('first_board_auction', 'suspensions', 'board_cohort_prices', 'heat_summary', 'lhb')
RECEIPTS = 'data_pipeline_optional_sources'
SCHEMA = 2
SUSPENSION_PROTOCOL = 'verified_events_v2'
_VOLATILE = {'_id', 'fetched_at', 'imported_at', 'updated_at', 'checked_at'}


def _stable(value):
    if isinstance(value, dict):
        return {str(k): _stable(v) for k, v in sorted(value.items()) if k not in _VOLATILE}
    if isinstance(value, (list, tuple)):
        return [_stable(v) for v in value]
    if isinstance(value, float) and not isfinite(value):
        return None
    return value


def _revision(value):
    return sha256(json.dumps(_stable(value), sort_keys=True, ensure_ascii=False,
                             default=str, separators=(',', ':')).encode()).hexdigest()[:24]


def _check(key, day, ready, reason, facts, *, status=None, **details):
    return {'key': key, 'dataset': key, 'date': day,
            'status': status or ('ready' if ready else 'missing'), 'ready': bool(ready),
            'blocking': False, 'consumers': ['review'], 'reason': reason,
            'revision': _revision({'date': day, 'key': key, 'facts': facts}), **details}


def _sessions(store, day):
    from shilun.review.board_data import BoardResearchData
    return [d for d in BoardResearchData(store).sessions(day, 60) if d <= day]


def _auction_check(store, day, dates):
    from shilun.review.board_auction import load_opening_auction, nonnegative_amount, SOURCE, VERSION
    from shilun.review.board_data import BoardResearchData
    days = dates[-2:]
    pools = BoardResearchData(store).pools(days, settled=True)
    facts, missing, unknown = [], [], []
    for target in days:
        pool = pools.get(target)
        if not pool or pool.get('date') != target or not pool.get('complete'):
            missing.append({'date': target, 'reason': '同日定稿涨停名单缺失'})
            facts.append({'date': target, 'pool': None})
            continue
        stocks = pool.get('stocks', [])
        if any(r.get('boards') is None for r in stocks):
            unknown.append(target)
        required = sorted({r['code'] for r in stocks if r.get('boards') == 1})
        doc = load_opening_auction(store, target)
        verified = (doc.get('date') == target and doc.get('source') == SOURCE and doc.get('schema') == VERSION)
        amounts = doc.get('amounts', {}) if verified else {}
        covered = {code: nonnegative_amount(amounts.get(code)) for code in required}
        missing.extend({'date': target, 'ticker': code, 'reason': '首板09:25竞价金额缺失'}
                       for code, value in covered.items() if value is None)
        facts.append({'date': target, 'required': required, 'amounts': covered,
                      'unknown_boards': target in unknown})
    complete = len(days) == 2 and days[-1] == day and not missing and not unknown
    reason = ('昨日与当日首板竞价金额完整（无首板时为明确零样本）' if complete else
              '昨日与当日首板竞价待齐：'+str(len(missing))+'项缺口；板数未确认日 '+str(len(unknown)))
    if len(days) < 2:
        reason = '昨日交易日历缺失，不能确认两日首板竞价完整性'
    return _check('first_board_auction', day, complete, reason, facts,
                  status='degraded' if facts and not complete else None,
                  missing=missing, unknown_board_dates=unknown, dates=days)


def _suspension_facts(store, dates):
    from shilun.review.suspensions import COLLECTION
    rows = list(store.collection(COLLECTION).find({'date': {'$in': dates}}, {'_id': 0}))
    facts = [{k: r.get(k) for k in ('date', 'ticker', 'suspend_type', 'suspend_timing')}
             for r in rows if r.get('date') in dates]
    unique = {_revision(row): row for row in facts}
    return sorted(unique.values(), key=lambda r: (r['date'], str(r.get('ticker')), str(r.get('suspend_type')), str(r.get('suspend_timing'))))


def _suspensions_check(store, day, dates):
    facts = _suspension_facts(store, dates)
    receipt = store.collection(RECEIPTS).find_one(
        {'date': day, 'key': 'suspensions', 'schema': SCHEMA}, {'_id': 0}) or {}
    signature = _revision(facts)
    source = receipt.get('source_report') or {}
    complete = (bool(dates) and dates[-1] == day and receipt.get('status') == 'ready'
                and receipt.get('from') == dates[0] and receipt.get('to') == day
                and receipt.get('content_revision') == signature
                and receipt.get('row_count') == len(facts)
                and receipt.get('producer_schema') == SUSPENSION_PROTOCOL
                and source.get('source_schema') == 2 and source.get('verified') is True
                and source.get('source_revision') == signature == source.get('readback_revision'))
    return _check('suspensions', day, complete,
                  '停复牌窗口已核验，包含明确零记录' if complete else '停复牌完整窗口尚未核验，不能把无记录当作无停牌',
                  {'producer_schema': SUSPENSION_PROTOCOL, 'dates': dates, 'rows': facts, 'complete': complete},
                  row_count=len(facts), start=dates[0] if dates else None)


def _price_facts(store, day, dates):
    from shilun.review.board_data import BoardResearchData, needed_prices
    from shilun.review.dated_pools import COLLECTION
    from shilun.review.suspensions import load_suspensions
    from shilun.review.board_price_quality import positive
    dates = dates[-31:]
    adapter = BoardResearchData(store)
    ups, broken = adapter.pools(dates, settled=True), adapter.pools(dates, 'broken', settled=True)
    previous = {r['analysis_date']: r for r in store.collection(COLLECTION).find(
        {'analysis_date': {'$in': dates}}, {'_id': 0}) if r.get('analysis_date') in dates}
    needed = needed_prices(dates, previous, ups, broken)
    # A missing cohort is not evidence for a zero-stock cohort.
    missing_pools = [{'date': target, 'kind': kind} for target in dates
                     for kind, docs in (('up', ups), ('broken', broken))
                     if not docs.get(target) or docs[target].get('date') != target or not docs[target].get('complete')]
    suspended = load_suspensions(store, dates)
    facts, gaps = [], []
    for target, tickers in sorted(needed.items()):
        bars = {r['ticker']: r for r in store.collection('market_daily_bars').find(
            {'date': target, 'ticker': {'$in': sorted(tickers)}}, {'_id': 0}) if r.get('date') == target}
        refs = {r['ticker']: r for r in store.collection('daily_price_limits').find(
            {'trade_date': {'$in': [target, target.replace('-', '')]}, 'ticker': {'$in': sorted(tickers)}}, {'_id': 0})
                if str(r.get('trade_date', '')).replace('-', '') == target.replace('-', '')}
        for ticker in sorted(tickers):
            bar = bars.get(ticker, {})
            close = positive(bar.get('close'))
            reference = positive(bar.get('pre_close')) or positive(refs.get(ticker, {}).get('pre_close'))
            is_suspended = target in suspended.get(ticker, ())
            facts.append({'date': target, 'ticker': ticker, 'close': close,
                          'reference': reference, 'suspended': is_suspended})
            if not is_suspended and not (close and reference):
                gaps.append({'date': target, 'ticker': ticker,
                             'reason': '次日收盘行情待补齐' if not close else '次日收益计价基准待补齐'})
    return {'dates': dates, 'prices': facts, 'missing_pools': missing_pools}, gaps


def _prices_check(store, day, dates):
    facts, gaps = _price_facts(store, day, dates)
    complete = bool(dates) and dates[-1] == day and not gaps and not facts['missing_pools']
    reason = ('涨停与炸板固定样本的行情和昨收已核验，停牌单列' if complete else
              f"样本行情缺口 {len(gaps)} 项；缺少定稿板池 {len(facts['missing_pools'])} 项，未按无样本处理")
    return _check('board_cohort_prices', day, complete, reason, facts,
                  status='degraded' if not complete else None, unresolved=gaps,
                  missing_pools=facts['missing_pools'])


def _heat_path(day):
    return Path.home() / '.duanxian-agents/cache/zt_summary' / f'{day}.json'


def _heat_check(store, day):
    doc = store.collection('review_heat_summaries').find_one({'date': day}, {'_id': 0}) or {}
    if doc.get('date') == day and doc.get('source') == 'vibe_settled_zt_summary':
        facts = {k: doc.get(k) for k in ('date', 'limit_up', 'highest_consec', 'broken_rate')}
        count, height, rate = (finite_number(facts[k]) for k in ('limit_up', 'highest_consec', 'broken_rate'))
        valid = (count is not None and count >= 0 and count == int(count)
                 and height is not None and height >= 0 and height == int(height)
                 and (rate is None or 0 <= rate <= 1))
        return _check('heat_summary', day, valid, '同日可选热度缓存已归档' if valid else '热度缓存字段未通过核验', facts)
    path = _heat_path(day)
    exists = path.is_file()
    return _check('heat_summary', day, not exists,
                  '无可选Vibe热度缓存；不适用，不发起远程重试' if not exists else '同日本地热度缓存待导入',
                  {'local_cache': exists, 'checksum': sha256(path.read_bytes()).hexdigest() if exists else None},
                  applicability='not_applicable' if not exists else 'available')


def _lhb_check(store, day):
    from shilun.review.strength_review import LHB
    doc = store.collection(LHB).find_one({'date': day, 'status': 'ready'}, {'_id': 0}) or {}
    facts = {k: doc.get(k) for k in ('date', 'status', 'summaries', 'seats', 'source')}
    for key in ('summaries', 'seats'):
        if isinstance(facts[key], list):
            facts[key] = sorted(facts[key], key=lambda row: json.dumps(_stable(row), sort_keys=True, default=str))
    valid = (doc.get('date') == day and doc.get('status') == 'ready'
             and all(isinstance(doc.get(k), list) and len(doc[k]) < 10000 for k in ('summaries', 'seats'))
             and all(str(row.get('trade_date', '')).replace('-', '') == day.replace('-', '')
                     for key in ('summaries', 'seats') for row in doc.get(key, [])))
    return _check('lhb', day, valid, '同日龙虎榜已归档，空榜可核验为零样本' if valid else '同日龙虎榜两组结果尚未完整归档', facts)


def _inspect_one(store, day, key, dates=None):
    if key not in OPTIONAL_KEYS:
        raise ValueError('Unknown optional source: '+str(key))
    if date.fromisoformat(day).isoformat() != day:
        raise ValueError('Optional source requires an exact ISO date')
    if key == 'heat_summary':
        return _heat_check(store, day)
    if key == 'lhb':
        return _lhb_check(store, day)
    dates = dates if dates is not None else _sessions(store, day)
    if not dates or dates[-1] != day:
        return _check(key, day, False, '所选日完整交易日历缺失', {'dates': dates})
    return {'first_board_auction': _auction_check, 'suspensions': _suspensions_check,
            'board_cohort_prices': _prices_check}[key](store, day, dates)


def inspect_optional_sources(store, day):
    """Inspect independent cache facts; never fill a gap or call a provider."""
    date.fromisoformat(day)
    try:
        dates = _sessions(store, day)
    except Exception as error:
        detail = error_details(error)
        return [_check(key, day, False, '可选来源交易日历核验失败：'+detail['error_message'],
                       detail, **detail) for key in OPTIONAL_KEYS]
    return [_inspect_safe(store, day, key, dates) for key in OPTIONAL_KEYS]


def _inspect_safe(store, day, key, dates=None):
    try:
        return _inspect_one(store, day, key, dates)
    except Exception as error:
        detail = error_details(error)
        return _check(key, day, False, '可选来源核验失败：'+detail['error_message'], detail, **detail)


class _RecordingClient:
    """Retain sanitized failures when a legacy adapter returns only a type."""
    def __init__(self):
        from shilun.data.providers.tushare_provider import TushareConfig, TushareDailyClient
        self.client = TushareDailyClient(TushareConfig.from_env())
        self.errors = []

    def _call_api(self, api, **kwargs):
        try:
            frame = self.client._call_api(api, **kwargs)
            target = kwargs.get('trade_date', '')
            if api == 'stk_auction':
                from shilun.review.board_auction import normalize_opening_auction
                normalize_opening_auction(frame, f'{target[:4]}-{target[4:6]}-{target[6:]}')
            elif api in ('top_list', 'top_inst'):
                if len(frame) >= 10000 or (not frame.empty and ('trade_date' not in frame.columns or
                        any(str(d).replace('-', '') != target for d in frame['trade_date']))):
                    raise ValueError('龙虎榜日期或完整性核验失败')
            return frame
        except Exception as error:
            self.errors.append(error_details(error))
            raise

    def fetch_daily_for_trade_date(self, target, **kwargs):
        try:
            frame = self.client.fetch_daily_for_trade_date(target, **kwargs)
            if (frame.empty or not {'date', 'ticker', 'close', 'pre_close'} <= set(frame.columns)
                    or any(str(d).replace('-', '') != target for d in frame['date'])
                    or frame['ticker'].duplicated().any()):
                raise ValueError('补价日线日期、字段或唯一性未通过核验')
            return frame
        except Exception as error:
            self.errors.append(error_details(error))
            raise


def prepare_optional_source(store, day, key):
    """Prepare exactly one source, then verify its archive instead of trusting a receipt."""
    if key not in OPTIONAL_KEYS:
        raise ValueError('Unknown optional source: '+str(key))
    if date.fromisoformat(day).isoformat() != day:
        raise ValueError('Optional source requires an exact ISO date')
    before = _inspect_safe(store, day, key)
    if before['ready']:
        return {**before, 'cached': True, 'reports': []}
    if key == 'board_cohort_prices' and not before.get('unresolved'):
        # Only cohort membership is missing. A price request cannot repair it.
        return {**before, 'cached': False, 'reports': [], **error_details(ValueError(before['reason']))}
    reports, client = [], None
    try:
        if key == 'first_board_auction':
            from shilun.review.board_auction import sync_first_board_auction
            client = _RecordingClient()
            for target in before.get('dates', []):
                reports.append(sync_first_board_auction(store, target, client=client))
        elif key == 'suspensions':
            from shilun.review.suspensions import sync_suspensions
            dates = _sessions(store, day)
            if not dates or dates[-1] != day:
                raise ValueError('停复牌回补所需交易日历缺失')
            report = sync_suspensions(store, dates[0], day)
            reports.append(report)
            facts = _suspension_facts(store, dates)
            if (report.get('from') != dates[0] or report.get('to') != day
                    or report.get('source_schema') != 2 or report.get('verified') is not True
                    or report.get('stock_day_count') != len(facts)
                    or report.get('source_revision') != _revision(facts)
                    or report.get('readback_revision') != _revision(facts)):
                raise ValueError(f"停复牌窗口核验未通过：请求 {dates[0]}→{day}；原始 {report.get('raw_count', '未知')}，"
                                 f"唯一事件 {report.get('unique_event_count', '未知')}，股票日 {report.get('stock_day_count', '未知')}，"
                                 f"实际读回 {len(facts)}；来源与读回内容指纹须一致")
            store.collection(RECEIPTS).update_one({'_id': f'{day}:{key}'}, {'$set': {
                'date': day, 'key': key, 'schema': SCHEMA, 'status': 'ready',
                'producer_schema': SUSPENSION_PROTOCOL,
                'from': dates[0], 'to': day, 'row_count': len(facts), 'content_revision': _revision(facts),
                'source_report': report,
                'checked_at': datetime.now(timezone.utc).isoformat()},
                '$unset': {'error_type': '', 'error_message': ''}}, upsert=True)
        elif key == 'board_cohort_prices':
            from shilun.review.board_price_quality import sync_board_price_gaps
            client = _RecordingClient()
            reports.append(sync_board_price_gaps(store, day, client=client))
        elif key == 'heat_summary':
            from shilun.review.dated_pools import sync_heat_summaries
            raw = json.loads(_heat_path(day).read_bytes())
            values = raw.get('summary') or {}
            count, height, rate = (finite_number(values.get(k)) for k in ('limit_up', 'highest_consec', 'broken_rate'))
            if (raw.get('date') != day or raw.get('schema') != 1 or raw.get('source') != 'akshare_zt_pool'
                    or count is None or count < 0 or count != int(count)
                    or height is None or height < 0 or height != int(height)
                    or rate is not None and not 0 <= rate <= 1):
                raise ValueError('同日热度缓存日期、来源或字段未通过核验')
            reports.extend(sync_heat_summaries(store, [day]))
        elif key == 'lhb':
            from shilun.review.strength_review import LHB, sync_lhb
            # The legacy adapter trusts status=ready before inspecting rows.
            # Preserve the rejected evidence but let a fresh request replace it.
            store.collection(LHB).update_one({'date': day, 'status': 'ready'}, {'$set': {
                'status': 'invalid', 'validation_error': before['reason']}}, upsert=False)
            client = _RecordingClient()
            reports.extend(sync_lhb(store, [day], client=client))
        after = _inspect_safe(store, day, key)
        result = {**after, 'cached': False, 'reports': reports}
        if not after['ready']:
            errors = client.errors if client is not None else []
            detail = errors[-1] if errors else error_details(ValueError(after['reason']))
            result.update(detail)
        return result
    except Exception as error:
        if isinstance(getattr(error, 'report', None), dict) and error.report not in reports:
            reports.append(error.report)
        detail = error_details(error)
        result = {**before, 'cached': False, 'reports': reports, **detail}
        if key == 'suspensions':
            source_report = dict(reports[-1]) if reports else {
                'source_schema': 2, 'requested_start': before.get('start'), 'requested_end': day,
                'raw_count': None, 'unique_event_count': None, 'stock_day_count': None,
                'written_count': None, 'readback_count': None, 'extra_cached_count': None,
                'missing_count': None, 'verified': False, 'unconfirmed_reasons': [detail['error_message']]}
            source_report['adapter_verified'] = False
            try:
                store.collection(RECEIPTS).update_one({'_id': f'{day}:{key}'}, {'$set': {
                    'date': day, 'key': key, 'schema': SCHEMA, 'producer_schema': SUSPENSION_PROTOCOL,
                    'status': 'unavailable', 'source_report': source_report,
                    'from': source_report.get('from') or source_report.get('requested_start'),
                    'to': source_report.get('to') or source_report.get('requested_end') or day,
                    **detail, 'checked_at': datetime.now(timezone.utc).isoformat()}}, upsert=True)
            except Exception as persist_error:
                result['report_persist_error'] = error_details(persist_error)
        return result
