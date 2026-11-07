"""Read-only September theme screening replay.

Run from the project root:
  python -m research.replay_theme_selection --start 2026-09-01 --end 2026-09-22
Use --collect to refresh market inputs; --fetch-taxonomy explicitly permits
provider requests. Default execution needs only the local archived inputs.
"""
import argparse
from collections import Counter
from dataclasses import asdict
from datetime import datetime, timezone
from hashlib import sha256
import csv
import json
from pathlib import Path
from statistics import mean

from shilun.review.strong_stock_candidates import VERSION as CORE_VERSION
from shilun.review.theme_selection import VERSION, SelectionRules, ThemeSelectionReplay
from shilun.review.theme_selection_data import TaxonomyResearchReader, WATCH_TICKERS, collect_market_inputs, read_gzip, write_gzip


def label(group):
    return group['name'] + f'（跟随{len(group["followers"])}只）'


def write_csv(path, rows, fields):
    with path.open('w', encoding='utf-8-sig', newline='') as handle:
        writer = csv.DictWriter(handle, fields, extrasaction='ignore')
        writer.writeheader()
        writer.writerows(rows)


def run(market, taxonomy, output):
    output.mkdir(parents=True, exist_ok=True)
    # Preserve this first-round report as the literal-matching baseline.
    replay = ThemeSelectionReplay(market, taxonomy, attribution_mode='literal')
    baseline = [replay.evaluate(day) for day in market['days']]
    variants = []
    for window, followers, dates in ((3, 2, 2), (3, 3, 2), (3, 2, 3), (5, 2, 2), (5, 3, 2), (5, 3, 3)):
        rules = SelectionRules(diffusion_sessions=window, minimum_followers=followers, minimum_active_dates=dates)
        results = baseline if rules == SelectionRules() else [replay.evaluate(day, rules) for day in market['days']]
        ids = [{g['id'] for g in r['directions']} for r in results]
        transitions = sum(len(today ^ previous) for previous, today in zip(ids, ids[1:]))
        survived, denominator = 0, 0
        for previous, today in zip(ids, ids[1:]):
            survived += len(previous & today)
            denominator += len(previous)
        variants.append({'rules': asdict(rules), 'average_directions': mean(map(len, ids)),
                         'empty_days': sum(not x for x in ids), 'membership_changes': transitions,
                         'next_day_retention': survived / denominator if denominator else None,
                         'partial_days': sum(r['status'] != 'ready' for r in results),
                         'daily': [{'date': r['date'], 'directions': [label(g) for g in r['directions']]} for r in results]})
    core_audit = [{'date': day, 'ticker': ticker, **next((r for r in market['cores'][day] if r['ticker'] == ticker), {'status': 'not_in_candidate_universe'})}
                  for day in market['days'] for ticker in WATCH_TICKERS]
    scopes = Counter(market['pools'].get(day, {}).get('scope', '范围未声明') for day in market['context_days'])
    coverage = []
    for day in market['days']:
        pool = replay.pools.get(day, {})
        reasons = market['reasons'].get(day, {}).get('reasons', {})
        coverage.append({'date': day, 'limit_pool_stocks': len(pool), 'reason_available': sum(bool(reasons.get(t[:6])) for t in pool),
                         'qualified_cores': len(replay.qualified(day)), 'taxonomy_ready_candidates': sum(bool(r.get('membership_complete')) for r in taxonomy['snapshots'].get(day, {}).get('rows', []))})
    manifest = {'version': replay.version, 'core_version': CORE_VERSION, 'generated_at': datetime.now(timezone.utc).isoformat(),
                'start': market['start'], 'end': market['end'], 'market_collected_at': market['collected_at'],
                'input_hashes': {name: sha256((output / name).read_bytes()).hexdigest() for name in ('market_inputs.json.gz', 'taxonomy_inputs.json.gz')},
                'pool_scopes': dict(scopes), 'taxonomy_diagnostics': taxonomy['diagnostics'],
                'coverage': coverage, 'variants': variants, 'sample_cores': core_audit}
    write_gzip(output / 'screening_audit.json.gz', {'manifest': manifest, 'days': baseline})
    (output / 'summary.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf8')
    daily = [{'date': r['date'], 'directions': '；'.join(label(g) for g in r['directions']) or '无符合条件的新入选方向',
              'qualified_core_observations': len(r['individual_observation']), 'status': r['status'], 'diagnostics': '；'.join(r['diagnostics'])} for r in baseline]
    write_csv(output / 'daily_shortlist.csv', daily, list(daily[0]))
    detail = [{'date': r['date'], 'code': c['code'], 'name': c['name'], 'status': c['status'],
               'selected': c.get('selected', False), 'core': c['cores'][0]['name'] if c['cores'] else '',
               'followers': len(c['followers']), 'active_dates': len(c['active_dates']), 'member_count': c['member_count'],
               'unknown_reason_events': len(c['unknown_reasons']), 'reasons': '；'.join(c['reasons'])}
              for r in baseline for c in r['candidates']]
    write_csv(output / 'candidate_reasons.csv', detail, list(detail[0]))
    unmatched = [{'review_date': r['date'], 'code': c['code'], 'topic': c['name'], **e}
                 for r in baseline for c in r['candidates'] for e in c['membership_only_events']]
    write_csv(output / 'unmatched_reason_events.csv', unmatched,
              ['review_date', 'code', 'topic', 'date', 'ticker', 'name', 'boards', 'reason', 'reason_source', 'reason_fetched_at'])
    lines = ['# 核心反推题材：9月逐日筛选回放', '',
        f'区间：{market["start"]} 至 {market["end"]}，{len(baseline)} 个交易日。规则：`{replay.version}`。', '',
        '## 回放结论', '',
        '**当前结果仅用于发现筛选缺陷，尚未通过主线识别验收。** 基线的原因字面匹配存在漏认：例如零售类原因并不一定写“新零售”，种业或粮食储备不一定写“粮食概念”。全部未匹配记录单独导出，不能据此认定这些方向没有扩散。', '',
        '这轮先保留3日/2只/2日作为待校准基线；3—5日、2—3只、至少2日作为对照范围。修复归因后须用同一输入重新比较，当前参数表不能确定医药退潮、零售崛起或最佳阈值。', '',
        '## 这次交付的范围', '',
        '这是盘后“新方向入选”研究：按每日已形成的核心资格，筛选归因明确的独立跟随和跨日扩散，输出最多三个方向及全部落选理由。名单变化不是生产观察名单的自动退出；保留、分歧、退潮和替换状态机在下一步接入。', '',
        '核心硬条件保持三连板＋该新段首板起十个交易日内至少五次收盘涨停。不能将后来获得的资格提前使用。15%收盘回撤仅保留预警。', '',
        '基线：近3个交易日至少2只非核心股票涨停，并覆盖至少2个交易日。分类和成分统一使用东方财富日期记录；涨停原因只作归因证据。只匹配完整原因标签（允许去掉“概念”后缀），不猜测行业近义词或催化。', '',
        baseline[0]['ranking_note'], '',
        '同一主核心合并为“主要发酵方向 · 核心”，保留原始分类ID与分支，股票和日期去重。昨日跟随队列先冻结，再看今日反馈，掉队股票不删除。', '',
        '## 每日入选结果', '', '| 日期 | 新入选研究方向 | 核心个股观察数 | 数据诊断 |', '|---|---|---:|---|']
    lines += [f'| {r["date"]} | {r["directions"]} | {r["qualified_core_observations"]} | {r["diagnostics"] or "关键输入完整"} |' for r in daily]
    lines += ['', '## 扩散参数对照', '',
              '这里只比较名单数量和稳定性；延长窗口天然提高留存，不代表预测收益更好。核心资格条件固定，不参与调参。', '',
              '| 窗口/最少跟随/最少活跃日 | 日均方向 | 无方向天数 | 方向进出次数 | 次日留存 |', '|---|---:|---:|---:|---:|']
    for v in variants:
        rule = v['rules']
        retention = f'{v["next_day_retention"]:.1%}' if v['next_day_retention'] is not None else '未知'
        lines.append(f'| {rule["diffusion_sessions"]}/{rule["minimum_followers"]}/{rule["minimum_active_dates"]} | {v["average_directions"]:.2f} | {v["empty_days"]} | {v["membership_changes"]} | {retention} |')
    lines += ['', '## 医药、粮食、零售专项核对', '',
              '以下关键词仅挑出人工核对样本，没有写进入选条件或排序白名单。', '',
              '| 日期 | 题材 | 主核心 | 独立跟随/活跃日 | 结果与原因 |', '|---|---|---|---:|---|']
    for r in baseline:
        for c in r['candidates']:
            if c['name'] in ('创新药', '医疗服务', '粮食概念', '新零售', 'CRO'):
                core = c['cores'][0]['name'] if c['cores'] else '—'
                lines.append(f'| {r["date"]} | {c["name"]} | {core} | {len(c["followers"])}/{len(c["active_dates"])} | {c["status"]}：{"；".join(c["reasons"])} |')
    lines += ['', '## 指定个股的资格与观察', '',
              '国芳、金健、闽东、哈药、百花均作为核对样本；未满足核心认证的样本保留核对记录，不据此强行准入题材。', '',
              '| 日期 | 个股 | 资格 | 认证日 | 最近涨停 | 走势累计涨幅 | 15%回撤预警 |', '|---|---|---|---|---|---:|---|']
    for r in core_audit:
        ret = r.get('cycle', {}).get('cumulative_return')
        value = f'{ret:.2%}' if ret is not None else '未知'
        lines.append(f'| {r["date"]} | {r.get("name", r["ticker"])} | {r["status"]} | {r.get("qualified_at") or "—"} | {r.get("latest_limit_date") or "—"} | {value} | {r.get("cycle", {}).get("drawdown_warning")} |')
    lines += ['', '## 数据覆盖与使用边界', '',
              f'- 分类接口诊断 {len(taxonomy["diagnostics"])} 条；具体代码与日期见 `summary.json`。',
              '- 每个题材必须使用对应交易日的成分。接口返回的周末记录被交易日历过滤，达到5000行上限时拆分日期重新取数。',
              '- 涨停池范围：' + '；'.join(f'{k}（{v}日）' for k, v in scopes.items()) + '。不将沪深池宣称为沪深京全市场完整池。',
              '- ' + market['scope_note'],
              '- 历史成分、行情和原因均在事后采集；这是按日期截断的重建，不是严格当时可得数据回测，也没有验证交易收益。',
              '- 原因是第三方编辑标签，字面未匹配可能漏掉近义催化；不能把缺原因、缺成分或范围外股票计为真实零。题材级跟随数为“已证实归因的下界”，不是题材所有涨停数。',
              '- 基线成员数量不设50家上限；大板块需同样满足核心归因和独立跟随证据。', '',
              '| 日期 | 涨停池股票 | 有原因 | 已认证核心 | 已取完整成分的候选分类 |', '|---|---:|---:|---:|---:|']
    lines += [f'| {c["date"]} | {c["limit_pool_stocks"]} | {c["reason_available"]} | {c["qualified_cores"]} | {c["taxonomy_ready_candidates"]} |' for c in coverage]
    lines += ['', '## 依据与复现', '',
              '- [蟹老板心法](/Users/shibicheng/.agents/skills/xielaoban-xinfa/SKILL.md)：A011:P14（核心带动与跟随晋级）、A424:P04（抱团修复但板块不扩散）、A252:P06（后续接力反馈）。量化门槛为本项目研究规则，非作者公式。',
              '- [Tushare东方财富成分接口文档](https://tushare.pro/document/2?doc_id=363)：日期参数、单次上限。',
              '- `market_inputs.json.gz`、`taxonomy_inputs.json.gz` 为输入；`summary.json` 保存SHA256、参数、采集时间和诊断；`screening_audit.json.gz` 保存全部逐股证据；`candidate_reasons.csv` 可逐项核对落选原因；`unmatched_reason_events.csv` 保存每个未归因的成分涨停事件。', '',
              '```sh', f'./.venv312/bin/python -m research.replay_theme_selection --start {market["start"]} --end {market["end"]} --output-dir {output}', '```', '']
    (output / 'report.md').write_text('\n'.join(lines), encoding='utf8')
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--start', default='2026-09-01')
    parser.add_argument('--end', default='2026-09-22')
    parser.add_argument('--output-dir', type=Path, default=Path('research/evidence/theme_selection_20260923'))
    parser.add_argument('--collect', action='store_true', help='read market inputs from the configured database; no database writes')
    parser.add_argument('--fetch-taxonomy', action='store_true', help='permit dated DC API requests, cached locally')
    args = parser.parse_args()
    output = args.output_dir
    if args.collect:
        from shilun.common.config import load_config
        from shilun.common.db import MongoSnapshotStore
        config = load_config()
        store = MongoSnapshotStore(config.mongo_uri, config.mongo_db, socket_timeout_ms=60000, initialize_indexes=False)
        try:
            market = collect_market_inputs(store, args.start, args.end)
            write_gzip(output / 'market_inputs.json.gz', market)
        finally:
            store.close()
    else:
        market = read_gzip(output / 'market_inputs.json.gz')
    if (market['start'], market['end'], market['core_version']) != (args.start, args.end, CORE_VERSION):
        raise ValueError('归档区间或核心规则版本不匹配；请使用独立目录重新采集')
    if args.fetch_taxonomy:
        from shilun.data.providers.tushare_provider import TushareConfig, TushareDailyClient
        taxonomy = TaxonomyResearchReader(output / 'provider_cache', TushareDailyClient(TushareConfig.from_env())).collect(market)
        write_gzip(output / 'taxonomy_inputs.json.gz', taxonomy)
    else:
        taxonomy = read_gzip(output / 'taxonomy_inputs.json.gz')
    run(market, taxonomy, output)
    print(f'Replay report: {output / "report.md"}')


if __name__ == '__main__':
    main()
