"""Reconcile frozen 23 September research and the 24 September plan.

This is a retrospective cache audit, not an execution backtest or proof that
the reconstructed data was available before the intended trading session.
Uses standard-library arithmetic, independently of the production evaluator.
"""
import hashlib
import json
from math import isclose, floor
from pathlib import Path
from statistics import median, mean

ROOT = Path(__file__).resolve().parents[1]
DAY = '2026-09-23'
FOLDER = ROOT / 'runtime/trade_decision' / DAY
OUT = ROOT / 'research/evidence/decision_chain_20260924'


def same(a, b):
    assert (a is None and b is None) or (
        a is not None and b is not None and isclose(a, b, abs_tol=1e-8)), (a, b)


def stats(rows, field, rate=False):
    vals = [r[field] for r in rows if r.get(field) is not None]
    return {'value': (mean(vals)*100 if rate else median(vals)) if vals else None,
            'n': len(vals), 'total': len(rows)}


def reconcile_metric(actual, expected):
    same(actual['value'], expected['value'])
    assert actual['n'] == expected['n'] and actual['total'] == expected['total']


def quartiles(rows, actual):
    assert len({(r['ticker'], r['feedback_date']) for r in rows}) == len(rows)
    vals = sorted(r['opening'] for r in rows if r.get('opening') is not None)
    assert actual['n'] == len(vals) and actual['total'] == len(rows)
    for key, q in [('p25', .25), ('p50', .5), ('p75', .75)]:
        expected = None
        if len(vals) >= 3:
            x = (len(vals)-1)*q
            i = floor(x)
            expected = vals[i] + (vals[min(i+1, len(vals)-1)]-vals[i])*(x-i)
        same(actual[key], expected)


def audit():
    r = json.loads((FOLDER / 'mode-research.json').read_text())
    content = {k: v for k, v in r.items() if k not in ('snapshot_id', 'built_at')}
    calculated = hashlib.sha256(json.dumps(content, ensure_ascii=False,
        sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()
    assert calculated == r['snapshot_id']
    assert r['analysis_date'] == DAY and r['next_date'] == '2026-09-24'
    checked_pairs, checked_metrics, checked_slices = set(), 0, 0
    mode_summary = []
    for window, modes in r['assessments']['windows'].items():
        for key, m in modes.items():
            days = m['daily']
            n = int(window)
            assert len(days) == n+10 and len({d['date'] for d in days}) == len(days)
            assert days == sorted(days, key=lambda d: d['date'])
            for d in days:
                assert d['formation_date'] < d['date'] <= DAY
                assert len({x['ticker'] for x in d['members']}) == d['cohort_n']
                for row in d['members'] + d.get('failed_members', []) + d.get('locked_members', []):
                    p = row.get('prices')
                    if not p:
                        assert row.get('opening') is None and row.get('close_return') is None
                        continue
                    assert p['feedback_date'] == d['date'] and p['formation_date'] == d['formation_date']
                    opening = (p['open']/p['previous_close']-1)*100
                    close = (p['close']/p['previous_close']-1)*100
                    same(opening, row['opening']); same(close, row['close_return'])
                    assert row['red'] == int(p['close'] > p['open'])
                    assert row['open_positive'] == int(opening > 0)
                    assert row['close_positive'] == int(close > 0)
                    checked_pairs.add((row['ticker'], d['date']))
                    first = row.get('first_board_evidence')
                    if first:
                        assert first['date'] == d['formation_date'] and first['prior_date'] < first['date']
                        assert first['prior_streak'] == 0
                        assert abs(first['close'] - first['up_limit']) < .005
                for metric, field, rate in [('primary', 'opening' if key == 'trend' else 'close_return', False),
                        ('red', 'red', True), ('breadth', 'open_positive' if key == 'trend' else 'close_positive', True)]:
                    reconcile_metric(d['metrics'][metric], stats(d['members'], field, rate))
                    checked_metrics += 1
            assert m['latest'] == days[-1]
            for dim in m['dimensions']:
                for period, subset, required in [('recent', days[-n:], n), ('baseline', days[:-n], 8)]:
                    metrics = [d['metrics'][dim['key']] for d in subset]
                    good = [a for a in metrics if a['n'] >= 3 and a['n'] >= a['total']*.95]
                    expected = (median([a['value'] for a in good]) if dim['key'] == 'primary'
                        else mean([a['value'] for a in good])) if len(good) >= required else None
                    same(dim[period]['value'], expected)
                    assert dim[period]['days_n'] == len(good)
            if window == '5':
                mode_summary.append({'mode': m['name'], 'status': m['status_label'],
                    'latest_n': m['latest']['cohort_n'], 'latest_median_pct': m['latest']['metrics']['primary']['value'],
                    'recent_median_pct': m['dimensions'][0]['recent']['value'],
                    'baseline_median_pct': m['dimensions'][0]['baseline']['value'],
                    'reasons': m['reasons'], 'risks': m['risks']})
    daily = r['assessments']['windows']['5']['trend']['daily']
    events = [{**row, 'feedback_date': d['date']} for d in daily for row in d['members']]
    sectors = r['first_board_sectors']
    for group in [sectors] + sectors['groups']:
        rows = events if group is sectors else [x for x in events if x['ticker'] in set(group['members'])]
        for n in (3, 5):
            selected = [x for x in rows if x['feedback_date'] in [d['date'] for d in daily[-n:]]]
            quartiles(selected, group['windows'][str(n)]); checked_slices += 1
        for day, actual in group['daily'].items():
            quartiles([x for x in rows if x['feedback_date'] == day], actual); checked_slices += 1
            if 'coordination' in actual:
                c = actual['coordination']
                assert c['date'] == actual['formation_date'] < day <= DAY
                assert c['member_n'] == len(set(group['members']))
                assert c['first_board_n'] == len(c['first_board_members']) or c['first_board_n'] is None
    next_boards = r['next_day_first_boards']
    assert next_boards['formation_date'] == DAY and next_boards['observation_date'] == '2026-09-24'
    assert next_boards['status'] == 'pending'
    assert next_boards['opening_premium_median'] is None and next_boards['positive_open_pct'] is None
    assert len({x['ticker'] for x in next_boards['members']}) == next_boards['cohort_n']
    result = {'status': 'passed', 'analysis_date': DAY, 'target_date': r['next_date'],
        'snapshot_id': r['snapshot_id'], 'price_pairs_checked': len(checked_pairs),
        'daily_metrics_checked': checked_metrics, 'quantile_slices_checked': checked_slices,
        'mode_summary': mode_summary, 'next_day_first_boards_n': next_boards['cohort_n'],
        'limitations': ['价格公式核对基于快照内原始价格，不代表独立行情源验证。',
            '板块归属使用23日成分快照重建历史，不能证明历史当日已知成员。',
            '24日生成的23日盘后预案是回溯验收，不是24日盘前已发布的交易计划。']}
    plan_path = FOLDER / 'plan.json'
    if plan_path.exists():
        p = json.loads(plan_path.read_text())
        ref = p['mode_research_ref']
        assert ref['snapshot_id'] == r['snapshot_id']
        for key in ('analysis_date', 'next_date', 'market', 'route', 'conclusion', 'default_band', 'next_day_first_boards'):
            assert p[key] == r[key], key
        assert len([d for d in p['directions'] if d['tier'] == 'main']) <= 3
        assert len([d for d in p['directions'] if d['tier'] == 'backup']) <= 2
        assert len(p['steps']) == 7
        result['plan'] = {'version': p['version'], 'built_at': p['built_at'],
            'route': p['route'], 'directions': [{'name': d['name'], 'tier': d['tier']} for d in p['directions']],
            'research_ref_exact': True, 'seven_steps': True}
    else:
        result['plan'] = {'status': 'not_ready'}
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / 'audit.json').write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps({k:v for k,v in result.items() if k not in ('mode_summary', 'limitations')}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    audit()
