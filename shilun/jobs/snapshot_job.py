from __future__ import annotations

import argparse
from copy import deepcopy
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

from shilun.common.config import AppConfig, load_config
from shilun.common.db import MarketSnapshotRecordStore, MongoSnapshotStore
from shilun.data import MongoMarketDataProvider, TushareConfig, TushareDailyClient
from shilun.jobs.snapshot_ranking import build_output_table, build_snapshot_record, rank_snapshot_records, render_markdown
from shilun.pipeline import ShilunPipeline


@dataclass(frozen=True)
class SnapshotJobRequest:
    target_date: str
    tickers: list[str] | None = None
    top_n: int = 100
    output_dir: str = "outputs"
    exclude_st: bool = False
    label_horizons: tuple[int, ...] = (1, 3, 5, 10)
    prefer_mongo_data: bool = True
    allow_tushare_fallback: bool = False


@dataclass(frozen=True)
class SnapshotJobResult:
    analysis_date: str
    scanned_count: int
    skipped_count: int
    report_path: Path
    csv_path: Path
    history_cache_path: Path


class SnapshotJob:
    """Run full-market feature, structure, and recommendation snapshots in batch."""

    def __init__(
        self,
        config: AppConfig | None = None,
        pipeline: ShilunPipeline | None = None,
        client: object | None = None,
        mongo_store: MongoSnapshotStore | None = None,
        market_snapshot_store: MarketSnapshotRecordStore | None = None,
    ) -> None:
        self.pipeline = pipeline or ShilunPipeline()
        self.config = config or load_config()
        self.mongo_store = mongo_store or (
            MongoSnapshotStore(self.config.mongo_uri or "", self.config.mongo_db) if self.config.mongo_uri else None
        )
        self.market_snapshot_store = market_snapshot_store or (
            getattr(self.mongo_store, "market_snapshots", self.mongo_store) if self.mongo_store is not None else None
        )
        self.client = client
        self._client_was_explicit = client is not None

    def __del__(self) -> None:  # pragma: no cover
        mongo_store = getattr(self, "mongo_store", None)
        if mongo_store is not None:
            try:
                mongo_store.close()
            except Exception:
                pass

    def _build_default_client(self) -> TushareDailyClient:
        return TushareDailyClient(
            TushareConfig(
                token=self.config.tushare_token or "",
                base_url=self.config.tushare_base_url or "",
                timeout=self.config.tushare_timeout,
                min_interval_seconds=self.config.tushare_min_interval_seconds,
            )
        )

    def run(self, request: SnapshotJobRequest) -> SnapshotJobResult:
        previous_client = self.client
        self.client = self._resolve_data_client(request)
        try:
            return self._run_once(request)
        except ValueError:
            if request.prefer_mongo_data and request.allow_tushare_fallback and not self._client_was_explicit:
                self.client = self._build_default_client()
                return self._run_once(replace(request, prefer_mongo_data=False))
            raise
        finally:
            self.client = previous_client

    def _resolve_data_client(self, request: SnapshotJobRequest) -> object:
        if self._client_was_explicit and self.client is not None:
            return self.client
        if request.prefer_mongo_data:
            if self.mongo_store is not None:
                return MongoMarketDataProvider(getattr(self.mongo_store, "raw_market", self.mongo_store))
            if not request.allow_tushare_fallback:
                raise ValueError("Mongo market data is required. Run TushareSyncJob first or enable Tushare fallback explicitly.")
        if request.allow_tushare_fallback:
            return self._build_default_client()
        raise ValueError("No market data provider configured for SnapshotJob.")

    def _run_once(self, request: SnapshotJobRequest) -> SnapshotJobResult:
        analysis_date = pd.Timestamp(request.target_date).strftime("%Y-%m-%d")
        top_n = max(1, int(request.top_n))
        label_horizons = tuple(sorted({int(horizon) for horizon in request.label_horizons if int(horizon) > 0}))
        output_dir = Path(request.output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        universe = self._load_universe(request.tickers, exclude_st=request.exclude_st)
        if universe.empty:
            raise ValueError("No eligible tickers found for the requested snapshot.")

        history_cache_path = output_dir / f"market_history_{analysis_date}.pkl"
        history = self._load_history(analysis_date, cache_path=history_cache_path, label_horizons=label_horizons)
        if history.empty:
            raise ValueError(f"No market data found for {analysis_date}.")

        today_ts = pd.Timestamp(analysis_date)
        eligible_today = set(history.loc[history["date"] == today_ts, "ticker"])
        universe = universe.loc[universe["ts_code"].isin(eligible_today)].reset_index(drop=True)
        if universe.empty:
            raise ValueError(f"No eligible stocks traded on {analysis_date}.")

        bars_by_ticker = {
            ticker: frame.sort_values("date").reset_index(drop=True)
            for ticker, frame in history.groupby("ticker", sort=False)
            if ticker in eligible_today
        }
        metadata_by_ticker = universe.set_index("ts_code").to_dict("index")
        daily_basic_by_ticker = self._load_daily_basic_map(analysis_date)
        sector_context_by_industry = self._build_sector_context_map(history=history, universe=universe, analysis_date=analysis_date)
        benchmark_bars = self._load_benchmark_bars(analysis_date)
        if self.pipeline.config.benchmark_ticker and (benchmark_bars is None or benchmark_bars.empty):
            raise ValueError(f"No benchmark data found for {self.pipeline.config.benchmark_ticker} in selected data source.")
        future_label_map = self._build_future_label_map(
            history=history,
            analysis_date=analysis_date,
            label_horizons=label_horizons,
        )
        benchmark_label_map = self._load_benchmark_label_map(
            analysis_date=analysis_date,
            label_horizons=label_horizons,
        )

        records: list[dict[str, object]] = []
        analysis_payloads: list[dict[str, object]] = []
        skipped_count = 0
        total = len(universe)
        for index, ticker in enumerate(universe["ts_code"].tolist(), start=1):
            bars = bars_by_ticker.get(ticker)
            if bars is None or bars.empty:
                skipped_count += 1
                continue

            try:
                analysis = self.pipeline.run_with_bars(
                    ticker=ticker,
                    analysis_date=analysis_date,
                    bars=bars,
                    benchmark_bars=benchmark_bars,
                    sector_context=sector_context_by_industry.get(str(metadata_by_ticker.get(ticker, {}).get("industry") or ""), {}),
                    daily_basic_context=daily_basic_by_ticker.get(ticker),
                    metadata_context=metadata_by_ticker.get(ticker),
                )
            except ValueError:
                skipped_count += 1
                continue

            latest_bar = bars.iloc[-1]
            metadata = metadata_by_ticker.get(ticker, {})
            record = build_snapshot_record(
                    ticker=ticker,
                    analysis=analysis,
                    latest_bar=latest_bar,
                    metadata=metadata,
                )
            record.update(future_label_map.get(ticker, {}))
            for horizon in label_horizons:
                benchmark_return = benchmark_label_map.get(f"benchmark_future_return_{horizon}d")
                future_return = record.get(f"future_return_{horizon}d")
                record[f"benchmark_future_return_{horizon}d"] = benchmark_return
                if benchmark_return is None or future_return is None:
                    record[f"excess_return_{horizon}d"] = None
                    record[f"outperform_benchmark_{horizon}d"] = None
                else:
                    excess_return = round(float(future_return) - float(benchmark_return), 4)
                    record[f"excess_return_{horizon}d"] = excess_return
                    record[f"outperform_benchmark_{horizon}d"] = int(excess_return > 0)
            records.append(record)
            analysis_payloads.append(
                self._build_analysis_payload(
                    ticker=ticker,
                    analysis_date=analysis_date,
                    analysis=analysis,
                    latest_bar=latest_bar,
                    metadata=metadata,
                    exclude_st=request.exclude_st,
                )
            )
            if index % 200 == 0:
                print(f"[snapshot] processed {index}/{total} stocks", flush=True)

        if not records:
            raise ValueError(f"No analysable stocks found for {analysis_date}.")

        ranked = rank_snapshot_records(records)
        ranked.insert(0, "rank", range(1, len(ranked) + 1))
        top_table = build_output_table(ranked.head(top_n))

        suffix = "_no_st" if request.exclude_st else ""
        csv_path = output_dir / f"market_top_{top_n}_{analysis_date}{suffix}.csv"
        report_path = output_dir / f"market_top_{top_n}_{analysis_date}{suffix}.md"
        top_table.to_csv(csv_path, index=False, encoding="utf-8-sig")
        report_path.write_text(
            render_markdown(
                analysis_date=analysis_date,
                scanned_count=len(ranked),
                skipped_count=skipped_count,
                table=top_table,
                exclude_st=request.exclude_st,
            ),
            encoding="utf-8",
        )
        market_snapshot_store = self.market_snapshot_store or (
            getattr(self.mongo_store, "market_snapshots", self.mongo_store) if self.mongo_store is not None else None
        )
        if market_snapshot_store is not None:
            try:
                rank_by_ticker = {str(row["ticker"]): int(row["rank"]) for row in ranked.to_dict(orient="records")}
                for payload in analysis_payloads:
                    payload["rank"] = rank_by_ticker.get(str(payload.get("ticker") or ""))
                market_snapshot_store.upsert_market_snapshot(
                    analysis_date=analysis_date,
                    top_n=top_n,
                    exclude_st=request.exclude_st,
                    records=ranked.head(top_n).to_dict(orient="records"),
                )
                market_snapshot_store.upsert_market_snapshot_records(
                    analysis_date=analysis_date,
                    exclude_st=request.exclude_st,
                    records=ranked.to_dict(orient="records"),
                )
                market_snapshot_store.upsert_analysis_batch(analysis_payloads)
            except Exception:
                pass

        return SnapshotJobResult(
            analysis_date=analysis_date,
            scanned_count=len(ranked),
            skipped_count=skipped_count,
            report_path=report_path,
            csv_path=csv_path,
            history_cache_path=history_cache_path,
        )

    def _load_universe(self, requested_tickers: list[str] | None, *, exclude_st: bool) -> pd.DataFrame:
        universe = self.client.fetch_stock_basic(fields="ts_code,name,industry,market")
        if exclude_st and "name" in universe.columns:
            st_mask = universe["name"].astype(str).str.upper().str.contains("ST", na=False)
            universe = universe.loc[~st_mask].reset_index(drop=True)
        if not requested_tickers:
            return universe
        normalized = {ticker.strip().upper() for ticker in requested_tickers if ticker and ticker.strip()}
        if not normalized:
            return universe
        return universe.loc[universe["ts_code"].isin(normalized)].reset_index(drop=True)

    def _load_history(self, analysis_date: str, cache_path: Path, label_horizons: tuple[int, ...]) -> pd.DataFrame:
        analysis_dt = datetime.strptime(analysis_date, "%Y-%m-%d")
        max_label_horizon = max(label_horizons, default=0)
        calendar_end_dt = analysis_dt + timedelta(days=max_label_horizon * 4 + 10)
        if cache_path.exists():
            cached_history = pd.read_pickle(cache_path)
            if not cached_history.empty and "date" in cached_history.columns:
                cached_max_date = pd.to_datetime(cached_history["date"]).max()
                if pd.notna(cached_max_date) and cached_max_date >= pd.Timestamp(calendar_end_dt.date()):
                    print(f"[snapshot] using cached history: {cache_path}", flush=True)
                    return cached_history
            print(f"[snapshot] refreshing stale history cache: {cache_path}", flush=True)

        start_date = (analysis_dt - timedelta(days=self.pipeline.config.lookback_days)).strftime("%Y%m%d")
        calendar = self.client.fetch_trade_calendar(
            start_date=start_date,
            end_date=calendar_end_dt.strftime("%Y%m%d"),
        )
        open_dates = calendar.loc[calendar["is_open"] == 1, "cal_date"].tolist()
        history_frames: list[pd.DataFrame] = []
        for index, trade_date in enumerate(open_dates, start=1):
            frame = self.client.fetch_daily_for_trade_date(str(trade_date))
            if not frame.empty:
                history_frames.append(frame)
            if index % 20 == 0:
                print(f"[snapshot] fetched {index}/{len(open_dates)} trade dates", flush=True)
        if not history_frames:
            return pd.DataFrame(columns=["ticker", "date", "open", "high", "low", "close", "volume", "amount"])
        history = pd.concat(history_frames, ignore_index=True)
        history = history.dropna(subset=["ticker", "date"]).sort_values(["ticker", "date"]).reset_index(drop=True)
        history.to_pickle(cache_path)
        print(f"[snapshot] cached history to {cache_path}", flush=True)
        return history

    def _load_benchmark_label_map(
        self,
        *,
        analysis_date: str,
        label_horizons: tuple[int, ...],
    ) -> dict[str, float | None]:
        benchmark_ticker = self.pipeline.config.benchmark_ticker
        if not benchmark_ticker or not label_horizons:
            return {}
        max_label_horizon = max(label_horizons)
        analysis_dt = datetime.strptime(analysis_date, "%Y-%m-%d")
        end_dt = analysis_dt + timedelta(days=max_label_horizon * 4 + 10)
        try:
            benchmark_history = self.client.fetch_daily(
                benchmark_ticker,
                start_date=analysis_dt.strftime("%Y%m%d"),
                end_date=end_dt.strftime("%Y%m%d"),
            )
        except Exception:
            return {}
        if benchmark_history.empty:
            return {}
        benchmark_history = benchmark_history.sort_values("date").reset_index(drop=True)
        return self._compute_future_labels_for_frame(
            benchmark_history,
            analysis_date=analysis_date,
            label_horizons=label_horizons,
            prefix="benchmark_future_return_",
            include_path_metrics=False,
        )

    def _load_benchmark_bars(self, analysis_date: str) -> pd.DataFrame | None:
        benchmark_ticker = self.pipeline.config.benchmark_ticker
        if not benchmark_ticker:
            return None
        analysis_dt = datetime.strptime(analysis_date, "%Y-%m-%d")
        start_dt = analysis_dt - timedelta(days=self.pipeline.config.lookback_days)
        try:
            return self.client.fetch_daily(
                benchmark_ticker,
                start_date=start_dt.strftime("%Y%m%d"),
                end_date=analysis_dt.strftime("%Y%m%d"),
            )
        except Exception:
            return None

    def _load_daily_basic_map(self, analysis_date: str) -> dict[str, dict[str, object]]:
        trade_date = pd.Timestamp(analysis_date).strftime("%Y%m%d")
        try:
            frame = self.client.fetch_daily_basic(trade_date=trade_date)
        except Exception:
            return {}
        if frame.empty or "ts_code" not in frame.columns:
            return {}
        return frame.set_index("ts_code").to_dict("index")

    @staticmethod
    def _build_sector_context_map(
        *,
        history: pd.DataFrame,
        universe: pd.DataFrame,
        analysis_date: str,
    ) -> dict[str, dict[str, object]]:
        merged = history.merge(universe[["ts_code", "industry"]], left_on="ticker", right_on="ts_code", how="left")
        merged = merged.loc[merged["industry"].notna()].copy()
        if merged.empty:
            return {}
        sector_close = (
            merged.groupby(["industry", "date"], as_index=False)["close"]
            .mean()
            .sort_values(["industry", "date"])
            .reset_index(drop=True)
        )
        sector_close["sector_return_5d"] = sector_close.groupby("industry")["close"].pct_change(5)
        sector_close["sector_return_20d"] = sector_close.groupby("industry")["close"].pct_change(20)
        latest = sector_close.loc[sector_close["date"] == pd.Timestamp(analysis_date)].copy()
        context: dict[str, dict[str, object]] = {}
        for row in latest.itertuples(index=False):
            sector_trend_score = 50.0
            sector_trend_score += max(-12.0, min(12.0, float(getattr(row, "sector_return_20d") or 0.0) * 140.0))
            sector_trend_score += max(-10.0, min(10.0, float(getattr(row, "sector_return_5d") or 0.0) * 120.0))
            sector_strength_score = 50.0
            sector_strength_score += max(-10.0, min(10.0, float(getattr(row, "sector_return_5d") or 0.0) * 100.0))
            sector_strength_score += max(-8.0, min(8.0, float(getattr(row, "sector_return_20d") or 0.0) * 90.0))
            sector_stage = "range"
            if float(getattr(row, "sector_return_20d") or 0.0) > 0:
                sector_stage = "early" if float(getattr(row, "sector_return_20d") or 0.0) < 0.05 else "mid"
                if float(getattr(row, "sector_return_20d") or 0.0) >= 0.12:
                    sector_stage = "late"
            context[str(row.industry)] = {
                "industry": str(row.industry),
                "sector_return_5d": round(float(getattr(row, "sector_return_5d") or 0.0), 4),
                "sector_return_20d": round(float(getattr(row, "sector_return_20d") or 0.0), 4),
                "sector_trend_score": round(max(0.0, min(100.0, sector_trend_score)), 4),
                "sector_strength_score": round(max(0.0, min(100.0, sector_strength_score)), 4),
                "sector_stage": sector_stage,
            }
        return context

    @classmethod
    def _build_future_label_map(
        cls,
        *,
        history: pd.DataFrame,
        analysis_date: str,
        label_horizons: tuple[int, ...],
    ) -> dict[str, dict[str, float | int | None]]:
        if history.empty or not label_horizons:
            return {}
        label_map: dict[str, dict[str, float | int | None]] = {}
        for ticker, frame in history.groupby("ticker", sort=False):
            label_map[str(ticker)] = cls._compute_future_labels_for_frame(
                frame.sort_values("date").reset_index(drop=True),
                analysis_date=analysis_date,
                label_horizons=label_horizons,
            )
        return label_map

    @staticmethod
    def _compute_future_labels_for_frame(
        frame: pd.DataFrame,
        *,
        analysis_date: str,
        label_horizons: tuple[int, ...],
        prefix: str = "future_return_",
        include_path_metrics: bool = True,
    ) -> dict[str, float | None]:
        if frame.empty or "date" not in frame.columns or "close" not in frame.columns:
            return {}
        ordered = frame.sort_values("date").reset_index(drop=True)
        analysis_ts = pd.Timestamp(analysis_date)
        current_rows = ordered.index[ordered["date"] == analysis_ts].tolist()
        if not current_rows:
            labels = {f"{prefix}{horizon}d": None for horizon in label_horizons}
            if include_path_metrics:
                for horizon in label_horizons:
                    labels[f"future_max_runup_{horizon}d"] = None
                    labels[f"future_max_drawdown_{horizon}d"] = None
            return labels
        current_index = int(current_rows[-1])
        current_close = float(ordered.iloc[current_index]["close"])
        labels: dict[str, float | None] = {}
        for horizon in label_horizons:
            future_index = current_index + int(horizon)
            key = f"{prefix}{horizon}d"
            if future_index >= len(ordered) or current_close == 0:
                labels[key] = None
                if include_path_metrics:
                    labels[f"future_max_runup_{horizon}d"] = None
                    labels[f"future_max_drawdown_{horizon}d"] = None
                continue
            future_close = float(ordered.iloc[future_index]["close"])
            labels[key] = round(future_close / current_close - 1.0, 4)
            if not include_path_metrics:
                continue
            future_window = ordered.iloc[current_index + 1 : future_index + 1]
            max_high = float(future_window["high"].max()) if "high" in future_window.columns else future_close
            min_low = float(future_window["low"].min()) if "low" in future_window.columns else future_close
            labels[f"future_max_runup_{horizon}d"] = round(max(0.0, max_high / current_close - 1.0), 4)
            labels[f"future_max_drawdown_{horizon}d"] = round(min(0.0, min_low / current_close - 1.0), 4)
        return labels

    @staticmethod
    def _build_analysis_payload(
        *,
        ticker: str,
        analysis_date: str,
        analysis: dict[str, object],
        latest_bar: pd.Series,
        metadata: dict[str, object],
        exclude_st: bool,
    ) -> dict[str, object]:
        latest_bar_payload = {
            "ticker": ticker,
            "date": pd.Timestamp(latest_bar["date"]).strftime("%Y-%m-%d") if "date" in latest_bar else analysis_date,
            "open": float(latest_bar.get("open", 0.0) or 0.0),
            "high": float(latest_bar.get("high", 0.0) or 0.0),
            "low": float(latest_bar.get("low", 0.0) or 0.0),
            "close": float(latest_bar.get("close", 0.0) or 0.0),
            "volume": float(latest_bar.get("volume", 0.0) or 0.0),
            "amount": float(latest_bar.get("amount", 0.0) or 0.0),
        }
        return {
            "ticker": ticker,
            "analysis_date": analysis_date,
            "exclude_st": bool(exclude_st),
            "metadata": deepcopy(metadata),
            "latest_bar": latest_bar_payload,
            "snapshot": deepcopy(analysis.get("snapshot") or {}),
            "decision": deepcopy(analysis.get("decision") or {}),
        }


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a full-market Shilun snapshot and export the top-ranked table.")
    parser.add_argument("--date", required=True, help="Analysis date in YYYY-MM-DD format.")
    parser.add_argument("--top-n", type=int, default=100, help="How many ranked rows to export.")
    parser.add_argument("--output-dir", default="outputs", help="Directory for CSV/Markdown outputs.")
    parser.add_argument("--tickers", nargs="*", help="Optional explicit ticker list.")
    parser.add_argument("--exclude-st", action="store_true", help="Exclude ST and *ST stocks from the universe.")
    parser.add_argument(
        "--allow-tushare-fallback",
        action="store_true",
        help="Allow SnapshotJob to call Tushare if Mongo market data is missing.",
    )
    args = parser.parse_args()

    job = SnapshotJob()
    result = job.run(
        SnapshotJobRequest(
            target_date=args.date,
            tickers=args.tickers,
            top_n=args.top_n,
            output_dir=args.output_dir,
            exclude_st=args.exclude_st,
            allow_tushare_fallback=args.allow_tushare_fallback,
        )
    )
    print(f"analysis_date={result.analysis_date}")
    print(f"scanned_count={result.scanned_count}")
    print(f"skipped_count={result.skipped_count}")
    print(f"csv_path={result.csv_path}")
    print(f"report_path={result.report_path}")
    print(f"history_cache_path={result.history_cache_path}")


if __name__ == "__main__":
    main()
