"""AkShare 增强数据同步任务。

把 akshare 三类数据写入 Mongo 独立集合：
- limit_up_pool      涨停池（key: ticker + trade_date）
- concept_boards     概念板块快照（key: board_code + snapshot_date）
- north_capital_flow 北向资金（key: trade_date）

任一接口失败抛 AkshareFetchError；不做多源回退。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

import pandas as pd

from shilun.common.db import MongoSnapshotStore
from shilun.data.providers.akshare_provider import AkshareClient, AkshareConfig


@dataclass(frozen=True)
class AkshareSyncResult:
    target_date: str
    limit_up_count: int
    concept_board_count: int
    north_capital_count: int
    message: str = ""


class AkshareSyncJob:
    def __init__(self, mongo_store: MongoSnapshotStore, client: AkshareClient | None = None):
        self.store = mongo_store
        self.client = client or AkshareClient(AkshareConfig())

    # ── 涨停池 ────────────────────────────────────────────────────────────
    def sync_limit_up_pool(self, trade_date: str) -> int:
        """同步指定交易日的涨停池到 Mongo。"""
        df = self.client.fetch_zt_pool(trade_date)
        if df.empty:
            self._record_state("akshare_limit_up_pool", trade_date, 0, "no_data")
            return 0
        payloads = df.to_dict("records")
        count = self.store._upsert_payloads("limit_up_pool", payloads, ("ticker", "trade_date"))
        self._record_state("akshare_limit_up_pool", trade_date, count, "success")
        return count

    # ── 概念板块 ───────────────────────────────────────────────────────────
    def sync_concept_boards(self) -> int:
        """同步当日概念板块快照（akshare 只返回当日，没有历史日期参数）。"""
        df = self.client.fetch_concept_boards()
        if df.empty:
            self._record_state("akshare_concept_boards", "today", 0, "no_data")
            return 0
        payloads = df.to_dict("records")
        count = self.store._upsert_payloads("concept_boards", payloads, ("board_code", "snapshot_date"))
        self._record_state("akshare_concept_boards", "today", count, "success")
        return count

    # ── 北向资金 ───────────────────────────────────────────────────────────
    def sync_north_capital_flow(self) -> int:
        """同步北向资金历史。akshare 一次性返回全量历史，每次刷写。"""
        df = self.client.fetch_hsgt_hist()
        if df.empty:
            self._record_state("akshare_north_capital", "all", 0, "no_data")
            return 0
        # 去掉北向资金 net_buy_amount 为 NaN 的行（接口最近几天可能为空）
        if "net_buy_amount" in df.columns:
            df = df.dropna(subset=["net_buy_amount"])
        payloads = df.to_dict("records")
        count = self.store._upsert_payloads("north_capital_flow", payloads, ("trade_date",))
        self._record_state("akshare_north_capital", "all", count, "success")
        return count

    # ── 一键同步 ───────────────────────────────────────────────────────────
    def sync_all(self, trade_date: str) -> AkshareSyncResult:
        """一次性同步全部 akshare 数据。"""
        limit_up = self.sync_limit_up_pool(trade_date)
        concept = self.sync_concept_boards()
        north = self.sync_north_capital_flow()
        return AkshareSyncResult(
            target_date=trade_date,
            limit_up_count=limit_up,
            concept_board_count=concept,
            north_capital_count=north,
            message=f"akshare sync done. limit_up={limit_up}, concept={concept}, north={north}.",
        )

    def _record_state(self, dataset: str, scope: str, row_count: int, status: str) -> None:
        self.store.upsert_sync_state(
            {
                "dataset": dataset,
                "scope": scope,
                "status": status,
                "row_count": row_count,
                "updated_at": datetime.now().isoformat(timespec="seconds"),
            }
        )
