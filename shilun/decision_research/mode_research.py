"""Dated mode evidence, independently generated and content-addressed."""
from datetime import datetime, timezone
import hashlib
import json

from .engine import (Evidence, BAND_NAMES, market_evidence, number)
from .cohorts import reconstruct_cores
from .assessment import citic_evidence, market_outlook, first_board_next_day_check
from .playbook_assessment import build_assessments, conclusion_from_assessments
from .first_board_sectors import build_sector_feedback

VERSION = 'mode-research-v5'


def digest(payload, default=None):
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True,
        separators=(',', ':'), allow_nan=False, default=default).encode()).hexdigest()


def source_fingerprint(data):
    # Acquisition time is metadata; identical cached inputs reuse one snapshot.
    # Match the audit export for Mongo datetime / BSON metadata as well.
    return digest({k: v for k, v in data.items() if k != 'loaded_at'}, default=str)


def snapshot_id(research):
    return digest({k: v for k, v in research.items() if k not in ('snapshot_id', 'built_at')})


def validate(research, data=None):
    if research.get('version') not in ('mode-research-v1', 'mode-research-v2', 'mode-research-v3', 'mode-research-v4', VERSION) or research.get('snapshot_id') != snapshot_id(research):
        raise ValueError('模式评估版本或内容校验失败，请重新生成评估')
    if data is not None and research.get('version') != VERSION:
        raise ValueError('预案需要当前独立玩法评估版本')
    if data is not None and (research.get('analysis_date') != data['date'] or
            research.get('source_fingerprint') != source_fingerprint(data)):
        raise ValueError('模式评估与预案的日期或输入数据不一致')


def reference(research):
    return {k: research[k] for k in ('analysis_date', 'version', 'snapshot_id', 'built_at')}


def build_mode_research(data):
    e = Evidence(data)
    events, _ = e.events()
    cores = reconstruct_cores(data, e)
    market = market_evidence(e, data)
    market['citic'] = citic_evidence(data)
    market['outlook'] = market_outlook(market)
    amount = market['turnover'].get('market_amount_cny')
    default_band = 'lt50' if number(amount) and amount < 2.1e12 else 'lt100' if number(amount) and amount <= 2.3e12 else 'all'
    assessments = build_assessments(e, cores, default_band)
    sectors = build_sector_feedback(e, data, assessments['windows']['5']['trend']['daily'])
    route, conclusion = conclusion_from_assessments(assessments, market)
    next_day_first_boards = first_board_next_day_check(events, e.date, data.get('next_date'), default_band)
    result = {'version': VERSION, 'analysis_date': e.date, 'next_date': data.get('next_date'),
        'built_at': datetime.now(timezone.utc).isoformat(), 'source_fingerprint': source_fingerprint(data),
        'market': market, 'first_board_sectors': sectors, 'route': route, 'conclusion': conclusion, 'assessments': assessments,
        'default_band': default_band, 'next_day_first_boards': next_day_first_boards,
        'crowding_observations': cores['current'], 'core_method': cores['method'], 'band_names': {**BAND_NAMES, 'lt100': '100亿以下（合并）'},
        'provenance': {'scope': data.get('scope'),
            'note': '日期成分为重建；历史核心按当时可见数据逐日重建，不是当时真实保存的交易名单。收益为行情统计，非策略回测。'},
        'method': {'cohorts': '首板：前一日首板；接力：昨日非一字的2板以上梯队；核心：昨日独立资格名单。次日失败不删除。',
            'health': '换手使用daily_basic.turnover_rate，成交量为手；自身前10日完整窗口作对照，不设跨股票健康阈值',
            'feedback': conclusion['recent_rule'],
            'enabled_strategies': ['ma5_v0_2']}}

    result['snapshot_id'] = snapshot_id(result)
    return result
