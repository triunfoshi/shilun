"""As-of-close strong-stock certification; no I/O and no theme selection.

A price-move cycle and a board-leg qualification window are separate identities.
Only the leg's own first consecutive run can satisfy its three-board condition.
All calendar positions count, including suspended or unavailable sessions.
"""
from __future__ import annotations

from copy import deepcopy
from shilun.review.sources import at_limit, finite_number as num

VERSION = 'core-qualification-v2'
from shilun.review.theme_rules import (CORE_WINDOW as WINDOW, CORE_MIN_BOARDS as MIN_BOARDS,
                                       CORE_MIN_STREAK as MIN_STREAK, DRAWDOWN_WARNING)
RULE = ('新连板段首板为第1日，在10个交易日内同时完成本段三连板及至少5次收盘涨停。'
        '走势首板与资格段首板分别保存；累计涨幅包含走势首板当日。'
        '15%收盘回撤只作转弱提醒，不单独终止走势。')


def _positive(value):
    value = num(value)
    return value if value is not None and value > 0 else None


def daily_evidence(day, bar, limit, *, suspended=False, no_limit=False):
    """Use official daily upper limits; never infer 5/10/20/30% from a name."""
    bar, limit = bar or {}, limit or {}
    close, upper = _positive(bar.get('close')), _positive(limit.get('up_limit'))
    # Older stk_limit archives sometimes contain a stale, locally enriched
    # reference. The same-day daily quote is authoritative for return chaining.
    daily_pre, limit_pre = _positive(bar.get('pre_close')), _positive(limit.get('pre_close'))
    pre = daily_pre or limit_pre
    low, high = _positive(bar.get('low')), _positive(bar.get('high'))
    state, missing = 'missing', []
    if suspended:
        state = 'suspended'
    elif not close or not low or not high or not low <= close <= high:
        missing = ['daily_bar']
    elif no_limit or limit.get('no_limit') is True:
        state = 'unrestricted'
    elif not upper:
        missing = ['official_up_limit']
    elif close > upper + .005:
        # Relisting/unrestricted trading and conflicting limit data must not
        # become ordinary non-limit days that manufacture a new leg boundary.
        missing = ['price_limit_conflict']
    elif num(bar.get('volume')) is None or num(bar.get('volume')) <= 0:
        missing = ['trading_volume']
    else:
        state = 'sealed' if at_limit(close, upper) else 'not_sealed'
    one_price = bool(state == 'sealed' and at_limit(low, upper) and at_limit(high, upper))
    return {'date': day, 'state': state, 'close': close, 'open': _positive(bar.get('open')),
            'low': low, 'high': high, 'pre_close': pre, 'up_limit': upper,
            'volume': num(bar.get('volume')), 'one_price': one_price if state == 'sealed' else None,
            'seal_shape': '一字板' if one_price else '非一字涨停' if state == 'sealed' else None,
            'missing_inputs': missing, 'daily_return': close / pre - 1 if close and pre else None,
            'price_basis': 'daily_pre_close' if daily_pre else 'price_limit_pre_close' if pre else 'missing',
            'reference_disagreement': bool(daily_pre and limit_pre and abs(daily_pre-limit_pre) > .005)}


def evaluate_stock(ticker, sessions, as_of, bars, limits, *, suspensions=(), no_limit_dates=()):
    """Evaluate a verified exchange calendar, optionally containing future dates.

    Future calendar dates can label a deadline. Future OHLC/limits are ignored.
    Missing calendar dates must be rejected by the adapter, not compressed here.
    """
    if sessions != sorted(set(sessions)) or as_of not in sessions:
        raise ValueError('必须提供去重、升序且包含复盘日的交易所日历')
    days = [d for d in sessions if d <= as_of]
    evidence = [daily_evidence(d, bars.get(d), limits.get(d), suspended=d in suspensions,
                               no_limit=d in no_limit_dates) for d in days]
    cycles, legs = [], []
    cycle, run = None, None
    quiet, boundary_known = 0, False
    previous_state = None
    previous_volume = None
    for i, row in enumerate(evidence):
        state, day = row['state'], row['date']
        row['volume_ratio'] = row['volume'] / previous_volume if row['volume'] is not None and previous_volume and previous_volume > 0 else None
        if row['volume'] is not None and row['volume'] > 0:
            previous_volume = row['volume']

        # Move prices to the cycle's first-board-preclose scale using each
        # session's official reference. No unadjusted close fallback.
        current_index, low_index = None, None
        if cycle and cycle['price_complete']:
            if state == 'suspended':
                current_index = cycle['close_index']
            elif row['daily_return'] is not None and row['low']:
                current_index = cycle['close_index'] * (1 + row['daily_return'])
                low_index = cycle['close_index'] * row['low'] / row['pre_close']
            else:
                cycle['price_complete'] = False
        quiet = 0 if state in ('sealed', 'missing') else quiet + 1
        reset_reason = None
        if cycle:
            if low_index is not None and cycle['first_low_index'] is not None and low_index < cycle['first_low_index'] - 1e-9:
                reset_reason = '跌破走势首板最低价'
            elif quiet > WINDOW:
                reset_reason = '连续超过10个交易日未涨停'
            if reset_reason:
                if current_index is not None:
                    cycle['close_index'] = current_index
                    if cycle['peak_close_index'] is None or current_index > cycle['peak_close_index']:
                        cycle['peak_close_index'], cycle['peak_date'] = current_index, day
                cycle['series'].append({'date': day, 'cum_return': current_index - 1 if current_index is not None else None,
                    'drawdown': current_index / cycle['peak_close_index'] - 1 if current_index is not None and cycle['peak_close_index'] else None,
                    'close_index': current_index})
                cycle.update(status='ended', ended_at=day, end_reason=reset_reason)
                cycle['end_evidence'] = deepcopy(row)
                for leg in legs:
                    if leg['cycle_id'] == cycle['cycle_id']:
                        leg['cycle_ended_at'] = day
                boundary_known = True
                cycle = None
                run = None
        elif quiet > WINDOW:
            boundary_known = True

        if state == 'sealed' and cycle is None:
            cycle = {'cycle_id': f'{ticker}:{day}', 'start_date': day,
                     'boundary_confirmed': boundary_known, 'status': 'active',
                     'ended_at': None, 'end_reason': None, 'price_complete': row['daily_return'] is not None,
                     'first_pre_close': row['pre_close'],
                     'first_low_index': row['low'] / row['pre_close'] if row['pre_close'] else None,
                     'close_index': 1.0, 'peak_close_index': None, 'peak_date': None,
                     'latest_limit_date': day, 'max_streak': 0, 'leg_ids': [], 'missing_dates': [], 'series': []}
            cycle['price_missing_dates'] = []
            cycles.append(cycle)
            current_index = 1 + row['daily_return'] if row['daily_return'] is not None else None
            boundary_known = False
        if cycle:
            if state == 'missing':
                cycle['missing_dates'].append(day)
            if state != 'suspended' and row['daily_return'] is None:
                cycle['price_missing_dates'].append(day)
            if current_index is not None:
                cycle['close_index'] = current_index
                if cycle['peak_close_index'] is None or current_index > cycle['peak_close_index']:
                    cycle['peak_close_index'], cycle['peak_date'] = current_index, day
            if state == 'sealed':
                cycle['latest_limit_date'] = day
            row['cycle_id'] = cycle['cycle_id']
            drawdown = current_index / cycle['peak_close_index'] - 1 if current_index is not None and cycle['peak_close_index'] else None
            cycle['series'].append({'date': day, 'cum_return': current_index - 1 if current_index is not None else None,
                                    'drawdown': drawdown, 'close_index': current_index})
        else:
            row['cycle_id'] = None

        if state == 'sealed':
            if run is None or previous_state != 'sealed':
                confirmed = previous_state in ('not_sealed', 'unrestricted')
                end_index = i + WINDOW - 1
                run = {'leg_id': f'{ticker}:{day}:leg', 'cycle_id': cycle['cycle_id'],
                       'start_date': day, 'start_index': i, 'boundary_confirmed': confirmed,
                       'window_end': sessions[end_index] if end_index < len(sessions) else None,
                       'window_end_index': end_index, 'initial_streak': 0, 'three_board_date': None,
                       'qualified_at': None, 'board_count': 0, 'max_streak': 0,
                       'missing_dates': [], 'evidence': [], 'status': 'pending'}
                legs.append(run)
                cycle['leg_ids'].append(run['leg_id'])
            run['initial_streak'] += 1
            if run['initial_streak'] == MIN_STREAK:
                run['three_board_date'] = day
            cycle['max_streak'] = max(cycle['max_streak'], run['initial_streak'])
        else:
            run = None

        for leg in legs:
            if i > leg['window_end_index'] or leg.get('cycle_ended_at'):
                continue
            leg['evidence'].append(deepcopy(row))
            if state == 'sealed':
                leg['board_count'] += 1
            if state == 'missing':
                leg['missing_dates'].append(day)
            streak = 0
            for observation in reversed(leg['evidence']):
                if observation['state'] != 'sealed':
                    break
                streak += 1
            leg['max_streak'] = max(leg['max_streak'], streak)
            if (leg['qualified_at'] is None and leg['boundary_confirmed'] and not leg['missing_dates']
                    and leg['three_board_date'] and leg['board_count'] >= MIN_BOARDS):
                leg['qualified_at'] = day
                leg['qualification_sessions'] = i - leg['start_index'] + 1
                leg['qualification_board_count'] = leg['board_count']
        previous_state = state
        # An unavailable interval cannot certify the start of a later cycle.
        if state == 'missing' and cycle is None:
            boundary_known = False

    for leg in legs:
        elapsed = len(days) - leg['start_index']
        leg['observed_sessions'] = min(elapsed, WINDOW)
        leg['remaining_sessions'] = max(0, WINDOW - elapsed)
        leg['initial_streak_in_window'] = min(leg['initial_streak'], WINDOW)
        reasons = []
        if leg['qualified_at']:
            leg['status'] = 'qualified'
        elif not leg['boundary_confirmed'] or leg['missing_dates']:
            leg['status'] = 'unverified'
            if not leg['boundary_confirmed']:
                reasons.append('新连板段首板边界未闭合')
            if leg['missing_dates']:
                reasons.append('资格窗口有缺失或冲突的逐日证据')
        elif leg.get('cycle_ended_at') or leg['remaining_sessions'] == 0 or (leg['initial_streak'] < MIN_STREAK and elapsed > leg['initial_streak']):
            leg['status'] = 'failed'
        else:
            leg['status'] = 'pending'
        if not leg['qualified_at']:
            if leg.get('cycle_ended_at'):
                reasons.append('资格形成前走势已结束')
            if not leg['three_board_date'] or leg['three_board_date'] > (leg['window_end'] or as_of):
                reasons.append('本段未完成三连板')
            if leg['board_count'] < MIN_BOARDS:
                reasons.append(f'窗口内涨停{leg["board_count"]}次，未满5次')
            if not leg['remaining_sessions']:
                reasons.append('十日资格窗口已结束')
        leg['reasons'] = reasons
        # Counters describe only facts available by as_of; no future filled days.
        leg.pop('start_index')
        leg.pop('window_end_index')
    for item in cycles:
        tail = item['series'][-1] if item['series'] else {}
        item['observed_return'] = tail.get('cum_return')
        item['cumulative_return'] = tail.get('cum_return') if item['boundary_confirmed'] else None
        item['drawdown'] = tail.get('drawdown')
        item['drawdown_warning'] = item['drawdown'] <= -DRAWDOWN_WARNING + 1e-9 if item['drawdown'] is not None else None
        item['peak_close_on_start_scale'] = item['peak_close_index'] * item['first_pre_close'] if item['price_complete'] and item['peak_close_index'] and item['first_pre_close'] else None
        item['price_complete'] = item['price_complete'] and not item['missing_dates']
        item['qualified_leg_ids'] = [l['leg_id'] for l in legs if l['cycle_id'] == item['cycle_id'] and l['qualified_at']]
    current = cycle
    current_legs = [l for l in legs if current and l['cycle_id'] == current['cycle_id']]
    certified = [l for l in current_legs if l['qualified_at']]
    pending = [l for l in current_legs if l['status'] == 'pending']
    unverified = [l for l in current_legs if l['status'] == 'unverified']
    focus = (certified[-1] if certified else max(pending, key=lambda l: (bool(l['three_board_date']), l['board_count'], l['start_date']))
             if pending else unverified[-1] if unverified else current_legs[-1] if current_legs else None)
    recent = any(r['state'] == 'sealed' for r in evidence[-WINDOW:])
    status = focus['status'] if focus else 'inactive'
    # A historical certification survives a later data gap, but the gap cannot
    # establish that its price cycle is still active on the selected date.
    if status == 'qualified' and current and (current['missing_dates'] or current['price_missing_dates']):
        status = 'unverified'
    return {'ticker': ticker, 'date': as_of, 'status': status, 'recent_limit_up': recent,
            'current_cycle': current, 'current_leg': current_legs[-1] if current_legs else None,
            'focus_leg': focus,
            'qualified_at': min((l['qualified_at'] for l in certified), default=None),
            'latest_qualified_leg': certified[-1] if certified else None,
            'current_streak': run['initial_streak'] if run else 0,
            'current_streak_boundary_confirmed': run['boundary_confirmed'] if run else True,
            'latest_limit_date': next((r['date'] for r in reversed(evidence) if r['state'] == 'sealed'), None),
            'latest_shape': evidence[-1]['seal_shape'] if evidence else None,
            'coverage': {'sessions': len(days), 'known_states': sum(r['state'] != 'missing' for r in evidence),
                         'missing_dates': [r['date'] for r in evidence if r['state'] == 'missing']},
            'cycles': cycles, 'legs': legs}
