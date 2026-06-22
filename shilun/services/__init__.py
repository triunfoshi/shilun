from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
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
        self.mongo_store = mongo_store if mongo_store is not None else self._build_mongo_store()
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
        result["data_source"] = "mongo"
        return result

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
