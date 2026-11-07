"""minute_bars_sync_job 定时任务测试。

不联网、不依赖真实 Mongo：用 in-memory Fake stores 桩测。
"""

from __future__ import annotations

import unittest
from datetime import datetime
from typing import Any
from unittest.mock import MagicMock

import pandas as pd

from shilun.jobs.minute_bars_sync_job import MinuteBarsSyncJob


class FakeCollection:
    """最小可用的 in-memory Mongo collection 桩。"""

    def __init__(self, docs: list[dict] | None = None) -> None:
        self._docs: list[dict] = list(docs or [])

    @staticmethod
    def _match(doc: dict, query: dict) -> bool:
        for key, expect in query.items():
            actual = doc.get(key)
            if isinstance(expect, dict):
                for op, op_val in expect.items():
                    if op == "$in" and actual not in op_val:
                        return False
            else:
                if actual != expect:
                    return False
        return True

    def find_one(self, query: dict, projection: dict | None = None):
        for doc in self._docs:
            if self._match(doc, query):
                return dict(doc)
        return None

    def update_one(self, query: dict, update: dict, upsert: bool = False):
        for doc in self._docs:
            if self._match(doc, query):
                doc.update(update.get("$set", {}))
                return
        if upsert:
            new_doc = dict(query)
            new_doc.update(update.get("$set", {}))
            self._docs.append(new_doc)

    class _Cursor:
        def __init__(self, docs):
            self._docs = docs

        def sort(self, key, direction=1):
            self._docs = sorted(self._docs, key=lambda d: d.get(key) or "", reverse=direction < 0)
            return self

        def limit(self, n):
            self._docs = self._docs[:n]
            return self

        def __iter__(self):
            return iter([dict(d) for d in self._docs])

    def find(self, query: dict, projection: dict | None = None):
        matched = [doc for doc in self._docs if self._match(doc, query)]
        return self._Cursor(matched)


class FakeStore:
    def __init__(self, sector_cache_docs: list[dict], minute_docs: list[dict] | None = None):
        self._collections = {
            "sector_trends_cache": FakeCollection(sector_cache_docs),
            "minute_bars_daily": FakeCollection(minute_docs),
        }
        self.sync_states: list[dict] = []

    def collection(self, name: str) -> FakeCollection:
        if name not in self._collections:
            self._collections[name] = FakeCollection()
        return self._collections[name]

    def upsert_sync_state(self, payload: dict[str, Any]) -> None:
        self.sync_states.append(dict(payload))

    def close(self) -> None:
        pass


class _FakeAkClient:
    """模拟 EastmoneyClient：按 ticker 返回构造好的分钟 K DataFrame。"""

    def __init__(self, tickers_to_serve: set[str], target_day: str, fail_tickers: set[str] | None = None):
        self.tickers_to_serve = tickers_to_serve
        self.target_day = target_day  # YYYY-MM-DD
        self.fail_tickers = fail_tickers or set()
        self.call_log: list[str] = []

    def fetch_minute_bars(self, ticker: str, period: str = "5"):
        self.call_log.append(ticker)
        if ticker in self.fail_tickers:
            from shilun.data.providers.eastmoney_provider import EastmoneyFetchError
            raise EastmoneyFetchError(source=f"minute_bars({ticker})", underlying=Exception("simulated"))
        if ticker not in self.tickers_to_serve:
            return pd.DataFrame()
        base = pd.Timestamp(f"{self.target_day} 09:30:00")
        rows = []
        for i in range(5):
            rows.append({
                "datetime": base + pd.Timedelta(minutes=5 * i),
                "open": 100.0 + i,
                "high": 101.0 + i,
                "low": 99.0 + i,
                "close": 100.5 + i,
                "volume": 1000,
                "amount": 100000,
            })
        return pd.DataFrame(rows)


class _FailingKlineClient:
    def __init__(self) -> None:
        self.call_log: list[str] = []

    def fetch_kline(self, ticker: str, **_: Any):
        self.call_log.append(ticker)
        from shilun.data.providers.klineshare_provider import KlineShareError
        raise KlineShareError("/v1/kline", "simulated")


class _AfterHoursKlineClient:
    def fetch_kline(self, ticker: str, **_: Any):
        return pd.DataFrame([
            {
                "datetime": pd.Timestamp("2026-07-09 14:55:00", tz="Asia/Shanghai"),
                "open": 10.0, "high": 10.2, "low": 9.9, "close": 10.1,
                "volume": 1000, "amount": 10000,
            },
            {
                "datetime": pd.Timestamp("2026-07-09 15:08:00", tz="Asia/Shanghai"),
                "open": 10.1, "high": 10.1, "low": 10.1, "close": 10.1,
                "volume": 0, "amount": 0,
            },
        ])


def _make_sector_cache(analysis_date: str, tickers_by_sector: dict[str, dict]) -> list[dict]:
    """构造一条 sector_trends_cache 记录，模拟 payload.top_sectors 结构。"""
    return [{
        "analysis_date": analysis_date,
        "benchmark_ticker": "000001.SH",
        "computed_at": f"{analysis_date}T20:00:00",
        "payload": {
            "top_sectors": [
                {
                    "sector_name": name,
                    "minute_candidate_tickers": data.get("minute", []),
                    "leader_candidates": [{"ticker": t} for t in data.get("leader", [])],
                    "zhongjun_candidates": [{"ticker": t} for t in data.get("zhongjun", [])],
                }
                for name, data in tickers_by_sector.items()
            ],
        },
    }]


class CollectCandidateTickersTest(unittest.TestCase):
    def test_extracts_unique_tickers_from_leader_and_zhongjun(self) -> None:
        cache = _make_sector_cache("2026-07-09", {
            "半导体": {"leader": ["A", "B", "C"], "zhongjun": ["D", "E"]},
            "光伏": {"leader": ["F"], "zhongjun": ["G", "A"]},  # A 重复
        })
        store = FakeStore(cache)
        job = MinuteBarsSyncJob(store, client=_FakeAkClient(set(), "2026-07-09"))
        result = job._collect_candidate_tickers("20260709")
        self.assertEqual(set(result), {"A", "B", "C", "D", "E", "F", "G"})

    def test_falls_back_to_latest_cache_when_target_missing(self) -> None:
        cache = _make_sector_cache("2026-07-08", {
            "白酒": {"leader": ["X"], "zhongjun": ["Y"]},
        })
        store = FakeStore(cache)
        job = MinuteBarsSyncJob(store, client=_FakeAkClient(set(), "2026-07-08"))
        result = job._collect_candidate_tickers("20260709")
        # target 2026-07-09 无缓存，应退化到 2026-07-08
        self.assertEqual(set(result), {"X", "Y"})

    def test_empty_cache_returns_empty_list(self) -> None:
        store = FakeStore([])
        job = MinuteBarsSyncJob(store, client=_FakeAkClient(set(), "2026-07-09"))
        self.assertEqual(job._collect_candidate_tickers("20260709"), [])

    def test_prefers_top20_minute_targets_over_visible_top3(self) -> None:
        minute_targets = [f"M{i:02d}" for i in range(20)]
        cache = _make_sector_cache("2026-07-09", {
            "半导体": {
                "minute": minute_targets,
                "leader": ["VISIBLE_LEADER"],
                "zhongjun": ["VISIBLE_ZHONGJUN"],
            },
        })
        store = FakeStore(cache)
        job = MinuteBarsSyncJob(store, client=_FakeAkClient(set(), "2026-07-09"))
        result = job._collect_candidate_tickers("20260709")
        self.assertEqual(result, minute_targets)
        self.assertNotIn("VISIBLE_LEADER", result)


class RunTest(unittest.TestCase):
    def test_falls_back_when_primary_kline_source_fails(self) -> None:
        store = FakeStore([])
        primary = _FailingKlineClient()
        fallback = _FakeAkClient({"A", "000001.SH", "399006.SZ"}, "2026-07-09")
        job = MinuteBarsSyncJob(store, client=primary, fallback_client=fallback)

        result = job.run("20260709", explicit_tickers=["A"])

        self.assertEqual(result.fetched_count, 3)
        self.assertEqual(result.failed_tickers, [])
        self.assertEqual(primary.call_log, ["000001.SH", "399006.SZ", "A"])
        self.assertEqual(fallback.call_log, ["000001.SH", "399006.SZ", "A"])

    def test_empty_primary_also_uses_fallback(self) -> None:
        store = FakeStore([])
        primary = _FailingKlineClient()
        primary.fetch_kline = lambda *args, **kwargs: pd.DataFrame()
        fallback = _FakeAkClient({"A", "000001.SH", "399006.SZ"}, "2026-07-09")
        result = MinuteBarsSyncJob(store, client=primary, fallback_client=fallback).run(
            "20260709", explicit_tickers=["A"])
        self.assertEqual(result.fetched_count, 3)
        self.assertEqual(result.failed_tickers, [])

    def test_fetches_only_missing_tickers(self) -> None:
        # sector cache 里有 3 只候选
        cache = _make_sector_cache("2026-07-09", {
            "半导体": {"leader": ["A"], "zhongjun": ["B", "C"]},
        })
        # 其中 B 已经落库
        minute_docs = [{"ticker": "B", "trade_date": "20260709", "interval": "5"}]
        store = FakeStore(cache, minute_docs)
        client = _FakeAkClient({"A", "C", "000001.SH", "399006.SZ"}, "2026-07-09")
        job = MinuteBarsSyncJob(store, client=client)

        result = job.run("20260709")

        # A, C 应该被拉；B 跳过；两个基准也被拉
        # 候选：A B C -> 3 只；missing 应该是 A, C + 2 个基准 = 4 只
        self.assertEqual(result.candidate_count, 3)
        self.assertEqual(result.missing_count, 4)
        self.assertEqual(result.fetched_count, 4)
        self.assertEqual(result.failed_tickers, [])
        # akshare 只对 missing 的 4 只调
        self.assertEqual(set(client.call_log), {"A", "C", "000001.SH", "399006.SZ"})

    def test_records_failed_tickers(self) -> None:
        cache = _make_sector_cache("2026-07-09", {
            "半导体": {"leader": ["A", "FAIL_TICKER"], "zhongjun": []},
        })
        store = FakeStore(cache)
        client = _FakeAkClient(
            {"A", "000001.SH", "399006.SZ"},
            "2026-07-09",
            fail_tickers={"FAIL_TICKER"},
        )
        job = MinuteBarsSyncJob(store, client=client)
        result = job.run("20260709")
        # 3 只成功，1 只失败
        self.assertEqual(result.fetched_count, 3)
        self.assertEqual(result.failed_tickers, ["FAIL_TICKER"])

    def test_explicit_tickers_overrides_cache(self) -> None:
        cache = _make_sector_cache("2026-07-09", {
            "半导体": {"leader": ["FROM_CACHE"], "zhongjun": []},
        })
        store = FakeStore(cache)
        client = _FakeAkClient({"OVERRIDE_A", "OVERRIDE_B", "000001.SH", "399006.SZ"}, "2026-07-09")
        job = MinuteBarsSyncJob(store, client=client)
        result = job.run("20260709", explicit_tickers=["OVERRIDE_A", "OVERRIDE_B"])
        # 应该拉 OVERRIDE_A/B + 基准，不拉 FROM_CACHE
        self.assertEqual(result.candidate_count, 2)
        self.assertNotIn("FROM_CACHE", client.call_log)

    def test_fetches_benchmarks_before_candidate_tickers(self) -> None:
        store = FakeStore([])
        client = _FakeAkClient(
            {"A", "B", "000001.SH", "399006.SZ"},
            "2026-07-09",
        )
        job = MinuteBarsSyncJob(store, client=client)

        job.run("20260709", explicit_tickers=["A", "B"])

        self.assertEqual(client.call_log[:2], ["000001.SH", "399006.SZ"])

    def test_parallel_candidate_sync_keeps_complete_results(self) -> None:
        store = FakeStore([])
        client = _FakeAkClient(
            {"A", "B", "C", "000001.SH", "399006.SZ"},
            "2026-07-09",
        )
        job = MinuteBarsSyncJob(store, client=client)

        result = job.run(
            "20260709",
            explicit_tickers=["A", "B", "C"],
            max_workers=4,
        )

        self.assertEqual(5, result.fetched_count)
        self.assertEqual([], result.failed_tickers)
        stored = store.collection("minute_bars_daily")._docs
        self.assertEqual(
            {"A", "B", "C", "000001.SH", "399006.SZ"},
            {item["ticker"] for item in stored},
        )

    def test_after_hours_fixed_price_bar_is_not_persisted(self) -> None:
        store = FakeStore([])
        job = MinuteBarsSyncJob(
            store,
            client=_AfterHoursKlineClient(),
            fallback_client=None,
        )

        result = job.run("20260709", explicit_tickers=["A"])

        self.assertEqual(3, result.fetched_count)
        stock = next(
            item for item in store.collection("minute_bars_daily")._docs
            if item["ticker"] == "A"
        )
        self.assertEqual(1, len(stock["bars"]))
        self.assertIn("14:55:00", stock["bars"][0]["datetime"])

    def test_sync_state_recorded(self) -> None:
        cache = _make_sector_cache("2026-07-09", {"S": {"leader": ["A"], "zhongjun": []}})
        store = FakeStore(cache)
        client = _FakeAkClient({"A", "000001.SH", "399006.SZ"}, "2026-07-09")
        job = MinuteBarsSyncJob(store, client=client)
        job.run("20260709")
        self.assertTrue(store.sync_states)
        state = store.sync_states[-1]
        self.assertEqual(state["dataset"], "minute_bars_daily")
        self.assertEqual(state["scope"], "20260709_5min")
        self.assertEqual(state["status"], "success")

    def test_all_success_status(self) -> None:
        cache = _make_sector_cache("2026-07-09", {"S": {"leader": [], "zhongjun": []}})
        store = FakeStore(cache)
        # 基准指数拉不到 → partial 状态
        client = _FakeAkClient(set(), "2026-07-09")
        job = MinuteBarsSyncJob(store, client=client)
        result = job.run("20260709")
        self.assertEqual(result.candidate_count, 0)
        self.assertEqual(result.fetched_count, 0)


if __name__ == "__main__":
    unittest.main()
