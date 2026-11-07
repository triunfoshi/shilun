"""Build immutable local acceptance bundles from the archived September inputs."""
import argparse
from collections import Counter
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path

from shilun.review.reason_attribution import VERSION as ATTRIBUTION_VERSION, match_reason, rule_records
from shilun.review.theme_selection import VERSION, SelectionRules, ThemeSelectionReplay
from shilun.review.theme_selection_data import read_gzip, write_gzip, WATCH_TICKERS
from shilun.review.theme_observation import VERSION as OBSERVATION_VERSION, member_observation
from shilun.review.theme_families import VERSION as FAMILY_VERSION, visible_topics
from shilun.review.event_theme_taxonomy import VERSION as EVENT_SCHEMA, SCOPE_NOTE, collect_event_taxonomy, event_attribution


def compact_candidate(row):
    return {k: row.get(k) for k in ('code', 'name', 'source_topics', 'member_count', 'status', 'selected', 'reason_codes', 'reasons',
            'cores', 'followers', 'active_dates', 'feedback', 'events', 'unknown_reasons', 'membership_only_events', 'diffusion_evaluated')}


def build(source, output, *, revision=None):
    market = read_gzip(source / 'market_inputs.json.gz')
    taxonomy = read_gzip(source / 'taxonomy_inputs.json.gz')
    if taxonomy.get('topic_schema') != EVENT_SCHEMA:
        taxonomy = collect_event_taxonomy(market)
    before = ThemeSelectionReplay(market, taxonomy, attribution_mode='literal')
    after = ThemeSelectionReplay(market, taxonomy)
    hashes = {f: sha256((source / f).read_bytes()).hexdigest() for f in ('market_inputs.json.gz', 'taxonomy_inputs.json.gz')}
    generated = datetime.now(timezone.utc).isoformat()
    revision = revision or sha256(json.dumps([hashes, rule_records(), VERSION, OBSERVATION_VERSION, generated], sort_keys=True).encode()).hexdigest()[:16]
    target = output / revision
    days = []
    changed = []
    for day in market['days']:
        old, new = before.evaluate(day), after.evaluate(day)
        old_candidates = {r['code']: r for r in old['candidates']}
        candidates = []
        for row in new['candidates']:
            if row['member_count'] is None:
                continue  # The research input is scoped to qualified-core candidates.
            previous = old_candidates[row['code']]
            result = {**compact_candidate(row), 'before': compact_candidate(previous)}
            result['observation'] = member_observation(after, day, row)
            # Compare identity/counts rather than enriched evidence fields.
            result['changed'] = (row['status'], bool(row.get('selected')), row['followers'], [r['ticker'] for r in row['cores']]) != (
                previous['status'], bool(previous.get('selected')), previous['followers'], [r['ticker'] for r in previous['cores']])
            candidates.append(result)
        events = []
        for event_day in after.window(day, SelectionRules().diffusion_sessions):
            pool = after.pools.get(event_day, {})
            reason_doc = market['reasons'].get(event_day, {})
            for ticker, stock in pool.items():
                reason = reason_doc.get('reasons', {}).get(ticker[:6], '')
                eligible_members = [r for r in visible_topics(after.catalogs.get(event_day, {})).values()
                                    if ticker in after.members.get((event_day, r['code']), set())]
                attributions = []
                for topic in eligible_members:
                    if topic.get('source_topics'):
                        evidence = after.events(topic['code'], event_day)
                        event = next(e for bucket in ('events', 'unknown', 'membership_only')
                                     for e in evidence[bucket] if e['ticker'] == ticker)
                        attribution = {**event['attribution'], 'source_attributions': event['source_attributions']}
                    else:
                        attribution = event_attribution(topic, reason, event_day) if after.event_themes else match_reason(topic, reason, event_day)
                    old_hit = any(e['ticker'] == ticker for e in before.events(topic['code'], event_day)['events'])
                    attributions.append({'code': topic['code'], 'name': topic['name'], **attribution, 'before_matched': old_hit,
                                         'membership_date': topic['membership_date'], 'member_count': topic['member_count']})
                cores = after.qualified(day)
                at_event = next((r for r in market['cores'].get(event_day, []) if r['ticker'] == ticker), {})
                events.append({'id': f'{event_day}:{ticker}', 'date': event_day, 'ticker': ticker, 'name': stock.get('name') or market['stocks'][ticker]['name'],
                    'boards': stock.get('boards'), 'reason': reason, 'source': reason_doc.get('source') or 'ths_dated_limit_reasons',
                    'fetched_at': reason_doc.get('fetched_at'),
                    'role': f'{cores[ticker]["qualified_at"]}认证核心' if ticker in cores else '复盘日未认证核心',
                    'qualification_date': cores.get(ticker, {}).get('qualified_at'), 'event_core_status': at_event.get('status', 'not_qualified'),
                    'attributions': attributions, 'scope': market['pools'].get(event_day, {}).get('scope')})
        samples = [r for r in market['cores'][day] if r['ticker'] in WATCH_TICKERS]
        summary = {'date': day, 'before': [g['name'] for g in old['directions']], 'after': [g['name'] for g in new['directions']],
                   'changed_candidates': sum(c['changed'] for c in candidates), 'diagnostics': new['diagnostics'],
                   'qualified_cores': len(after.qualified(day)),
                   'pending_events': sum(any(a['status'] in ('ambiguous', 'missing') for a in e['attributions']) for e in events),
                   'event_count': len(events)}
        bundle = {'revision': revision, 'date': day, 'status': new['status'], 'summary': summary,
                  'topic_schema': EVENT_SCHEMA,
                  'directions': new['directions'], 'candidates': candidates, 'events': events, 'samples': samples,
                  'diagnostics': new['diagnostics'], 'ranking_note': new['ranking_note'],
                  'pool_scope': market['pools'].get(day, {}).get('scope'), 'rules': new['rules'],
                  'versions': {'selection': VERSION, 'attribution': ATTRIBUTION_VERSION, 'observation': OBSERVATION_VERSION,
                               'family': 'none-event-labels', 'topic_schema': EVENT_SCHEMA, 'core': market['core_version']}}
        write_gzip(target / f'{day}.json.gz', bundle)
        days.append(summary)
        for c in candidates:
            if c['changed']:
                changed.append({'date': day, 'name': c['name'], 'before': len(c['before']['followers']), 'after': len(c['followers']),
                                'status': c['status'], 'selected': bool(c.get('selected')), 'reasons': c['reasons']})
        print(f'acceptance {day}: {len(new["directions"])} directions; {summary["changed_candidates"]} candidates changed', flush=True)
    manifest = {'revision': revision, 'generated_at': generated, 'default_date': market['days'][-1],
                'topic_schema': EVENT_SCHEMA,
                'dates': market['days'], 'days': days, 'versions': bundle['versions'], 'mapping_rules': [],
                'input_hashes': hashes, 'scope_note': market['scope_note'], 'rules': bundle['rules'],
                'stage_note': '归因与新方向筛选验收；跨日延续、退出及退出后核心跟踪见共用短线题材周期。',
                'limitations': [SCOPE_NOTE, '仅围绕已认证核心相关事件进行周期准入核验，不是所有事件标签都会形成交易方向。',
                    '历史记录和映射事后整理，属于复盘重建，不是当时可得数据回测。',
                    '相关业务映射不是名称同义，也不证明真实催化；多条明示原因分别保留。',
                    '缺原因、归因歧义及未命中分别显示；待核验不等于真实零。',
                    '成员观察独立于涨停归因；归因未确认仍保留观察，同日与核心上涨只作旁证，不自动计入扩散。'],
                'payload_hashes': {d: sha256((target / f'{d}.json.gz').read_bytes()).hexdigest() for d in market['days']}}
    temp = output / 'manifest.json.tmp'
    temp.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf8')
    temp.replace(output / 'manifest.json')
    report = ['# 涨停原因归因修正：验收对照', '', f'生成时间：{generated}；版本：{ATTRIBUTION_VERSION}。', '',
              '页面：研究 → R2 板块研究 → PART2 板块动向 → 归因与筛选依据（验收）。P2 短线情绪提供同一入口。', '',
              '## 每日方向对照', '', '| 日期 | 字面匹配初稿 | 归因修正后 |', '|---|---|---|']
    report += [f'| {r["date"]} | {"；".join(r["before"]) or "无"} | {"；".join(r["after"]) or "无"} |' for r in days]
    report += ['', '## 规则表', '', '| 统一题材 / DC代码 | 关系 | 允许词组 | 业务边界 |', '|---|---|---|---|']
    report += [f'| {r["name"]} / {r["code"]} | {r["relation_label"]} | {"、".join(r["phrases"])} | {r["explanation"]} |' for r in rule_records()]
    report += ['', '## 有变化的题材', '', '| 日期 | 题材 | 跟随修正前 → 后 | 新结果 | 原因 |', '|---|---|---:|---|---|']
    report += [f'| {r["date"]} | {r["name"]} | {r["before"]} → {r["after"]} | {"入选" if r["selected"] else r["status"]} | {"；".join(r["reasons"])} |' for r in changed]
    report += ['', '## 边界', '', manifest['stage_note'], '', *['- ' + s for s in manifest['limitations']], '',
              '核心三连板及本段十日五板条件、15%回撤预警、3日/2只/2日的扩散基线均保持不变。多方向事件按股票及日期去重，同主核心合并。', '']
    (source / 'attribution_comparison.md').write_text('\n'.join(report), encoding='utf8')
    return manifest


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=Path('research/evidence/theme_selection_20260923'))
    parser.add_argument('--output', type=Path, default=Path('runtime/theme_acceptance'))
    args = parser.parse_args()
    build(args.source, args.output)
