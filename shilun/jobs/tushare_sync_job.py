from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timedelta

from shilun.common.config import load_config
from shilun.common.db import MongoSnapshotStore, RawMarketDataStore
from shilun.data import TushareConfig, TushareDailyClient
from shilun.market.part1 import BENCHMARK_INDEX_OPTIONS


@dataclass(frozen=True)
class TushareSyncRequest:
    target_date: str
    start_date: str | None = None
    end_date: str | None = None
    lookback_days: int = 360
    label_horizons: tuple[int, ...] = (1, 3, 5, 10)
    exchange: str = "SSE"
    benchmark_ticker: str | None = "000300.SH"
    latest_only: bool = False
    incremental: bool = False
    skip_if_exists: bool = False
    continue_on_error: bool = False
    progress: bool = False
    incremental_lookback_days: int = 14
    incremental_overlap_days: int = 3
    refresh_stock_basic: bool = False
    benchmark_lookback_days: int = 120
    sync_moneyflow: bool = True
    sync_all_benchmarks: bool = False


@dataclass(frozen=True)
class TushareSyncResult:
    target_date: str
    sync_trade_date: str
    start_date: str
    end_date: str
    stock_basic_count: int
    trade_calendar_count: int
    daily_bar_count: int
    daily_basic_count: int
    moneyflow_count: int
    benchmark_bar_count: int
    skipped: bool = False
    message: str = ""
    failed_trade_dates: tuple[str, ...] = ()
    synced_trade_dates: tuple[str, ...] = ()
    skipped_trade_dates: tuple[str, ...] = ()


class TushareSyncJob:
    """Sync Tushare market data into Mongo without running strategies."""

    def __init__(
        self,
        *,
        client: TushareDailyClient | None = None,
        mongo_store: MongoSnapshotStore | None = None,
        raw_market_store: RawMarketDataStore | None = None,
    ) -> None:
        config = load_config()
        self.client = client or TushareDailyClient(
            TushareConfig(
                token=config.tushare_token or "",
                base_url=config.tushare_base_url or "",
                timeout=config.tushare_timeout,
                min_interval_seconds=config.tushare_min_interval_seconds,
            )
        )
        if mongo_store is None and raw_market_store is None:
            if not config.mongo_uri:
                raise ValueError("SHILUN_MONGO_URI is required for TushareSyncJob.")
            mongo_store = MongoSnapshotStore(config.mongo_uri, config.mongo_db)
        self.mongo_store = mongo_store
        self.raw_market_store = raw_market_store or getattr(mongo_store, "raw_market", mongo_store)

    def __del__(self) -> None:  # pragma: no cover
        mongo_store = getattr(self, "mongo_store", None)
        if mongo_store is not None:
            try:
                mongo_store.close()
            except Exception:
                pass

    def run(self, request: TushareSyncRequest) -> TushareSyncResult:
        if request.incremental:
            return self._run_incremental(request)
        if request.latest_only:
            return self._run_latest_trade_date(request)
        return self._run_history_window(request)

    def _run_incremental(self, request: TushareSyncRequest) -> TushareSyncResult:
        """Poll Tushare for only locally-missing trade dates.

        这是主动轮询式增量：对方 API 不推送 binlog，我们用本地 sync_state
        作为水位线，并用 Mongo 唯一键保证重复窗口安全。
        """
        target_dt = datetime.strptime(request.target_date, "%Y-%m-%d")
        target_trade_date = target_dt.strftime("%Y%m%d")
        calendar_start = request.start_date or self._incremental_calendar_start(request, target_dt)
        calendar_end = request.end_date or target_trade_date

        trade_calendar = self.client.fetch_trade_calendar(
            start_date=calendar_start,
            end_date=calendar_end,
            exchange=request.exchange,
        )
        trade_calendar_count = self.raw_market_store.upsert_trade_calendar(trade_calendar)
        open_dates = sorted(
            str(value)
            for value in (trade_calendar.loc[trade_calendar["is_open"] == 1, "cal_date"].tolist() if not trade_calendar.empty else [])
            if str(value) <= target_trade_date
        )
        if not open_dates:
            self._upsert_sync_state(
                {
                    "dataset": "tushare_daily",
                    "scope": request.exchange,
                    "status": "no_open_trade_date",
                    "target_date": request.target_date,
                    "start_date": calendar_start,
                    "end_date": calendar_end,
                    "updated_at": datetime.now().isoformat(timespec="seconds"),
                }
            )
            return TushareSyncResult(
                target_date=request.target_date,
                sync_trade_date="",
                start_date=calendar_start,
                end_date=calendar_end,
                stock_basic_count=0,
                trade_calendar_count=trade_calendar_count,
                daily_bar_count=0,
                daily_basic_count=0,
                moneyflow_count=0,
                benchmark_bar_count=0,
                skipped=True,
                message=f"No open trade date found before {request.target_date}.",
            )

        stock_basic_count = 0
        if request.refresh_stock_basic or not self._find_sync_state("tushare_stock_basic", "all"):
            stock_basic = self.client.fetch_stock_basic(fields="ts_code,symbol,name,area,industry,market,list_date")
            stock_basic_count = self.raw_market_store.upsert_stock_basic(stock_basic)
            self._upsert_sync_state(
                {
                    "dataset": "tushare_stock_basic",
                    "scope": "all",
                    "status": "success",
                    "row_count": stock_basic_count,
                    "updated_at": datetime.now().isoformat(timespec="seconds"),
                }
            )

        daily_bar_count = 0
        daily_basic_count = 0
        moneyflow_count = 0
        benchmark_bar_count = 0
        failed_trade_dates: list[str] = []
        synced_trade_dates: list[str] = []
        skipped_trade_dates: list[str] = []

        for index, trade_date in enumerate(open_dates, start=1):
            if request.skip_if_exists and self.raw_market_store.find_daily_bars_for_trade_date(trade_date):
                if request.sync_moneyflow and not self._has_moneyflow_for_trade_date(trade_date):
                    moneyflow_count += self._sync_moneyflow_for_trade_date(request, trade_date, failed_trade_dates)
                    synced_trade_dates.append(self._display_date(trade_date))
                    if request.progress:
                        print(f"[incremental] fill moneyflow {trade_date} ({index}/{len(open_dates)})", flush=True)
                    continue
                skipped_trade_dates.append(self._display_date(trade_date))
                if request.progress:
                    print(f"[incremental] skip existing {trade_date} ({index}/{len(open_dates)})", flush=True)
                continue
            if request.progress:
                print(f"[incremental] sync missing {trade_date} ({index}/{len(open_dates)})", flush=True)
            try:
                daily_bars = self.client.fetch_daily_for_trade_date(trade_date)
                daily_bar_count += self.raw_market_store.upsert_daily_bars(daily_bars)
                daily_basic = self.client.fetch_daily_basic(trade_date=trade_date)
                daily_basic_count += self.raw_market_store.upsert_daily_basic(daily_basic)
                moneyflow_count += self._sync_moneyflow_for_trade_date(request, trade_date, failed_trade_dates)
                synced_trade_dates.append(self._display_date(trade_date))
            except Exception as error:
                failed_trade_dates.append(f"{trade_date}:{error}")
                if not request.continue_on_error:
                    raise

        benchmark_tickers = self._benchmark_tickers(request)
        benchmark_start = ""
        benchmark_end = ""
        if benchmark_tickers and not all(
            self._has_benchmark_window(open_dates[-1], ticker)
            for ticker in benchmark_tickers
        ):
            benchmark_start = self._benchmark_start_date(request, open_dates[-1])
            benchmark_end = open_dates[-1]
        elif benchmark_tickers and synced_trade_dates:
            benchmark_start = synced_trade_dates[0].replace("-", "")
            benchmark_end = synced_trade_dates[-1].replace("-", "")
        if benchmark_start and benchmark_end:
            for benchmark_ticker in benchmark_tickers:
                benchmark_bars = self.client.fetch_index_daily(
                    benchmark_ticker,
                    start_date=benchmark_start,
                    end_date=benchmark_end,
                )
                benchmark_bar_count += self.raw_market_store.upsert_daily_bars(benchmark_bars)

        latest_open_date = open_dates[-1]
        status = "partial_error" if failed_trade_dates else "success"
        self._upsert_sync_state(
            {
                "dataset": "tushare_daily",
                "scope": request.exchange,
                "status": status,
                "target_date": request.target_date,
                "last_trade_date": self._display_date(latest_open_date),
                "last_trade_date_raw": latest_open_date,
                "start_date": calendar_start,
                "end_date": calendar_end,
                "synced_trade_dates": synced_trade_dates,
                "skipped_trade_dates": skipped_trade_dates,
                "failed_trade_dates": failed_trade_dates,
                "daily_bar_count": daily_bar_count,
                "daily_basic_count": daily_basic_count,
                "moneyflow_count": moneyflow_count,
                "benchmark_bar_count": benchmark_bar_count,
                "updated_at": datetime.now().isoformat(timespec="seconds"),
            }
        )
        skipped = not synced_trade_dates and not failed_trade_dates
        return TushareSyncResult(
            target_date=request.target_date,
            sync_trade_date=self._display_date(latest_open_date),
            start_date=calendar_start,
            end_date=calendar_end,
            stock_basic_count=stock_basic_count,
            trade_calendar_count=trade_calendar_count,
            daily_bar_count=daily_bar_count,
            daily_basic_count=daily_basic_count,
            moneyflow_count=moneyflow_count,
            benchmark_bar_count=benchmark_bar_count,
            skipped=skipped,
            message=(
                f"Incremental sync checked {len(open_dates)} open dates; "
                f"synced={len(synced_trade_dates)} skipped_existing={len(skipped_trade_dates)} "
                f"failed={len(failed_trade_dates)}."
            ),
            failed_trade_dates=tuple(failed_trade_dates),
            synced_trade_dates=tuple(synced_trade_dates),
            skipped_trade_dates=tuple(skipped_trade_dates),
        )

    def _run_latest_trade_date(self, request: TushareSyncRequest) -> TushareSyncResult:
        """Sync only the latest open trade date on or before target_date.

        三期数据链路改进点：每日 5 点/7 点调度只补最新交易日，不重复扫一年历史。
        如果 Mongo 已有该交易日日线数据，直接跳过并返回明确状态。
        """
        target_dt = datetime.strptime(request.target_date, "%Y-%m-%d")
        target_trade_date = target_dt.strftime("%Y%m%d")
        calendar_start = (target_dt - timedelta(days=14)).strftime("%Y%m%d")
        calendar_end = target_trade_date

        trade_calendar = self.client.fetch_trade_calendar(
            start_date=calendar_start,
            end_date=calendar_end,
            exchange=request.exchange,
        )
        trade_calendar_count = self.raw_market_store.upsert_trade_calendar(trade_calendar)
        open_dates = sorted(
            str(value)
            for value in (trade_calendar.loc[trade_calendar["is_open"] == 1, "cal_date"].tolist() if not trade_calendar.empty else [])
            if str(value) <= target_trade_date
        )
        if not open_dates:
            return TushareSyncResult(
                target_date=request.target_date,
                sync_trade_date="",
                start_date=calendar_start,
                end_date=calendar_end,
                stock_basic_count=0,
                trade_calendar_count=trade_calendar_count,
                daily_bar_count=0,
                daily_basic_count=0,
                moneyflow_count=0,
                benchmark_bar_count=0,
                skipped=True,
                message=f"No open trade date found before {request.target_date}.",
            )
        sync_trade_date = open_dates[-1]
        existing_rows = self.raw_market_store.find_daily_bars_for_trade_date(sync_trade_date)
        benchmark_tickers = self._benchmark_tickers(request)
        benchmark_ready = all(
            self._has_benchmark_window(sync_trade_date, ticker)
            for ticker in benchmark_tickers
        )
        moneyflow_ready = (not request.sync_moneyflow) or self._has_moneyflow_for_trade_date(sync_trade_date)
        if request.skip_if_exists and existing_rows and benchmark_ready and moneyflow_ready:
            return TushareSyncResult(
                target_date=request.target_date,
                sync_trade_date=self._display_date(sync_trade_date),
                start_date=sync_trade_date,
                end_date=sync_trade_date,
                stock_basic_count=0,
                trade_calendar_count=trade_calendar_count,
                daily_bar_count=len(existing_rows),
                daily_basic_count=0,
                moneyflow_count=0,
                benchmark_bar_count=0,
                skipped=True,
                message=f"Mongo already has {len(existing_rows)} daily bars for {self._display_date(sync_trade_date)}.",
            )

        stock_basic = self.client.fetch_stock_basic(fields="ts_code,symbol,name,area,industry,market,list_date")
        stock_basic_count = self.raw_market_store.upsert_stock_basic(stock_basic)

        daily_bars = self.client.fetch_daily_for_trade_date(sync_trade_date)
        daily_bar_count = self.raw_market_store.upsert_daily_bars(daily_bars)

        daily_basic = self.client.fetch_daily_basic(trade_date=sync_trade_date)
        daily_basic_count = self.raw_market_store.upsert_daily_basic(daily_basic)
        failed_trade_dates: list[str] = []
        moneyflow_count = self._sync_moneyflow_for_trade_date(request, sync_trade_date, failed_trade_dates)

        benchmark_bar_count = 0
        for benchmark_ticker in benchmark_tickers:
            benchmark_bars = self.client.fetch_index_daily(
                benchmark_ticker,
                start_date=self._benchmark_start_date(request, sync_trade_date),
                end_date=sync_trade_date,
            )
            benchmark_bar_count += self.raw_market_store.upsert_daily_bars(benchmark_bars)

        return TushareSyncResult(
            target_date=request.target_date,
            sync_trade_date=self._display_date(sync_trade_date),
            start_date=sync_trade_date,
            end_date=sync_trade_date,
            stock_basic_count=stock_basic_count,
            trade_calendar_count=trade_calendar_count,
            daily_bar_count=daily_bar_count,
            daily_basic_count=daily_basic_count,
            moneyflow_count=moneyflow_count,
            benchmark_bar_count=benchmark_bar_count,
            message=f"Synced latest trade date {self._display_date(sync_trade_date)}; moneyflow_failed={len(failed_trade_dates)}.",
            failed_trade_dates=tuple(failed_trade_dates),
        )

    def _run_history_window(self, request: TushareSyncRequest) -> TushareSyncResult:
        target_dt = datetime.strptime(request.target_date, "%Y-%m-%d")
        max_horizon = max(request.label_horizons, default=0)
        start_date = request.start_date or (target_dt - timedelta(days=request.lookback_days)).strftime("%Y%m%d")
        end_date = request.end_date or (target_dt + timedelta(days=max_horizon * 4 + 10)).strftime("%Y%m%d")
        target_trade_date = target_dt.strftime("%Y%m%d")

        stock_basic = self.client.fetch_stock_basic(fields="ts_code,symbol,name,area,industry,market,list_date")
        stock_basic_count = self.raw_market_store.upsert_stock_basic(stock_basic)

        trade_calendar = self.client.fetch_trade_calendar(
            start_date=start_date,
            end_date=end_date,
            exchange=request.exchange,
        )
        trade_calendar_count = self.raw_market_store.upsert_trade_calendar(trade_calendar)

        daily_bar_count = 0
        moneyflow_count = 0
        open_dates = trade_calendar.loc[trade_calendar["is_open"] == 1, "cal_date"].tolist() if not trade_calendar.empty else []
        failed_trade_dates: list[str] = []
        for index, trade_date in enumerate(open_dates, start=1):
            trade_date_text = str(trade_date)
            if request.skip_if_exists and self.raw_market_store.find_daily_bars_for_trade_date(trade_date_text):
                if request.sync_moneyflow and not self._has_moneyflow_for_trade_date(trade_date_text):
                    if request.progress:
                        print(f"[history] fill moneyflow {trade_date_text} ({index}/{len(open_dates)})", flush=True)
                    moneyflow_count += self._sync_moneyflow_for_trade_date(request, trade_date_text, failed_trade_dates)
                    continue
                if request.progress:
                    print(f"[history] skip existing {trade_date_text} ({index}/{len(open_dates)})", flush=True)
                continue
            if request.progress:
                print(f"[history] sync daily {trade_date_text} ({index}/{len(open_dates)})", flush=True)
            try:
                daily_bars = self.client.fetch_daily_for_trade_date(trade_date_text)
                daily_bar_count += self.raw_market_store.upsert_daily_bars(daily_bars)
                moneyflow_count += self._sync_moneyflow_for_trade_date(request, trade_date_text, failed_trade_dates)
            except Exception as error:
                failed_trade_dates.append(f"{trade_date_text}:{error}")
                if not request.continue_on_error:
                    raise

        daily_basic = self.client.fetch_daily_basic(trade_date=target_trade_date)
        daily_basic_count = self.raw_market_store.upsert_daily_basic(daily_basic)

        benchmark_bar_count = 0
        for benchmark_ticker in self._benchmark_tickers(request):
            benchmark_bars = self.client.fetch_index_daily(
                benchmark_ticker,
                start_date=start_date,
                end_date=end_date,
            )
            benchmark_bar_count += self.raw_market_store.upsert_daily_bars(benchmark_bars)

        return TushareSyncResult(
            target_date=request.target_date,
            sync_trade_date=self._display_date(target_trade_date),
            start_date=start_date,
            end_date=end_date,
            stock_basic_count=stock_basic_count,
            trade_calendar_count=trade_calendar_count,
            daily_bar_count=daily_bar_count,
            daily_basic_count=daily_basic_count,
            moneyflow_count=moneyflow_count,
            benchmark_bar_count=benchmark_bar_count,
            message=(
                f"Synced history window {start_date} to {end_date}; "
                f"failed_dates={len(failed_trade_dates)}."
            ),
            failed_trade_dates=tuple(failed_trade_dates),
        )

    def _sync_moneyflow_for_trade_date(
        self,
        request: TushareSyncRequest,
        trade_date: str,
        failed_trade_dates: list[str],
    ) -> int:
        if not request.sync_moneyflow:
            return 0
        updater = getattr(self.raw_market_store, "upsert_moneyflow", None)
        if not callable(updater):
            return 0
        try:
            moneyflow = self.client.fetch_moneyflow(trade_date=trade_date)
            return int(updater(moneyflow))
        except Exception as error:
            failed_trade_dates.append(f"moneyflow:{trade_date}:{error}")
            return 0

    @staticmethod
    def _display_date(value: str) -> str:
        text = str(value)
        if len(text) == 8 and text.isdigit():
            return f"{text[:4]}-{text[4:6]}-{text[6:]}"
        return text

    def _incremental_calendar_start(self, request: TushareSyncRequest, target_dt: datetime) -> str:
        state = self._find_sync_state("tushare_daily", request.exchange) or {}
        last_raw = str(state.get("last_trade_date_raw") or state.get("last_trade_date") or "")
        if last_raw:
            normalized = last_raw.replace("-", "")
            if len(normalized) == 8 and normalized.isdigit():
                last_dt = datetime.strptime(normalized, "%Y%m%d")
                return (last_dt - timedelta(days=max(0, request.incremental_overlap_days))).strftime("%Y%m%d")
        return (target_dt - timedelta(days=max(1, request.incremental_lookback_days))).strftime("%Y%m%d")

    def _find_sync_state(self, dataset: str, scope: str) -> dict | None:
        finder = getattr(self.raw_market_store, "find_sync_state", None)
        if callable(finder):
            return finder(dataset=dataset, scope=scope)
        return None

    def _upsert_sync_state(self, payload: dict) -> None:
        updater = getattr(self.raw_market_store, "upsert_sync_state", None)
        if callable(updater):
            updater(payload)

    def _has_benchmark_bar(self, trade_date: str, benchmark_ticker: str | None) -> bool:
        if not benchmark_ticker:
            return True
        finder = getattr(self.raw_market_store, "find_daily_bars", None)
        if callable(finder):
            records = finder(start_date=trade_date, end_date=trade_date, tickers=[benchmark_ticker])
            return bool(records)
        records = self.raw_market_store.find_daily_bars_for_trade_date(trade_date)
        return any(str(record.get("ticker") or record.get("ts_code")) == benchmark_ticker for record in records)

    @staticmethod
    def _benchmark_tickers(request: TushareSyncRequest) -> tuple[str, ...]:
        tickers: list[str] = []
        if request.benchmark_ticker:
            tickers.append(request.benchmark_ticker.upper())
        if request.sync_all_benchmarks:
            tickers.extend(str(item["ticker"]).upper() for item in BENCHMARK_INDEX_OPTIONS)
        return tuple(dict.fromkeys(tickers))

    def _has_benchmark_window(self, trade_date: str, benchmark_ticker: str | None, min_records: int = 20) -> bool:
        if not benchmark_ticker:
            return True
        finder = getattr(self.raw_market_store, "find_daily_bars", None)
        if callable(finder):
            records = finder(
                start_date=self._benchmark_start_date(TushareSyncRequest(target_date=self._display_date(trade_date)), trade_date),
                end_date=trade_date,
                tickers=[benchmark_ticker],
            )
            return len(records) >= min_records and self._has_benchmark_bar(trade_date, benchmark_ticker)
        return self._has_benchmark_bar(trade_date, benchmark_ticker)

    def _has_moneyflow_for_trade_date(self, trade_date: str) -> bool:
        finder = getattr(self.raw_market_store, "find_moneyflow", None)
        if not callable(finder):
            return False
        normalized = str(trade_date).replace("-", "")
        records = finder(start_date=normalized, end_date=normalized)
        return bool(records)

    def _benchmark_start_date(self, request: TushareSyncRequest, end_trade_date: str) -> str:
        normalized = str(end_trade_date).replace("-", "")
        end_dt = datetime.strptime(normalized, "%Y%m%d")
        return (end_dt - timedelta(days=max(20, request.benchmark_lookback_days))).strftime("%Y%m%d")

    def _missing_benchmark_dates(self, open_dates: list[str], benchmark_ticker: str | None) -> list[str]:
        if not benchmark_ticker:
            return []
        return [self._display_date(trade_date) for trade_date in open_dates if not self._has_benchmark_bar(trade_date, benchmark_ticker)]


def _today_text() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def main() -> None:
    parser = argparse.ArgumentParser(description="Sync Tushare market data into Mongo without running strategies.")
    parser.add_argument("--date", default=_today_text(), help="Target date in YYYY-MM-DD format. Defaults to today.")
    parser.add_argument("--start-date", help="Optional sync start date in YYYYMMDD format.")
    parser.add_argument("--end-date", help="Optional sync end date in YYYYMMDD format.")
    parser.add_argument("--lookback-days", type=int, default=360, help="Lookback calendar days when start date is omitted.")
    parser.add_argument("--exchange", default="SSE", help="Trade calendar exchange.")
    parser.add_argument("--benchmark-ticker", default="000300.SH", help="Benchmark ticker to sync.")
    parser.add_argument("--latest-only", action="store_true", help="Sync only the latest open trade date on or before --date.")
    parser.add_argument("--incremental", action="store_true", help="Poll only locally-missing trade dates using sync_state watermarks.")
    parser.add_argument("--skip-if-exists", action="store_true", help="Skip latest-only sync when Mongo already has daily bars.")
    parser.add_argument("--history-year", action="store_true", help="Sync the latest one-year history ending at --date.")
    parser.add_argument("--incremental-lookback-days", type=int, default=14, help="Initial lookback window when sync_state is absent.")
    parser.add_argument("--incremental-overlap-days", type=int, default=3, help="Calendar overlap after the saved sync_state watermark.")
    parser.add_argument("--refresh-stock-basic", action="store_true", help="Refresh stock_basic during incremental sync.")
    parser.add_argument("--benchmark-lookback-days", type=int, default=120, help="Index lookback window for benchmark MA/support-pressure data.")
    parser.add_argument("--continue-on-error", action="store_true", help="Continue history sync when a single trade date times out.")
    parser.add_argument("--progress", action="store_true", help="Print per-trade-date progress during history sync.")
    args = parser.parse_args()

    start_date = args.start_date
    end_date = args.end_date
    lookback_days = args.lookback_days
    if args.history_year:
        target_dt = datetime.strptime(args.date, "%Y-%m-%d")
        start_date = start_date or (target_dt - timedelta(days=365)).strftime("%Y%m%d")
        end_date = end_date or target_dt.strftime("%Y%m%d")
        lookback_days = 365

    result = TushareSyncJob().run(
        TushareSyncRequest(
            target_date=args.date,
            start_date=start_date,
            end_date=end_date,
            lookback_days=lookback_days,
            exchange=args.exchange,
            benchmark_ticker=args.benchmark_ticker,
            incremental=args.incremental,
            latest_only=args.latest_only,
            skip_if_exists=args.skip_if_exists or args.history_year or args.incremental,
            continue_on_error=args.continue_on_error or args.history_year,
            progress=args.progress,
            incremental_lookback_days=args.incremental_lookback_days,
            incremental_overlap_days=args.incremental_overlap_days,
            refresh_stock_basic=args.refresh_stock_basic,
            benchmark_lookback_days=args.benchmark_lookback_days,
        )
    )
    print(f"target_date={result.target_date}")
    print(f"sync_trade_date={result.sync_trade_date}")
    print(f"start_date={result.start_date}")
    print(f"end_date={result.end_date}")
    print(f"skipped={result.skipped}")
    print(f"message={result.message}")
    print(f"stock_basic_count={result.stock_basic_count}")
    print(f"trade_calendar_count={result.trade_calendar_count}")
    print(f"daily_bar_count={result.daily_bar_count}")
    print(f"daily_basic_count={result.daily_basic_count}")
    print(f"moneyflow_count={result.moneyflow_count}")
    print(f"benchmark_bar_count={result.benchmark_bar_count}")
    print(f"failed_trade_dates={len(result.failed_trade_dates)}")
    print(f"synced_trade_dates={len(result.synced_trade_dates)}")
    print(f"skipped_trade_dates={len(result.skipped_trade_dates)}")
    for failed_trade_date in result.failed_trade_dates[:20]:
        print(f"failed_trade_date={failed_trade_date}")


def scheduler_main() -> None:
    from apscheduler.schedulers.blocking import BlockingScheduler
    from apscheduler.triggers.cron import CronTrigger

    scheduler = BlockingScheduler(timezone="Asia/Shanghai")

    def sync_latest() -> None:
        target_date = _today_text()
        result = TushareSyncJob().run(
            TushareSyncRequest(
                target_date=target_date,
                latest_only=True,
                skip_if_exists=True,
            )
        )
        print(
            "scheduled_tushare_sync "
            f"target_date={result.target_date} sync_trade_date={result.sync_trade_date} "
            f"skipped={result.skipped} daily_bar_count={result.daily_bar_count} message={result.message}",
            flush=True,
        )

    scheduler.add_job(
        sync_latest,
        CronTrigger(hour="5,7", minute=0, second=0, timezone="Asia/Shanghai"),
        id="tushare_latest_to_mongo_5_7",
        replace_existing=True,
    )
    print("Tushare sync scheduler started. Runs daily at 05:00 and 07:00 Asia/Shanghai.", flush=True)
    scheduler.start()


if __name__ == "__main__":
    main()
