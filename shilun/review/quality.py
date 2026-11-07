"""Data-quality contract for a materialized review snapshot."""

from __future__ import annotations

from datetime import date
from typing import Any


def _ratio(numerator: int, denominator: int) -> float | None:
    if denominator <= 0:
        return None
    # Coverage is a set-overlap concept.  Keep the contract defensive when a
    # caller accidentally passes a broader source universe than the target
    # stock cross-section.
    return round(min(1.0, max(0.0, numerator / denominator)), 4)


def _lag(later: str | None, earlier: str | None) -> int | None:
    try:
        return (date.fromisoformat(str(later)[:10]) - date.fromisoformat(str(earlier)[:10])).days
    except (TypeError, ValueError):
        return None


def assess_review_data_quality(
    *,
    analysis_date: str,
    generated_date: str,
    stock_master_count: int,
    current_bar_count: int,
    previous_bar_count: int,
    daily_basic_count: int,
    moneyflow_count: int,
    price_limit_count: int,
    limit_pool_date: str | None,
    futures_date: str | None,
) -> dict[str, Any]:
    """Assess whether the snapshot supports history review or current execution.

    The intended grain is one stock per trading date.  Market facts can remain
    useful when optional enhanced feeds are stale, but ladder, genealogy, and
    expectation observations must then be explicitly degraded.
    """

    master_coverage = _ratio(current_bar_count, stock_master_count)
    previous_coverage = _ratio(previous_bar_count, current_bar_count)
    basic_coverage = _ratio(daily_basic_count, current_bar_count)
    moneyflow_coverage = _ratio(moneyflow_count, current_bar_count)
    limit_coverage = _ratio(price_limit_count, current_bar_count)
    analysis_lag = _lag(generated_date, analysis_date)
    limit_lag = _lag(analysis_date, limit_pool_date)
    futures_lag = _lag(analysis_date, futures_date)

    checks = [
        {
            "id": "daily_bar_cross_section",
            "label": "个股日线横截面",
            "status": "pass" if current_bar_count >= 500 else "fail",
            "observed": current_bar_count,
            "coverage": master_coverage,
            "impact": "大盘广度、成交额、三战法",
        },
        {
            "id": "previous_session_join",
            "label": "前一有效交易日覆盖",
            "status": "pass" if previous_coverage is not None and previous_coverage >= 0.8 else "fail",
            "observed": previous_bar_count,
            "coverage": previous_coverage,
            "impact": "涨跌家数、相对强弱、次日溢价",
        },
        {
            "id": "daily_basic_join",
            "label": "市值与换手覆盖",
            "status": "pass" if basic_coverage is not None and basic_coverage >= 0.8 else "warn",
            "observed": daily_basic_count,
            "coverage": basic_coverage,
            "impact": "容量中军与市值权限",
        },
        {
            "id": "moneyflow_join",
            "label": "个股资金流覆盖",
            "status": "pass" if moneyflow_coverage is not None and moneyflow_coverage >= 0.7 else "warn",
            "observed": moneyflow_count,
            "coverage": moneyflow_coverage,
            "impact": "红K净流入与板块现金流",
        },
        {
            "id": "price_limit_join",
            "label": "涨跌停价覆盖",
            "status": "pass" if limit_coverage is not None and limit_coverage >= 0.8 else "warn",
            "observed": price_limit_count,
            "coverage": limit_coverage,
            "impact": "硬涨停/跌停计数",
        },
        {
            "id": "enhanced_limit_pool",
            "label": "连板增强数据",
            "status": "pass" if limit_lag == 0 else "warn",
            "observed": limit_pool_date,
            "lag_days": limit_lag,
            "impact": "梯队、龙头谱系、板型和今日预期验证",
        },
        {
            "id": "cffex_positions",
            "label": "股指期货机构多空",
            "status": "pass" if futures_lag is not None and futures_lag <= 1 else "warn",
            "observed": futures_date,
            "lag_days": futures_lag,
            "impact": "机构宏观态度",
        },
        {
            "id": "currentness",
            "label": "相对当前日期",
            "status": "pass" if analysis_lag is not None and analysis_lag <= 3 else "fail",
            "observed": analysis_date,
            "lag_days": analysis_lag,
            "impact": "能否用于当前交易动作",
        },
    ]
    core_fail = any(item["status"] == "fail" for item in checks[:2])
    historical_review_ready = not core_fail
    current_execution_ready = bool(
        historical_review_ready
        and analysis_lag is not None
        and analysis_lag <= 3
        and limit_lag == 0
        and moneyflow_coverage is not None
        and moneyflow_coverage >= 0.7
    )
    if core_fail:
        status, label = "blocked", "核心数据不可用"
    elif analysis_lag is None or analysis_lag > 3:
        status, label = "historical_only", "仅可历史复盘"
    elif any(item["status"] == "warn" for item in checks):
        status, label = "degraded", "部分模块降级"
    else:
        status, label = "ready", "数据可执行"
    return {
        "status": status,
        "label": label,
        "grain": "一只股票 × 一个有效交易日；指数和增强数据按各自来源日期关联",
        "historical_review_ready": historical_review_ready,
        "current_execution_ready": current_execution_ready,
        "checks": checks,
        "policy": "核心横截面失败则阻断；增强数据缺失只降级对应模块；历史数据与当日数据绝不混写。",
    }


__all__ = ["assess_review_data_quality"]
