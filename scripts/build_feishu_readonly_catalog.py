"""Build the bot's audited query catalog from the live Python route schema.

No HTTP route is executed. New GETs require an explicit source audit; other
unreviewed methods are denied. Run from the repository root after route changes.
"""
from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import re
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
OUTPUT = ROOT / 'shilun/config/feishu_readonly_catalog.json'
AUDITS = tuple(ROOT / 'docs/evidence' / name for name in (
    'feishu_readonly_review_market_audit.json', 'feishu_readonly_trade_audit.json',
    'feishu_readonly_operations_audit.json'))
METHODS = {'GET', 'POST', 'PUT', 'PATCH', 'DELETE', 'HEAD', 'OPTIONS'}
CANDIDATE_STRATEGIES = {
    'ma5_v0_2': 'MA5趋势',
    'red_k_net_inflow': '红K净流入',
    'structure_repair_520': '结构修复520',
    'weekly_breakout': '周K突破套利 / 周K突破战法',
    'oversold_first_board@0.1.0': '超跌首板点火',
}
CANDIDATE_STRATEGY_NOTE = (
    '选品池必须显式指定strategy_id，不沿用页面默认MA5。中文战法映射：'
    + '；'.join(f'{label}={identifier}' for identifier, label in CANDIDATE_STRATEGIES.items())
    + '。周K突破、周K突破套利、周K突破战法均使用weekly_breakout，不使用wk_breakout或中文显示名作为参数。'
      '选品池战法包含复盘战法，不等同于执行策略目录。用户未说明战法时先确认；'
      '归档未就绪与真实零候选必须根据响应status区分。'
)
MODULES = {
    'daily_review': ('每日复盘', '所选交易日归档复盘、判断与证据。'),
    'market_state': ('指数、量能与市场情绪', '大盘权限、指数量能广度、市场情绪及海外行情。'),
    'first_board': ('首板研究', '首板名单、竞价封单及新面孔观察。'),
    'board_effects': ('打板效应与连板反馈', '首板、最高板、全体涨停、炸板的历史收益与连板晋级。'),
    'strength': ('强势股与核心资格', '区间强势股排名、核心认证、龙虎榜及分时证据。'),
    'event_themes': ('事件题材与行业分类', '题材事件树、轮动、原因证据、板块成分和分钟资金流。'),
    'sentiment_history': ('情绪历史', '短线市场情绪历史曲线及阶段比较。'),
    'theme_cycles': ('题材周期', '活跃与归档题材周期、分歧修复及确定版本证据。'),
    'theme_selection': ('题材归因评估', '题材选取与归因验收归档及逐日证据。'),
    'trade_decision': ('交易预案与模式研究', '冻结预案、次日跟踪、模式研究与可用日期。'),
    'journal_ledger': ('交易日志主账', '交割成交、库存轮、分红税费、版本历史及生产成交核对。'),
    'journal_reviews': ('交易过程复盘与待办', '交易轮过程复盘、历史版本、上下文及复盘任务缺口。'),
    'journal_research': ('交易研究与规则定义', '术语规则、模式卡、假设、观察、冻结计划与研究快照。'),
    'journal_allocations': ('数量分配与收益统计', '数量归属、分配历史、模式收益统计及分组成员。'),
    'journal_imports': ('交割单来源与导入预览', '原始数据来源、批次、文件检查及不落库的导入预览。'),
    'journal_portfolio': ('组合风险与组合快照', '组合现金、风险预算、情景预览及已冻结组合快照。'),
    'holdings': ('持仓管理', '持仓列表、详情、止损预览及日检事件历史。'),
    'execution': ('执行工作台与交易记录', '委托、成交、驾驶舱、风险队列、场景、回放及数据就绪。'),
    'strategies': ('策略目录与止损规则', '策略版本、规则目录及只读止损目标预览。'),
    'market_sectors': ('板块动向与龙头榜', '已归档板块趋势、资金结构和每日龙头榜。'),
    'candidate_pool': ('策略候选池', '各策略的所选日期候选池和筛选依据。'),
    'market_intraday': ('盘中行情与候选监控', '只读归档报价、分钟走势与候选状态，不触发行情刷新。'),
    'stock_analysis': ('个股检索与事实分析', '股票名称代码检索、日线技术分析及只读归档个股面板。'),
    'operations': ('数据状态与系统渠道', '数据覆盖、预计算任务状态、数据源健康及消息渠道状态。'),
}
TITLES = {
    '/api/v1/review/dashboard': '读取指定交易日复盘归档与判断',
    '/api/v1/review/sentiment-context': '指数情绪周期与行业结构',
    '/api/v1/review/market-facts': '已归档指数量能、广度和涨跌分布',
    '/api/v1/review/overseas': '海外指数、美股科技与港股行情',
    '/api/v1/review/live-emotion': '当前涨停、炸板、跌停与连板情绪',
    '/api/v1/review/strength': '区间强势股排名',
    '/api/v1/review/strength/detail': '强势股区间日线、涨停原因与龙虎榜',
    '/api/v1/review/strength/minutes': '强势股指定日期归档分钟走势',
    '/api/v1/review/strong-stocks': '强势核心资格认证名单',
    '/api/v1/review/strong-stocks/detail': '核心资格认证明细',
    '/api/v1/review/taxonomy': '统一行业与概念分类目录',
    '/api/v1/review/topic-detail': '归档题材成员与证据详情',
    '/api/v1/review/board-dynamics': '连板晋级与昨日涨停次日反馈',
    '/api/v1/review/first-board': '首板名单、竞价封单和连板天梯',
    '/api/v1/review/new-faces': '换手新面孔观察名单',
    '/api/v1/review/topic-flow': '题材板块归档分钟资金流',
    '/api/v1/review/board-effects': '打板效应：首板、最高板及全体涨停期望收益统计',
    '/api/v1/review/shortline/themes': '同日事件题材树、活跃题材与轮动证据',
    '/api/v1/review/shortline/history': '短线情绪历史与每日反馈',
    '/api/v1/review/theme-selection': '题材归因评估可用归档目录',
    '/api/v1/review/theme-selection/day': '指定日期题材归因评估证据',
    '/api/v1/review/theme-cycles': '活跃及归档题材周期',
    '/api/v1/review/theme-cycles/detail': '同日同版本题材周期详情',
    '/api/v1/tushare/health': '数据源健康与只读连通性探针',
    '/api/v1/data/status': '指定日期数据覆盖与流水线状态',
    '/api/v1/data/precompute-sectors/status/{job_id}': '板块预计算任务状态',
    '/api/v1/market/permission': '已归档大盘权限、指数结构与关键位',
    '/api/v1/market/sectors': '已归档板块动向与资金结构',
    '/api/v1/market/candidates': '指定策略和日期的候选池',
    '/api/v1/market/leaders': '已归档每日龙头榜',
    '/api/v1/market/intraday': '只读盘中归档报价、候选状态与分钟走势',
    '/api/v1/stocks/search': '按名称或代码检索股票',
    '/api/v1/stock/panel': '个股只读事实面板、归档分时及策略叠加',
    '/api/v1/analyze': '指定股票日期的Mongo日线技术分析',
    '/api/v1/telegram/analyze': '指定股票日期的Telegram格式分析预览',
}


def module_for(path: str) -> str:
    if path.startswith('/api/v1/trade-journal'):
        part = path.removeprefix('/api/v1/trade-journal')
        if any(piece in part for piece in ('/sources', '/batches', '/imports', '/import-files')):
            return 'journal_imports'
        if '/portfolio-' in part:
            return 'journal_portfolio'
        if '/allocations' in part or '/analytics' in part:
            return 'journal_allocations'
        if '/review' in part or part.startswith('/tasks') or '/market-checks' in part or '/rule-comparison' in part:
            return 'journal_reviews'
        if any(piece in part for piece in ('/definitions', '/observations', '/snapshots', '/plans', '/mode-cards', '/hypotheses', '/decisions')) or part.endswith('/context'):
            return 'journal_research'
        return 'journal_ledger'
    if path.startswith('/api/v1/trade-decision'):
        return 'trade_decision'
    if path.startswith('/api/v1/holdings'):
        return 'holdings'
    if path.startswith('/api/v1/trading'):
        return 'execution'
    if path.startswith('/api/v1/strategies'):
        return 'strategies'
    if path.startswith('/api/v1/review'):
        part = path.removeprefix('/api/v1/review')
        if part.startswith('/theme-selection'):
            return 'theme_selection'
        if part.startswith('/theme-cycles'):
            return 'theme_cycles'
        if part.startswith(('/strength', '/strong-stocks')):
            return 'strength'
        if part == '/shortline/history':
            return 'sentiment_history'
        if part.startswith(('/shortline', '/topic-', '/taxonomy')):
            return 'event_themes'
        if part.startswith(('/first-board', '/new-faces', '/research')):
            return 'first_board'
        if part.startswith(('/board-effects', '/board-dynamics')):
            return 'board_effects'
        if part.startswith(('/market-facts', '/live-emotion', '/sentiment-context', '/overseas', '/futures')):
            return 'market_state'
        return 'daily_review'
    if path.startswith('/api/v1/market/'):
        return {'permission': 'market_state', 'sectors': 'market_sectors', 'leaders': 'market_sectors',
                'candidates': 'candidate_pool', 'intraday': 'market_intraday'}.get(path.rsplit('/', 1)[-1], 'operations')
    if path.startswith(('/api/v1/stocks/', '/api/v1/stock/', '/api/v1/analyze', '/api/v1/telegram/analyze')):
        return 'stock_analysis'
    return 'operations'


def resolve_schema(value: Any, document: dict, trail: tuple[str, ...] = ()) -> Any:
    if isinstance(value, list):
        return [resolve_schema(item, document, trail) for item in value]
    if not isinstance(value, dict):
        return deepcopy(value)
    if any(key in value for key in ('$dynamicRef', '$recursiveRef')):
        raise ValueError('Unsupported schema reference')
    if '$ref' in value:
        reference = value['$ref']
        if not isinstance(reference, str) or not reference.startswith('#/components/schemas/') or reference in trail:
            raise ValueError('External or cyclic schema reference')
        result: Any = document
        try:
            for name in reference[2:].split('/'):
                result = result[name.replace('~1', '/').replace('~0', '~')]
        except (KeyError, TypeError):
            raise ValueError('Missing schema reference') from None
        resolved = resolve_schema(result, document, (*trail, reference))
        return {**resolved, **resolve_schema({k: v for k, v in value.items() if k != '$ref'}, document, trail)}
    return {key: resolve_schema(item, document, trail) for key, item in value.items()}


def _date_schema(schema: dict, name: str) -> dict:
    result = deepcopy(schema)
    if name in {'date', 'analysis_date', 'start_date', 'end_date', 'date_from', 'date_to', 'start', 'end', 'trade_date'}:
        if result.get('type') == 'string' and result.get('format') not in {'date-time', 'time'}:
            if result.get('minLength') != 8 and r'\d{8}' not in result.get('pattern', ''):
                result.setdefault('format', 'date')
        if 'anyOf' in result:
            result['anyOf'] = [_date_schema(item, name) for item in result['anyOf']]
    return result


def build_catalog(document: dict, metadata: dict, audits: list[dict]) -> dict:
    decisions = {}
    for row in audits:
        key = (row['method'].upper(), row['path'])
        if key in decisions and decisions[key]['read_only'] != row['read_only']:
            raise ValueError('Conflicting audit decisions')
        decisions[key] = row
    operations = [(method.upper(), path, operation) for path, methods in document['paths'].items()
                  if path.startswith('/api/v1/') for method, operation in methods.items()
                  if method.upper() in METHODS]
    unknown = [(method, path) for method, path, _ in operations if method == 'GET' and (method, path) not in decisions]
    if unknown:
        raise ValueError('Unreviewed GET operations: ' + ', '.join(path for _, path in unknown))
    allowed_names = Counter(metadata.get((method, path), {}).get('name', operation.get('operationId', 'query'))
                            for method, path, operation in operations if decisions.get((method, path), {}).get('read_only') is True)
    queries, denied = [], []
    for method, path, operation in operations:
        audit = decisions.get((method, path), {})
        module = module_for(path)
        meta = metadata.get((method, path), {})
        name = meta.get('name') or operation.get('operationId') or 'query'
        if audit.get('read_only') is not True or method not in {'GET', 'POST'}:
            denied.append({'method': method, 'path': path, 'endpoint': name, 'module': module,
                           'reason': audit.get('unsafe_reason') or audit.get('reason') or '未经只读审计的写操作，不向机器人开放。'})
            continue
        query_id = name if allowed_names[name] == 1 else name+'_'+sha256(f'{method} {path}'.encode()).hexdigest()[:8]
        parameters = []
        for parameter in operation.get('parameters', []):
            if parameter.get('in') not in {'path', 'query'}:
                raise ValueError('Unsupported parameter location')
            schema = _date_schema(resolve_schema(parameter.get('schema', {}), document), parameter['name'])
            parameters.append({'name': parameter['name'], 'in': parameter['in'],
                               'required': parameter.get('required', False), 'schema': schema,
                               'description': parameter.get('description', '')})
        if path == '/api/v1/market/candidates':
            strategy = next(parameter for parameter in parameters if parameter['name'] == 'strategy_id')
            strategy['required'] = True
            strategy['schema'].pop('default', None)
            strategy['schema'].update(enum=list(CANDIDATE_STRATEGIES),
                                      description=CANDIDATE_STRATEGY_NOTE, examples=['weekly_breakout'])
            strategy['description'] = CANDIDATE_STRATEGY_NOTE
        fixed = deepcopy(audit.get('fixed_params') or audit.get('enforced_parameters') or {})
        for parameter in parameters:
            if parameter['name'] in {'force', 'refresh', 'allow_tushare_fallback'}:
                fixed[parameter['name']] = False
        names = {parameter['name'] for parameter in parameters}
        if set(fixed)-names:
            raise ValueError('Audit fixed parameter absent from route')
        if 'read_only' in names and fixed.get('read_only') is not True:
            raise ValueError('Read-only compatibility flag must be explicitly enforced')
        for parameter in parameters:
            if parameter['name'] in fixed:
                parameter['schema'].update(const=deepcopy(fixed[parameter['name']]), default=deepcopy(fixed[parameter['name']]))
        parameter_schema = {'type': 'object', 'properties': {p['name']: deepcopy(p['schema']) for p in parameters},
                            'additionalProperties': False}
        required = [p['name'] for p in parameters if p['required']]
        if required:
            parameter_schema['required'] = required
        body = operation.get('requestBody')
        body_schema = None
        if body is not None:
            content = body.get('content', {}).get('application/json')
            if not content:
                raise ValueError('Unsupported body content type')
            body_schema = resolve_schema(content['schema'], document)
        summary = audit.get('query_title') or TITLES.get(path) or MODULES[module][0]+'查询'
        description = '\n'.join(value for value in (operation.get('description', ''), audit.get('reason', ''), MODULES[module][1]) if value)
        if path == '/api/v1/market/candidates':
            description += '\n'+CANDIDATE_STRATEGY_NOTE
        queries.append({'id': query_id, 'module': module, 'module_name': MODULES[module][0], 'summary': summary,
                        'description': description, 'method': method, 'path': path, 'read_only': True,
                        'endpoint': name, 'source_module': meta.get('source_module', ''),
                        'parameters': parameters, 'parameter_schema': parameter_schema,
                        'body_schema': body_schema, 'fixed_parameters': fixed})
    queries.sort(key=lambda item: (item['module'], item['id']))
    denied.sort(key=lambda item: (item['path'], item['method']))
    counts = Counter(query['module'] for query in queries)
    modules = [{'id': key, 'name': name, 'description': description, 'query_count': counts[key]}
               for key, (name, description) in MODULES.items() if counts[key]]
    return {'version': 1, 'source': 'FastAPI OpenAPI plus explicit static route audits',
            'audit_sources': [str(path.relative_to(ROOT)) for path in AUDITS],
            'modules': modules, 'queries': queries, 'denied': denied,
            'coverage': {'all_operations': len(operations), 'classified_operations': len(queries)+len(denied),
                         'get_operations': sum(method == 'GET' for method, _, _ in operations),
                         'read_only_get': sum(q['method'] == 'GET' for q in queries),
                         'read_only_post': sum(q['method'] == 'POST' for q in queries),
                         'query_count': len(queries), 'denied_operations': len(denied),
                         'module_count': len(modules), 'unclassified': []}}


def load_openapi() -> tuple[dict, dict]:
    # Importing this application's global analysis service ordinarily creates a
    # Mongo store. Suppress that constructor only in this offline build process.
    from unittest.mock import patch
    from shilun.services import MongoFirstAnalysisService
    with patch.object(MongoFirstAnalysisService, '_build_mongo_store', return_value=None):
        from shilun.api import app
    metadata = {(method, route.path): {'name': route.endpoint.__name__, 'source_module': route.endpoint.__module__}
                for route in app.routes if hasattr(route, 'endpoint') for method in (getattr(route, 'methods', None) or [])}
    return app.openapi(), metadata


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=OUTPUT)
    args = parser.parse_args()
    document, metadata = load_openapi()
    audits = [row for path in AUDITS for row in json.loads(path.read_text())['routes']]
    catalog = build_catalog(document, metadata, audits)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(catalog, ensure_ascii=False, indent=2, allow_nan=False)+'\n')
    print(json.dumps({'output': str(args.output), 'coverage': catalog['coverage']}, ensure_ascii=False))


if __name__ == '__main__':
    main()
