"""Dated, resumable theme materialization invoked by the existing close pipeline."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import argparse
import json
from pathlib import Path
import threading
import uuid

from pymongo.errors import DuplicateKeyError

from shilun.common.config import load_config
from shilun.common.db import MongoSnapshotStore
from shilun.review.theme_selection_data import collect_market_inputs, TaxonomyResearchReader, read_gzip, write_gzip
from shilun.review.theme_cycle_store import atomic_json, build as build_cycles
from shilun.review.theme_acceptance_builder import build as build_acceptance
from shilun.review.theme_distribution import CONTROL, publish
from shilun.review.event_theme_taxonomy import collect_event_taxonomy, VERSION as EVENT_SCHEMA

ROOT = Path('runtime/theme_daily')
STAGES = {'collect_market': '读取行情、涨停池与核心资格', 'collect_taxonomy': '按同日涨停原因整理事件题材',
          'validate_inputs': '核对数据日期与覆盖', 'build_cycles': '计算跨日题材周期',
          'build_attribution': '生成同版归因明细', 'publish': '发布并核验云端归档', 'complete': '已完成'}


class ThemeInputError(ValueError):
    def __init__(self, missing):
        self.missing = missing
        super().__init__('题材输入未齐全')


def extend_market(previous, fresh, refresh_from):
    """Keep prior dated evidence; append new sessions without moving the baseline."""
    if (previous['start'] != fresh['start'] or previous['context_days'][0] != fresh['context_days'][0]
            or previous['core_version'] != fresh['core_version']):
        raise ThemeInputError([{'dataset': 'baseline', 'reason': '预热起点不一致'}])
    result = deepcopy(fresh)
    for key in ('pools', 'cores'):
        result[key].update({d: deepcopy(v) for d, v in previous[key].items() if d < refresh_from})
    # Same-day historical reason repairs must reach the next replay. Previously
    # the frozen input checkpoint overwrote a collector's repaired reason with
    # its old empty document forever. Preserve successful old entries only when
    # fresh collection failed, returned a wrong date, or omitted that stock.
    for day, old in previous.get('reasons', {}).items():
        if day >= refresh_from:
            continue
        new = fresh.get('reasons', {}).get(day, {})
        valid = (new.get('date') == day and isinstance(new.get('reasons'), dict)
                 and any(str(value or '').strip() for value in new['reasons'].values()))
        if not valid:
            result.setdefault('reasons', {})[day] = deepcopy(old)
            continue
        retained = {code: value for code, value in old.get('reasons', {}).items()
                    if old.get('date') == day and str(value or '').strip()}
        repaired = {code: value for code, value in new['reasons'].items() if str(value or '').strip()}
        result.setdefault('reasons', {})[day] = {**deepcopy(new), 'reasons': {**retained, **repaired},
            'repair_merge': {'date': day, 'fresh_source': new.get('source'),
                             'retained_source': old.get('source'),
                             'retained_codes': sorted(set(retained)-set(repaired))}}
    for key in ('bars', 'limits'):
        for ticker, values in previous[key].items():
            result[key].setdefault(ticker, {}).update({d: deepcopy(v) for d, v in values.items() if d < refresh_from})
    for ticker, days in previous.get('suspensions', {}).items():
        result['suspensions'][ticker] = sorted(set(d for d in days if d < refresh_from)
            | {d for d in fresh.get('suspensions', {}).get(ticker, ()) if d >= refresh_from})
    result['stocks'] = {**previous['stocks'], **fresh['stocks']}
    old_benchmark = previous.get('benchmark', {}).get('bars', {})
    result['benchmark']['bars'].update({d: deepcopy(v) for d, v in old_benchmark.items() if d < refresh_from})
    return result


def validate_inputs(market, taxonomy, days):
    missing = []
    for day in days:
        pool = market.get('pools', {}).get(day, {})
        if not pool.get('complete') or pool.get('snapshot_mode') == 'live' or pool.get('date') != day:
            missing.append({'date': day, 'dataset': 'settled_limit_pool', 'reason': '当日收盘涨停池未完整归档'})
        if day not in market.get('cores', {}):
            missing.append({'date': day, 'dataset': 'cores', 'reason': '当日核心资格未生成'})
        if not market.get('benchmark', {}).get('bars', {}).get(day, {}).get('close'):
            missing.append({'date': day, 'dataset': 'benchmark', 'reason': '缺少同日上证收盘价'})
        reasons = market.get('reasons', {}).get(day, {})
        if pool.get('stocks') and (reasons.get('date') != day or not reasons.get('reasons')):
            missing.append({'date': day, 'dataset': 'limit_reasons', 'reason': '当日涨停原因尚未归档'})
        snapshot = taxonomy.get('snapshots', {}).get(day, {})
        event_themes = taxonomy.get('topic_schema') == EVENT_SCHEMA
        if event_themes:
            # Known event labels can be published with explicit attribution gaps;
            # absent candidates or generic-only reasons are not a missing catalog.
            if snapshot.get('date') != day or snapshot.get('topic_schema') != EVENT_SCHEMA:
                missing.append({'date': day, 'dataset': 'event_themes', 'reason': '同日事件标签未生成或口径不一致'})
        elif not snapshot.get('rows') or not snapshot.get('core_mapping_complete') or snapshot.get('date') != day:
            missing.append({'date': day, 'dataset': 'taxonomy', 'reason': '当日分类或核心所属题材未完整核验'})
        for row in snapshot.get('rows', []):
            if row.get('has_qualified_core') and (not row.get('membership_complete') or row.get('membership_date') != day):
                missing.append({'date': day, 'dataset': 'members', 'code': row['code'], 'reason': '候选题材缺少同日完整成分'})
    if missing:
        raise ThemeInputError(missing)


def run(store, day, *, root=None, collector=None, reader=None, publisher=None):
    root = Path(root or ROOT)
    root.mkdir(parents=True, exist_ok=True)
    now, owner = datetime.now(timezone.utc), uuid.uuid4().hex
    leases = store.collection('job_leases')
    try:
        claim = leases.update_one({'_id': 'theme_daily_pipeline', 'expires_at': {'$lte': now}},
            {'$set': {'owner': owner, 'expires_at': now + timedelta(hours=2)}}, upsert=True)
    except DuplicateKeyError as error:
        from shilun.jobs.data_pipeline_job import PipelineBusy
        raise PipelineBusy('另一题材任务正在运行') from error
    if not (claim.modified_count or claim.upserted_id):
        from shilun.jobs.data_pipeline_job import PipelineBusy
        raise PipelineBusy('另一题材任务正在运行')
    state = {'date': day, 'status': 'running', 'stage': 'collect_market', 'started_at': now.isoformat(),
             'retry_command': f'python -m shilun.jobs.theme_daily_job --date {day}'}

    def save(**values):
        state.update(values, updated_at=datetime.now(timezone.utc).isoformat())
        state['stage_label'] = STAGES[state['stage']]
        store.collection(CONTROL).replace_one({'_id': 'job'}, {'_id': 'job', **state}, upsert=True)
        store.upsert_sync_state({'dataset': 'theme_daily', 'scope': 'latest', **state})
        print(f'theme_daily date={day} stage={state["stage"]} status={state["status"]}', flush=True)

    stop = threading.Event()
    def heartbeat():
        while not stop.wait(30):
            try:
                leases.update_one({'_id': 'theme_daily_pipeline', 'owner': owner},
                    {'$set': {'expires_at': datetime.now(timezone.utc) + timedelta(hours=2)}})
            except Exception:
                continue
    pulse = threading.Thread(target=heartbeat, daemon=True)
    pulse.start()
    try:
        save()
        current = store.collection(CONTROL).find_one({'_id': 'current'})
        if current and day < current['date']:
            raise ThemeInputError([{'dataset': 'date', 'reason': '目标日早于已发布日期，不覆盖更新结果'}])
        checkpoint = root/'inputs.json'
        source = root/json.loads(checkpoint.read_text())['source'] if checkpoint.exists() else root/'seed'
        if not (source/'market_inputs.json.gz').exists() or not (source/'taxonomy_inputs.json.gz').exists():
            raise ThemeInputError([{'dataset': 'baseline', 'reason': '缺少已验收的历史基线输入'}])
        previous, taxonomy = read_gzip(source/'market_inputs.json.gz'), read_gzip(source/'taxonomy_inputs.json.gz')
        if (source/'benchmark_inputs.json.gz').exists():
            previous['benchmark'] = read_gzip(source/'benchmark_inputs.json.gz')
        fresh = (collector or collect_market_inputs)(store, previous['start'], day)
        days = [d for d in fresh['context_days'] if d > previous['end'] or d == day]
        if not days or days[-1] != day or not set(previous['days']).issubset(fresh['days']):
            raise ThemeInputError([{'dataset': 'calendar', 'reason': '目标日或历史交易日覆盖不完整'}])
        market = extend_market(previous, fresh, days[0])
        save(stage='collect_taxonomy')
        if reader is None:
            # Rebuild the stable warmup prefix with the new schema. Never mix
            # prior DC snapshots with newly dated event labels in one revision.
            taxonomy = collect_event_taxonomy(market)
        else:
            # Explicit legacy reader is retained for offline migration research.
            retain = {r['code'] for snapshot in taxonomy['snapshots'].values() for r in snapshot['rows'] if r.get('membership_complete')}
            delta = reader.collect({**market, 'context_days': days}, retain_codes=retain)
            taxonomy['snapshots'].update(delta['snapshots'])
            taxonomy['diagnostics'] = delta['diagnostics']
        save(stage='validate_inputs')
        validate_inputs(market, taxonomy, days)
        staged = root/'inputs'/day/EVENT_SCHEMA if taxonomy.get('topic_schema') == EVENT_SCHEMA else root/'inputs'/day
        write_gzip(staged/'market_inputs.json.gz', market)
        write_gzip(staged/'taxonomy_inputs.json.gz', taxonomy)
        save(stage='build_cycles')
        cycle_root, acceptance_root = root/'cycles', root/'acceptance'
        cycle = build_cycles(staged, cycle_root)
        save(stage='build_attribution')
        build_acceptance(staged, acceptance_root, revision=cycle['revision'])
        save(stage='publish')
        result = (publisher or publish)(store, {'cycle': cycle_root, 'acceptance': acceptance_root})
        atomic_json(checkpoint, {'source': str(staged.relative_to(root)), 'date': day, 'revision': result['revision']})
        target = read_gzip(cycle_root/cycle['revision']/f'{day}.json.gz')
        save(stage='complete', status='complete', revision=result['revision'],
             quality_status=target['status'],
             diagnostic_count=len(target.get('diagnostics', [])), finished_at=datetime.now(timezone.utc).isoformat())
        return dict(state)
    except Exception as error:
        save(status='failed', error_type=type(error).__name__, missing=getattr(error, 'missing', []),
             message=f'题材更新停在“{STAGES[state["stage"]]}”。上一完整版本保留；补齐所列数据后可重跑当天题材任务。')
        raise
    finally:
        stop.set()
        pulse.join(timeout=2)
        leases.delete_one({'_id': 'theme_daily_pipeline', 'owner': owner})


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--date', required=True, type=lambda value: datetime.strptime(value, '%Y-%m-%d').date().isoformat())
    parser.add_argument('--root', type=Path, default=ROOT)
    args = parser.parse_args(argv)
    config = load_config()
    store = MongoSnapshotStore(config.mongo_uri, config.mongo_db, socket_timeout_ms=60_000, initialize_indexes=False)
    try:
        run(store, args.date, root=args.root)
        return 0
    except Exception as error:
        print(f'theme_daily failed type={type(error).__name__}', flush=True)
        return 1
    finally:
        store.close()


if __name__ == '__main__':
    raise SystemExit(main())
