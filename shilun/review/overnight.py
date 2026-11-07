"""Vibe-Astock overseas integration, adapted for VC-Astock on 2026-09-13.

Source: simonlin1212/vibe-astock, d7a0e568424f5c0b1de9e39b1ddd733ecdbf9edb,
duanxian/overseas.py and vr/gstock.py (Apache-2.0; see licenses/vibe-astock.txt).
Changes: standalone adapter, bounded fallback requests, finite-value validation,
explicit partial/source metadata. No MongoDB writes or historical backdating.
"""

from __future__ import annotations

from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
import json
import math
import urllib.parse
import urllib.request
from zoneinfo import ZoneInfo

CHINA = ZoneInfo("Asia/Shanghai")
INDICES = (
    ("usDJI", "道琼斯", "美股", "100.DJIA"),
    ("usINX", "标普500", "美股", "100.SPX"),
    ("usIXIC", "纳斯达克", "美股", "100.NDX"),
    ("hkHSI", "恒生指数", "港股", "100.HSI"),
    ("hkHSTECH", "恒生科技", "港股", "124.HSTECH"),
)
MAG7 = (
    ("usAAPL", "苹果", "AAPL"), ("usMSFT", "微软", "MSFT"),
    ("usNVDA", "英伟达", "NVDA"), ("usGOOGL", "谷歌", "GOOGL"),
    ("usAMZN", "亚马逊", "AMZN"), ("usMETA", "Meta", "META"),
    ("usTSLA", "特斯拉", "TSLA"),
)
AI_HARD_TECH = (
    ("usSNDK", "SanDisk", "SNDK"), ("usLITE", "Lumentum", "LITE"),
    ("usMU", "美光", "MU"), ("usCRWV", "CoreWeave", "CRWV"),
    ("usAXTI", "AXT Inc.", "AXTI"), ("usAVGO", "博通", "AVGO"),
    ("usSKHY", "SKHY", "SKHY"), ("usMRVL", "迈威尔科技", "MRVL"),
    ("usCOHR", "Coherent", "COHR"),
)
SOXQ = ("usSOXQ", "费城半导体指数", "SOXQ")


def _read(url: str, timeout: int) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read(1048576)


def _number(value) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (TypeError, ValueError):
        return None


def _session(quote_time: str) -> str | None:
    parts = (quote_time or "").strip().replace("/", "-").split(" ")[0].split("-")
    try:
        return date(*(int(part) for part in parts)).isoformat() if len(parts) == 3 else None
    except (ValueError, TypeError):
        return None


def _market_label(market: str, session: str | None, now: datetime) -> str | None:
    """Keep Vibe's session-aware, approximate Beijing trading-window labels."""
    if not session:
        return None
    minutes = now.hour * 60 + now.minute
    if market == "美股":
        us_day = now.date() if now.hour >= 12 else now.date() - timedelta(days=1)
        trading = (minutes >= 21 * 60 or minutes < 5 * 60) and session == us_day.isoformat()
        state = "盘中" if trading else "收盘"
    elif session != now.date().isoformat() or minutes >= 16 * 60 + 10:
        state = "收盘"
    else:
        state = "盘前" if minutes < 9 * 60 + 30 else "盘中"
    return f"{market} {session} {state}"


def _tencent() -> dict[str, list[str]]:
    symbols = [item[0] for item in (*INDICES, *MAG7, *AI_HARD_TECH, SOXQ)]
    try:
        raw = _read("https://qt.gtimg.cn/q=" + ",".join(symbols), 12).decode("gbk", "replace")
    except Exception:
        return {}
    rows = {}
    for line in raw.split(";"):
        if '"' not in line or "~" not in line:
            continue
        symbol = line.split("=")[0].strip().removeprefix("v_")
        fields = line.split('"')[1].split("~")
        if len(fields) > 32 and symbol in symbols:
            rows[symbol] = fields
    return rows


def _eastmoney_index(item: tuple) -> dict | None:
    symbol, name, region, secid = item
    query = urllib.parse.urlencode({"secid": secid, "fields": "f43,f57,f58,f59,f60,f170"})
    for host in ("push2.eastmoney.com", "push2delay.eastmoney.com"):
        try:
            raw = json.loads(_read(f"https://{host}/api/qt/stock/get?{query}", 4))
            data = raw.get("data") or {}
            price, pct = _number(data.get("f43")), _number(data.get("f170"))
            decimals = data.get("f59", 2)
            if type(decimals) is not int or not 0 <= decimals <= 8:
                decimals = 2
            if price is None or price <= 0 or pct is None:
                continue
            return {"symbol": symbol, "ticker": secid.split('.', 1)[1], "name": "纳斯达克100" if secid == "100.NDX" else name,
                    "region": region, "price": round(price / 10 ** decimals, decimals),
                    "change_pct": round(pct / 100, 2), "session": None, "source": "eastmoney"}
        except Exception:
            continue
    return None


def overseas_snapshot() -> dict:
    raw = _tencent()

    def row(symbol: str, name: str, extra: dict) -> dict | None:
        fields = raw.get(symbol)
        if not fields:
            return None
        price, pct = _number(fields[3]), _number(fields[32])
        if price is None or price <= 0 or pct is None:
            return None
        return {"symbol": symbol, "name": name, "price": round(price, 2),
                "change_pct": round(pct, 2), "session": _session(fields[30]),
                "source": "tencent", **extra}

    indices = [r for s, n, region, _ in INDICES if (r := row(s, n, {"region": region, "ticker": s[2:]}))]
    mag7 = [r for s, n, ticker in MAG7 if (r := row(s, n, {"ticker": ticker}))]
    # Keep the requested watchlist visible even when a provider omits an
    # individual ticker. Missing cards carry no invented price or percentage.
    ai_hard_tech = []
    for symbol, name, ticker in AI_HARD_TECH:
        parsed = row(symbol, name, {"ticker": ticker})
        ai_hard_tech.append(parsed or {"symbol": symbol, "name": name, "ticker": ticker,
                                       "price": None, "change_pct": None, "session": None,
                                       "source": "tencent", "available": False})
    ai_index = row(SOXQ[0], SOXQ[1], {"ticker": SOXQ[2], "kind": "index"}) or {
        "symbol": SOXQ[0], "name": SOXQ[1], "ticker": SOXQ[2], "kind": "index",
        "price": None, "change_pct": None, "session": None, "source": "tencent",
        "available": False,
    }
    ai_actual = any(item.get("available", True) for item in ai_hard_tech) or ai_index.get("available", True)
    fallback = not indices
    if fallback:
        # Vibe's page uses global/indices only if its Tencent indices are empty.
        with ThreadPoolExecutor(max_workers=5) as pool:
            indices = [item for item in pool.map(_eastmoney_index, INDICES) if item]

    def session(rows: list[dict]) -> str | None:
        counts = Counter(item["session"] for item in rows if item.get("session"))
        return counts.most_common(1)[0][0] if counts else None

    now = datetime.now(CHINA)
    us = session([r for r in indices if r["region"] == "美股"]) or session(mag7)
    hk = session([r for r in indices if r["region"] == "港股"])
    return {
        "available": bool(indices or mag7 or ai_actual),
        "snapshot_mode": "latest",
        "historical_archive_available": False,
        "history_note": "外围行情读取供应商最新报价，尚未保存可按复盘日查询的历史归档。",
        "reason": "" if indices or mag7 or ai_actual else "腾讯及东方财富备用外围行情均未返回有效数据",
        "indices": indices, "mag7": mag7, "ai_hard_tech": ai_hard_tech,
        "ai_index": ai_index, "us_session": us, "hk_session": hk,
        "us_label": "美股指数 · 备用源未提供行情日期" if fallback else _market_label("美股", us, now),
        "hk_label": "港股指数 · 备用源未提供行情日期" if fallback else _market_label("港股", hk, now),
        "fetched_at": now.isoformat(timespec="seconds"),
        "source_label": "东方财富备用指数 / 腾讯个股" if fallback and mag7 else "东方财富备用行情" if fallback else "腾讯行情",
        "partial": len(indices) != 5 or len(mag7) != 7
                   or any(not item.get("available", True) for item in ai_hard_tech)
                   or not ai_index.get("available", True),
        "note": "备用纳斯达克为 NASDAQ100，非腾讯纳斯达克综合指数；行情日期不补为今天。" if fallback else "",
    }
