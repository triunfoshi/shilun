"""Canonical P2 market mood, also used by board-effect cohort grouping."""
from typing import Any

MOOD_COLLECTION = 'review_market_mood'
MOOD_RULE_VERSION = 'mood_v1.2_moderated_downside'
READY_CODES = frozenset(('cold', 'normal', 'active', 'hot'))


def mood_ready(mood):
    return (mood or {}).get('code') in READY_CODES and mood.get('rules_version') == MOOD_RULE_VERSION


def load_market_moods(store, dates):
    """Read current P2 moods; an incomplete snapshot cannot hide a repaired day."""
    from shilun.review.service import REVIEW_COLLECTION, REVIEW_VERSION
    result = {}
    for row in store.collection(MOOD_COLLECTION).find(
        {'date': {'$in': dates}, 'engine_version': REVIEW_VERSION}, {'_id': 0}):
        mood = row.get('mood') or {}
        result[row['date']] = {**mood, 'date': row['date'], 'source': 'P2共享情绪归档',
                              'engine_version': REVIEW_VERSION}
    for row in store.collection(REVIEW_COLLECTION).find(
        {'analysis_date': {'$in': dates}, 'engine_version': REVIEW_VERSION},
        {'_id': 0, 'analysis_date': 1, 'sentiment.market_mood': 1}):
        mood = row.get('sentiment', {}).get('market_mood') or {}
        d = row['analysis_date']
        # Recomputed archives also carry the latest gap diagnostics. An old
        # incomplete snapshot must not erase those reasons with an empty mood.
        if mood_ready(mood) or d not in result:
            result[d] = {**mood, 'date': d, 'source': 'P2收盘快照', 'engine_version': REVIEW_VERSION}
    for d, mood in result.items():
        if mood.get('code') in READY_CODES and not mood_ready(mood):
            result[d] = {**mood, 'code': 'pending', 'reason': '该日情绪规则版本待更新',
                         'missing_inputs': ['mood_rule_version']}
    return result


def regime_for_mood(mood):
    # Fixed mapping; a 20/30-day window must never change the same day's label.
    return {'cold': '情绪弱', 'normal': '情绪中', 'active': '情绪强', 'hot': '情绪强'}.get((mood or {}).get('code'), '环境未覆盖')


def _market_mood_state(
    *,
    current_count: int,
    previous_count: int,
    max_board: int,
    lianban_count: int,
    up_ratio: float | None,
    market_amount_yi: float | None,
    amount_ma20_yi: float | None,
    feedback_median: float | None,
    relimit_rate: float | None,
    limit_down_count: int,
    data_ready: bool,
    downside_penalty_override: int | None = None,
) -> dict[str, Any]:
    """Classify short-term mood from observable evidence.

    Positive factors keep their agreed 100-point weights.  Downside risk is an
    independent deduction, so a stronger跌停扩散 penalty does not silently
    change the positive-factor weights.
    """
    if not data_ready:
        return {
            "code": "pending",
            "label": "待确认",
            "score": None,
            "base_score": None,
            "downside_penalty": None,
            "reason": "涨停供给或必要跨日样本尚未齐全，暂不把缺失当作冰点。",
            "components": [],
            "rules_version": MOOD_RULE_VERSION,
        }

    if previous_count > 0:
        supply_ratio = current_count / previous_count
        supply_score = 25 if supply_ratio >= 1.30 else 20 if supply_ratio >= 1.15 else 12 if supply_ratio >= 0.90 else 5
        supply_evidence = f"涨停 {current_count} 家 / 昨日 {previous_count} 家（{supply_ratio:.2f}x）"
    else:
        supply_ratio = None
        supply_score = 20 if current_count >= 50 else 15 if current_count >= 30 else 8 if current_count > 0 else 0
        supply_evidence = f"涨停 {current_count} 家；缺少昨日有效基准"

    feedback_score = 20 if (feedback_median is not None and feedback_median >= 0.03 and relimit_rate is not None and relimit_rate >= 0.35) else \
        14 if (feedback_median is not None and feedback_median >= 0.0 and relimit_rate is not None and relimit_rate >= 0.20) else \
        8 if (feedback_median is not None and feedback_median >= -0.02) else 2
    height_score = 20 if max_board >= 5 else 16 if max_board >= 4 else 12 if max_board >= 3 else 7 if max_board >= 2 else 3
    breadth_score = 15 if up_ratio is not None and up_ratio >= 0.70 else 11 if up_ratio is not None and up_ratio >= 0.55 else 7 if up_ratio is not None and up_ratio >= 0.45 else 2
    amount_ratio = market_amount_yi / amount_ma20_yi if market_amount_yi is not None and amount_ma20_yi not in (None, 0) else None
    liquidity_score = 10 if amount_ratio is not None and amount_ratio >= 1.10 else 7 if amount_ratio is not None and amount_ratio >= 0.90 else 4 if amount_ratio is not None and amount_ratio >= 0.75 else 2
    diffusion_score = 10 if lianban_count >= 10 else 7 if lianban_count >= 6 else 4 if lianban_count >= 3 else 1
    components = [
        {"name": "涨停供给", "score": supply_score, "max": 25, "evidence": supply_evidence},
        {"name": "昨日涨停反馈", "score": feedback_score, "max": 20, "evidence": f"中位溢价 {feedback_median if feedback_median is not None else '—'}；再板率 {relimit_rate if relimit_rate is not None else '—'}"},
        {"name": "连板高度", "score": height_score, "max": 20, "evidence": f"最高 {max_board} 板"},
        {"name": "赚钱效应", "score": breadth_score, "max": 15, "evidence": f"上涨占比 {up_ratio:.1%}" if up_ratio is not None else "上涨占比 —"},
        {"name": "量能", "score": liquidity_score, "max": 10, "evidence": f"成交额 / MA20 {amount_ratio:.2f}x" if amount_ratio is not None else "成交额基准 —"},
        {"name": "接力扩散", "score": diffusion_score, "max": 10, "evidence": f"二板以上 {lianban_count} 家"},
    ]
    base_score = sum(item["score"] for item in components)
    if downside_penalty_override is not None:
        downside_penalty, penalty_reason = downside_penalty_override, "按跌停风险状态机扣分"
    elif limit_down_count >= max(30, int(previous_count * 0.60)):
        downside_penalty, penalty_reason = 18, f"跌停 {limit_down_count} 家，按数量代理扣分（非扩散认证）"
    elif limit_down_count >= max(15, int(previous_count * 0.35)):
        downside_penalty, penalty_reason = 12, f"跌停 {limit_down_count} 家，按数量代理扣分（非扩散认证）"
    elif limit_down_count >= max(8, int(previous_count * 0.20)):
        downside_penalty, penalty_reason = 6, f"跌停 {limit_down_count} 家，保留数量代理风险扣分"
    else:
        downside_penalty, penalty_reason = 0, "跌停未达到扩散扣分阈值"
    score = max(0, min(100, base_score - downside_penalty))
    if score < 30:
        code, label = "cold", "冰点"
    elif score < 60:
        code, label = "normal", "普通"
    elif score < 80:
        code, label = "active", "活跃"
    else:
        code, label = "hot", "亢奋"
    return {
        "code": code,
        "label": label,
        "score": score,
        "base_score": base_score,
        "downside_penalty": downside_penalty,
        "reason": penalty_reason,
        "components": components,
        "rules_version": MOOD_RULE_VERSION,
        "supply_ratio": round(supply_ratio, 4) if supply_ratio is not None else None,
    }
