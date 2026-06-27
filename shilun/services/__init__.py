from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
import math
from typing import Any

import pandas as pd

from shilun.common.config import AppConfig, load_config
from shilun.common.db import MongoSnapshotStore
from shilun.data import MongoMarketDataProvider
from shilun.pipeline import PipelineConfig, ShilunPipeline


class _MongoOnlyImporter:
    """Placeholder importer so Mongo-first analysis never builds a Tushare client."""


@dataclass(frozen=True)
class AnalyzeRequest:
    ticker: str
    analysis_date: str
    allow_tushare_fallback: bool = False


class MongoFirstAnalysisService:
    """Single-stock analysis service that reads market data from Mongo first."""

    def __init__(
        self,
        *,
        config: AppConfig | None = None,
        pipeline: Any | None = None,
        market_data_provider: Any | None = None,
        mongo_store: MongoSnapshotStore | None = None,
    ) -> None:
        self.config = config or load_config()
        self.pipeline = pipeline or ShilunPipeline(importer=_MongoOnlyImporter())
        self.mongo_store = (
            mongo_store
            if mongo_store is not None
            else self._build_mongo_store()
            if market_data_provider is None
            else None
        )
        self.market_data_provider = market_data_provider or (
            MongoMarketDataProvider(getattr(self.mongo_store, "raw_market", self.mongo_store)) if self.mongo_store is not None else None
        )
        self._fallback_pipeline: ShilunPipeline | None = None

    def __del__(self) -> None:  # pragma: no cover
        if self.mongo_store is not None:
            try:
                self.mongo_store.close()
            except Exception:
                pass

    def analyze(self, request: AnalyzeRequest) -> dict[str, Any]:
        ticker = request.ticker.upper()
        analysis_date = pd.Timestamp(request.analysis_date).strftime("%Y-%m-%d")
        if self.market_data_provider is None:
            if request.allow_tushare_fallback:
                return self._analyze_with_tushare_fallback(ticker=ticker, analysis_date=analysis_date)
            raise ValueError("Mongo market data is required. Run TushareSyncJob first or enable Tushare fallback explicitly.")
        try:
            return self._analyze_from_mongo(ticker=ticker, analysis_date=analysis_date)
        except ValueError:
            if request.allow_tushare_fallback:
                return self._analyze_with_tushare_fallback(ticker=ticker, analysis_date=analysis_date)
            raise

    def _analyze_from_mongo(self, *, ticker: str, analysis_date: str) -> dict[str, Any]:
        start_date, end_date = self._date_window(analysis_date)
        bars = self.market_data_provider.fetch_daily(ticker, start_date=start_date, end_date=end_date)
        if bars.empty:
            raise ValueError(f"No Mongo market data found for {ticker} before {analysis_date}. Run TushareSyncJob first.")
        metadata = self._load_metadata(ticker)
        daily_basic = self._load_daily_basic(ticker=ticker, analysis_date=analysis_date)
        benchmark_bars = self._load_benchmark_bars(start_date=start_date, end_date=end_date)
        result = self.pipeline.run_with_bars(
            ticker=ticker,
            analysis_date=analysis_date,
            bars=bars,
            benchmark_bars=benchmark_bars,
            sector_context={
                "industry": metadata.get("industry"),
                "market": metadata.get("market"),
            },
            daily_basic_context=daily_basic,
            metadata_context=metadata,
        )
        result["market_overview"] = self._build_market_overview(
            bars=bars,
            analysis_date=analysis_date,
            metadata=metadata,
            daily_basic=daily_basic,
        )
        result["data_source"] = "mongo"
        return result

    @staticmethod
    def _build_market_overview(
        *,
        bars: pd.DataFrame,
        analysis_date: str,
        metadata: dict[str, Any],
        daily_basic: dict[str, Any],
    ) -> dict[str, Any]:
        frame = bars.copy()
        rename_map = {}
        if "trade_date" in frame.columns and "date" not in frame.columns:
            rename_map["trade_date"] = "date"
        if "vol" in frame.columns and "volume" not in frame.columns:
            rename_map["vol"] = "volume"
        if rename_map:
            frame = frame.rename(columns=rename_map)
        frame["date"] = pd.to_datetime(frame.get("date"), errors="coerce")
        for column in ("open", "high", "low", "close", "volume", "amount"):
            if column not in frame.columns:
                frame[column] = 0.0
            frame[column] = pd.to_numeric(frame[column], errors="coerce").fillna(0.0)
        frame = frame.loc[frame["date"] <= pd.Timestamp(analysis_date)].sort_values("date").reset_index(drop=True)
        if frame.empty:
            return {}
        frame["ma5"] = frame["close"].rolling(5, min_periods=1).mean()
        frame["ma10"] = frame["close"].rolling(10, min_periods=1).mean()
        frame["ma20"] = frame["close"].rolling(20, min_periods=1).mean()
        frame["pct_chg"] = frame["close"].pct_change().fillna((frame["close"] / frame["open"]) - 1.0)
        latest = frame.iloc[-1]
        previous = frame.iloc[-2] if len(frame) >= 2 else latest

        def number(value: Any, digits: int = 4) -> float | None:
            try:
                parsed = float(value)
            except (TypeError, ValueError):
                return None
            return round(parsed, digits) if math.isfinite(parsed) else None

        series = [
            {
                "date": row["date"].strftime("%Y-%m-%d"),
                "close": number(row.get("close")),
                "pct_chg": number(row.get("pct_chg"), 6),
                "ma5": number(row.get("ma5")),
                "ma10": number(row.get("ma10")),
                "ma20": number(row.get("ma20")),
                "amount": number(row.get("amount"), 2),
            }
            for _, row in frame.tail(60).iterrows()
        ]
        return {
            "name": metadata.get("name"),
            "industry": metadata.get("industry"),
            "market": metadata.get("market"),
            "analysis_date": latest["date"].strftime("%Y-%m-%d"),
            "open": number(latest.get("open")),
            "high": number(latest.get("high")),
            "low": number(latest.get("low")),
            "close": number(latest.get("close")),
            "previous_close": number(previous.get("close")),
            "pct_chg": number(latest.get("pct_chg"), 6),
            "volume": number(latest.get("volume"), 2),
            "amount": number(latest.get("amount"), 2),
            "turnover_rate": number(daily_basic.get("turnover_rate_f") or daily_basic.get("turnover_rate")),
            "total_mv": number(daily_basic.get("total_mv"), 2),
            "circ_mv": number(daily_basic.get("circ_mv"), 2),
            "pe": number(daily_basic.get("pe_ttm") or daily_basic.get("pe")),
            "pb": number(daily_basic.get("pb")),
            "ps": number(daily_basic.get("ps_ttm") or daily_basic.get("ps")),
            "price_series": series,
            "data_frequency": "daily",
        }

    def _analyze_with_tushare_fallback(self, *, ticker: str, analysis_date: str) -> dict[str, Any]:
        if self._fallback_pipeline is None:
            self._fallback_pipeline = ShilunPipeline()
        result = self._fallback_pipeline.run(ticker=ticker, analysis_date=analysis_date)
        result["data_source"] = "tushare_fallback"
        return result

    def _date_window(self, analysis_date: str) -> tuple[str, str]:
        end_dt = datetime.strptime(analysis_date, "%Y-%m-%d")
        lookback_days = int(getattr(getattr(self.pipeline, "config", None), "lookback_days", PipelineConfig.lookback_days))
        start_dt = end_dt - timedelta(days=lookback_days)
        return start_dt.strftime("%Y%m%d"), end_dt.strftime("%Y%m%d")

    def _load_metadata(self, ticker: str) -> dict[str, Any]:
        frame = self.market_data_provider.fetch_stock_basic(fields="ts_code,name,industry,market")
        if frame.empty or "ts_code" not in frame.columns:
            return {}
        matched = frame.loc[frame["ts_code"] == ticker]
        if matched.empty:
            return {}
        return matched.iloc[0].to_dict()

    def _load_daily_basic(self, *, ticker: str, analysis_date: str) -> dict[str, Any]:
        trade_date = pd.Timestamp(analysis_date).strftime("%Y%m%d")
        frame = self.market_data_provider.fetch_daily_basic(ts_code=ticker, trade_date=trade_date)
        if frame.empty:
            return {}
        return frame.iloc[0].to_dict()

    def _load_benchmark_bars(self, *, start_date: str, end_date: str) -> pd.DataFrame | None:
        benchmark_ticker = getattr(getattr(self.pipeline, "config", None), "benchmark_ticker", None)
        if not benchmark_ticker:
            return None
        frame = self.market_data_provider.fetch_daily(benchmark_ticker, start_date=start_date, end_date=end_date)
        return None if frame.empty else frame

    def _build_mongo_store(self) -> MongoSnapshotStore | None:
        if not self.config.mongo_uri:
            return None
        try:
            return MongoSnapshotStore(self.config.mongo_uri, self.config.mongo_db)
        except Exception:
            return None

__all__ = ["AnalyzeRequest", "MongoFirstAnalysisService"]
