"""股指期货主力席位日度快照。

新浪当前合约排行负责前 20 多/空席位，并与新浪同一合约中信期货日度明细核验；
IF/IH/IC 再用同花顺交叉验证。任何来源失败都保留显式状态，不用旧值或零值伪装成功。
"""

from __future__ import annotations

import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from typing import Any

import requests
from bs4 import BeautifulSoup


THS_URL = "https://data.10jqka.com.cn/gzqh/"
SINA_URL = "https://vip.stock.finance.sina.com.cn/q/view/vCffex_Positions_ccjj.php"
SINA_RANKING_URL = "https://vip.stock.finance.sina.com.cn/q/view/vCffex_Positions_cjcc.php"
PRODUCTS = ("IF", "IH", "IC", "IM")
_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 Chrome/138.0 Safari/537.36"
)


class CffexPositionsError(ValueError):
    """页面结构缺失或数据日不符合要求。"""


def _integer(value: Any) -> int:
    text = re.sub(r"[^0-9+\-]", "", str(value or ""))
    if text in {"", "+", "-"}:
        return 0
    return int(text)


def discover_main_contracts(html: str) -> dict[str, str]:
    """从同花顺首页提取当前展示合约；IM 缺少页签时跟随 IF 到期月。"""

    contracts: dict[str, str] = {}
    for contract in re.findall(r"/gzqh/((?:IF|IH|IC|IM)\d{4})/", html):
        contracts.setdefault(contract[:2], contract)
    if "IF" in contracts and "IM" not in contracts:
        contracts["IM"] = f"IM{contracts['IF'][2:]}"
    missing = [product for product in PRODUCTS if product not in contracts]
    if missing:
        raise CffexPositionsError(f"同花顺首页未找到当前合约：{', '.join(missing)}")
    return contracts


def _ranking_table(soup: BeautifulSoup, title_suffix: str) -> list[dict[str, Any]]:
    block = next(
        (
            item for item in soup.select(".gzqh-block")
            if str((item.select_one(".hd") or {}).get_text(" ", strip=True) if item.select_one(".hd") else "").endswith(title_suffix)
        ),
        None,
    )
    if block is None:
        raise CffexPositionsError(f"同花顺缺少表格：{title_suffix}")
    rows: list[dict[str, Any]] = []
    for row in block.select("tbody tr"):
        cells = [cell.get_text(" ", strip=True) for cell in row.select("td")]
        if len(cells) < 4 or not re.match(r"^\d+", cells[0]):
            continue
        rows.append({
            "rank": _integer(cells[0]),
            "member": re.sub(r"[（(]代客[)）]", "", cells[1]).strip(),
            "position": _integer(cells[2]),
            "change": _integer(cells[3]),
        })
    if not rows:
        raise CffexPositionsError(f"同花顺表格为空：{title_suffix}")
    return rows


def parse_ths_contract_page(html: str, *, source_url: str = THS_URL) -> dict[str, Any]:
    soup = BeautifulSoup(html, "lxml")
    contract_node = soup.select_one("#j_instrumentId")
    date_node = soup.select_one("#j_date")
    contract = str(contract_node.get("val") if contract_node else "").upper()
    date_digits = str(date_node.get("val") if date_node else "")
    if not re.fullmatch(r"(?:IF|IH|IC|IM)\d{4}", contract) or not re.fullmatch(r"\d{8}", date_digits):
        raise CffexPositionsError("同花顺页面缺少合约或数据日")
    long_rows = _ranking_table(soup, "多单持仓排行")
    short_rows = _ranking_table(soup, "空单持仓排行")
    long_total = sum(item["position"] for item in long_rows)
    short_total = sum(item["position"] for item in short_rows)
    long_change = sum(item["change"] for item in long_rows)
    short_change = sum(item["change"] for item in short_rows)
    citic_long = next((item for item in long_rows if item["member"].startswith("中信期货")), None)
    citic_short = next((item for item in short_rows if item["member"].startswith("中信期货")), None)
    return {
        "product": contract[:2],
        "contract": contract,
        "data_date": f"{date_digits[:4]}-{date_digits[4:6]}-{date_digits[6:]}",
        "top20_long": long_total,
        "top20_short": short_total,
        "top20_net": long_total - short_total,
        "top20_net_short": short_total - long_total,
        "top20_long_change": long_change,
        "top20_short_change": short_change,
        "top20_net_change": long_change - short_change,
        "citic_long": (citic_long or {}).get("position"),
        "citic_short": (citic_short or {}).get("position"),
        "citic_net": (
            int((citic_long or {}).get("position") or 0) - int((citic_short or {}).get("position") or 0)
            if citic_long or citic_short else None
        ),
        "citic_long_change": (citic_long or {}).get("change"),
        "citic_short_change": (citic_short or {}).get("change"),
        "source_url": source_url,
    }


def parse_sina_citic_page(html: str, *, contract: str, source_url: str = SINA_URL) -> dict[str, Any]:
    soup = BeautifulSoup(html, "lxml")
    for row in soup.select("table.listT tr"):
        cells = [cell.get_text(" ", strip=True) for cell in row.select("td")]
        if len(cells) < 12 or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", cells[0]):
            continue
        long_position = _integer(cells[4])
        short_position = _integer(cells[7])
        return {
            "contract": contract,
            "data_date": cells[0],
            "citic_long": long_position,
            "citic_short": short_position,
            "citic_net": long_position - short_position,
            "citic_long_change": _integer(cells[6]),
            "citic_short_change": _integer(cells[9]),
            "source_url": source_url,
        }
    raise CffexPositionsError("新浪页面缺少中信期货日度持仓行")


def _parse_sina_ranking_table(table: Any) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for row in table.select("tr"):
        cells = [cell.get_text(" ", strip=True) for cell in row.select("td")]
        if len(cells) < 4 or not re.fullmatch(r"\d+", cells[0]):
            continue
        rows.append({
            "rank": _integer(cells[0]),
            "member": cells[1].strip(),
            "position": _integer(cells[2]),
            "change": _integer(cells[3]),
        })
    return rows


def parse_sina_ranking_page(
    html: str,
    *,
    contract: str,
    source_url: str = SINA_RANKING_URL,
) -> dict[str, Any]:
    """解析新浪单合约成交/多单/空单三张前20席位表。"""

    soup = BeautifulSoup(html, "lxml")
    tables = [table for table in soup.select("table.listT") if _parse_sina_ranking_table(table)]
    if len(tables) < 3:
        raise CffexPositionsError("新浪持仓排行页缺少成交/多单/空单表格")
    long_rows = _parse_sina_ranking_table(tables[1])
    short_rows = _parse_sina_ranking_table(tables[2])
    date_node = soup.select_one("#calen")
    data_date = str(date_node.get("value") if date_node else "")
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", data_date):
        raise CffexPositionsError("新浪持仓排行页缺少数据日")
    long_total = sum(item["position"] for item in long_rows)
    short_total = sum(item["position"] for item in short_rows)
    long_change = sum(item["change"] for item in long_rows)
    short_change = sum(item["change"] for item in short_rows)
    citic_long = next((item for item in long_rows if item["member"].startswith("中信期货")), None)
    citic_short = next((item for item in short_rows if item["member"].startswith("中信期货")), None)
    return {
        "product": contract[:2],
        "contract": contract,
        "data_date": data_date,
        "top20_long": long_total,
        "top20_short": short_total,
        "top20_net": long_total - short_total,
        "top20_net_short": short_total - long_total,
        "top20_long_change": long_change,
        "top20_short_change": short_change,
        "top20_net_change": long_change - short_change,
        "citic_long": (citic_long or {}).get("position"),
        "citic_short": (citic_short or {}).get("position"),
        "citic_net": (
            int((citic_long or {}).get("position") or 0) - int((citic_short or {}).get("position") or 0)
            if citic_long or citic_short else None
        ),
        "citic_long_change": (citic_long or {}).get("change"),
        "citic_short_change": (citic_short or {}).get("change"),
        "source_url": source_url,
    }


def _decode(response: requests.Response) -> str:
    response.raise_for_status()
    response.encoding = "gb18030"
    return response.text


def _get(url: str, *, headers: dict[str, str], timeout: float, params: dict[str, Any] | None = None) -> str:
    last_error: Exception | None = None
    for attempt in range(2):
        try:
            return _decode(requests.get(url, params=params, headers=headers, timeout=timeout))
        except Exception as error:  # noqa: BLE001 - 短暂代理断开允许重试一次
            last_error = error
            if attempt == 0:
                time.sleep(0.2)
    raise CffexPositionsError(str(last_error or "请求失败"))


def _fetch_product(contract: str, *, target_date: str, timeout: float) -> dict[str, Any]:
    sina_headers = {"User-Agent": _UA}
    ranking = parse_sina_ranking_page(
        _get(
            SINA_RANKING_URL,
            params={"symbol": contract, "date": target_date},
            headers=sina_headers,
            timeout=timeout,
        ),
        contract=contract,
    )
    start_date = (datetime.strptime(target_date, "%Y-%m-%d") - timedelta(days=12)).strftime("%Y-%m-%d")
    sina = parse_sina_citic_page(
        _get(
            SINA_URL,
            params={
                "symbol": contract,
                "id": "0018",
                "start_date": start_date,
                "end_date": target_date,
            },
            headers=sina_headers,
            timeout=timeout,
        ),
        contract=contract,
    )
    comparable = ("data_date", "citic_long", "citic_short", "citic_long_change", "citic_short_change")
    mismatch = [key for key in comparable if ranking.get(key) != sina.get(key)]
    result = {
        **ranking,
        "sina_citic": sina,
        "source_consistency": "matched" if not mismatch else "mismatch",
        "source_mismatch_fields": mismatch,
    }
    # 同花顺当前没有 IM 排行；其余三类用于第三重校验，失败不抹掉已匹配的新浪双表。
    if contract[:2] != "IM":
        ths_url = f"{THS_URL}{contract}/"
        try:
            ths = parse_ths_contract_page(
                _get(
                    ths_url,
                    headers={"User-Agent": _UA, "Referer": THS_URL},
                    timeout=timeout,
                ),
                source_url=ths_url,
            )
            ths_fields = (*comparable, "top20_long", "top20_short", "top20_long_change", "top20_short_change")
            ths_mismatch = [key for key in ths_fields if ranking.get(key) != ths.get(key)]
            result["ths_crosscheck"] = ths
            result["ths_consistency"] = "matched" if not ths_mismatch else "mismatch"
            result["ths_mismatch_fields"] = ths_mismatch
        except Exception as error:  # noqa: BLE001 - 新浪双表仍可独立成数
            result["ths_consistency"] = "unavailable"
            result["ths_error"] = str(error)[:300]
    else:
        result["ths_consistency"] = "not_supported_for_im"
    return result


def _institutional_summary(products: list[dict[str, Any]]) -> dict[str, Any]:
    valid = [item for item in products if item.get("status") == "ok"]
    if not valid:
        return {
            "available": False,
            "risk_score": None,
            "behavior": "期指席位数据不可用",
            "conclusion": "同花顺与新浪均未形成可核验的当日快照。",
        }
    top20_net_short = sum(int(item.get("top20_net_short") or 0) for item in valid)
    top20_net_short_change = sum(
        int(item.get("top20_short_change") or 0) - int(item.get("top20_long_change") or 0)
        for item in valid
    )
    citic_net_short = sum(
        int(item.get("citic_short") or 0) - int(item.get("citic_long") or 0)
        for item in valid
    )
    citic_net_short_change = sum(
        int(item.get("citic_short_change") or 0) - int(item.get("citic_long_change") or 0)
        for item in valid
    )
    denominator = max(1, sum(int(item.get("top20_long") or 0) + int(item.get("top20_short") or 0) for item in valid))
    change_ratio = top20_net_short_change / denominator
    risk_score = max(0, min(100, round(50 + change_ratio * 1200, 1)))
    if top20_net_short_change > 0 and citic_net_short_change > 0:
        behavior = "空头同步增加"
        conclusion = "前20席位与中信期货净空同步上升，机构风险偏好下降。"
    elif top20_net_short_change < 0 and citic_net_short_change < 0:
        behavior = "空头同步下降"
        conclusion = "前20席位与中信期货净空同步下降，机构风险压力释放。"
    elif top20_net_short_change < 0:
        behavior = "主力净空下降"
        conclusion = "前20席位净空下降，但中信期货净空变化未同向，按分歧处理。"
    elif top20_net_short_change > 0:
        behavior = "主力净空增加"
        conclusion = "前20席位净空增加，但中信期货净空变化未同向，风险偏谨慎。"
    else:
        behavior = "净空变化有限"
        conclusion = "机构净空变化有限，暂未形成明确方向。"
    return {
        "available": True,
        "risk_score": risk_score,
        "behavior": behavior,
        "conclusion": conclusion,
        "top20_net_short": top20_net_short,
        "top20_net_short_change": top20_net_short_change,
        "citic_net_short": citic_net_short,
        "citic_net_short_change": citic_net_short_change,
    }


def fetch_cffex_positions(*, target_date: str, timeout: float = 8.0) -> dict[str, Any]:
    """并行抓取四大股指期货当前展示合约并做双来源核验。"""

    home = _get(THS_URL, headers={"User-Agent": _UA, "Referer": THS_URL}, timeout=timeout)
    contracts = discover_main_contracts(home)
    products: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = {
            executor.submit(_fetch_product, contract, target_date=target_date, timeout=timeout): product
            for product, contract in contracts.items()
        }
        for future in as_completed(futures):
            product = futures[future]
            try:
                result = future.result()
                status = "ok" if result.get("data_date") == target_date else "stale"
                products.append({**result, "status": status})
            except Exception as error:  # noqa: BLE001 - 单品种失败保留明确状态
                products.append({
                    "product": product,
                    "contract": contracts[product],
                    "data_date": None,
                    "status": "error",
                    "error": str(error)[:300],
                })
    products.sort(key=lambda item: PRODUCTS.index(str(item.get("product"))))
    return {
        "status": "ok" if all(item.get("status") == "ok" for item in products) else "partial",
        "target_date": target_date,
        "fetched_at": datetime.now().isoformat(timespec="seconds"),
        "method": "新浪当前合约前20席位 + 同合约中信期货日度明细；IF/IH/IC 再与同花顺交叉核验，IM 因同花顺未提供排行采用新浪双表；不跨月合约相加。",
        "products": products,
        "institutional_summary": _institutional_summary(products),
        "sources": [THS_URL, SINA_RANKING_URL, SINA_URL],
    }


__all__ = [
    "CffexPositionsError",
    "discover_main_contracts",
    "fetch_cffex_positions",
    "parse_sina_citic_page",
    "parse_sina_ranking_page",
    "parse_ths_contract_page",
]
