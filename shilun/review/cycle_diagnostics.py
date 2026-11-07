"""Explain missing cycle evidence without disguising it as market uncertainty."""
NAMES = {'current_limit_pool': '当日涨停池', 'previous_limit_pool': '上一交易日涨停池', 'broken_count': '当日开板次数'}


def require_cycle_inputs(cycle, missing):
    if not missing:
        return cycle
    return {**cycle, 'code': 'pending', 'label': '周期数据待补齐', 'diagnostic_status': 'data_missing',
            'missing_inputs': sorted(set(missing)),
            'reason': '缺少' + '、'.join(NAMES.get(k, k) for k in missing) + '，不能将缺失当作零。',
            'next_action': '补齐所选日及上一交易日板池后重算。', 'rule_version': 'sector-cycle-diagnostics-v1'}


def context_cycle(meta, sector):
    if not meta.get('industry_code'):
        return {'code': 'pending', 'label': '分类关联待补齐',
                'diagnostic_status': meta.get('classification_status') or 'stock_unmapped',
                'reason': '未取得该股在所选日唯一的东方财富三级行业 ID。',
                'next_action': '检查统一分类同步与行业成分冲突。'}
    if not sector or not sector.get('cycle'):
        return {'code': 'pending', 'label': '周期计算待完成', 'diagnostic_status': 'calculation_missing',
                'sector_code': meta['industry_code'], 'reason': '分类已关联，该行业的所选日周期结果尚未生成。',
                'next_action': '生成所选日复盘并检查行业行情覆盖。'}
    return sector['cycle']
