"""Dated cohort feedback; adapted from Vibe-AStock d7a0e56 (Apache-2.0).

The original seven predicates and pooled statistics are retained. Rolling
windows, prior-session highest boards and broken boards use the same reducer.
All returns here are percentage points, never simulated trading fills.
"""
from __future__ import annotations

from collections import Counter
from statistics import mean, median

from shilun.review.sources import at_limit, finite_number
from shilun.review.board_price_quality import price_coverage, price_exceptions

VERSION = "board-effects-6-complete-limit-up-cohort"
GROUPS = (
    ("first", "首板打板", "昨日首次涨停且最终封板时间有效，观察次日反馈"),
    ("early", "首板·早封", "昨日首板且10:00前最终封板"),
    ("late", "首板·尾盘封", "昨日首板且14:30后最终封板"),
    ("industry", "首板·涨停数前二行业", "昨日首板且属于昨日涨停数最多的两个行业"),
    ("continuation", "连板接力", "昨日已2板及以上的个股"),
    ("three_plus", "三板及以上", "原版高标接力：昨日连续涨停数≥3，固定板数口径"),
    ("all", "全体涨停", "昨日完整收盘涨停名单，含一字板、回封股及封板时间缺失股"),
    ("highest", "最高板效应", "昨日非ST主板完整涨停池中的最高连板，包含并列最高"),
    ("broken", "炸板次日反馈", "昨日触及涨停但收盘未封住；回封股仍属于封板组"),
)
from shilun.review.market_mood import regime_for_mood

REGIMES = ("情绪强", "情绪中", "情绪弱", "环境未覆盖")
SEAL_BUCKETS = (("开盘秒板", "093500"), ("9:35—10:00", "100000"),
                ("10:00—11:00", "110000"), ("11:00—14:00", "140000"), ("14:00后", "150500"))


def seal_time(value):
    if value in (None, "", "—"):
        return None
    value = str(value).replace(":", "").split(".")[0].zfill(6)
    if len(value) != 6 or not value.isdigit() or not ("090000" <= value <= "150500"):
        return None
    return value


def return_pct(price, base):
    price, base = finite_number(price), finite_number(base)
    return (price / base - 1) * 100 if price is not None and price > 0 and base and base > 0 else None


def stats(rows, field="ret"):
    observed = [(r, finite_number(r.get(field))) for r in rows]
    valid = [(r, v) for r, v in observed if v is not None]
    values = [v for _, v in valid]
    limit_states = [at_limit(r.get("close"), r.get("limit_price")) for r, _ in valid]
    known = [v for v in limit_states if v is not None]
    return {
        "sample": len(values), "sample_total": len(rows),
        "stock_count": len({r["code"] for r, _ in valid}),
        "day_count": len({r["date"] for r, _ in valid}),
        "avg": round(mean(values), 2) if values else None,
        "median": round(median(values), 2) if values else None,
        "win_rate": round(sum(v > 0 for v in values) / len(values), 3) if values else None,
        "best": round(max(values), 2) if values else None,
        "worst": round(min(values), 2) if values else None,
        "limit_up_rate": round(sum(known) / len(known), 3) if known else None,
        "limit_up_sample": len(known),
    }


def _selected(key, row, top_industries):
    boards, time = row.get("prev_boards"), seal_time(row.get("seal_time"))
    return {
        "first": boards == 1,
        "early": boards == 1 and time is not None and time <= "100000",
        "late": boards == 1 and time is not None and time >= "143000",
        "industry": boards == 1 and row.get("sector") in top_industries,
        "continuation": boards is not None and boards >= 2,
        "three_plus": boards is not None and boards >= 3,
        "all": True,
        "highest": row.get("is_highest") is True,
        "broken": True,
    }[key]


def build_effects(*, dates, per_day, broken_by_day=None, highest_by_day=None, heat=None, highest_ready=None, requested=30, sources=None, moods=None, industry_cohorts=None):
    """`dates` includes one preceding session; missing dates remain in windows.

    `per_day` and `broken_by_day` map feedback dates to immutable prior cohorts.
    A present [] is verified empty; an absent key is unknown. A row with missing
    next-day price remains part of its cohort and cannot change its identity.
    """
    if requested not in (20, 30):
        raise ValueError("打板效应仅支持20或30个交易日")
    sequence = sorted(set(dates))
    pairs = dict(zip(sequence[1:], sequence))
    window = sequence[1:][-requested:]
    recent_dates, prior_dates = window[-7:], window[:-7]
    broken_by_day, heat = broken_by_day or {}, heat or {}
    highest_ready = highest_ready or set()
    normalized, broken, excluded = {}, {}, {}
    for source, output in ((per_day, normalized), (broken_by_day, broken)):
        for day in window:
            if day not in source:
                continue
            seen, output[day] = set(), []
            for raw in source[day]:
                if raw["code"] in seen:
                    raise ValueError("同一日期股票重复，不能改变统计权重")
                seen.add(raw["code"])
                if output is normalized and seal_time(raw.get("seal_time")) is None:
                    excluded[(day, raw["code"])] = {"date": day, "cohort_date": pairs[day], "code": raw["code"], "name": raw.get("name"), "reason": "最终封板时间未知或无效"}
                output[day].append({**raw, "date": day, "cohort_date": pairs[day]})
    moods = moods or {}
    regimes = {day: regime_for_mood(moods.get(pairs[day])) for day in window}
    regime_dates = {}
    for day in window:
        mood = moods.get(pairs[day]) or {}
        regime_dates[day] = {'cohort_date': pairs[day], 'regime': regimes[day],
            'code': mood.get('code'), 'label': mood.get('label'), 'score': mood.get('score'),
            'source': mood.get('source') or 'P2共享情绪引擎', 'rules_version': mood.get('rules_version'),
            'missing_inputs': mood.get('missing_inputs') or [],
            'reason': (mood.get('reason') or '该日尚无当前版本情绪归档，需补算') if regimes[day] == '环境未覆盖' else ''}
    industry_inputs = normalized if industry_cohorts is None else industry_cohorts
    industry_status = {}
    for day in window:
        cohort = industry_inputs.get(day)
        if cohort is not None and len({r['code'] for r in cohort}) != len(cohort):
            raise ValueError('行业排名涨停池出现重复代码')
        missing = [r['code'] for r in (cohort or []) if not r.get('sector')
                   or r.get('classification_status', 'ready') != 'ready'
                   or r.get('taxonomy_date', pairs[day]) != pairs[day]]
        counts = Counter(r['sector'] for r in (cohort or []) if r.get('sector'))
        # Legacy fixtures retain their stable source order for Vibe comparison.
        top = sorted(counts, key=lambda s: -counts[s] if industry_cohorts is None else (-counts[s], s))[:2]
        names = {r.get('sector'): r.get('sector_name') or r.get('sector') for r in (cohort or [])}
        industry_status[day] = {'cohort_date': pairs[day], 'pool_count': len(cohort or []),
            'mapped_count': len(cohort or [])-len(missing), 'missing_codes': missing,
            'status': 'pool_missing' if cohort is None else 'classification_missing' if missing else 'ready',
            'reason': '首板日涨停池待补齐' if cohort is None else '首板日行业分类待补齐' if missing else '',
            'top_industries': [{'code': s, 'name': names[s], 'limit_up_count': counts[s]} for s in top] if not missing and cohort is not None else []}
    results = []
    # The full prior-session pool also feeds industry classification. A
    # provider's feedback subset must never redefine the all-limit-up cohort.
    population = normalized if industry_cohorts is None else {
        d: [{**r, 'date': d, 'cohort_date': pairs[d]} for r in rows]
        for d, rows in industry_cohorts.items() if d in pairs}
    baseline = stats([r for rows in population.values() for r in rows])
    for key, name, description in GROUPS:
        source = broken if key == "broken" else normalized
        if key == "all":
            source = population
        if key == 'industry' and industry_cohorts is not None:
            source = {d: [{**r, 'date': d, 'cohort_date': pairs[d]} for r in rows
                          if seal_time(r.get('seal_time')) is not None]
                      for d, rows in industry_cohorts.items() if d in pairs}
        if key == "highest" and highest_by_day is not None:
            source = {d: [{**r, "date": d, "cohort_date": pairs[d]} for r in rows]
                      for d, rows in highest_by_day.items() if d in pairs}
            if any(len({r["code"] for r in rows}) != len(rows) for rows in source.values()):
                raise ValueError("最高板名单出现重复代码")
            for day, rows in source.items():
                for r in rows:
                    if seal_time(r.get("seal_time")) is None:
                        excluded[(day, r["code"])] = {"date": day, "cohort_date": pairs[day], "code": r["code"], "name": r.get("name"), "reason": "最终封板时间未知或无效"}
                source[day] = [r for r in rows if seal_time(r.get("seal_time")) is not None]
        by_date = {}
        for day in window:
            if day not in source or key == "highest" and day not in highest_ready:
                continue
            if key == 'industry' and industry_status[day]['status'] != 'ready':
                continue
            top = [r['code'] for r in industry_status[day]['top_industries']]
            by_date[day] = [r for r in source[day] if _selected(key, r, top)
                            and (key in ("all", "broken") or seal_time(r.get("seal_time")) is not None)]
        rows = [r for day_rows in by_date.values() for r in day_rows]
        daily = []
        for i, day in enumerate(window):
            rolling_dates = window[max(0, i-6):i+1]
            roll = stats([r for d in rolling_dates for r in by_date.get(d, [])])
            roll["covered_days"] = sum(d in by_date for d in rolling_dates)
            roll["expected_days"] = len(rolling_dates)
            daily.append({"date": day, "cohort_date": pairs[day], "available": day in by_date,
                          **stats(by_date.get(day, [])), "rolling": roll,
                          'price_coverage': price_coverage(by_date.get(day, [])),
                          **({'industry_coverage': industry_status[day], 'missing_reason': industry_status[day]['reason']} if key == 'industry' else {})})
        overall = stats(rows)
        recent = stats([r for d in recent_dates for r in by_date.get(d, [])])
        prior = stats([r for d in prior_dates for r in by_date.get(d, [])])
        delta = {k: round(recent[k]-prior[k], 3) if recent[k] is not None and prior[k] is not None else None for k in ("avg", "median", "win_rate")}
        results.append({"id": key, "name": name, "description": description,
            "overall": overall, "recent": recent, "prior": prior, "change": delta,
            'price_coverage': price_coverage(rows),
            "excess": round(overall["avg"]-baseline["avg"], 2) if overall["avg"] is not None and baseline["avg"] is not None and key not in ("all", "broken") else None,
            "comparison_role": "population" if key == "all" else "independent_risk" if key == "broken" else "relative_to_all",
            "by_regime": {regime: stats([r for r in rows if regimes[r["date"]] == regime]) for regime in REGIMES},
            "regime_coverage": {'classified': sum(regimes[r['date']] != '环境未覆盖' for r in rows),
                                'total': len(rows), 'missing_cohort_dates': sorted({r['cohort_date'] for r in rows if regimes[r['date']] == '环境未覆盖'})},
            "segments": {field: stats(rows, field) for field in ("open_ret", "intraday_ret", "limit_cost_ret")},
            "daily": daily, "rows": rows,
            **({'industry_note': '按首板发生日的东方财富三级行业归属，从当日完整涨停池选出涨停数最多的两个行业（同数按行业代码排序），再观察其中首板股的次日反馈。缺行业分类不记为无样本。历史分类为按日期事后重建，未经当时发布时点验证。'} if key == 'industry' else {}),
            "coverage": {"days": len(by_date), "expected": len(window),
                         "missing": [d for d in window if d not in by_date],
                         "recent_days": sum(d in by_date for d in recent_dates),
                         "recent_expected": len(recent_dates),
                         "prior_days": sum(d in by_date for d in prior_dates),
                         "prior_expected": len(prior_dates)},
        })
    buckets = {name: [] for name, _ in SEAL_BUCKETS}
    for row in results[0]["rows"]:
        time = seal_time(row.get("seal_time"))
        if not time:
            continue
        for name, upper in SEAL_BUCKETS:
            if time <= upper:
                buckets[name].append(row)
                break
    curve = [{"bucket": name, **stats(rows)} for name, rows in buckets.items()]
    used = sorted(d for d, rows in normalized.items() if not rows or any(num is not None for num in (finite_number(r.get("ret")) for r in rows)))
    return {"available": bool(used), "rule_version": VERSION, "layer": "market_phenomenon",
            "days_requested": requested, "days_used": len(used),
            "calendar_days": len(window), "calendar_shortfall": max(0, requested-len(window)),
            "date_from": used[0] if used else None, "date_to": used[-1] if used else None,
            "window_from": window[0] if window else None, "window_to": window[-1] if window else None,
            "missing_days": [d for d in window if d not in used],
            "regime_cuts": {"low": 30, "high": 60},
            "regime_dates": regime_dates,
            "regime_source": "P2收盘市场情绪：冰点(<30)=弱，普通(30–59)=中，活跃/亢奋(≥60)=强；按昨日名单形成日分组，不随窗口重分位。",
            "mood_missing_dates": sorted({pairs[d] for d in window if regimes[d] == "环境未覆盖"}), "strategies": results,
            "seal_curve": curve, "sources": sources or [],
            'price_exceptions': price_exceptions(results),
            "excluded_samples": list(excluded.values()), "excluded_count": len(excluded),
            "quality_note": f"{len(excluded)}条最终封板时间未知或无效的样本仍保留在全体涨停中；需要封板时间的细分组不计入。炸板未回封另组统计。" if excluded else None,
            "sample_caveat": "全体涨停为昨日完整收盘涨停名单，包含一字股、回封股和封板时间缺失股；缺收益保留在名单覆盖中，不计零。细分封板组沿用有效封板时间口径；炸板未回封为独立风险对照。收益不含费用或成交模拟。情绪分组采用昨日名单形成日的P2收盘状态；属于收盘后条件统计，不代表盘中买入时已知。",
            "reason": "" if used else "所选窗口尚无已归档的次日反馈，请刷新数据；缺失日不会补成零。"}
