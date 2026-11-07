"""后台驱动盘中 Decision Timeline，并投递飞书通知 Outbox。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time
from typing import Any

import requests

from shilun.common.config import load_config
from shilun.common.db import MongoSnapshotStore
from shilun.jobs.trading_intraday_sync_job import MARKET_TIMEZONE, is_market_poll_time
from shilun.notifications import FeishuBotClient
from shilun.trading.intraday_alerts import (
    OUTBOX_COLLECTION,
    WATCHLIST_COLLECTION,
    dispatch_feishu_outbox,
    list_watchlist,
)
from shilun.trading.workbench import queue_workbench_notifications


@dataclass(frozen=True)
class IntradayNotificationResult:
    status: str
    monitored_count: int
    event_count: int
    sent_count: int
    failed_count: int
    message: str
    queued_count: int = 0


def run_once(*, now: datetime | None = None, api_base: str = "http://127.0.0.1:8000") -> IntradayNotificationResult:
    current = now or datetime.now(tz=MARKET_TIMEZONE)
    config = load_config()
    if not config.intraday_notifications_enabled:
        return IntradayNotificationResult(
            "disabled",
            0,
            0,
            0,
            0,
            "盘中实时群播已关闭；行情、决策事件与样本仍继续落库",
        )
    market_session = is_market_poll_time(current)
    local = current.astimezone(MARKET_TIMEZONE)
    closeout_window = (
        local.weekday() < 5
        and time(15, 5) < local.time().replace(tzinfo=None) <= time(15, 35)
    )
    if not market_session and not closeout_window:
        return IntradayNotificationResult("skipped", 0, 0, 0, 0, "当前不在盘中通知时段")

    if not config.mongo_uri:
        return IntradayNotificationResult("skipped", 0, 0, 0, 0, "Mongo 未配置")
    store = MongoSnapshotStore(config.mongo_uri, config.mongo_db)
    try:
        watchlist = list_watchlist(store.collection(WATCHLIST_COLLECTION))
        tickers = [str(item.get("ticker") or "").upper() for item in watchlist if item.get("ticker")]
        event_count = 0
        error_message = ""
        if tickers and market_session:
            try:
                response = requests.get(
                    f"{api_base.rstrip('/')}/api/v1/market/intraday",
                    params={
                        "benchmark_ticker": "000001.SH",
                        "include_candidates": "true",
                        "selected_tickers": ",".join(tickers),
                        "selected_only": "true",
                        "source": "auto",
                        "record_decisions": "true",
                    },
                    timeout=45,
                )
                response.raise_for_status()
                payload = response.json()
                event_count = int(payload.get("recorded_decision_count") or 0)
            except Exception as error:  # noqa: BLE001 - 仍允许重试历史 Outbox
                error_message = str(error)[:300]

        delivery = {"sent": 0, "failed": 0}
        queued_count = queue_workbench_notifications(store, now=current)
        if config.feishu_webhook_url:
            delivery = dispatch_feishu_outbox(
                store.collection(OUTBOX_COLLECTION),
                FeishuBotClient(config.feishu_webhook_url, timeout=config.tushare_timeout),
                now=current,
            )
        status = "success" if not error_message and not delivery["failed"] else "partial"
        return IntradayNotificationResult(
            status,
            len(tickers),
            event_count,
            delivery["sent"],
            delivery["failed"],
            error_message or f"monitored={len(tickers)}, sent={delivery['sent']}",
            queued_count,
        )
    finally:
        store.close()


def main() -> None:
    print(run_once(), flush=True)


if __name__ == "__main__":
    main()
