"""Compare lifecycle stability on archived inputs, without optimizing returns."""
import argparse
from dataclasses import asdict
import json
from pathlib import Path
from shilun.review.theme_selection_data import read_gzip
from shilun.review.theme_selection import ThemeSelectionReplay
from shilun.review.theme_cycles import ThemeCycleReplay
from shilun.review.theme_rules import CycleRules
from shilun.review.theme_cycle_store import build


def run(source, output):
    m = read_gzip(source/'market_inputs.json.gz');t = read_gzip(source/'taxonomy_inputs.json.gz')
    if (source/'benchmark_inputs.json.gz').exists():
        m['benchmark'] = read_gzip(source/'benchmark_inputs.json.gz')
    comparisons = []
    for confirmations in (2, 3, 4):
        rules = CycleRules(weakness_sessions=confirmations)
        engine = ThemeCycleReplay(ThemeSelectionReplay(m, t), rules)
        days = [r for r in engine.run(m['context_days']) if r['date'] in m['days']]
        comparisons.append({'rules': asdict(rules), 'days_with_gaps': sum(bool(r['diagnostics']) for r in days),
            'exits': len(days[-1]['archived']), 'last_active': [(c['name'],c['core']['name']) for c in days[-1]['active']],
            'daily': [{'date': r['date'], 'active': [{'id':c['id'],'name':c['name'],'core':c['core']['name'],
                        'stage':c['stage_label']} for c in r['active']],
                      'exits': [{'name':c['name'],'core':c['core']['name'],'reason':c['exit_reason']} for c in r['archived'] if c['exit_date']==r['date']],
                      'waiting':r['waiting']} for r in days]})
    info = build(source, output)
    report = {'revision': info['revision'], 'variants': comparisons, 'input_hashes': info['input_hashes']}
    (source/'cycle_replay.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))
    base = comparisons[1]
    lines = ['# 短线题材跨日周期：逐日验收', '', f'归档 {info["revision"]}；观察日 {m["start"]} 至 {m["end"]}；预热 {m["context_days"][0]} 起。', '',
        '## 当前结论', '', '状态机、固定队列与页面共用归档已实现；阶段阈值属于待验收的研究规则，不能当作已经验证的赚钱主线。', '',
        '第一轮仅以当日涨跌确认连续走弱，9月整段没有退出，暴露了名额长期被旧方向占用的问题。改为固定队列近三日累计反馈后再逐日确认；没有按股票或题材写例外。', '',
        '题材退出不等于股票开启新连板段。重启检查当前段的独立资格、退出后至少两日重新准入和当日承接改善；不能因仍是同一个有效连板段而永久禁止重启。', '',
        '## 基线规则', '', '- 核心三连板与十日五板、3日/2只/2日扩散、最多3方向均复用统一配置。',
        '- 新零售与零售概念合并为零售主线，保留原始DC标签；入选时冻结前排核心候选与非核心跟随，涨停事件按股票和日期去重。',
        '- 退出后继续对照原核心、原前后排与同期上证；超额为累计涨幅之差（百分点），核心走强不自动改写板块退潮。',
        '- 核心及固定后排近3日累计收益均为负，且固定后排当天无涨停接力，连续3次确认后退出；达到15%收盘回撤且连续2次同弱也退出。',
        '- 15%只用走势起点至观察日的复权收盘链；预警单独不退出。单日分歧保留；连续2日有核心、后排及接力修复才回流，随后继续改善可加强。',
        '- 正常旧方向优先保留；替换需旧方向连续2次走弱、新方向连续2日准入且当日接力成立。新题材等待不等于不强。',
        '- 缺数据或源冲突清空连续确认计数，暂停当前阶段，显示上一确认阶段及日期；修复后按同一起点全段重算。', '',
        '## 每日结果', '', '| 日期 | 当前观察 | 当日退出 |', '|---|---|---|']
    lines += [f'| {r["date"]} | '+ '；'.join(f'{c["name"]}·{c["core"]}（{c["stage"]}）' for c in r['active'])+' | '+ '；'.join(c['name']+'·'+c['core'] for c in r['exits'])+' |' for r in base['daily']]
    lines += ['', '## 退出确认次数对照', '', '| 连续确认次数 | 已退出周期数 | 最后一天当前方向 |', '|---|---:|---|']
    lines += [f'| {r["rules"]["weakness_sessions"]} | {r["exits"]} | '+ '；'.join(n+'·'+c for n,c in r['last_active'])+' |' for r in comparisons]
    lines += ['', '这张对照只比较名单稳定性，未比较收益；基线取3次。15%回撤与2次同弱的预警分支在三组中保持一致，因此结果相同也不代表参数不敏感。', '',
        '## 待人工复核的差异', '', '- 零售主线9月17日退潮的历史记录保留；随后国芳走强进入独立的退出后核心跟踪。9月21日至22日重新满足新方向准入，重入仍须满足承接及名额条件；展开候选等待可看具体原因。',
        '- 本次预热窗口内未形成已入选医药周期，所以不能宣称已经复现“9月初医药退潮”。需要向前扩展输入，使用新基线目录回放后再评估；不能把未入选伪造成退出。',
        '- 粮食在9月15日退出的是金健和当时固定跟随组合。后续金健再次上涨、其他成员走强分别保留在资格和成员观察中，不能自动视作原后排全部修复。', '',
        '## 数据边界', '', *['- '+x for x in info['limitations']], '',
        '- 板池实际范围按输入来源，尚不宣称完整沪深京；开板反馈由日线触板且收盘未封识别，不提供推测的开板次数。',
        '- 页面当前可验收至9月22日；之后日期明确显示尚未生成。生产同步与ECS调度接入按后续部署步骤处理，本步骤没有新增定时任务。', '',
        '## 复现', '', '```sh', './.venv312/bin/python -m research.replay_theme_cycles --source research/evidence/theme_selection_20260923', '```', '']
    (source/'cycle_replay.md').write_text('\n'.join(lines), encoding='utf8')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--output', type=Path, default=Path('runtime/theme_cycles'))
    a = parser.parse_args();print(run(a.source, a.output)['revision'])
