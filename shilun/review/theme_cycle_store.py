"""Atomic, immutable short-term cycle archive shared by all page consumers."""
from copy import deepcopy
from datetime import datetime, timezone
from functools import lru_cache
from hashlib import sha256
import json
from pathlib import Path
import re
import fcntl

from shilun.review.theme_cycles import ThemeCycleReplay, VERSION
from shilun.review.theme_rules import rule_config
from shilun.review.theme_selection import ThemeSelectionReplay
from shilun.review.theme_selection_data import read_gzip, write_gzip
from shilun.review.reason_attribution import VERSION as ATTRIBUTION_VERSION
from shilun.review.strong_stock_candidates import VERSION as CORE_VERSION
from shilun.review.event_theme_taxonomy import VERSION as EVENT_SCHEMA, SCOPE_NOTE, collect_event_taxonomy

ROOT = Path(__file__).resolve().parents[2] / 'runtime' / 'theme_cycles'


def atomic_json(path, data):
    path = Path(path)
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf8')
    temp.replace(path)


def manifest(root=None):
    result = json.loads((Path(root or ROOT)/'manifest.json').read_text(encoding='utf8'))
    if (not re.fullmatch('[a-f0-9]{16}', str(result.get('revision', '')))
            or result.get('version') != VERSION or result.get('rules') != rule_config()
            or result.get('topic_schema') != EVENT_SCHEMA):
        raise ValueError('周期归档规则已过期，请重新生成')
    return result


@lru_cache(maxsize=12)
def _read(root, revision, day, digest):
    path = Path(root)/revision/f'{day}.json.gz'
    if sha256(path.read_bytes()).hexdigest() != digest:
        raise ValueError('周期归档校验失败')
    result = read_gzip(path)
    if result.get('date') != day or result.get('revision') != revision or result.get('rules') != rule_config():
        raise ValueError('周期日期或规则版本不一致')
    return result


def day_payload(day, *, revision=None, root=None):
    from shilun.review import theme_distribution
    if root is None and theme_distribution.enabled():
        return theme_distribution.read('cycle', day, revision=revision)
    root = Path(root or ROOT)
    try:
        job = json.loads((root/'job.json').read_text(encoding='utf8'))
    except (OSError, ValueError):
        job = {'status': 'unknown'}
    try:
        info = manifest(root)
    except FileNotFoundError:
        return {'date': day, 'status': 'not_materialized', 'diagnostic_status': 'task_incomplete',
                'active': [], 'archived': [], 'available_dates': [], 'job': job, 'message': '短线周期尚未生成，请先完成收盘周期归档。'}
    if revision and revision != info['revision']:
        raise ValueError('周期归档已更新，请重新读取')
    day = day or info['dates'][-1]
    if day not in info['dates']:
        return {'date': day, 'status': 'not_materialized', 'diagnostic_status': 'task_incomplete',
                'active': [], 'archived': [], 'available_dates': info['dates'], 'revision': info['revision'],
                'job': job,
                'message': f'所选日短线周期尚未生成；最新归档为 {info["dates"][-1]}。未用其他日期代替。'}
    return {**deepcopy(_read(str(root), info['revision'], day, info['payload_hashes'][day])), 'job': job}


def build(source, output=None):
    """A failed run leaves the complete previous revision available. No writes to DB."""
    source, output = Path(source), Path(output or ROOT)
    output.mkdir(parents=True, exist_ok=True)
    with (output/'build.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        now = datetime.now(timezone.utc).isoformat()
        atomic_json(output/'job.json', {'status': 'running', 'started_at': now})
        try:
            market = read_gzip(source/'market_inputs.json.gz')
            taxonomy = read_gzip(source/'taxonomy_inputs.json.gz')
            if taxonomy.get('topic_schema') != EVENT_SCHEMA:
                taxonomy = collect_event_taxonomy(market)
            if market.get('core_version') != CORE_VERSION:
                raise ValueError('核心资格归档版本过期，需先重新采集')
            hashes = {f: sha256((source/f).read_bytes()).hexdigest() for f in ('market_inputs.json.gz', 'taxonomy_inputs.json.gz')}
            benchmark_path = source/'benchmark_inputs.json.gz'
            if benchmark_path.exists():
                market['benchmark'] = read_gzip(benchmark_path)
                hashes[benchmark_path.name] = sha256(benchmark_path.read_bytes()).hexdigest()
            # Include the implementation digest: an unchanged version string must
            # not disguise a changed evaluator in a cached revision.
            code_files = ('theme_cycles.py', 'theme_rules.py', 'theme_selection.py', 'reason_attribution.py',
                          'event_theme_taxonomy.py', 'vibe_shortline.py',
                          'theme_observation.py', 'theme_families.py', 'theme_core_tracking.py')
            implementation = {f: sha256(Path(__file__).with_name(f).read_bytes()).hexdigest() for f in code_files}
            revision = sha256(json.dumps([hashes, implementation, rule_config(), now], sort_keys=True).encode()).hexdigest()[:16]
            target = output/revision
            engine = ThemeCycleReplay(ThemeSelectionReplay(market, taxonomy))
            days = market['days']
            if not days or days != sorted(set(days)):
                raise ValueError('输出交易日无效')
            context = market['context_days']
            if any(d not in context for d in days):
                raise ValueError('输出日期缺少回放输入')
            prior_quality = {}
            try:
                previous = json.loads((output/'manifest.json').read_text())
                if (previous['warmup_start'] != context[0] or days[0] > previous['dates'][0]
                        or days[-1] < previous['dates'][-1]):
                    raise ValueError('同一归档须保持预热起点并覆盖已有日期；新基线请使用独立输出目录')
                prior_quality = {r['key']: r for r in read_gzip(output/previous['revision']/'quality.json.gz')}
            except FileNotFoundError:
                pass
            issues, summaries = {}, []
            for day in context:
                result = engine.step(day)
                if day not in days:
                    continue
                for issue in result['diagnostics']:
                    issue['last_checked_at'] = now
                    previous_issue = prior_quality.get(issue['key'], {})
                    issues[issue['key']] = {**issue, 'status': 'open', 'first_seen_at': previous_issue.get('first_seen_at', now),
                                            'checks': previous_issue.get('checks', 0)+1, 'resolution': None}
                result.update(revision=revision, generated_at=now, available_dates=days,
                              warmup_start=context[0], versions={'cycle': VERSION, 'core': CORE_VERSION, 'attribution': ATTRIBUTION_VERSION})
                write_gzip(target/f'{day}.json.gz', result)
                summaries.append({'date': day, 'status': result['status'],
                                  'active': [{'id': c['id'], 'name': c['name'], 'core': c['core']['name'], 'stage': c['stage_label']} for c in result['active']],
                                  'exited': [c['id'] for c in result['archived'] if c['exit_date'] == day], 'diagnostic_count': len(result['diagnostics'])})
            for key, issue in prior_quality.items():
                if key not in issues:
                    issues[key] = ({**issue, 'status': 'resolved', 'last_checked_at': now,
                                    'resolution': '同日输入按当前规则重新计算，原问题已不再出现', 'resolved_at': now}
                                   if issue['date'] in days and issue['status'] == 'open' else issue)
            write_gzip(target/'quality.json.gz', list(issues.values()))
            result = {'revision': revision, 'version': VERSION, 'rules': rule_config(), 'generated_at': now,
                      'topic_schema': taxonomy.get('topic_schema', 'legacy-dc'),
                      'dates': days, 'warmup_start': context[0], 'days': summaries, 'input_hashes': hashes,
                      'implementation_hashes': implementation, 'scope_note': market.get('scope_note'),
                      'quality_counts': {s: sum(r['status'] == s for r in issues.values()) for s in ('open', 'resolved')},
                      'limitations': ([SCOPE_NOTE] if taxonomy.get('topic_schema') == EVENT_SCHEMA else []) + ['历史分类、原因与价格事后采集重建，并非严格时点回测，未验证收益。',
                          '回放从预热期开始；此前已形成但不满足本次准入的题材不会被强行补入。',
                          '候选来自截至当日已归因的事件标签及已认证核心。' if taxonomy.get('topic_schema') == EVENT_SCHEMA else '归因候选范围以已认证核心相关的已归档完整成分为限；不是全市场每个概念。'],
                      'payload_hashes': {d: sha256((target/f'{d}.json.gz').read_bytes()).hexdigest() for d in days}}
            atomic_json(output/'manifest.json', result)
            atomic_json(output/'job.json', {'status': 'complete', 'revision': revision, 'started_at': now,
                                           'finished_at': datetime.now(timezone.utc).isoformat()})
            return result
        except Exception as error:
            atomic_json(output/'job.json', {'status': 'failed', 'started_at': now, 'error_type': type(error).__name__,
                                           'message': '周期生成未完成，上一完整版本保留；检查输入归档后重跑。'})
            raise
