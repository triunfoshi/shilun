"""日终分钟 K 线预拉取任务（v6 分时共振支撑）。

背景：v6 分时共振评分需要每只候选票当日的 5min K 线。如果放在
`_compute_sector_trends_full` 里边算边拉或在同一 HTTP 请求里完整重算，会让
预计算耗时成倍增长；且用户白天多次触发预计算会重复拉相同数据。

本任务在**收盘后固定时间**（默认 15:15 Asia/Shanghai）跑：
    1. 从最新 sector_trends_cache 里收集候选票（每板块的 leader/zhongjun）
    2. 加入基准指数（000001.SH / 399006.SZ）
    3. 过滤掉已落库的 (ticker, trade_date)
    4. 剩下的优先通过 KlineShare 拉 5min K（无 Key 才回退东方财富），落 Mongo `minute_bars_daily`

后续用户点"查询板块动向"时只读取缓存和已落库分钟 K，不在查询请求里
访问外部行情。新补齐的分钟 K 在下一次预计算进入龙头/中军评分，保证同一
份缓存的覆盖率、解释和分数来自同一个计算批次。

也可以手动跑：
    python -m shilun.jobs.minute_bars_sync_job --date 2026-07-09
    python -m shilun.jobs.minute_bars_sync_job --date 2026-07-09 --tickers 688256.SH,002185.SZ
"""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import pandas as pd

from shilun.common.config import load_config
from shilun.common.db import MongoSnapshotStore
from shilun.data.providers.eastmoney_provider import EastmoneyClient
from shilun.data.providers.minute_provider import MinuteClient
from shilun.data.providers.klineshare_provider import KlineShareClient, KlineShareConfig, KlineShareError
from shilun.market.minute_bars_store import (
    COLLECTION_NAME as MINUTE_COLL,
    find_missing_tickers,
    upsert_minute_bars,
)

DEFAULT_INTERVAL = "5"
DEFAULT_BENCHMARK_TICKERS = ("000001.SH", "399006.SZ")
MAX_DISTINCT_SYMBOLS_PER_RUN = 200


@dataclass(frozen=True)
class MinuteBarsSyncResult:
    trade_date: str            # YYYYMMDD
    interval: str
    candidate_count: int       # 从候选池提取的票数
    missing_count: int         # 过滤后需要拉的票数
    fetched_count: int         # 成功拉到且落库的票数
    failed_tickers: list[str]  # 拉取失败的票
    elapsed_seconds: float


class MinuteBarsSyncJob:
    def __init__(
        self,
        mongo_store: MongoSnapshotStore,
        client: Any | None = None,
        fallback_client: Any | None = None,
        interval: str = DEFAULT_INTERVAL,
    ):
        self.store = mongo_store
        self.client = client or MinuteClient()
        self.fallback_client = (
            fallback_client
            if fallback_client is not None
            else MinuteClient()
            if isinstance(self.client, KlineShareClient)
            else None
        )
        self.interval = interval

    def run(
        self,
        trade_date: str,
        *,
        explicit_tickers: list[str] | None = None,
        benchmark_tickers: tuple[str, ...] = DEFAULT_BENCHMARK_TICKERS,
        max_workers: int = 1,
    ) -> MinuteBarsSyncResult:
        """主入口。trade_date 格式 YYYYMMDD 或 YYYY-MM-DD 都行。"""
        yyyymmdd = trade_date.replace("-", "")
        collection = self.store.collection(MINUTE_COLL)

        # 收集候选票
        if explicit_tickers:
            candidates = [t.upper().strip() for t in explicit_tickers if t.strip()]
        else:
            candidates = self._collect_candidate_tickers(yyyymmdd)

        # 加入基准指数
        # KlineShare 的分钟 K / 实时 / 分时共用每分钟 200 个不同品种上限。
        # 先稳定去重并为双指数预留额度，避免 set 无序导致每次跳过不同股票。
        benchmark_list = list(dict.fromkeys(t.upper() for t in benchmark_tickers))
        candidate_cap = max(0, MAX_DISTINCT_SYMBOLS_PER_RUN - len(benchmark_list))
        candidates = list(dict.fromkeys(candidates))[:candidate_cap]
        # 基准指数是所有分时共振评分的共同依赖，必须优先于个股拉取。
        # 数据源对“每分钟不同品种数”有限制；若把指数排在 60+ 个候选之后，
        # 会出现个股已落库但指数因限流失败，最终所有候选仍只能走日线代理。
        all_tickers = list(dict.fromkeys([*benchmark_list, *candidates]))

        missing = find_missing_tickers(collection, all_tickers, yyyymmdd, interval=self.interval)

        started = time.time()
        worker_count = max(1, min(int(max_workers), 8, len(missing) or 1))
        if worker_count == 1:
            outcomes = [
                (ticker, self._fetch_and_upsert_one(collection, ticker, yyyymmdd))
                for ticker in missing
            ]
        else:
            # KlineShare 每个 symbol 是独立 GET；候选池预计算使用小规模并发，
            # 把 100+ 只股票的同次点击从数分钟降到可接受范围。结果仍按 missing
            # 原顺序收集，失败清单稳定可复现；默认 workers=1 保持定时任务语义。
            with ThreadPoolExecutor(max_workers=worker_count) as executor:
                ok_values = list(executor.map(
                    lambda ticker: self._fetch_and_upsert_one(collection, ticker, yyyymmdd),
                    missing,
                ))
            outcomes = list(zip(missing, ok_values))
        fetched = sum(1 for _, ok in outcomes if ok)
        failed = [ticker for ticker, ok in outcomes if not ok]

        elapsed = time.time() - started
        result = MinuteBarsSyncResult(
            trade_date=yyyymmdd,
            interval=self.interval,
            candidate_count=len(candidates),
            missing_count=len(missing),
            fetched_count=fetched,
            failed_tickers=failed,
            elapsed_seconds=round(elapsed, 2),
        )
        self._record_state(yyyymmdd, result)
        return result

    def _collect_candidate_tickers(self, yyyymmdd: str) -> list[str]:
        """从最新的 sector_trends_cache 里提取候选票。

        优先找 (analysis_date == today) 的缓存；若没有则退化到取最新一条缓存
        （通常是前一交易日），确保定时任务在盘中重跑时也能拿到票池。
        """
        target_date = f"{yyyymmdd[:4]}-{yyyymmdd[4:6]}-{yyyymmdd[6:8]}"
        from shilun.trading.market_facts import _sector_cache
        cached = _sector_cache(self.store, target_date)
        if not cached:
            return []

        payload = cached.get("payload") or {}
        tickers: set[str] = {
            str(row["ticker"]).upper() for row in payload.get("candidates") or []
            if row.get("ticker") and (row.get("selection_gate") or {}).get("passed")
        }
        for pool_key in ("top_sectors",):
            for sector in payload.get(pool_key) or []:
                minute_targets = [
                    str(ticker or "").upper()
                    for ticker in (sector.get("minute_candidate_tickers") or [])
                    if ticker
                ]
                if minute_targets:
                    tickers.update(minute_targets)
                    continue
                for cand_key in ("leader_candidates", "zhongjun_candidates"):
                    for item in sector.get(cand_key) or []:
                        tk = str(item.get("ticker") or "").upper()
                        if tk:
                            tickers.add(tk)
        return sorted(tickers)

    def _fetch_and_upsert_one(self, collection: Any, ticker: str, yyyymmdd: str) -> bool:
        clients = [self.client]
        if self.fallback_client is not None and self.fallback_client is not self.client:
            clients.append(self.fallback_client)
        # KlineShare's stock API does not serve exchange indices. Route these
        # directly to the dedicated index-capable provider.
        if isinstance(self.client, KlineShareClient) and self.fallback_client is not None:
            from shilun.market.part1 import BENCHMARK_INDEX_OPTIONS
            if ticker in {item["ticker"] for item in BENCHMARK_INDEX_OPTIONS}:
                clients = [self.fallback_client]
        df = None
        for used_client in clients:
            try:
                if hasattr(used_client, "fetch_kline"):
                    frame = used_client.fetch_kline(ticker, period=max(60, int(self.interval) * 60),
                                                    count=120, adjust_type="none")
                elif isinstance(used_client, (EastmoneyClient, MinuteClient)):
                    frame = used_client.fetch_minute_bars(ticker, period=self.interval, trade_date=yyyymmdd)
                else:
                    frame = used_client.fetch_minute_bars(ticker, period=self.interval)
                if frame is None or frame.empty:
                    continue
                target_day = pd.Timestamp(yyyymmdd).date()
                timestamps = pd.to_datetime(frame["datetime"], errors="coerce")
                if timestamps.dt.tz is not None:
                    timestamps = timestamps.dt.tz_convert("Asia/Shanghai")
                clocks = timestamps.dt.time
                in_session = (
                    ((clocks >= pd.Timestamp("09:30").time()) & (clocks <= pd.Timestamp("11:30").time()))
                    | ((clocks >= pd.Timestamp("13:00").time()) & (clocks <= pd.Timestamp("15:00").time()))
                )
                selected = (timestamps.dt.date == target_day) & in_session
                frame = frame[selected].copy()
                frame["datetime"] = timestamps[selected]
                if frame.empty:
                    continue
                df = frame.reset_index(drop=True)
                break
            except Exception:  # Try the bounded fallback for errors, empty data and wrong dates.
                continue
        if df is None or df.empty:
            return False

        bars = df.to_dict("records")
        for r in bars:
            if hasattr(r.get("datetime"), "isoformat"):
                r["datetime"] = r["datetime"].isoformat()
        try:
            upsert_minute_bars(
                collection,
                ticker=ticker,
                trade_date=yyyymmdd,
                interval=self.interval,
                bars=bars,
                source=df.attrs.get("source") or ("klineshare" if hasattr(used_client, "fetch_kline") else "eastmoney"),
            )
            return True
        except Exception:  # noqa: BLE001
            return False

    def _record_state(self, yyyymmdd: str, result: MinuteBarsSyncResult) -> None:
        self.store.upsert_sync_state(
            {
                "dataset": "minute_bars_daily",
                "scope": f"{yyyymmdd}_{self.interval}min",
                "status": "success" if not result.failed_tickers else "partial",
                "row_count": result.fetched_count,
                "candidate_count": result.candidate_count,
                "missing_count": result.missing_count,
                "failed_count": len(result.failed_tickers),
                "elapsed_seconds": result.elapsed_seconds,
                "updated_at": datetime.now().isoformat(timespec="seconds"),
            }
        )


def _today_yyyymmdd() -> str:
    return datetime.now().strftime("%Y%m%d")


def main() -> int:
    parser = argparse.ArgumentParser(description="Prefetch minute bars for v6 intraday resonance")
    parser.add_argument("--date", default=_today_yyyymmdd(), help="YYYYMMDD or YYYY-MM-DD")
    parser.add_argument("--tickers", default="", help="Comma-separated tickers to force fetch (overrides candidate collection)")
    parser.add_argument("--interval", default=DEFAULT_INTERVAL)
    args = parser.parse_args()

    config = load_config()
    if not config.mongo_uri:
        raise SystemExit("SHILUN_MONGO_URI not configured")

    store = MongoSnapshotStore(config.mongo_uri, config.mongo_db)
    try:
        if config.klineshare_api_key:
            client = KlineShareClient(KlineShareConfig(
                api_key=config.klineshare_api_key,
                base_url=config.klineshare_base_url,
                timeout=config.klineshare_timeout,
            ))
        else:
            client = MinuteClient()
        job = MinuteBarsSyncJob(store, client=client, interval=args.interval)
        explicit_tickers = [t.strip() for t in args.tickers.split(",") if t.strip()] or None
        result = job.run(args.date, explicit_tickers=explicit_tickers)
        print(
            f"minute_bars_sync trade_date={result.trade_date} interval={result.interval} "
            f"candidates={result.candidate_count} missing={result.missing_count} "
            f"fetched={result.fetched_count} failed={len(result.failed_tickers)} "
            f"elapsed={result.elapsed_seconds}s",
            flush=True,
        )
        if result.failed_tickers:
            print(f"  failed_tickers={result.failed_tickers}", flush=True)
        return 2 if result.failed_tickers else 0
    finally:
        store.close()


def scheduler_main() -> None:
    """在生产环境挂 APScheduler，每交易日 15:15 Asia/Shanghai 触发一次。"""
    from apscheduler.schedulers.blocking import BlockingScheduler
    from apscheduler.triggers.cron import CronTrigger

    scheduler = BlockingScheduler(timezone="Asia/Shanghai")

    def sync_today() -> None:
        config = load_config()
        if not config.mongo_uri:
            print("scheduled_minute_bars_sync skipped: SHILUN_MONGO_URI not set", flush=True)
            return
        store = MongoSnapshotStore(config.mongo_uri, config.mongo_db)
        try:
            result = MinuteBarsSyncJob(store).run(_today_yyyymmdd())
            print(
                f"scheduled_minute_bars_sync trade_date={result.trade_date} "
                f"candidates={result.candidate_count} missing={result.missing_count} "
                f"fetched={result.fetched_count} failed={len(result.failed_tickers)} "
                f"elapsed={result.elapsed_seconds}s",
                flush=True,
            )
        finally:
            store.close()

    # 15:15 收盘后 15 分钟触发，给分钟数据源留缓冲时间。跳过周末。
    scheduler.add_job(
        sync_today,
        CronTrigger(day_of_week="mon-fri", hour=15, minute=15, second=0, timezone="Asia/Shanghai"),
        id="minute_bars_sync_1515",
        replace_existing=True,
    )
    print("Minute bars sync scheduler started. Runs at 15:15 Asia/Shanghai on weekdays.", flush=True)
    scheduler.start()


if __name__ == "__main__":
    raise SystemExit(main())
