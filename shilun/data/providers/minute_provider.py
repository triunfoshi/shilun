"""Dated minute bars with explicit, verified gateway index routing."""
from datetime import datetime, timedelta
import os
from zoneinfo import ZoneInfo

import pandas as pd

from shilun.common.config import _read_env_file
from shilun.data.providers.eastmoney_provider import EastmoneyClient
from shilun.data.providers.tushare_provider import TushareConfig, TushareDailyClient


class MinuteFetchError(RuntimeError):
    pass


class MinuteClient:
    def __init__(self, tushare=None, eastmoney=None, *, index_api=None):
        self.tushare = tushare
        self.eastmoney = eastmoney or EastmoneyClient()
        self.index_api = index_api or os.getenv("SHILUN_TUSHARE_INDEX_MINUTE_API") or _read_env_file().get(
            "SHILUN_TUSHARE_INDEX_MINUTE_API", "idx_mins")
        if self.index_api not in {"idx_mins", "stk_mins"}:
            raise ValueError("Unsupported index minute API")

    def fetch_minute_bars(self, ticker, period="5", *, trade_date=None):
        if str(period) not in {"1", "5", "15", "30", "60"}:
            raise ValueError("Unsupported minute interval")
        ticker = ticker.upper()
        end = pd.Timestamp(trade_date).date() if trade_date else datetime.now(ZoneInfo("Asia/Shanghai")).date()
        start = end - timedelta(days=7)
        from shilun.market.part1 import BENCHMARK_INDEX_OPTIONS
        api = self.index_api if ticker in {x["ticker"] for x in BENCHMARK_INDEX_OPTIONS} else "stk_mins"
        try:
            if self.tushare is None:
                config = TushareConfig.from_env()
                if not config.token:
                    raise ValueError("Tushare token not configured")
                self.tushare = TushareDailyClient(config)
            frame = self.tushare._call_api(api, ts_code=ticker, freq=f"{period}min",
                start_date=f"{start} 09:00:00", end_date=f"{end} 15:00:00")
            required = {"ts_code", "trade_time", "open", "close", "high", "low", "vol", "amount"}
            if frame is None or frame.empty or not required <= set(frame.columns) or len(frame) >= 8000:
                raise ValueError("Missing or truncated minute response")
            if not frame["ts_code"].eq(ticker).all():
                raise ValueError("Minute ticker mismatch")
            frame = frame.rename(columns={"trade_time": "datetime", "vol": "volume"}).copy()
            frame["datetime"] = pd.to_datetime(frame["datetime"], errors="raise")
            for column in ("open", "close", "high", "low", "volume", "amount"):
                frame[column] = pd.to_numeric(frame[column], errors="raise")
            valid = (frame["low"] > 0) & (frame["low"] <= frame[["open", "close"]].min(axis=1)) & (frame["high"] >= frame[["open", "close"]].max(axis=1))
            if not valid.all() or not frame["datetime"].dt.date.between(start, end).all():
                raise ValueError("Minute price/date mismatch")
            if trade_date and not frame["datetime"].dt.date.eq(end).any():
                raise ValueError("Requested date is unavailable")
            # Tushare minute volume is shares and amount is yuan; no daily-bar
            # lot/thousand-yuan conversions apply to these endpoints.
            frame = frame.drop_duplicates("datetime").sort_values("datetime").reset_index(drop=True)
            frame.attrs["source"] = f"tushare_{api}"
            return frame
        except Exception as primary_error:
            try:
                frame = self.eastmoney.fetch_minute_bars(ticker, period, trade_date=trade_date)
                if frame is None or frame.empty or (trade_date and not frame["datetime"].dt.date.eq(end).any()):
                    raise ValueError("Requested date is unavailable")
                frame.attrs["source"] = "eastmoney"
                return frame
            except Exception as fallback_error:
                raise MinuteFetchError(f"Minute sources unavailable: {type(primary_error).__name__}, {type(fallback_error).__name__}") from None
