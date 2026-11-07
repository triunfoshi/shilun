"""Tushare 实时行情客户端（走新浪源 hq.sinajs.cn，不需要 token）。

用于盘中监控。相比 akshare 的东财 push2 域（本机可能被限流），
新浪源在国内稳定性极高、延迟 1-3 秒。

实测性能：
- 4 个指数：0.6s
- 500 只个股：0.33s
- 20 只候选票：0.45s
"""

from __future__ import annotations

import logging
from typing import Any

import pandas as pd

logger = logging.getLogger(__name__)


class TushareRealtimeError(RuntimeError):
    def __init__(self, source: str, error: Exception):
        self.source = source
        self.underlying = error
        super().__init__(f"{source} fetch failed: {error}")


class TushareRealtimeClient:
    """走 tushare 的 get_realtime_quotes（新浪源，无 token）。

    ticker 格式统一使用 Tushare 标准：
    - 上证：000001.SH
    - 深证：399001.SZ
    - 北交所：430047.BJ
    """

    _BENCHMARK_TS_MAP = {
        "000001.SH": "sh000001",  # 上证指数
        "000300.SH": "sh000300",  # 沪深300
        "399001.SZ": "sz399001",  # 深证成指
        "399006.SZ": "sz399006",  # 创业板指
        "000905.SH": "sh000905",  # 中证500
        "000852.SH": "sh000852",  # 中证1000
        "000016.SH": "sh000016",  # 上证50
        "000688.SH": "sh000688",  # 科创50
    }

    def __init__(self) -> None:
        try:
            import tushare as ts  # type: ignore
        except ImportError as error:
            raise RuntimeError("tushare 未安装。pip install tushare") from error
        self._ts = ts

    @staticmethod
    def _ticker_to_sina(ticker: str) -> str:
        """把 Tushare 格式转成 sina 格式（'000001.SH' → 'sh000001'）。"""
        ticker = str(ticker).upper().strip()
        if ticker in TushareRealtimeClient._BENCHMARK_TS_MAP:
            return TushareRealtimeClient._BENCHMARK_TS_MAP[ticker]
        if "." in ticker:
            code, suffix = ticker.split(".", 1)
            if suffix == "SH":
                return f"sh{code}"
            if suffix == "SZ":
                return f"sz{code}"
            if suffix == "BJ":
                return f"bj{code}"
        # 无后缀时按代码前缀猜（保守）
        if ticker.startswith(("60", "68", "5", "9", "000")):
            return f"sh{ticker}"
        if ticker.startswith(("00", "30", "39")):
            return f"sz{ticker}"
        return ticker.lower()

    @staticmethod
    def _sina_to_ticker(sina_code: str) -> str:
        """反向：sina 格式 → Tushare 格式。"""
        s = str(sina_code).lower().strip()
        if s.startswith("sh"):
            return f"{s[2:]}.SH"
        if s.startswith("sz"):
            return f"{s[2:]}.SZ"
        if s.startswith("bj"):
            return f"{s[2:]}.BJ"
        # 已经是纯数字：按规则判断
        if s.startswith(("000", "60", "68", "5", "9")):
            return f"{s}.SH"
        if s.startswith(("00", "30", "39")):
            return f"{s}.SZ"
        return s.upper()

    def fetch_index_realtime(self, tickers: list[str] | None = None) -> pd.DataFrame:
        """获取指数实时行情。默认拉主流基准。

        返回列：ticker / name / current / pct_chg / open / high / low
              / prev_close / volume / amount / update_at
        """
        if tickers is None:
            tickers = list(self._BENCHMARK_TS_MAP.keys())[:4]  # 默认 4 大基准
        sina_codes = [self._ticker_to_sina(t) for t in tickers]

        try:
            df = self._ts.get_realtime_quotes(sina_codes)
        except Exception as error:  # noqa: BLE001
            raise TushareRealtimeError("index_realtime", error) from error

        if df is None or df.empty:
            return pd.DataFrame()

        return self._normalize(df, tickers)

    def fetch_stock_realtime_batch(self, tickers: list[str]) -> pd.DataFrame:
        """获取多只个股的实时行情。单次调用可支持 500+ 只。"""
        if not tickers:
            return pd.DataFrame()
        sina_codes = [self._ticker_to_sina(t) for t in tickers]
        try:
            df = self._ts.get_realtime_quotes(sina_codes)
        except Exception as error:  # noqa: BLE001
            raise TushareRealtimeError("stock_realtime_batch", error) from error

        if df is None or df.empty:
            return pd.DataFrame()

        return self._normalize(df, tickers)

    def _normalize(self, df: pd.DataFrame, tickers: list[str]) -> pd.DataFrame:
        """把新浪返回的中文列名标准化成我们内部使用的英文字段。"""
        rename = {
            "name": "name",
            "price": "current",
            "pre_close": "prev_close",
            "open": "open",
            "high": "high",
            "low": "low",
            "volume": "volume",
            "amount": "amount",
        }
        # 只保留需要的列
        keep_cols = ["code", "date", "time"] + list(rename.keys())
        available = [c for c in keep_cols if c in df.columns]
        df = df[available].rename(columns=rename).copy()

        # 数值化
        for col in ["current", "prev_close", "open", "high", "low", "volume", "amount"]:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")

        # 计算 pct_chg
        df["pct_chg"] = (df["current"] - df["prev_close"]) / df["prev_close"]
        df.loc[df["prev_close"] == 0, "pct_chg"] = 0.0

        # 反向映射优先使用本次请求中的精确 ticker。纯代码 000001 同时可能
        # 表示上证指数或平安银行，仅靠前缀无法消歧。
        requested_by_code = {
            str(ticker).split(".", 1)[0].lower().removeprefix("sh").removeprefix("sz").removeprefix("bj"): str(ticker).upper()
            for ticker in tickers
        }
        df["ticker"] = df["code"].astype(str).apply(
            lambda code: requested_by_code.get(str(code).lower(), self._sina_to_ticker(code))
        )

        # 剔除 price=0 的（可能是无效股票或停牌）
        df = df[df["current"] > 0].copy()

        # 新浪返回真实行情日期/时间。必须用它判断新鲜度，不能用请求完成时间
        # 冒充行情时间（休市日请求仍会返回上一交易日收盘数据）。
        fetched_at = pd.Timestamp.now(tz="Asia/Shanghai").isoformat(timespec="seconds")
        if "date" in df.columns and "time" in df.columns:
            quote_at = pd.to_datetime(
                df["date"].astype(str).str.strip() + " " + df["time"].astype(str).str.strip(),
                errors="coerce",
            )
            quote_at = quote_at.dt.tz_localize("Asia/Shanghai", ambiguous="NaT", nonexistent="NaT")
            df["quote_at"] = quote_at.apply(
                lambda value: value.isoformat(timespec="seconds") if not pd.isna(value) else None
            )
        else:
            df["quote_at"] = None
        df["fetched_at"] = fetched_at
        df["update_at"] = df["quote_at"]
        df["timestamp_kind"] = "source_quote_at"
        return df.reset_index(drop=True)
