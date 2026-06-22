from jqdata import *
import builtins
import math
import traceback


"""
石论聚宽单文件回测版

设计目标：
1. 不依赖 `shilun import`
2. 可以直接粘到聚宽策略编辑器运行
3. 用“石论一期”的核心思路做一个可回测的轻量版信号

说明：
- 这是“可测试 alpha 的单文件版本”，不是把整个项目完整搬进聚宽
- 这里没有接入训练模型，概率值使用规则近似
- 这里先只做股票账户，不覆盖期货/融资融券
"""


def initialize(context):
    set_benchmark("000300.XSHG")
    set_option("use_real_price", True)
    set_option("order_volume_ratio", 1)
    set_order_cost(
        OrderCost(
            open_tax=0,
            close_tax=0.001,
            open_commission=0.0003,
            close_commission=0.0003,
            close_today_commission=0,
            min_commission=5,
        ),
        type="stock",
    )

    log.info("石论聚宽单文件回测版 2.4 启动")

    g.benchmark = "000300.XSHG"
    g.lookback = 120
    g.max_positions = 4
    g.target_weight = 0.25
    g.trim_weight = 0.125
    g.min_history = 80
    g.max_candidates = 300
    g.listing_days = 250
    g.min_turnover_ratio = 0.5
    g.max_turnover_ratio = 15.0
    g.min_circulating_market_cap = 50
    g.min_hold_days = 5
    g.position_meta = {}
    g.fixed_stock_pool = []
    g.atr_position_base = 0.035
    g.atr_position_floor = 0.60
    g.atr_position_ceiling = 1.15
    g.profit_protect_min_gain = 0.08
    g.profit_protect_retrace = 0.04
    g.profit_protect_activation_atr = 2.5
    g.profit_protect_retrace_atr = 1.3
    g.hard_stop_loss = 0.09
    g.hard_stop_atr = 2.4

    run_daily(before_market_open, time="before_open", reference_security=g.benchmark)
    run_daily(rebalance, time="14:50", reference_security=g.benchmark)
    run_daily(after_market_close, time="after_close", reference_security=g.benchmark)


def before_market_open(context):
    log.info("before_market_open: %s" % str(context.current_dt.time()))


def rebalance(context):
    try:
        current_data = get_current_data()
        sync_position_meta(context)
        holdings = set(context.portfolio.positions.keys())
        total_value = context.portfolio.total_value
        benchmark_state = get_benchmark_state(g.benchmark, g.lookback)
        stock_pool = get_candidate_stocks(context)
        analysis_map = build_analysis_map(stock_pool, holdings, current_data, benchmark_state, total_value)

        keep_list, sell_reasons = evaluate_current_positions(
            context=context,
            holdings=holdings,
            analysis_map=analysis_map,
            benchmark_state=benchmark_state,
            total_value=total_value,
        )
        buy_candidates = select_new_entries(
            stock_pool=stock_pool,
            current_positions=holdings,
            keep_list=keep_list,
            analysis_map=analysis_map,
            benchmark_state=benchmark_state,
            current_data=current_data,
            total_value=total_value,
        )
        target_weights = build_target_weights(keep_list, buy_candidates, analysis_map, benchmark_state, total_value)
        new_buy_list = [security for security in buy_candidates if security not in holdings and security in target_weights]
        tradable_count = len(
            [
                analysis
                for analysis in analysis_map.values()
                if analysis is not None
                and analysis["entry_weight"] > 0
                and is_order_value_feasible(
                    analysis["security"],
                    analysis,
                    current_data,
                    analysis["entry_weight"],
                    total_value,
                )
            ]
        )
        desired_cash_ratio = builtins.max(0.0, 1.0 - builtins.sum(target_weights.values()))

        log.info(
            "pool=%s candidates=%s held=%s sell_count=%s buy_count=%s tradable=%s cash_ratio=%.2f benchmark=%s"
            % (
                len(stock_pool),
                len([analysis for analysis in analysis_map.values() if analysis is not None]),
                len(holdings),
                len(sell_reasons),
                len(new_buy_list),
                tradable_count,
                desired_cash_ratio,
                benchmark_state["weekly_trend"],
            )
        )
        rebalance_positions(
            context=context,
            current_data=current_data,
            target_weights=target_weights,
            sell_reasons=sell_reasons,
            buy_list=new_buy_list,
            analysis_map=analysis_map,
        )
    except Exception:
        log.error("rebalance failed:\n%s" % traceback.format_exc())


def after_market_close(context):
    log.info("after_market_close: %s" % str(context.current_dt.time()))
    trades = get_trades()
    for trade in trades.values():
        log.info("成交记录: %s" % str(trade))
    log.info("##############################################################")


def sync_position_meta(context):
    today = str(context.current_dt.date())
    current_positions = set(context.portfolio.positions.keys())

    for security in list(g.position_meta.keys()):
        if security not in current_positions:
            del g.position_meta[security]

    for security in current_positions:
        position = context.portfolio.positions.get(security)
        entry_price = position_cost_basis(position)
        current_price = position_latest_price(position)
        meta = g.position_meta.get(security)
        if meta is None:
            g.position_meta[security] = {
                "entry_date": today,
                "hold_days": 0,
                "entry_signal": "restored",
                "entry_price": entry_price,
                "peak_price": current_price or entry_price,
                "last_seen_date": today,
            }
            continue
        if meta.get("last_seen_date") != today:
            meta["hold_days"] = int(meta.get("hold_days", 0)) + 1
            meta["last_seen_date"] = today
        if meta.get("entry_price") in (None, 0) and entry_price not in (None, 0):
            meta["entry_price"] = entry_price
        peak_price = meta.get("peak_price")
        observed_price = current_price or meta.get("entry_price")
        if observed_price not in (None, 0):
            meta["peak_price"] = observed_price if peak_price in (None, 0) else builtins.max(float(peak_price), float(observed_price))


def build_analysis_map(stock_pool, holdings, current_data, benchmark_state, total_value):
    analysis_map = {}
    universe = []
    seen = set()

    for security in list(holdings) + list(stock_pool):
        if security in seen:
            continue
        seen.add(security)
        universe.append(security)

    for security in universe:
        try:
            if security not in holdings and should_skip_security(current_data, security):
                continue
            bars = get_bars(security, g.lookback)
            if len(bars["close"]) < g.min_history:
                if security in holdings:
                    analysis_map[security] = None
                continue
            analysis = analyze_security(security, bars, benchmark_state)
            analysis["entry_weight"] = desired_entry_weight(analysis, benchmark_state, total_value)
            analysis["hold_weight"] = desired_hold_weight(analysis, benchmark_state, total_value)
            analysis_map[security] = analysis
        except Exception:
            log.error("analyze failed %s:\n%s" % (security, traceback.format_exc()))
            if security in holdings:
                analysis_map[security] = None
            else:
                continue

    return analysis_map


def get_bars(security, count):
    history = attribute_history(
        security,
        count,
        unit="1d",
        fields=("open", "high", "low", "close", "volume", "money"),
        skip_paused=True,
        df=False,
        fq="pre",
    )
    return {
        "open": list(history["open"]),
        "high": list(history["high"]),
        "low": list(history["low"]),
        "close": list(history["close"]),
        "volume": list(history["volume"]),
        "money": list(history["money"]),
    }


def get_candidate_stocks(context):
    prev_date = context.previous_date
    current_data = get_current_data()
    all_stocks = get_all_securities(types=["stock"], date=prev_date)
    fixed_stock_pool = set(getattr(g, "fixed_stock_pool", []) or [])
    primary_query = query(
        valuation.code,
        valuation.turnover_ratio,
        valuation.circulating_market_cap,
    ).filter(
        valuation.turnover_ratio > g.min_turnover_ratio,
        valuation.turnover_ratio < g.max_turnover_ratio,
        valuation.circulating_market_cap > g.min_circulating_market_cap,
    ).order_by(
        valuation.circulating_market_cap.desc()
    )

    df = get_fundamentals(primary_query, date=prev_date)
    if df is None or len(df) == 0:
        fallback_query = query(
            valuation.code,
            valuation.turnover_ratio,
            valuation.circulating_market_cap,
        ).filter(
            valuation.circulating_market_cap > g.min_circulating_market_cap,
        ).order_by(
            valuation.circulating_market_cap.desc()
        )
        df = get_fundamentals(fallback_query, date=prev_date)
    if df is None or len(df) == 0:
        return []

    stocks = []
    for security in list(df["code"]):
        if fixed_stock_pool and security not in fixed_stock_pool:
            continue
        if security not in all_stocks.index:
            continue
        if security.startswith("688") or security.startswith("8"):
            continue
        start_date = all_stocks.loc[security, "start_date"]
        if (prev_date - start_date).days < g.listing_days:
            continue
        if should_skip_security(current_data, security):
            continue
        stocks.append(security)
        if len(stocks) >= g.max_candidates:
            break

    return stocks


def get_benchmark_state(security, count):
    bars = get_bars(security, count)
    closes = bars["close"]
    ma60 = mean(closes[-60:])
    ma60_prev = mean(closes[-70:-10]) if len(closes) >= 70 else ma60
    close_to_ma60 = ratio(closes[-1], ma60) - 1
    ma60_slope = ratio(ma60, ma60_prev) - 1
    return {
        "return_5d": pct_change(closes, 5),
        "return_20d": pct_change(closes, 20),
        "weekly_trend": derive_weekly_trend(close_to_ma60, ma60_slope),
    }


def analyze_security(security, bars, benchmark_state):
    closes = bars["close"]
    highs = bars["high"]
    lows = bars["low"]
    volumes = bars["volume"]

    close = closes[-1]
    ma20 = mean(closes[-20:])
    ma60 = mean(closes[-60:])
    ma20_prev = mean(closes[-21:-1]) if len(closes) >= 21 else ma20
    ma60_prev = mean(closes[-61:-1]) if len(closes) >= 61 else ma60
    return_1d = pct_change(closes, 1)
    return_5d = pct_change(closes, 5)
    return_10d = pct_change(closes, 10)
    return_20d = pct_change(closes, 20)
    close_to_ma20 = ratio(close, ma20) - 1
    close_to_ma60 = ratio(close, ma60) - 1
    ma20_slope = ratio(ma20, ma20_prev) - 1
    ma60_slope = ratio(ma60, ma60_prev) - 1
    recent_high_20 = builtins.max(highs[-20:])
    recent_low_20 = builtins.min(lows[-20:])
    close_to_recent_high = ratio(close, recent_high_20) - 1
    close_to_recent_low = ratio(close, recent_low_20) - 1
    volume_ratio = ratio(volumes[-1], mean(volumes[-10:]))
    volatility_10d = realized_volatility(closes[-11:])
    atr20 = average_true_range(highs, lows, closes, period=20)
    atr_pct = ratio(atr20, close)

    weekly_trend = derive_weekly_trend(close_to_ma60, ma60_slope)
    daily_state = derive_daily_state(return_1d, return_5d, close_to_ma20)
    structure_type = derive_structure_type(weekly_trend, daily_state, close_to_recent_high)
    structure_score = derive_structure_score(close_to_ma20, return_5d)
    volume_state = derive_volume_state(volume_ratio)
    breakout_quality = derive_breakout_quality(close_to_recent_high, volume_state)
    pullback_quality = derive_pullback_quality(close_to_ma20)
    price_action_quality = derive_price_action_quality(return_1d, volatility_10d)
    chip_pressure = derive_chip_pressure(close_to_recent_high)
    chip_support = derive_chip_support(close_to_recent_low)
    risk_score = derive_risk_score(volatility_10d, close_to_ma20, breakout_quality)
    invalidation_level = derive_invalidation_level(recent_low_20, ma20)
    invalidation_pressure = derive_invalidation_pressure(close, invalidation_level)

    excess_return_5d = return_5d - benchmark_state["return_5d"]
    excess_return_20d = return_20d - benchmark_state["return_20d"]
    relative_strength_label = derive_relative_strength_label(excess_return_20d, excess_return_5d)

    p_continue_10d = estimate_continue_probability(
        weekly_trend=weekly_trend,
        structure_type=structure_type,
        structure_score=structure_score,
        breakout_quality=breakout_quality,
        pullback_quality=pullback_quality,
        volume_ratio=volume_ratio,
        excess_return_20d=excess_return_20d,
        risk_score=risk_score,
    )
    p_fail_5d = estimate_fail_probability(
        risk_score=risk_score,
        breakout_quality=breakout_quality,
        price_action_quality=price_action_quality,
        close_to_ma20=close_to_ma20,
    )

    conclusion_label = decide_conclusion(
        weekly_trend=weekly_trend,
        structure_type=structure_type,
        structure_score=structure_score,
        breakout_quality=breakout_quality,
        pullback_quality=pullback_quality,
        p_continue_10d=p_continue_10d,
        p_fail_5d=p_fail_5d,
        risk_score=risk_score,
        relative_strength_label=relative_strength_label,
        close_to_ma60=close_to_ma60,
        ma60_slope=ma60_slope,
    )

    rank_score = structure_score + 100 * p_continue_10d - risk_score + 50 * builtins.max(excess_return_20d, 0)

    return {
        "security": security,
        "weekly_trend": weekly_trend,
        "daily_state": daily_state,
        "structure_type": structure_type,
        "structure_score": structure_score,
        "volume_state": volume_state,
        "breakout_quality": breakout_quality,
        "pullback_quality": pullback_quality,
        "price_action_quality": price_action_quality,
        "chip_pressure": chip_pressure,
        "chip_support": chip_support,
        "risk_score": risk_score,
        "invalidation_level": invalidation_level,
        "invalidation_pressure": invalidation_pressure,
        "close": close,
        "atr20": atr20,
        "atr_pct": atr_pct,
        "close_to_ma20": close_to_ma20,
        "return_10d": return_10d,
        "excess_return_5d": excess_return_5d,
        "excess_return_20d": excess_return_20d,
        "relative_strength_label": relative_strength_label,
        "p_continue_10d": p_continue_10d,
        "p_fail_5d": p_fail_5d,
        "conclusion_label": conclusion_label,
        "rank_score": rank_score,
        "close_to_ma60": close_to_ma60,
        "ma60_slope": ma60_slope,
    }


def evaluate_current_positions(context, holdings, analysis_map, benchmark_state, total_value):
    keep_candidates = []
    sell_reasons = {}

    for security in holdings:
        analysis = analysis_map.get(security)
        refresh_position_meta_from_analysis(security, analysis)
        reason = exit_reason_for_position(security, analysis, benchmark_state)
        if reason is None:
            keep_candidates.append(security)
        else:
            sell_reasons[security] = reason

    max_kept = max_kept_positions(benchmark_state["weekly_trend"], total_value)
    if max_kept is not None and len(keep_candidates) > max_kept:
        ranked = sorted(
            keep_candidates,
            key=lambda item: analysis_map[item]["rank_score"] if analysis_map.get(item) else -9999,
            reverse=True,
        )
        keep_list = ranked[:max_kept]
        for security in ranked[max_kept:]:
            sell_reasons[security] = "benchmark_down"
        return keep_list, sell_reasons

    return keep_candidates, sell_reasons


def select_new_entries(stock_pool, current_positions, keep_list, analysis_map, benchmark_state, current_data, total_value):
    max_positions = max_entry_positions(benchmark_state["weekly_trend"], total_value)
    open_slots = builtins.max(0, max_positions - len(keep_list))
    if open_slots <= 0:
        return []

    candidates = []
    for security in stock_pool:
        if security in current_positions:
            continue
        analysis = analysis_map.get(security)
        if analysis is None:
            continue
        if analysis["entry_weight"] <= 0:
            continue
        if not is_order_value_feasible(security, analysis, current_data, analysis["entry_weight"], total_value):
            continue
        candidates.append(analysis)

    candidates.sort(key=lambda item: item["rank_score"], reverse=True)
    return [item["security"] for item in candidates[:open_slots]]


def build_target_weights(keep_list, buy_list, analysis_map, benchmark_state, total_value):
    target_weights = {}
    for security in keep_list:
        analysis = analysis_map.get(security)
        if analysis is None:
            continue
        weight = desired_hold_weight(analysis, benchmark_state, total_value)
        if weight > 0:
            target_weights[security] = weight
    for security in buy_list:
        analysis = analysis_map.get(security)
        if analysis is None:
            continue
        weight = desired_entry_weight(analysis, benchmark_state, total_value)
        if weight > 0:
            target_weights[security] = weight
    total_target = builtins.sum(target_weights.values())
    max_ratio = max_investment_ratio(benchmark_state["weekly_trend"], total_value)
    if total_target > max_ratio and total_target > 0:
        scale = max_ratio / total_target
        for security in list(target_weights.keys()):
            target_weights[security] = target_weights[security] * scale
    return target_weights


def rebalance_positions(context, current_data, target_weights, sell_reasons, buy_list, analysis_map):
    current_positions = set(context.portfolio.positions.keys())
    total_value = context.portfolio.total_value

    for security in current_positions:
        if security in target_weights:
            continue
        security_data = current_data[security]
        if is_at_low_limit(security_data):
            log.info("skip sell %s: at low limit" % security)
            continue
        log.info("sell %s | reason=%s" % (security, sell_reasons.get(security, "rebalance_out")))
        place_target_weight_order(context, security, 0.0, current_data, analysis_map.get(security))

    for security in buy_list:
        analysis = analysis_map.get(security)
        if analysis is None:
            continue
        log.info(
            "buy %s | conclusion=%s | p_continue=%.2f | risk=%s | relative=%s | target_weight=%.3f"
            % (
                security,
                analysis["conclusion_label"],
                analysis["p_continue_10d"],
                analysis["risk_score"],
                analysis["relative_strength_label"],
                target_weights.get(security, 0.0),
            )
        )

    for security, target_weight in target_weights.items():
        security_data = current_data[security]
        is_holding = security in current_positions
        if (not is_holding) and is_at_high_limit(security_data):
            log.info("skip buy %s: at high limit" % security)
            continue
        if is_holding:
            current_weight = current_position_weight(context, security)
            if should_reduce_existing_position(current_weight, target_weight):
                if is_at_low_limit(security_data):
                    log.info("skip trim %s: at low limit" % security)
                    continue
                log.info(
                    "trim %s | current_weight=%.3f -> target_weight=%.3f | invalidation_pressure=%s"
                    % (
                        security,
                        current_weight,
                        target_weight,
                        analysis_map.get(security, {}).get("invalidation_pressure", "unknown"),
                    )
                )
                if not place_target_weight_order(context, security, target_weight, current_data, analysis_map.get(security)):
                    log.info("skip trim %s: board lot would not change position" % security)
            continue
        if not place_target_weight_order(context, security, target_weight, current_data, analysis_map.get(security)):
            log.info("skip buy %s: target weight below one board lot" % security)
            continue
        if not is_holding:
            g.position_meta[security] = {
                "entry_date": str(context.current_dt.date()),
                "hold_days": 0,
                "entry_signal": analysis_map[security]["conclusion_label"],
                "entry_price": analysis_map[security]["close"],
                "peak_price": analysis_map[security]["close"],
                "last_seen_date": str(context.current_dt.date()),
            }


def should_skip_security(current_data, security):
    info = current_data[security]
    if getattr(info, "paused", False):
        return True
    if getattr(info, "is_st", False):
        return True
    return False


def desired_entry_weight(analysis, benchmark_state, total_value):
    conclusion = analysis["conclusion_label"]
    p_continue_10d = analysis["p_continue_10d"]
    risk_score = analysis["risk_score"]
    weekly_trend = analysis["weekly_trend"]
    relative_strength_label = analysis["relative_strength_label"]
    breakout_quality = analysis["breakout_quality"]
    benchmark_weekly_trend = benchmark_state["weekly_trend"]
    profile = account_profile(total_value)

    if conclusion != "high_quality_continuation":
        return 0.0
    if risk_score > 62:
        return 0.0
    if p_continue_10d < 0.64:
        return 0.0
    if relative_strength_label in ("明显弱于基准", "略弱于基准"):
        return 0.0
    if weekly_trend == "down":
        return 0.0
    if breakout_quality != "valid":
        return 0.0
    if benchmark_weekly_trend == "down":
        if (
            p_continue_10d >= 0.70
            and risk_score <= 52
            and relative_strength_label == "显著强于基准"
        ):
            return volatility_adjusted_weight(profile["trim_weight"], analysis)
        return 0.0
    if benchmark_weekly_trend == "range":
        if p_continue_10d >= 0.67 and risk_score <= 56 and relative_strength_label in ("显著强于基准", "略强于基准"):
            return volatility_adjusted_weight(profile["trim_weight"], analysis)
        return 0.0
    if p_continue_10d >= 0.68 and risk_score <= 56:
        return volatility_adjusted_weight(profile["high_weight"], analysis)
    return volatility_adjusted_weight(profile["confirm_weight"], analysis)


def desired_hold_weight(analysis, benchmark_state, total_value):
    conclusion = analysis["conclusion_label"]
    benchmark_weekly_trend = benchmark_state["weekly_trend"]
    profile = account_profile(total_value)

    if conclusion == "defense_first":
        return 0.0
    pressure = analysis.get("invalidation_pressure", "safe")
    if pressure == "breach":
        return 0.0
    if benchmark_weekly_trend == "down":
        if analysis["relative_strength_label"] == "显著强于基准":
            return invalidation_adjusted_weight(
                volatility_adjusted_weight(profile["trim_weight"], analysis),
                pressure,
                profile,
            )
        return 0.0
    if conclusion == "high_quality_continuation":
        return invalidation_adjusted_weight(
            volatility_adjusted_weight(profile["high_weight"], analysis),
            pressure,
            profile,
        )
    if conclusion in ("confirmation_needed", "momentum_but_heavy_overhead"):
        return invalidation_adjusted_weight(
            volatility_adjusted_weight(profile["trim_weight"], analysis),
            pressure,
            profile,
        )
    return 0.0


def exit_reason_for_position(security, analysis, benchmark_state):
    if analysis is None:
        return None

    hold_days = g.position_meta.get(security, {}).get("hold_days", 0)
    close_price = analysis["close"]
    invalidation_level = analysis["invalidation_level"]
    invalidation_pressure = analysis.get("invalidation_pressure", "safe")

    if invalidation_level is not None and close_price <= invalidation_level:
        return "break_invalidation"
    if should_force_disaster_stop(security, analysis):
        return "hard_stop"
    if should_take_profit_protection(security, analysis):
        return "profit_protection"
    if hold_days < g.min_hold_days:
        return None
    if analysis["risk_score"] > 85 or analysis["p_fail_5d"] >= 0.78:
        return "risk_up"
    if invalidation_pressure == "danger" and analysis["p_fail_5d"] >= 0.70:
        return "near_invalidation"
    if analysis["conclusion_label"] == "defense_first":
        return "defense_first"
    if analysis["p_continue_10d"] < 0.46 or analysis["risk_score"] > 78:
        return "risk_up"
    if analysis["breakout_quality"] == "invalid":
        return "breakout_invalid"
    if analysis["close_to_ma20"] < -0.04:
        return "break_ma20"
    if (
        analysis["weekly_trend"] == "down"
        and analysis["relative_strength_label"] not in ("显著强于基准", "略强于基准")
    ):
        return "benchmark_down"
    if benchmark_state["weekly_trend"] == "down" and analysis["relative_strength_label"] not in ("显著强于基准", "略强于基准"):
        return "benchmark_down"
    return None


def max_entry_positions(benchmark_weekly_trend, total_value):
    base_positions = account_profile(total_value)["max_positions"]
    if benchmark_weekly_trend == "up":
        return base_positions
    if benchmark_weekly_trend == "range":
        return builtins.max(1, base_positions - 1)
    return 1


def max_kept_positions(benchmark_weekly_trend, total_value):
    if benchmark_weekly_trend == "down":
        return 1 if total_value < 120000 else 2
    return None


def account_profile(total_value):
    if total_value < 60000:
        return {
            "max_positions": 2,
            "high_weight": 0.45,
            "confirm_weight": 0.30,
            "trim_weight": 0.20,
            "danger_weight": 0.10,
        }
    if total_value < 120000:
        return {
            "max_positions": 3,
            "high_weight": 0.32,
            "confirm_weight": 0.20,
            "trim_weight": 0.14,
            "danger_weight": 0.08,
        }
    return {
        "max_positions": g.max_positions,
        "high_weight": g.target_weight,
        "confirm_weight": 0.18,
        "trim_weight": g.trim_weight,
        "danger_weight": 0.08,
    }


def max_investment_ratio(benchmark_weekly_trend, total_value):
    if total_value < 60000:
        if benchmark_weekly_trend == "up":
            return 0.90
        if benchmark_weekly_trend == "range":
            return 0.60
        return 0.30
    if benchmark_weekly_trend == "up":
        return 0.85
    if benchmark_weekly_trend == "range":
        return 0.55
    return 0.25


def reference_price_for_order(security_data, analysis):
    last_price = getattr(security_data, "last_price", None)
    if last_price not in (None, 0):
        return float(last_price)
    day_open = getattr(security_data, "day_open", None)
    if day_open not in (None, 0):
        return float(day_open)
    close_price = analysis.get("close")
    if close_price not in (None, 0):
        return float(close_price)
    return None


def current_position_weight(context, security):
    total_value = context.portfolio.total_value
    if total_value <= 0:
        return 0.0
    position = context.portfolio.positions.get(security)
    if position is None:
        return 0.0
    position_value = getattr(position, "value", None)
    if position_value is None:
        return 0.0
    return float(position_value) / float(total_value)


def current_position_amount(context, security):
    position = context.portfolio.positions.get(security)
    if position is None:
        return 0
    amount = getattr(position, "total_amount", None)
    if amount is None:
        return 0
    return int(amount)


def should_reduce_existing_position(current_weight, target_weight):
    if current_weight <= 0 or target_weight >= current_weight:
        return False
    return target_weight <= current_weight * 0.75 or (current_weight - target_weight) >= 0.05


def refresh_position_meta_from_analysis(security, analysis):
    if analysis is None:
        return
    meta = g.position_meta.get(security)
    if meta is None:
        return
    close_price = analysis.get("close")
    if close_price in (None, 0):
        return
    if meta.get("entry_price") in (None, 0):
        meta["entry_price"] = float(close_price)
    peak_price = meta.get("peak_price")
    meta["peak_price"] = float(close_price) if peak_price in (None, 0) else builtins.max(float(peak_price), float(close_price))


def should_take_profit_protection(security, analysis):
    meta = g.position_meta.get(security, {})
    entry_price = meta.get("entry_price")
    peak_price = meta.get("peak_price")
    close_price = analysis.get("close")
    if entry_price in (None, 0) or peak_price in (None, 0) or close_price in (None, 0):
        return False
    current_return = float(close_price) / float(entry_price) - 1.0
    peak_return = float(peak_price) / float(entry_price) - 1.0
    atr_pct = analysis.get("atr_pct") or 0.0
    activation_threshold = builtins.max(
        getattr(g, "profit_protect_min_gain", 0.08),
        float(atr_pct) * getattr(g, "profit_protect_activation_atr", 2.5),
    )
    retrace_threshold = builtins.max(
        getattr(g, "profit_protect_retrace", 0.04),
        float(atr_pct) * getattr(g, "profit_protect_retrace_atr", 1.3),
    )
    return peak_return >= activation_threshold and (peak_return - current_return) >= retrace_threshold


def should_force_disaster_stop(security, analysis):
    meta = g.position_meta.get(security, {})
    entry_price = meta.get("entry_price")
    close_price = analysis.get("close")
    if entry_price in (None, 0) or close_price in (None, 0):
        return False
    current_return = float(close_price) / float(entry_price) - 1.0
    atr_pct = analysis.get("atr_pct") or 0.0
    stop_threshold = builtins.max(
        getattr(g, "hard_stop_loss", 0.09),
        float(atr_pct) * getattr(g, "hard_stop_atr", 2.4),
    )
    return current_return <= -stop_threshold


def volatility_adjusted_weight(base_weight, analysis):
    if base_weight <= 0:
        return 0.0
    atr_pct = analysis.get("atr_pct")
    if atr_pct in (None, 0):
        return base_weight
    target_atr_pct = getattr(g, "atr_position_base", 0.035)
    floor_ratio = getattr(g, "atr_position_floor", 0.60)
    ceiling_ratio = getattr(g, "atr_position_ceiling", 1.15)
    scale = clip_float(target_atr_pct / float(atr_pct), floor_ratio, ceiling_ratio)
    return base_weight * scale


def is_order_value_feasible(security, analysis, current_data, target_weight, total_value):
    if target_weight <= 0 or total_value <= 0:
        return False
    security_data = current_data[security]
    price = reference_price_for_order(security_data, analysis)
    if price in (None, 0):
        return False
    min_lot_value = price * 100
    return total_value * target_weight >= min_lot_value


def target_shares_for_weight(total_value, target_weight, price):
    if total_value <= 0 or target_weight <= 0 or price in (None, 0):
        return 0
    raw_shares = int((float(total_value) * float(target_weight)) / float(price))
    return int(raw_shares / 100) * 100


def place_target_weight_order(context, security, target_weight, current_data, analysis):
    if target_weight <= 0:
        order_target(security, 0)
        return True
    security_data = current_data[security]
    price = reference_price_for_order(security_data, analysis or {})
    target_shares = target_shares_for_weight(context.portfolio.total_value, target_weight, price)
    if target_shares < 100:
        return False
    current_amount = current_position_amount(context, security)
    if abs(target_shares - current_amount) < 100:
        return False
    order_target(security, target_shares)
    return True


def is_at_high_limit(security_data):
    last_price = getattr(security_data, "last_price", None)
    if last_price in (None, 0):
        last_price = getattr(security_data, "day_open", None)
    high_limit = getattr(security_data, "high_limit", None)
    return last_price is not None and high_limit is not None and last_price >= high_limit


def is_at_low_limit(security_data):
    last_price = getattr(security_data, "last_price", None)
    if last_price in (None, 0):
        last_price = getattr(security_data, "day_open", None)
    low_limit = getattr(security_data, "low_limit", None)
    return last_price is not None and low_limit is not None and last_price <= low_limit


def decide_conclusion(
    weekly_trend,
    structure_type,
    structure_score,
    breakout_quality,
    pullback_quality,
    p_continue_10d,
    p_fail_5d,
    risk_score,
    relative_strength_label,
    close_to_ma60,
    ma60_slope,
):
    if p_fail_5d >= 0.65 or risk_score >= 80:
        return "defense_first"
    if close_to_ma60 < 0 and ma60_slope < 0 and p_continue_10d < 0.62:
        return "defense_first"
    if relative_strength_label == "明显弱于基准" and breakout_quality != "valid":
        return "defense_first"

    if (
        weekly_trend == "up"
        and structure_type in ("trend_continue", "breakout_pullback")
        and structure_score >= 60
        and breakout_quality == "valid"
        and pullback_quality in ("healthy", "neutral")
        and p_continue_10d >= 0.58
        and p_fail_5d <= 0.45
        and relative_strength_label in ("显著强于基准", "略强于基准", "与基准大体同步")
    ):
        return "high_quality_continuation"

    if (
        structure_score >= 50
        and breakout_quality in ("valid", "suspicious")
        and p_continue_10d >= 0.52
        and risk_score <= 72
    ):
        return "confirmation_needed"

    if relative_strength_label in ("显著强于基准", "略强于基准") and risk_score <= 65 and breakout_quality == "valid":
        return "momentum_but_heavy_overhead"

    return "defense_first"


def derive_weekly_trend(close_to_ma20, ma_slope):
    if close_to_ma20 is None or ma_slope is None:
        return "range"
    if close_to_ma20 > 0 and ma_slope > 0:
        return "up"
    if close_to_ma20 < 0 and ma_slope < 0:
        return "down"
    return "range"


def derive_daily_state(return_1d, return_5d, close_to_ma20):
    if return_5d is None or close_to_ma20 is None:
        return "transition"
    if return_5d > 0.03 and close_to_ma20 > 0:
        return "trend"
    if return_5d < -0.03:
        return "exhaustion"
    if return_1d is not None and return_1d > 0:
        return "rebound"
    return "consolidation"


def derive_structure_type(weekly_trend, daily_state, close_to_recent_high):
    if weekly_trend == "up" and daily_state == "trend":
        return "trend_continue"
    if close_to_recent_high is not None and close_to_recent_high > -0.02:
        return "breakout_pullback"
    if daily_state == "rebound":
        return "weak_rebound"
    return "range_pivot"


def derive_volume_state(volume_ratio):
    if volume_ratio is None:
        return "neutral"
    if volume_ratio > 1.2:
        return "expand"
    if volume_ratio < 0.8:
        return "contract"
    return "neutral"


def derive_breakout_quality(close_to_recent_high, volume_state):
    if close_to_recent_high is None:
        return "unknown"
    if close_to_recent_high > -0.01 and volume_state == "expand":
        return "valid"
    if close_to_recent_high > -0.03:
        return "suspicious"
    return "invalid"


def derive_pullback_quality(close_to_ma20):
    if close_to_ma20 is None:
        return "unknown"
    if close_to_ma20 > -0.01:
        return "healthy"
    if close_to_ma20 > -0.03:
        return "neutral"
    return "damaged"


def derive_price_action_quality(return_1d, volatility_10d):
    if return_1d is None or volatility_10d is None:
        return "unknown"
    if return_1d > 0 and volatility_10d < 0.05:
        return "healthy"
    if return_1d < 0 and volatility_10d > 0.06:
        return "exhausting"
    return "divergent"


def derive_chip_pressure(close_to_recent_high):
    if close_to_recent_high is None:
        return "unknown"
    if close_to_recent_high > -0.02:
        return "low"
    if close_to_recent_high > -0.08:
        return "mid"
    return "high"


def derive_chip_support(close_to_recent_low):
    if close_to_recent_low is None:
        return "unknown"
    if close_to_recent_low > 0.12:
        return "high"
    if close_to_recent_low > 0.05:
        return "mid"
    return "low"


def derive_structure_score(close_to_ma20, return_5d):
    score = 50.0
    if close_to_ma20 is not None:
        score += builtins.max(-20.0, builtins.min(20.0, close_to_ma20 * 200))
    if return_5d is not None:
        score += builtins.max(-20.0, builtins.min(20.0, return_5d * 150))
    return clip_int(score, 0, 100)


def derive_risk_score(volatility_10d, close_to_ma20, breakout_quality):
    score = 40.0
    if volatility_10d is not None:
        score += builtins.min(35.0, volatility_10d * 400)
    if close_to_ma20 is not None and close_to_ma20 < 0:
        score += builtins.min(20.0, abs(close_to_ma20) * 150)
    if breakout_quality == "invalid":
        score += 15.0
    return clip_int(score, 0, 100)


def derive_invalidation_level(recent_low_20, ma20):
    if recent_low_20 is None and ma20 is None:
        return None
    if recent_low_20 is None:
        return ma20
    if ma20 is None:
        return recent_low_20
    return builtins.min(recent_low_20, ma20)


def derive_invalidation_pressure(close_price, invalidation_level):
    if close_price in (None, 0) or invalidation_level in (None, 0):
        return "safe"
    distance = float(close_price) / float(invalidation_level) - 1.0
    if distance <= 0:
        return "breach"
    if distance <= 0.015:
        return "danger"
    if distance <= 0.04:
        return "warning"
    return "safe"


def invalidation_adjusted_weight(base_weight, pressure, profile):
    if base_weight <= 0:
        return 0.0
    if pressure == "breach":
        return 0.0
    if pressure == "danger":
        return builtins.min(base_weight, profile["danger_weight"])
    if pressure == "warning":
        return builtins.min(base_weight, profile["trim_weight"])
    return base_weight


def derive_relative_strength_label(excess_return_20d, excess_return_5d):
    if excess_return_20d > 0.08 and excess_return_5d > 0.03:
        return "显著强于基准"
    if excess_return_20d > 0.02 and excess_return_5d > 0:
        return "略强于基准"
    if excess_return_20d < -0.05 and excess_return_5d < 0:
        return "明显弱于基准"
    if excess_return_20d < -0.01:
        return "略弱于基准"
    return "与基准大体同步"


def estimate_continue_probability(
    weekly_trend,
    structure_type,
    structure_score,
    breakout_quality,
    pullback_quality,
    volume_ratio,
    excess_return_20d,
    risk_score,
):
    score = 0.42
    score += (structure_score - 55.0) / 220.0
    score += builtins.max(-0.05, builtins.min(0.05, excess_return_20d * 0.8))
    if weekly_trend == "up":
        score += 0.04
    elif weekly_trend == "down":
        score -= 0.08
    if structure_type == "trend_continue":
        score += 0.05
    elif structure_type == "breakout_pullback":
        score += 0.02
    if breakout_quality == "valid":
        score += 0.05
    elif breakout_quality == "suspicious":
        score -= 0.04
    elif breakout_quality == "invalid":
        score -= 0.10
    if pullback_quality == "healthy":
        score += 0.03
    elif pullback_quality == "damaged":
        score -= 0.05
    if volume_ratio > 1.2:
        score += 0.02
    elif volume_ratio < 0.85:
        score -= 0.03
    score -= builtins.max(0.0, risk_score - 45.0) / 180.0
    return clip_float(score, 0.05, 0.85)


def estimate_fail_probability(risk_score, breakout_quality, price_action_quality, close_to_ma20):
    score = 0.12 + builtins.max(0.0, risk_score - 40.0) / 120.0
    if breakout_quality == "invalid":
        score += 0.12
    elif breakout_quality == "suspicious":
        score += 0.05
    if price_action_quality == "exhausting":
        score += 0.08
    elif price_action_quality == "divergent":
        score += 0.03
    if close_to_ma20 is not None and close_to_ma20 < -0.03:
        score += 0.08
    elif close_to_ma20 is not None and close_to_ma20 < -0.01:
        score += 0.04
    return clip_float(score, 0.05, 0.90)


def pct_change(values, periods):
    if len(values) <= periods:
        return 0.0
    prev = values[-periods - 1]
    curr = values[-1]
    if prev in (None, 0):
        return 0.0
    return curr / prev - 1.0


def realized_volatility(closes):
    returns = []
    for i in range(1, len(closes)):
        prev_close = closes[i - 1]
        close = closes[i]
        if prev_close in (None, 0):
            continue
        returns.append(close / prev_close - 1.0)
    if not returns:
        return 0.0
    avg = builtins.sum(returns) / float(len(returns))
    variance = builtins.sum((item - avg) * (item - avg) for item in returns) / float(len(returns))
    return math.sqrt(builtins.max(0.0, variance))


def average_true_range(highs, lows, closes, period=20):
    if not highs or not lows or not closes:
        return 0.0
    true_ranges = []
    for index in range(1, len(closes)):
        high = highs[index]
        low = lows[index]
        prev_close = closes[index - 1]
        true_range = builtins.max(
            high - low,
            abs(high - prev_close),
            abs(low - prev_close),
        )
        true_ranges.append(true_range)
    if not true_ranges:
        return 0.0
    window = true_ranges[-period:]
    return mean(window)


def position_cost_basis(position):
    if position is None:
        return None
    for attr in ("avg_cost", "cost_basis", "price"):
        value = getattr(position, attr, None)
        if value not in (None, 0):
            return float(value)
    return None


def position_latest_price(position):
    if position is None:
        return None
    for attr in ("price", "avg_cost", "cost_basis"):
        value = getattr(position, attr, None)
        if value not in (None, 0):
            return float(value)
    return None


def mean(values):
    valid = [value for value in values if value is not None]
    if not valid:
        return 0.0
    return builtins.sum(valid) / float(len(valid))


def ratio(a, b):
    if b in (None, 0):
        return 1.0
    return float(a) / float(b)


def clip_int(value, low, high):
    return int(builtins.max(low, builtins.min(high, round(value))))


def clip_float(value, low, high):
    return builtins.max(low, builtins.min(high, value))
