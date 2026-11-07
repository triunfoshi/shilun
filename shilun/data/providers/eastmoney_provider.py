"""Bounded direct Eastmoney access; no AKShare runtime or global proxy mutation."""
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import time
import pandas as pd
import requests

class EastmoneyFetchError(RuntimeError):
    def __init__(self, source: str, error: Exception):
        self.source, self.underlying = source, error
        super().__init__(f"{source} unavailable ({type(error).__name__})")

@dataclass(frozen=True)
class EastmoneyConfig:
    timeout: float = 15
    retries: int = 1
    retry_backoff: float = 1
    bypass_system_proxy: bool = True

def parse_dated_limit_pool(payload: dict, requested_date: str) -> pd.DataFrame:
    """Validate Eastmoney's response date before discarding its envelope.

    AkShare's pool wrapper drops qdate and uses an unbounded request. A historic
    request can instead return the latest qdate with an empty pool. That is
    unavailable history, not proof of zero limit-up stocks on the requested day.
    """
    data = payload.get("data")
    if payload.get("rc") != 0 or not isinstance(data, dict):
        raise ValueError("东方财富涨停池未返回有效数据")
    if str(data.get("qdate", "")).replace("-", "") != requested_date.replace("-", ""):
        raise ValueError("东方财富涨停池返回日期与请求日期不一致，历史数据不可用")
    pool = data.get("pool")
    if not isinstance(pool, list):
        raise ValueError("东方财富涨停池结构缺失")
    mapping = {"c": "代码", "n": "名称", "zdp": "涨跌幅", "p": "最新价",
               "amount": "成交额", "ltsz": "流通市值", "tshare": "总市值",
               "hs": "换手率", "fund": "封板资金", "fbt": "首次封板时间",
               "lbt": "最后封板时间", "zbc": "炸板次数", "lbc": "连板数", "hybk": "所属行业"}
    records = []
    for raw in pool:
        if not isinstance(raw, dict):
            raise ValueError("东方财富涨停池股票行结构缺失")
        code = str(raw.get("c", "")).zfill(6)
        boards = pd.to_numeric(raw.get("lbc"), errors="coerce")
        if len(code) != 6 or not code.isdigit() or code == "000000" or pd.isna(boards) or not 1 <= boards <= 1000 or boards != int(boards):
            raise ValueError("东方财富涨停池代码或连板数缺失")
        item = {label: raw.get(key) for key, label in mapping.items()}
        item.update({"代码": code, "连板数": int(boards)})
        price = pd.to_numeric(raw.get("p"), errors="coerce")
        item["最新价"] = price / 1000 if pd.notna(price) else None
        for label in ("首次封板时间", "最后封板时间"):
            item[label] = str(item[label]).zfill(6) if item[label] is not None else None
        stats = raw.get("zttj") or {}
        item["涨停统计"] = f"{stats.get('days')}/{stats.get('ct')}" if isinstance(stats, dict) and stats else None
        records.append(item)
    return pd.DataFrame(records)


class EastmoneyClient:
    source = "eastmoney"
    def __init__(self, config: EastmoneyConfig | None = None):
        self.config = config or EastmoneyConfig()

    def _retry(self, function, source):
        for attempt in range(self.config.retries + 1):
            try:
                return function()
            except Exception as error:
                if attempt == self.config.retries:
                    raise EastmoneyFetchError(source, error) from error
                time.sleep(self.config.retry_backoff * (attempt + 1))

    def _get(self, url, params):
        def request():
            with requests.Session() as session:
                session.trust_env = not self.config.bypass_system_proxy
                response = session.get(url, params=params, timeout=self.config.timeout)
                response.raise_for_status()
                payload = response.json()
                if payload.get("rc") != 0 or not isinstance(payload.get("data"), dict):
                    raise ValueError("东方财富数据不可用")
                return payload["data"]
        return self._retry(request, "eastmoney")

    @staticmethod
    def secid(ticker):
        code, exchange = ticker.upper().split(".")
        if len(code) != 6 or not code.isdigit() or exchange not in {"SH", "SZ", "BJ"}:
            raise ValueError("Invalid exchange-qualified ticker")
        return f"{1 if exchange == 'SH' else 0}.{code}"

    def fetch_minute_bars(self, ticker, period="5", *, trade_date=None):
        if str(period) not in {"1", "5", "15", "30", "60"}:
            raise ValueError("Unsupported minute interval")
        end = pd.Timestamp(trade_date) if trade_date else pd.Timestamp(datetime.now(ZoneInfo("Asia/Shanghai")).date())
        start = end - timedelta(days=7)
        data = self._get("https://push2his.eastmoney.com/api/qt/stock/kline/get", {
            "secid": self.secid(ticker), "klt": str(period), "fqt": "0",
            "beg": start.strftime("%Y%m%d"), "end": end.strftime("%Y%m%d"), "lmt": "1000",
            "fields1": "f1,f2,f3,f4,f5,f6", "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61"})
        if str(data.get("code")) != ticker.split(".")[0]:
            raise EastmoneyFetchError("minute_bars", ValueError("代码不一致"))
        rows = []
        for line in data.get("klines") or []:
            values = line.split(",")
            if len(values) < 7:
                raise EastmoneyFetchError("minute_bars", ValueError("分钟行结构不完整"))
            rows.append(dict(zip(("datetime", "open", "close", "high", "low", "volume", "amount"), values)))
        df = pd.DataFrame(rows)
        if df.empty:
            return df
        df["datetime"] = pd.to_datetime(df["datetime"], errors="raise")
        for key in ("open", "close", "high", "low", "volume", "amount"):
            df[key] = pd.to_numeric(df[key], errors="raise")
        # Eastmoney reports volume in lots; the common contract uses shares.
        df["volume"] *= 100
        valid = (df["low"] <= df[["open", "close"]].min(axis=1)) & (df["high"] >= df[["open", "close"]].max(axis=1)) & (df["low"] > 0)
        if not valid.all() or not df["datetime"].dt.date.le(end.date()).all():
            raise EastmoneyFetchError("minute_bars", ValueError("OHLC 或日期不一致"))
        return df.drop_duplicates("datetime").sort_values("datetime").reset_index(drop=True)

    def fetch_stock_realtime_batch(self, tickers):
        rows = []
        tickers = list(dict.fromkeys(tickers))
        for offset in range(0, len(tickers), 80):
            requested = {self.secid(t): t.upper() for t in tickers[offset:offset+80]}
            data = self._get("https://push2.eastmoney.com/api/qt/ulist.np/get", {
                "secids": ",".join(requested), "fltt": "2",
                "fields": "f2,f3,f6,f12,f13,f14,f15,f16,f17,f18,f124"})
            for raw in data.get("diff") or []:
                ticker = requested.get(f"{raw.get('f13')}.{raw.get('f12')}")
                if not ticker or not isinstance(raw.get("f2"), (int, float)) or raw["f2"] <= 0:
                    continue
                stamp = raw.get("f124")
                quote_at = datetime.fromtimestamp(stamp, ZoneInfo("Asia/Shanghai")).isoformat() if isinstance(stamp, (int, float)) and stamp > 0 else None
                rows.append({"ticker": ticker, "name": raw.get("f14"), "current": raw["f2"],
                    "pct_chg": raw.get("f3"), "amount": raw.get("f6"), "high": raw.get("f15"),
                    "low": raw.get("f16"), "open": raw.get("f17"), "prev_close": raw.get("f18"),
                    "quote_at": quote_at, "update_at": quote_at, "timestamp_kind": "provider" if quote_at else "unknown",
                    "fetched_at": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat()})
        return pd.DataFrame(rows)

    def fetch_index_realtime(self):
        from shilun.market.part1 import BENCHMARK_INDEX_OPTIONS
        return self.fetch_stock_realtime_batch([item["ticker"] for item in BENCHMARK_INDEX_OPTIONS])

    def fetch_zt_pool(self, trade_date: str) -> pd.DataFrame:
        """获取指定日期的涨停池（东方财富，含连板数/封板时间/炸板次数/所属行业）。

        Args:
            trade_date: yyyymmdd 格式
        """
        normalized = str(trade_date).replace("-", "")
        def fetch_dated_pool():
            with requests.Session() as session:
                session.trust_env = not self.config.bypass_system_proxy
                response = session.get(
                    "https://push2ex.eastmoney.com/getTopicZTPool",
                    params={"ut": "7eea3edcaed734bea9cbfc24409ed989", "dpt": "wz.ztzt",
                            "Pageindex": "0", "pagesize": "10000", "sort": "fbt:asc", "date": normalized},
                    timeout=self.config.timeout,
                )
                response.raise_for_status()
                return parse_dated_limit_pool(response.json(), normalized)
        df = self._retry(fetch_dated_pool, "zt_pool")
        if df is None or df.empty:
            return pd.DataFrame()
        # 字段对齐到我们的存储格式
        rename = {
            "代码": "ticker",
            "名称": "name",
            "涨跌幅": "pct_chg",
            "最新价": "close",
            "成交额": "amount",
            "流通市值": "float_market_value",
            "总市值": "total_market_value",
            "换手率": "turnover_ratio",
            "封板资金": "limit_up_fund",
            "首次封板时间": "first_seal_time",
            "最后封板时间": "last_seal_time",
            "炸板次数": "broken_count",
            "涨停统计": "limit_up_stat",
            "连板数": "consecutive_count",
            "所属行业": "industry",
        }
        df = df.rename(columns=rename)
        df["trade_date"] = normalized
        keep = [c for c in rename.values() if c in df.columns] + ["trade_date"]
        return df[keep].copy()

