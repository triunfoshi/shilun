from __future__ import annotations

from typing import Any

import pandas as pd

from shilun.common.db import MongoSnapshotStore, RawMarketDataStore


class MongoMarketDataProvider:
    """Read market data from Mongo with the same surface used by SnapshotJob.

    三期改进点：策略扫描默认读 Mongo，同步层才允许触达 Tushare。
    """

    def __init__(self, store: MongoSnapshotStore | RawMarketDataStore) -> None:
        # M5 P3: provider only needs raw market data. Passing the full
        # MongoSnapshotStore still works, but internally we narrow the surface.
        self.store = getattr(store, "raw_market", store)

    def fetch_stock_basic(
        self,
        list_status: str = "L",
        fields: str = "ts_code,symbol,name,area,industry,market,list_date",
    ) -> pd.DataFrame:
        columns = [column.strip() for column in fields.split(",") if column.strip()]
        frame = pd.DataFrame(self.store.find_stock_basic())
        if frame.empty:
            return pd.DataFrame(columns=columns)
        for column in columns:
            if column not in frame.columns:
                frame[column] = pd.NA
        return frame[columns].reset_index(drop=True)

    def fetch_trade_calendar(self, start_date: str, end_date: str, exchange: str = "SSE") -> pd.DataFrame:
        records = self.store.find_trade_calendar(start_date=start_date, end_date=end_date, exchange=exchange)
        if not records:
            return pd.DataFrame(columns=["exchange", "cal_date", "is_open", "pretrade_date"])
        frame = pd.DataFrame(records)
        if "is_open" in frame.columns:
            frame["is_open"] = pd.to_numeric(frame["is_open"], errors="coerce").fillna(0).astype(int)
        return frame.sort_values("cal_date").reset_index(drop=True)

    def fetch_daily(self, ts_code: str, start_date: str, end_date: str) -> pd.DataFrame:
        records = self.store.find_daily_bars(start_date=start_date, end_date=end_date, tickers=[ts_code])
        return self._daily_frame(records)

    def fetch_daily_for_trade_date(self, trade_date: str) -> pd.DataFrame:
        records = self.store.find_daily_bars_for_trade_date(trade_date)
        return self._daily_frame(records)

    def fetch_daily_basic(
        self,
        ts_code: str | None = None,
        trade_date: str | None = None,
        fields: str = (
            "ts_code,trade_date,turnover_rate,turnover_rate_f,volume_ratio,pe,pb,ps_ttm,"
            "dv_ttm,total_share,float_share,free_share,total_mv,circ_mv"
        ),
    ) -> pd.DataFrame:
        columns = [column.strip() for column in fields.split(",") if column.strip()]
        if not trade_date:
            return pd.DataFrame(columns=columns)
        records = self.store.find_daily_basic(trade_date=trade_date, ts_code=ts_code)
        frame = pd.DataFrame(records)
        if frame.empty:
            return pd.DataFrame(columns=columns)
        for column in columns:
            if column not in frame.columns:
                frame[column] = pd.NA
        if "trade_date" in frame.columns:
            frame["trade_date"] = pd.to_datetime(frame["trade_date"], format="%Y%m%d", errors="coerce")
        return frame[columns].reset_index(drop=True)

    def fetch_fina_indicator(
        self,
        ts_code: str,
        limit: int = 4,
        fields: str = (
            "ts_code,end_date,ann_date,roe_dt,or_yoy,op_yoy,q_sales_yoy,q_dtprofit_yoy,"
            "grossprofit_margin,debt_to_assets,ocf_to_or,current_ratio,quick_ratio"
        ),
    ) -> pd.DataFrame:
        return pd.DataFrame(columns=[column.strip() for column in fields.split(",") if column.strip()])

    def fetch_chip_perf(self, ts_code: str, trade_date: str) -> pd.DataFrame:
        return pd.DataFrame(columns=["ts_code", "trade_date"])

    @staticmethod
    def _daily_frame(records: list[dict[str, Any]]) -> pd.DataFrame:
        columns = ["ticker", "date", "open", "high", "low", "close", "volume", "amount"]
        frame = pd.DataFrame(records)
        if frame.empty:
            return pd.DataFrame(columns=columns)
        for column in columns:
            if column not in frame.columns:
                frame[column] = pd.NA
        frame = frame[columns].copy()
        frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
        for column in ["open", "high", "low", "close", "volume", "amount"]:
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
        return frame.sort_values(["ticker", "date"]).reset_index(drop=True)
