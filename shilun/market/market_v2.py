"""大盘计算 V2：结构、风格、核心板块、情绪、机构与资金行为。"""

from __future__ import annotations

from typing import Any


CORE_SECTOR_GROUPS: tuple[dict[str, Any], ...] = (
    {
        "group": "AI产业链",
        "subthemes": ("算力", "CPO/光模块", "PCB", "液冷/IDC"),
        "keywords": ("IT设备", "软件服务", "通信设备", "元器件", "半导体"),
        "coverage": "行业代理：当前行业口径无法把 CPO、液冷、IDC 完全拆开。",
    },
    {
        "group": "半导体",
        "subthemes": ("存储", "GPU", "半导体设备", "晶圆制造", "封测"),
        "keywords": ("半导体",),
        "coverage": "半导体行业代理：细分赛道需概念板块成分映射后才能独立评分。",
    },
    {
        "group": "新能源",
        "subthemes": ("锂电", "光伏"),
        "keywords": ("电气设备", "电池", "光伏", "锂电"),
        "coverage": "行业/名称关键词合并。",
    },
    {
        "group": "防御方向",
        "subthemes": ("银行", "电力", "黄金", "消费"),
        "keywords": ("银行", "电力", "黄金", "食品", "饮料", "家用电器", "医药"),
        "coverage": "用银行、电力、黄金及必选消费行业代理避险方向。",
    },
)


def _number(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, value))


def _index_structure(payload: dict[str, Any]) -> dict[str, Any]:
    metrics = payload.get("metrics") or {}
    levels = payload.get("levels") or {}
    close = _number(metrics.get("index_close"))
    ma5 = _number(metrics.get("index_ma5"))
    ma10 = _number(metrics.get("index_ma10"))
    ma20 = _number(metrics.get("index_ma20"))
    slope = _number(metrics.get("ma5_slope"))
    if close > ma5 > ma10 > ma20 and slope > 0:
        stage = "上涨趋势"
        score = 85.0
    elif close < ma20 and ma5 < ma10:
        stage = "下跌阶段"
        score = 20.0
    elif close < ma5 or slope < 0:
        stage = "调整阶段"
        score = 38.0
    else:
        stage = "震荡阶段"
        score = 58.0
    support = _number(levels.get("support_1"))
    pressure = _number(levels.get("pressure_1"))
    if support and close < support:
        conclusion = f"收盘跌破第一支撑 {support:.2f}，短线结构转弱。"
        score -= 10
    elif pressure and close > pressure:
        conclusion = f"收盘站上第一压力 {pressure:.2f}，等待量能确认。"
        score += 8
    else:
        conclusion = f"收盘仍在支撑 {support:.2f} 与压力 {pressure:.2f} 之间。" if support and pressure else stage
    return {
        "stage": stage,
        "score": round(_clamp(score), 1),
        "conclusion": conclusion,
        "close": metrics.get("index_close"),
        "pct_chg": metrics.get("index_pct_chg"),
        "open": metrics.get("index_open"),
        "high": metrics.get("index_high"),
        "low": metrics.get("index_low"),
        "amount": metrics.get("amount"),
        "amount_ma5": metrics.get("amount_ma5"),
        "amount_ratio_5": metrics.get("amount_ratio_5"),
        "ma5": metrics.get("index_ma5"),
        "ma10": metrics.get("index_ma10"),
        "ma20": metrics.get("index_ma20"),
        "ma5_slope": metrics.get("ma5_slope"),
        "support_1": levels.get("support_1"),
        "support_2": levels.get("support_2"),
        "pressure_1": levels.get("pressure_1"),
        "gap_state": metrics.get("gap_state") or "未识别明显缺口",
    }


def _style_rotation(payload: dict[str, Any]) -> dict[str, Any]:
    context = payload.get("cross_index_context") or {}
    items = list(context.get("items") or [])
    by_ticker = {str(item.get("ticker")): item for item in items}
    growth = by_ticker.get("399006.SZ")
    value = by_ticker.get("000300.SH")
    pressure = None
    if growth and value:
        pressure = _number(growth.get("pct_chg")) - _number(value.get("pct_chg"))
    if pressure is None:
        label = "成长/价值比较待数据"
        score = 50.0
    elif pressure <= -0.01:
        label = "成长风格退潮"
        score = 30.0
    elif pressure >= 0.01:
        label = "成长风格占优"
        score = 75.0
    else:
        label = "成长与价值分化有限"
        score = 55.0
    missing = [
        ticker for ticker in ("000905.SH", "000852.SH")
        if ticker not in by_ticker
    ]
    return {
        "items": items,
        "growth_pressure": round(pressure, 4) if pressure is not None else None,
        "conclusion": label,
        "score": score,
        "missing_tickers": missing,
        "note": "成长压力指数 = 创业板涨跌幅 - 沪深300涨跌幅。",
    }


def _matching_sectors(sectors: list[dict[str, Any]], keywords: tuple[str, ...]) -> list[dict[str, Any]]:
    return [
        item for item in sectors
        if any(keyword in str(item.get("sector_name") or "") for keyword in keywords)
    ]


def _core_sector_monitor(sector_payload: dict[str, Any] | None) -> dict[str, Any]:
    all_sectors = list((sector_payload or {}).get("all_sectors") or [])
    sectors = all_sectors or list((sector_payload or {}).get("top_sectors") or [])
    source_scope = "all_sectors" if all_sectors else "top_sectors_only" if sectors else "missing"
    groups: list[dict[str, Any]] = []
    for definition in CORE_SECTOR_GROUPS:
        matched = _matching_sectors(sectors, definition["keywords"])
        total_amount = sum(_number((item.get("metrics") or {}).get("amount")) for item in matched)
        total_amount_ma5 = sum(_number((item.get("metrics") or {}).get("amount_ma5")) for item in matched)
        weighted_return = (
            sum(
                _number((item.get("metrics") or {}).get("return_1d"))
                * _number((item.get("metrics") or {}).get("amount"))
                for item in matched
            ) / total_amount
            if total_amount else None
        )
        limit_up = sum(int(_number((item.get("metrics") or {}).get("limit_up_count"))) for item in matched)
        big_up = sum(int(_number((item.get("metrics") or {}).get("big_up_count"))) for item in matched)
        leaders = [
            candidate
            for item in matched
            for candidate in (item.get("leader_candidates") or [])[:1]
        ]
        leader = max(leaders, key=lambda item: _number(item.get("leader_score")), default=None)
        fund_dates = sorted({
            str((item.get("fund_flow") or {}).get("as_of_date"))
            for item in matched if (item.get("fund_flow") or {}).get("as_of_date")
        })
        official_net = sum(_number((item.get("metrics") or {}).get("main_net_inflow")) for item in matched)
        stale = any(bool((item.get("fund_flow") or {}).get("is_stale")) for item in matched)
        if not matched:
            conclusion = "当前行业映射未覆盖，不能判断资金状态。"
            score = None
        else:
            amount_ratio = total_amount / total_amount_ma5 if total_amount_ma5 else 0.0
            score = _clamp(50 + _number(weighted_return) * 650 + (amount_ratio - 1) * 22)
            direction = "走强" if _number(weighted_return) > 0.01 else "走弱" if _number(weighted_return) < -0.01 else "震荡"
            flow = "净流入" if official_net > 0 else "净流出" if official_net < 0 else "资金方向不明"
            conclusion = f"{definition['group']}当日{direction}，{flow}；{'资金日已滞后，' if stale else ''}结合量价判断。"
        groups.append({
            "group": definition["group"],
            "subthemes": list(definition["subthemes"]),
            "coverage": definition["coverage"],
            "matched_sectors": [item.get("sector_name") for item in matched],
            "return_1d": round(weighted_return, 4) if weighted_return is not None else None,
            "amount": round(total_amount, 2) if matched else None,
            "amount_ratio_5": round(total_amount / total_amount_ma5, 4) if total_amount_ma5 else None,
            "limit_up_count": limit_up if matched else None,
            "big_up_count": big_up if matched else None,
            "official_main_net_inflow": round(official_net, 2) if matched else None,
            "official_main_net_inflow_unit": "万元（Tushare net_mf_amount）",
            "moneyflow_dates": fund_dates,
            "moneyflow_is_stale": stale,
            "leader": leader,
            "score": round(score, 1) if score is not None else None,
            "conclusion": conclusion,
        })
    available_scores = [_number(item.get("score")) for item in groups if item.get("score") is not None]
    all_fund_dates = sorted({
        date_text
        for item in groups
        for date_text in (item.get("moneyflow_dates") or [])
        if date_text
    })
    analysis_date = (sector_payload or {}).get("analysis_date")
    funds_date = all_fund_dates[-1] if all_fund_dates else None
    return {
        "available": bool(sectors),
        "groups": groups,
        "score": round(sum(available_scores) / len(available_scores), 1) if available_scores else None,
        "source": "sector_trends_cache",
        "source_scope": source_scope,
        "coverage_note": (
            "使用全行业缓存。"
            if source_scope == "all_sectors"
            else "当前缓存仅含强势榜，未入榜行业不会被当作 0；请重新预计算并保留全行业以获得完整监控。"
            if source_scope == "top_sectors_only"
            else "板块缓存缺失，本模块不计入总分。"
        ),
        "analysis_date": analysis_date,
        "funds_date": funds_date,
        "funds_date_is_stale": bool(funds_date and analysis_date and funds_date != analysis_date),
        "funds_dates": all_fund_dates,
    }


def _sentiment(payload: dict[str, Any], limit_up_rows: list[dict[str, Any]]) -> dict[str, Any]:
    metrics = payload.get("metrics") or {}
    up = int(_number(metrics.get("up_count")))
    down = int(_number(metrics.get("down_count")))
    total = max(1, up + down)
    up_ratio = up / total
    limit_up = len(limit_up_rows) if limit_up_rows else int(_number(metrics.get("limit_up_count")))
    limit_down = int(_number(metrics.get("limit_down_count")))
    max_board = max((int(_number(item.get("consecutive_count"))) for item in limit_up_rows), default=None)
    resealed = sum(int(_number(item.get("broken_count"))) > 0 for item in limit_up_rows)
    up_ma5 = _number(metrics.get("up_count_ma5"), 0.0)
    down_ma5 = _number(metrics.get("down_count_ma5"), 0.0)
    limit_up_ma5 = _number(metrics.get("limit_up_count_ma5"), 0.0)
    limit_down_ma5 = _number(metrics.get("limit_down_count_ma5"), 0.0)

    def relative_to_average(current: float, average: float) -> float | None:
        return round(current / average - 1.0, 4) if average > 0 else None

    score = _clamp(50 + (up_ratio - 0.5) * 80 + min(limit_up, 100) * 0.18 - min(limit_down, 100) * 0.35)
    if up_ratio < 0.30 or limit_down >= 30:
        stage = "退潮阶段"
    elif up_ratio > 0.65 and limit_up >= 50:
        stage = "活跃/扩散阶段"
    else:
        stage = "分歧震荡阶段"
    return {
        "score": round(score, 1),
        "stage": stage,
        "up_count": up,
        "up_count_ma5": round(up_ma5, 2) if up_ma5 else None,
        "up_count_vs_ma5": relative_to_average(up, up_ma5),
        "down_count": down,
        "down_count_ma5": round(down_ma5, 2) if down_ma5 else None,
        "down_count_vs_ma5": relative_to_average(down, down_ma5),
        "limit_up_count": limit_up,
        "limit_up_count_ma5": round(limit_up_ma5, 2) if limit_up_ma5 else None,
        "limit_up_count_vs_ma5": relative_to_average(limit_up, limit_up_ma5),
        "limit_down_count": limit_down,
        "limit_down_count_ma5": round(limit_down_ma5, 2) if limit_down_ma5 else None,
        "limit_down_count_vs_ma5": relative_to_average(limit_down, limit_down_ma5),
        "max_consecutive_board": max_board,
        "opened_then_resealed_count": resealed if limit_up_rows else None,
        "broken_board_rate": None,
        "broken_board_status": "缺少炸板池；涨停池的 broken_count 只能统计开板后回封，不能冒充炸板率。",
        "highest_standard": [
            {"ticker": item.get("ticker"), "name": item.get("name"), "boards": item.get("consecutive_count")}
            for item in sorted(limit_up_rows, key=lambda item: int(_number(item.get("consecutive_count"))), reverse=True)[:3]
        ],
    }


def _turnover_score(payload: dict[str, Any]) -> float:
    ratio = _number((payload.get("metrics") or {}).get("amount_ratio_5"), 1.0)
    return round(_clamp(50 + (ratio - 1.0) * 55), 1)


def build_market_v2(
    payload: dict[str, Any],
    *,
    sector_payload: dict[str, Any] | None = None,
    limit_up_rows: list[dict[str, Any]] | None = None,
    institutional_futures: dict[str, Any] | None = None,
) -> dict[str, Any]:
    structure = _index_structure(payload)
    style = _style_rotation(payload)
    sectors = _core_sector_monitor(sector_payload)
    sentiment = _sentiment(payload, list(limit_up_rows or []))
    futures = institutional_futures or {
        "status": "unavailable",
        "products": [],
        "institutional_summary": {"available": False, "risk_score": None, "conclusion": "期指席位数据待同步。"},
    }
    futures_summary = futures.get("institutional_summary") or {}
    institution_health = (
        100 - _number(futures_summary.get("risk_score"))
        if futures_summary.get("risk_score") is not None else None
    )
    modules = {
        "index_trend": {"weight": 0.25, "score": structure["score"], "label": "指数趋势"},
        "sentiment": {"weight": 0.20, "score": sentiment["score"], "label": "市场情绪"},
        "sector_strength": {"weight": 0.25, "score": sectors.get("score"), "label": "板块强度"},
        "institution": {"weight": 0.20, "score": round(institution_health, 1) if institution_health is not None else None, "label": "机构多空"},
        "turnover": {"weight": 0.10, "score": _turnover_score(payload), "label": "成交资金"},
    }
    available_weight = sum(item["weight"] for item in modules.values() if item["score"] is not None)
    weighted = sum(item["weight"] * _number(item["score"]) for item in modules.values() if item["score"] is not None)
    health = round(weighted / available_weight, 1) if available_weight else None
    if health is None:
        health_label = "数据不足"
    elif health >= 85:
        health_label = "强势"
    elif health >= 70:
        health_label = "偏强"
    elif health >= 50:
        health_label = "震荡"
    elif health >= 30:
        health_label = "弱势"
    else:
        health_label = "风险阶段"
    technology = next((item for item in sectors.get("groups") or [] if item.get("group") == "AI产业链"), {})
    if technology.get("return_1d") is None:
        sector_action = "主线数据待确认"
    elif _number(technology.get("return_1d")) < -0.01:
        sector_action = "主线资金撤离"
    elif _number(technology.get("return_1d")) > 0.01:
        sector_action = "主线仍有承接"
    else:
        sector_action = "主线分歧"
    institution_behavior = futures_summary.get("behavior") or "机构数据待确认"
    if structure["stage"] in {"下跌阶段", "调整阶段"} and sentiment["stage"] == "退潮阶段":
        capital_behavior = "降低风险敞口"
        phase = "风险释放阶段，等待恐慌与结构同时企稳"
    elif sector_action == "主线资金撤离":
        capital_behavior = "从成长主线撤退/切换"
        phase = "风格切换阶段，先确认防御方向是否持续"
    else:
        capital_behavior = "结构性轮动"
        phase = "震荡轮动阶段，等待主线和指数共振"
    return {
        "version": "market_v2.0",
        "analysis_date": payload.get("analysis_date"),
        "index_structure": structure,
        "style_rotation": style,
        "core_sectors": sectors,
        "sentiment": sentiment,
        "institutional_futures": futures,
        "capital_behavior": {
            "index": structure["stage"],
            "style": style["conclusion"],
            "mainline": sector_action,
            "institution": institution_behavior,
            "behavior": capital_behavior,
            "phase": phase,
            "summary": f"指数{structure['stage']}；{style['conclusion']}；{sector_action}；{institution_behavior} → {capital_behavior}。",
        },
        "market_health": {
            "score": health,
            "label": health_label,
            "coverage": round(available_weight, 2),
            "complete": available_weight >= 0.999,
            "modules": modules,
            "formula": "指数趋势25% + 市场情绪20% + 板块强度25% + 机构多空20% + 成交资金10%；缺失模块不伪造分数，并显示覆盖率。",
        },
    }


__all__ = ["CORE_SECTOR_GROUPS", "build_market_v2"]
