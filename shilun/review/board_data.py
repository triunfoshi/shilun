"""Shared first-board/effects evidence adapter. GETs read; refreshes materialize.

Source mapping adapted from Vibe-AStock d7a0e56, Apache-2.0 (licenses/).
Local Vibe archives are optional migration input, never executable dependencies.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, time, timedelta
from hashlib import sha256
import json
import os
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

from shilun.review.board_effects import build_effects, return_pct, seal_time
from shilun.review.board_auction import load_opening_auction, nonnegative_amount, sync_first_board_auction
from shilun.review.dated_pools import COLLECTION, fetch_previous, normalize_previous, stock_code, sync_heat_summaries
from shilun.review.live_emotion import _POOL_UT
from shilun.review.sources import at_limit, finite_number as num, load_review_sessions

POOL_COLLECTION = "review_board_pools"
REASON_COLLECTION = "review_board_reasons"
CHINA = ZoneInfo("Asia/Shanghai")


def today():
    return datetime.now(CHINA).date().isoformat()


def settled_target(value=None):
    now = datetime.now(CHINA)
    target = date.fromisoformat(value) if value else now.date()
    if target > now.date():
        raise ValueError("不能读取未来日期")
    if target == now.date() and now.time() < time(15, 10):
        target -= timedelta(days=1)
    return target.isoformat()


def _day(value):
    value = str(value or "").replace("-", "")[:8]
    return f"{value[:4]}-{value[4:6]}-{value[6:]}" if len(value) == 8 and value.isdigit() else ""


def _code(value):
    text = str(value or "").split(".")[0]
    return stock_code(text).split(".")[0]


def primary_board(row):
    code = row["code"]
    return code.startswith(("60", "000", "001", "002", "003")) and "ST" not in row.get("name", "").upper()


def normalize_pool(raw, *, eastmoney=False):
    rows, seen = [], set()
    for item in raw:
        code = _code(item.get("c") if eastmoney else item.get("ticker") or item.get("ts_code"))
        if code in seen:
            raise ValueError("涨停池出现重复代码")
        seen.add(code)
        def value(remote, local):
            return item.get(remote if eastmoney else local)
        boards = num(value("lbc", "consecutive_count"))
        price = num(value("p", "close"))
        rows.append({"code": code, "ticker": stock_code(code), "name": value("n", "name") or code,
            "price": price / 1000 if eastmoney and price is not None else price,
            "pct": num(value("zdp", "pct_chg")), "amount": num(item.get("amount")),
            "seal_amount": nonnegative_amount(value("fund", "limit_up_fund")),
            "float_cap": num(value("ltsz", "float_market_value")),
            "industry": value("hybk", "industry") or "", "reason": item.get("limit_reason") or item.get("reason") or "",
            "seal_time": seal_time(value("fbt", "first_seal_time")),
            "last_seal_time": seal_time(value("lbt", "last_seal_time")),
            "break_count": num(value("zbc", "broken_count")),
            # Preserve discontinuous N-day/M-board facts for the Vibe event tree.
            "zt_stat": item.get("zttj") if eastmoney else item.get("zt_stat") or item.get("zttj") or item.get("limit_up_stat"),
            "boards": int(boards) if boards is not None and boards >= 1 and boards == int(boards) else None,
            "up_limit": num(item.get("ztp")) / 1000 if eastmoney and num(item.get("ztp")) else None})
    return rows


def fetch_pool(day, kind):
    endpoint = "getTopicZTPool" if kind == "up" else "getTopicZBPool"
    with requests.Session() as session:
        session.trust_env = False
        response = session.get(f"https://push2ex.eastmoney.com/{endpoint}",
            params={"ut": _POOL_UT, "dpt": "wz.ztzt", "Pageindex": 0, "pagesize": 10000,
                    "sort": "fbt:asc", "date": day.replace("-", "")},
            headers={"User-Agent": "Mozilla/5.0", "Referer": "https://quote.eastmoney.com/"}, timeout=8)
        response.raise_for_status()
        payload = response.json()
    data = payload.get("data") or {}
    raw = data.get("pool")
    if payload.get("rc") != 0 or str(data.get("qdate")) != day.replace("-", "") or not isinstance(raw, list) or data.get("tc") != len(raw):
        raise ValueError("涨停池日期或完整性未通过核验")
    return {"date": day, "kind": kind, "stocks": normalize_pool(raw, eastmoney=True),
            "source": "eastmoney_dated_pool", "fetched_at": datetime.now(CHINA).isoformat(),
            "field_signature": sorted({k for r in raw for k in r}), "complete": True}


def fetch_reasons(day):
    """Public THS attribution, validating every row's event timestamp (Vibe source)."""
    reasons, seen, total = {}, set(), None
    with requests.Session() as session:
        session.trust_env = False
        for page in range(1, 5):
            response = session.get("https://data.10jqka.com.cn/dataapi/limit_up/limit_up_pool",
                params={"page": page, "limit": 200,
                        "field": "199112,10,9001,330323,330324,330325,9002,330329,133971,133970,1968584,3475914,9003,9004",
                        "filter": "HS,GEM2STAR", "order_field": "330324", "order_type": "0", "date": day.replace("-", "")},
                headers={"User-Agent": "Mozilla/5.0"}, timeout=8)
            response.raise_for_status()
            envelope = response.json()
            if envelope.get("status_code") != 0:
                raise ValueError("涨停原因源未返回有效数据")
            data = envelope["data"]
            count = int(data["page"]["total"])
            if total is not None and count != total:
                raise ValueError("原因名单在分页期间变化")
            total = count
            for row in data["info"]:
                code = _code(row.get("code"))
                stamp = num(row.get("first_limit_up_time"))
                if code in seen or stamp is None or datetime.fromtimestamp(stamp, CHINA).date().isoformat() != day:
                    raise ValueError("涨停原因日期或代码未通过核验")
                seen.add(code)
                reason = str(row.get("reason_type") or "").strip()
                if reason and reason not in ("-", "暂无"):
                    from shilun.review.vibe_shortline import clean_reason
                    reasons[code] = clean_reason(reason)
            if len(seen) == total:
                if not seen:
                    raise ValueError("原因源无可核验日期的记录")
                return {"date": day, "reasons": reasons, "source": "ths_dated_limit_reasons",
                        "source_note": f"同花顺涨停揭秘；{day}；原因覆盖{len(reasons)}/{len(seen)}。源站编辑归因，不代表公司确认；不含北交所。",
                        "fetched_at": datetime.now(CHINA).isoformat()}
    raise ValueError("涨停原因分页不完整")


def sync_reasons(store, day):
    """Materialize dated editorial reasons; retry uncovered stocks on refresh."""
    collection = store.collection(REASON_COLLECTION)
    cached = collection.find_one({'date': day}, {'_id': 0}) or {}
    pool = BoardResearchData(store).pools([day]).get(day, {})
    required = {r['code'] for r in pool.get('stocks', [])}
    existing = cached.get('reasons', {})
    if existing and required and required <= existing.keys():
        return {'date': day, 'dataset': 'reasons', 'status': 'ok', 'cached': True}
    try:
        result = fetch_reasons(day)
        result['reasons'] = {**existing, **result['reasons']}
        collection.update_one({'date': day}, {'$set': result}, upsert=True)
        return {'date': day, 'dataset': 'reasons', 'status': 'ok', 'count': len(result['reasons'])}
    except Exception as error:
        from shilun.jobs.pipeline_state import error_details
        return {'date': day, 'dataset': 'reasons', 'status': 'unavailable', **error_details(error)}


def fetch_historical_pool(client, day, kind):
    """Tushare dated U/Z lists. Validate date/type before dropping the envelope."""
    flag = "U" if kind == "up" else "Z"
    frame = client._call_api("limit_list_d", trade_date=day.replace("-", ""), limit_type=flag, limit=1000)
    if frame.empty or len(frame) >= 1000:
        raise ValueError("历史涨停池为空或可能被截断，不能确认完整性")
    raw = frame.to_dict("records")
    if any(_day(r.get("trade_date")) != day or r.get("limit") != flag for r in raw):
        raise ValueError("历史涨停池日期或类型不匹配")
    mapped = [{**r, "ticker": r["ts_code"], "consecutive_count": r.get("limit_times"),
               "first_seal_time": r.get("first_time"), "last_seal_time": r.get("last_time"),
               "broken_count": r.get("open_times"), "float_market_value": r.get("float_mv"),
               "limit_up_fund": r.get("fd_amount")} for r in raw]
    return {"date": day, "kind": kind, "stocks": normalize_pool(mapped), "complete": True,
            "source": "tushare_limit_list_d", "fetched_at": datetime.now(CHINA).isoformat(),
            "field_signature": sorted(frame.columns), "scope": "沪深非ST；与东财池范围可能不同"}


def needed_prices(dates, previous, ups, broken):
    needed = {day: {r["ticker"] for r in previous.get(day, {}).get("rows", [])} for day in dates}
    for i, day in enumerate(dates):
        for pool_day in dates[max(0,i-1):i+1]:
            for documents in (ups, broken):
                needed[day].update(r["ticker"] for r in documents.get(pool_day, {}).get("stocks", []))
    return needed


class BoardResearchData:
    def __init__(self, store):
        self.store = store
        self.cache_root = Path(os.environ.get("SHILUN_VIBE_CACHE_ROOT", str(Path.home()/".duanxian-agents/cache")))

    def sessions(self, target, days):
        start = (date.fromisoformat(target)-timedelta(days=days*3+20)).isoformat()
        return load_review_sessions(self.store, target, start)[-(days+1):]

    def _latest(self):
        target = settled_target()
        row = self.store.collection("trade_calendar").find_one(
            {"cal_date": {"$lte": target.replace("-", "")}, "is_open": {"$in": [1, "1", True]}},
            {"_id": 0, "cal_date": 1}, sort=[("cal_date", -1)])
        return _day(row.get("cal_date")) if row else target

    def pools(self, dates, kind="up", *, settled=False):
        documents = {r["date"]: r for r in self.store.collection(POOL_COLLECTION).find(
            {"date": {"$in": dates}, "kind": kind, "complete": True,
             **({"snapshot_mode": {"$ne": "live"}} if settled else {})}, {"_id": 0})}
        if kind == "up":
            grouped = {}
            for row in self.store.collection("limit_up_pool").find(
                    {"trade_date": {"$in": dates+[d.replace("-", "") for d in dates]}}, {"_id": 0}):
                day = _day(row.get("trade_date"))
                grouped.setdefault(day, []).append(row)
            for day, rows in grouped.items():
                if day not in documents:
                    state = (self.store.find_sync_state(dataset="eastmoney_limit_up_pool", scope=day.replace("-", ""))
                             or self.store.find_sync_state(dataset="akshare_limit_up_pool", scope=day.replace("-", "")))
                    if not state or state.get("status") != "success" or state.get("row_count") != len(rows):
                        continue
                    documents[day] = {"date": day, "kind": kind, "stocks": normalize_pool(rows),
                                      "source": "shilun_limit_up_pool", "complete": True}
                # Older normalized archives omitted the discontinuous N/M-board
                # statistic. Restore this field from the exact same-day raw
                # archive only; never substitute another session's labels.
                stats = {_code(row.get('ticker') or row.get('ts_code')):
                         row.get('zt_stat') or row.get('zttj') or row.get('limit_up_stat') for row in rows}
                for stock in documents.get(day, {}).get('stocks', []):
                    if not stock.get('zt_stat') and stats.get(stock['code']):
                        stock['zt_stat'] = stats[stock['code']]
        return documents

    def first_board(self, target=None):
        requested = target
        if not target:
            latest = self.store.collection(POOL_COLLECTION).find_one(
                {"date": {"$lte": today()}, "kind": "up", "complete": True}, {"_id": 0, "date": 1}, sort=[("date", -1)])
            target = max(latest["date"], self._latest()) if latest else self._latest()
        if target > today():
            raise ValueError("不能读取未来日期")
        document = self.pools([target]).get(target)
        if not document:
            return {"available": False, "date": target, "requested_date": requested,
                    "stocks": [], "reason": "该日期尚无完整涨停池，点击刷新首板获取；历史日期不会替换成今天。"}
        reasons = self.store.collection(REASON_COLLECTION).find_one({"date": target}, {"_id": 0}) or {}
        auction = load_opening_auction(self.store, target)
        tickers = [r["ticker"] for r in document["stocks"] if r.get("boards") == 1]
        # First-board shape checks need only this cohort and four price fields.
        # Loading every market-wide indicator document can exhaust the DB timeout.
        bars = {r["ticker"].split(".")[0]: r for r in self.store.collection("market_daily_bars").find(
            {"date": target, "ticker": {"$in": tickers}},
            {"_id": 0, "ticker": 1, "open": 1, "high": 1, "low": 1, "close": 1})} if tickers else {}
        limits = {r["ticker"].split(".")[0]: r for r in self.store.collection("daily_price_limits").find(
            {"trade_date": {"$in": [target, target.replace("-", "")]}, "ticker": {"$in": tickers}},
            {"_id": 0, "ticker": 1, "up_limit": 1})} if tickers else {}
        stocks = []
        for row in document["stocks"]:
            if row["boards"] != 1:
                continue
            bar, upper = bars.get(row["code"], {}), limits.get(row["code"], {}).get("up_limit")
            one = None
            if upper and num(bar.get('open')) is not None and num(bar.get('close')) is not None and row.get('break_count') is not None:
                one = at_limit(bar.get('open'), upper) and at_limit(bar.get('close'), upper) and row['break_count'] == 0
            elif num(bar.get("high")) is not None and num(bar.get("low")) is not None and upper:
                one = at_limit(bar.get("high"), upper) and at_limit(bar.get("low"), upper)
            reason = reasons.get("reasons", {}).get(row["code"]) or row.get("reason") or ""
            auction_amount = nonnegative_amount(auction.get("amounts", {}).get(row["code"]))
            seal_amount = nonnegative_amount(row.get("seal_amount"))
            stocks.append({**row, "reason": reason, "one_price": one,
                           "auction_amount": auction_amount,
                           "auction_amount_source": auction.get("source") if auction_amount is not None else None,
                           "seal_amount": seal_amount,
                           "seal_amount_source": (row.get("seal_amount_source") or document["source"]) if seal_amount is not None else None,
                           "seal_snapshot_mode": document.get("snapshot_mode", "settled"),
                           "reason_source": reasons.get("source", "涨停池") if reason else None})
        from shilun.market.taxonomy import classify_stocks
        stocks = classify_stocks(self.store, target, stocks)
        # An unknown auction amount must not outrank a measured zero. Seal
        # orders are the secondary key, never a substitute for auction money.
        stocks.sort(key=lambda r: (
            -(r["auction_amount"] if r["auction_amount"] is not None else -1),
            -(r["seal_amount"] if r["seal_amount"] is not None else -1),
            r.get("seal_time") or "999999", r["code"]))
        missing = sum(not r["reason"] for r in stocks)
        return {"available": True, "date": target, "requested_date": requested,
                "total_zt": len(document["stocks"]), "first_count": len(stocks), "stocks": stocks,
                "source": document["source"], "fetched_at": document.get("fetched_at"),
                "amount_coverage": {"auction": sum(r["auction_amount"] is not None for r in stocks),
                                    "seal": sum(r["seal_amount"] is not None for r in stocks), "total": len(stocks)},
                "auction_fetched_at": auction.get("fetched_at"),
                "sort_order": ["auction_amount_desc", "seal_amount_desc", "seal_time_asc", "code_asc"],
                "reason_note": f"{missing}只首板暂无对应日期的涨停原因，行业字段不代替题材。" if missing else None,
                "reason_source_note": reasons.get("source_note"), "snapshot_mode": document.get("snapshot_mode", "settled")}

    def effects(self, days=30, target=None):
        target = settled_target(target) if target else self._latest()
        dates = self.sessions(target, days)
        if len(dates) < 2:
            return build_effects(dates=dates, per_day={}, requested=days)
        previous = {r["analysis_date"]: r for r in self.store.collection(COLLECTION).find(
            {"analysis_date": {"$in": dates[1:]}}, {"_id": 0})}
        ups, broken = self.pools(dates, settled=True), self.pools(dates, "broken", settled=True)
        heat = {r["date"]: r for r in self.store.collection("review_heat_summaries").find({"date": {"$in": dates}}, {"_id": 0})}
        needed = needed_prices(dates, previous, ups, broken)
        bar_query = [{"date": d, "ticker": {"$in": sorted(c)}} for d,c in needed.items() if c]
        price_query = [{"trade_date": {"$in": [d,d.replace("-", "")]}, "ticker": {"$in": sorted(c)}} for d,c in needed.items() if c]
        bars = {(r["date"], r["ticker"].split(".")[0]): r for r in self.store.collection("market_daily_bars").find(
            {"$or": bar_query}, {"_id": 0, "date": 1, "ticker": 1, "open": 1, "close": 1, "pre_close": 1})} if bar_query else {}
        price_rows = list(self.store.collection("daily_price_limits").find(
            {"$or": price_query},
            {"_id": 0, "ticker": 1, "trade_date": 1, "up_limit": 1, "pre_close": 1})) if price_query else []
        limits = {(_day(r["trade_date"]), r["ticker"].split(".")[0]): r.get("up_limit") for r in price_rows}
        references = {(_day(r["trade_date"]), r["ticker"].split(".")[0]): num(r.get("pre_close")) for r in price_rows}
        per_day, failed, highest_ready, highest_rows, sources = {}, {}, set(), {}, []
        for prior, day in zip(dates, dates[1:]):
            up = ups.get(prior)
            primary = [r for r in up["stocks"] if primary_board(r)] if up else []
            height = max((r["boards"] or 0 for r in primary), default=0)
            high_codes = {r["code"] for r in primary if r["boards"] == height}
            if up and all(r["boards"] is not None for r in primary):
                highest_ready.add(day)
            doc = previous.get(day)
            if doc and doc.get("previous_date") == prior:
                per_day[day] = [{"code": r["ticker"].split(".")[0], "name": r.get("name"),
                    "ret": r["return_1d"]*100, "prev_boards": r["consecutive_count"],
                    "sector": r.get("industry") or "", "seal_time": r.get("previous_seal_time"),
                    "close": r["close"], "limit_price": r.get("up_limit")} for r in doc["rows"]]
                sources.append({"date": day, "source": doc.get("source"), "checksum": doc.get("checksum")})
            elif up:
                per_day[day] = []
                for r in up["stocks"]:
                    bar = bars.get((day, r["code"]), {})
                    base = num(bar.get("pre_close")) or references.get((day, r["code"]))
                    per_day[day].append({"code": r["code"], "name": r["name"], "ret": return_pct(bar.get("close"), base),
                        "prev_boards": r["boards"], "sector": r["industry"], "seal_time": r.get("last_seal_time"),
                        "close": num(bar.get("close")), "limit_price": limits.get((day, r["code"]))})
                sources.append({"date": day, "source": "dated_pool_and_daily_bars"})
            for r in per_day.get(day, []):
                # Eastmoney previous-pool sometimes sends 000000. Only repair
                # from the same cohort day's actual final seal, never 09:30.
                if seal_time(r.get("seal_time")) is None and up:
                    original = next((p for p in up["stocks"] if p["code"] == r["code"]), {})
                    if corrected := seal_time(original.get("last_seal_time")):
                        r["seal_time_raw"] = r.get("seal_time")
                        r["seal_time"] = corrected
                        r["seal_time_source"] = "cohort_day_up_pool"
                r["is_highest"] = day in highest_ready and r["code"] in high_codes
                r["cohort_height"] = height if day in highest_ready else None
                bar = bars.get((day, r["code"]), {})
                if num(bar.get("close")) is not None and r["close"] is not None and abs(bar["close"]-r["close"]) < .005 and num(r["ret"]) is not None and r["ret"] > -100:
                    base = r["close"]/(1+r["ret"]/100)
                    r["open_ret"] = return_pct(bar.get("open"), base)
                    r["intraday_ret"] = return_pct(r["close"], bar.get("open"))
            if day in highest_ready:
                observed = {r["code"]: r for r in per_day.get(day, [])}
                highest_rows[day] = []
                for r in primary:
                    if r["code"] not in high_codes:
                        continue
                    if r["code"] in observed:
                        highest_rows[day].append(observed[r["code"]])
                    else:
                        bar = bars.get((day, r["code"]), {})
                        highest_rows[day].append({"code": r["code"], "name": r["name"], "is_highest": True,
                            "seal_time": r.get("last_seal_time"),
                            "prev_boards": r["boards"], "cohort_height": height, "sector": r["industry"],
                            "ret": return_pct(bar.get("close"), num(bar.get("pre_close")) or references.get((day, r["code"]))),
                            "close": num(bar.get("close")), "limit_price": limits.get((day, r["code"]))})
            if prior in broken:
                failed[day] = []
                up_codes = {r["code"] for r in up["stocks"]} if up else set()
                for r in broken[prior]["stocks"]:
                    if r["code"] in up_codes:
                        continue
                    bar = bars.get((day, r["code"]), {})
                    base = num(bar.get("pre_close")) or references.get((day, r["code"]))
                    upper = limits.get((prior, r["code"])) or r.get("up_limit")
                    failed[day].append({"code": r["code"], "name": r["name"], "sector": r["industry"],
                        "ret": return_pct(bar.get("close"), base), "close": num(bar.get("close")),
                        "limit_price": limits.get((day, r["code"])), "open_ret": return_pct(bar.get("open"), base),
                        "intraday_ret": return_pct(bar.get("close"), bar.get("open")),
                        "limit_cost_ret": return_pct(bar.get("close"), upper)})
            if prior not in heat and up:
                n, z = len(up["stocks"]), len(broken[prior]["stocks"]) if prior in broken else None
                heat[prior] = {"limit_up": n, "highest_consec": max((r["boards"] or 0 for r in up["stocks"]), default=0),
                               "broken_rate": z/(n+z) if z is not None and n+z else None}
        from shilun.market.taxonomy import classify_stocks
        industry_cohorts = {}
        for prior, d in zip(dates, dates[1:]):
            # The cohort was formed on prior, not on the feedback date d.
            # Count the full prior pool, including names omitted by a feedback
            # provider or missing next-day prices; neither can change top two.
            up = ups.get(prior)
            records = {r['code']: r for group in (per_day.get(d, []), failed.get(d, []),
                highest_rows.get(d, []), up['stocks'] if up else []) for r in group}
            mapped = classify_stocks(self.store, prior,
                [{**r, 'ticker': stock_code(r['code'])} for r in records.values()], industry_only=True)
            fields = ('industry', 'industry_code', 'industry_source', 'taxonomy_version',
                      'taxonomy_date', 'taxonomy_scope', 'classification_status')
            lookup = {r['code']: {k: r.get(k) for k in fields} for r in mapped}
            def classified(row):
                info = lookup.get(row['code'], {})
                return {**row, **info, 'sector': info.get('industry_code') or '',
                        'sector_name': info.get('industry') or '分类待补齐'}
            for collection in (per_day, failed, highest_rows):
                if d in collection:
                    collection[d] = [classified(r) for r in collection[d]]
            if up is not None:
                observed = {r['code']: r for r in per_day.get(d, [])}
                industry_cohorts[d] = []
                for r in up['stocks']:
                    bar = bars.get((d, r['code']), {})
                    base = num(bar.get('pre_close')) or references.get((d, r['code']))
                    feedback = observed.get(r['code']) or {
                        'ret': return_pct(bar.get('close'), base), 'close': num(bar.get('close')),
                        'limit_price': limits.get((d, r['code'])),
                        'open_ret': return_pct(bar.get('open'), base),
                        'intraday_ret': return_pct(bar.get('close'), bar.get('open'))}
                    industry_cohorts[d].append(classified({**r, **feedback,
                        'prev_boards': r['boards'], 'seal_time': r.get('last_seal_time')}))
        from shilun.review.market_mood import load_market_moods
        from shilun.review.suspensions import load_suspensions
        from shilun.review.board_price_quality import annotate_prices
        annotate_prices((per_day, failed, highest_rows, industry_cohorts), bars,
                        load_suspensions(self.store, dates[1:]))
        moods = load_market_moods(self.store, dates[:-1])
        result = build_effects(dates=dates, per_day=per_day, broken_by_day=failed, highest_by_day=highest_rows, heat=heat,
                               highest_ready=highest_ready, requested=days, sources=sources, moods=moods,
                               industry_cohorts=industry_cohorts)
        result["as_of"] = target
        result["archive"] = {"feedback_days": len(previous), "up_pool_days": len(ups), "broken_pool_days": len(broken),
                             "field_signatures": len({json.dumps(d.get("field_signature")) for d in (*ups.values(), *broken.values()) if d.get("field_signature")})}
        return result

    def _import_reasons(self, day):
        path = self.cache_root/"zt_reasons"/f"{day}.json"
        if not path.is_file():
            return
        raw = path.read_bytes()
        data = json.loads(raw)
        if data.get("schema") != 1 or data.get("date") != day or not isinstance(data.get("reasons"), dict):
            raise ValueError("涨停原因日期未通过核验")
        self.store.collection(REASON_COLLECTION).update_one({"date": day}, {"$set": {
            **data, "source": "vibe_dated_reasons", "checksum": sha256(raw).hexdigest()}}, upsert=True)

    def refresh(self, days=30, target=None, *, first_only=False):
        requested = target or today()
        target = requested if first_only else settled_target(requested)
        if target > today():
            raise ValueError("不能读取未来日期")
        from shilun.data.providers.tushare_provider import TushareConfig, TushareDailyClient
        client = TushareDailyClient(TushareConfig.from_env())
        reports = []
        if not first_only:
            start = (date.fromisoformat(target)-timedelta(days=days*3+20)).strftime("%Y%m%d")
            try:
                frame = client.fetch_trade_calendar(start, target.replace("-", ""))
                if frame.empty:
                    raise ValueError("交易日历为空")
                self.store.upsert_trade_calendar(frame)
            except Exception as error:
                reports.append({"dataset": "calendar", "status": "unavailable", "error_type": type(error).__name__})
            dates = self.sessions(target, days)
        else:
            dates = [target] if target is not None else []
        for prior, day in zip(dates, dates[1:]):
            if self.store.collection(COLLECTION).find_one({"analysis_date": day}, {"_id": 1}):
                continue
            try:
                path = self.cache_root/"prev_pool"/f"{day}.json"
                if path.is_file():
                    raw = path.read_bytes()
                    rows, source, checksum = normalize_previous(json.loads(raw)), "vibe_settled_previous_pool", sha256(raw).hexdigest()
                else:
                    if day != target:
                        continue  # Older feedback is rebuilt from dated U/Z lists and prices.
                    rows, source, checksum = fetch_previous(day), "eastmoney_dated_previous_pool", None
                if not rows:
                    continue
                self.store.collection(COLLECTION).update_one({"analysis_date": day}, {"$set": {
                    "analysis_date": day, "previous_date": prior, "rows": rows, "source": source,
                    "checksum": checksum, "imported_at": datetime.now(CHINA).isoformat()}}, upsert=True)
            except Exception as error:
                reports.append({"date": day, "dataset": "previous_pool", "status": "unavailable", "error_type": type(error).__name__})
        existing_up, existing_broken = self.pools(dates), self.pools(dates, "broken")
        tasks = [(d, k) for d in dates for k in (("up",) if first_only else ("up", "broken"))
                 if d == target or d not in (existing_up if k == "up" else existing_broken)]
        def fetch(task):
            day, kind = task
            try:
                if day < today():
                    return task, fetch_historical_pool(client, day, kind)
                return task, fetch_pool(day, kind)
            except Exception as error:
                return task, {"error_type": type(error).__name__}
        with ThreadPoolExecutor(max_workers=4) as executor:
            for (day, kind), data in executor.map(fetch, tasks):
                if "error_type" in data:
                    reports.append({"date": day, "dataset": kind, "status": "unavailable", **data})
                else:
                    data["snapshot_mode"] = "live" if day == today() and datetime.now(CHINA).time() < time(15, 10) else "settled"
                    self.store.collection(POOL_COLLECTION).update_one({"date": day, "kind": kind}, {"$set": data}, upsert=True)
        for day in dates:
            try:
                self._import_reasons(day)
            except (ValueError, OSError):
                reports.append({"date": day, "dataset": "reasons", "status": "unavailable"})
        reports.append(sync_reasons(self.store, target))
        if first_only:
            reports.append(sync_first_board_auction(self.store, target, client=client))
        if not first_only:
            reports.extend(sync_heat_summaries(self.store, dates, cache_dir=self.cache_root/"zt_summary"))
            # Query/write just the dated cohorts. Whole-market jobs remain responsible
            # for the global data inventory; this refresh does not duplicate that work.
            previous = {r["analysis_date"]: r for r in self.store.collection(COLLECTION).find({"analysis_date": {"$in": dates}}, {"_id": 0})}
            needed = needed_prices(dates, previous, self.pools(dates), self.pools(dates, "broken"))
            from shilun.review.suspensions import sync_suspensions
            from shilun.review.board_price_quality import sync_cohort_prices
            if dates:
                try:
                    sync_suspensions(self.store, dates[0], dates[-1])
                except Exception as error:
                    reports.append({'dataset': 'suspensions', 'status': 'unavailable', 'error_type': type(error).__name__})
            reports.append(sync_cohort_prices(self.store, needed, client=client))
            for day in dates:
                codes = sorted(needed[day])
                if not codes:
                    continue
                try:
                    if self.store.collection("daily_price_limits").count_documents({"trade_date": {"$in": [day, day.replace("-", "")]}, "ticker": {"$in": codes}, "up_limit": {"$gt": 0}}) < len(codes):
                        frame = client.fetch_all_stock_limits(day.replace("-", ""))
                        if frame.empty or any(_day(d) != day for d in frame["trade_date"]):
                            raise ValueError("涨跌停价日期未通过核验")
                        self.store.upsert_price_limits(frame[frame["ticker"].isin(codes)])
                except Exception as error:
                    reports.append({"date": day, "dataset": "daily_prices", "status": "unavailable", "error_type": type(error).__name__})
        if not first_only:
            from shilun.market.industry_history import sync_industry_history
            try:
                report = sync_industry_history(self.store, dates[:-1], client=client)
                reports.append({'dataset': 'dated_industry', **report})
            except Exception as error:
                reports.append({'dataset': 'dated_industry', 'status': 'unavailable', 'error_type': type(error).__name__})
            from shilun.review.mood_history import backfill_moods
            try:
                report = backfill_moods(self.store, target, days+1)
                reports.append({'dataset': 'P2_market_mood', 'status': 'partial' if report['pending'] else 'ok', **report})
            except Exception as error:
                reports.append({'dataset': 'P2_market_mood', 'status': 'unavailable', 'error_type': type(error).__name__})
        result = self.first_board(target) if first_only else self.effects(days, target)
        result["refresh_report"] = reports
        return result
