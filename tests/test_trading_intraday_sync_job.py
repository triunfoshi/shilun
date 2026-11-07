from __future__ import annotations

import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd

from shilun.data.providers.klineshare_provider import KlineShareError
from shilun.jobs.trading_intraday_sync_job import (
    REALTIME_COLLECTION,
    TradingIntradaySyncJob,
    closed_kline_frame,
    is_market_poll_time,
    minute_bar_target_tickers,
)
from shilun.market.minute_bars_store import COLLECTION_NAME as MINUTE_COLLECTION
from shilun.trading.market_facts import collect_execution_monitor_pool


MARKET_TZ = ZoneInfo("Asia/Shanghai")


class _Cursor:
    def __init__(self, docs):
        self.docs = list(docs)

    def sort(self, key, direction=1):
        self.docs.sort(key=lambda item: item.get(key) or "", reverse=direction < 0)
        return self

    def __iter__(self):
        return iter([dict(item) for item in self.docs])


class _Collection:
    def __init__(self, docs=None):
        self.docs = list(docs or [])

    @staticmethod
    def _match(doc, query):
        for key, expected in query.items():
            actual = doc.get(key)
            if isinstance(expected, dict) and "$in" in expected:
                if actual not in expected["$in"]:
                    return False
            elif actual != expected:
                return False
        return True

    def find(self, query, projection=None):
        return _Cursor([item for item in self.docs if self._match(item, query)])

    def find_one(self, query, projection=None, sort=None):
        docs = [item for item in self.docs if self._match(item, query)]
        if sort:
            key, direction = sort[0]
            docs.sort(key=lambda item: item.get(key) or "", reverse=direction < 0)
        return dict(docs[0]) if docs else None

    def update_one(self, query, update, upsert=False):
        for item in self.docs:
            if self._match(item, query):
                item.update(update.get("$set", {}))
                return
        if upsert:
            self.docs.append({**query, **update.get("$set", {}), **update.get("$setOnInsert", {})})


class _Store:
    def __init__(self):
        self.collections = {
            "holdings": _Collection([
                {"ticker": "603986.SH", "status": "active", "entry_date": "2026-07-10"},
                {"ticker": "000001.SZ", "status": "reduced", "entry_date": "2026-07-09"},
                {"ticker": "600000.SH", "status": "closed", "entry_date": "2026-07-08"},
            ]),
            REALTIME_COLLECTION: _Collection(),
            MINUTE_COLLECTION: _Collection(),
        }
        self.sync_states = []

    def collection(self, name):
        return self.collections.setdefault(name, _Collection())

    def upsert_sync_state(self, payload):
        self.sync_states.append(dict(payload))


class _Client:
    def __init__(self):
        self.realtime_calls = []
        self.kline_calls = []
        self.trend_calls = []

    def fetch_realtime(self, tickers):
        self.realtime_calls.append(list(tickers))
        return pd.DataFrame([
            {
                "ticker": ticker,
                "current": 100.0,
                "depth_bids": [],
                "depth_asks": [],
                "quote_at": "2026-07-13T10:17:00+08:00",
                "fetched_at": "2026-07-13T10:17:01+08:00",
            }
            for ticker in tickers
        ])

    def fetch_kline(self, ticker, **kwargs):
        self.kline_calls.append((ticker, kwargs))
        return pd.DataFrame([
            {
                "datetime": pd.Timestamp("2026-07-13T10:10:00+08:00"),
                "open": 99,
                "high": 101,
                "low": 98,
                "close": 100,
                "volume": 1000,
                "amount": 100000,
            },
            {
                "datetime": pd.Timestamp("2026-07-13T10:15:00+08:00"),
                "open": 100,
                "high": 102,
                "low": 99,
                "close": 101,
                "volume": 500,
                "amount": 50500,
            },
        ])

    def fetch_trend(self, ticker):
        self.trend_calls.append(ticker)
        return pd.DataFrame([{
            "ticker": ticker,
            "datetime": pd.Timestamp("2026-07-13T10:15:00+08:00"),
            "price": 100,
            "avg_price": 99.5,
            "price_unit": "cny_per_share",
            "source": "klineshare",
        }])


class TradingIntradaySyncJobTests(unittest.TestCase):
    def test_market_poll_window(self) -> None:
        self.assertTrue(is_market_poll_time(datetime(2026, 7, 13, 10, 0, tzinfo=MARKET_TZ)))
        self.assertFalse(is_market_poll_time(datetime(2026, 7, 13, 12, 0, tzinfo=MARKET_TZ)))
        self.assertFalse(is_market_poll_time(datetime(2026, 7, 11, 10, 0, tzinfo=MARKET_TZ)))

    def test_closed_frame_excludes_current_forming_period(self) -> None:
        frame = pd.concat([
            pd.DataFrame([{
                "datetime": pd.Timestamp("2026-07-10T15:00:00+08:00"),
                "open": 98,
                "high": 100,
                "low": 97,
                "close": 99,
                "volume": 800,
                "amount": 79200,
            }]),
            _Client().fetch_kline("603986.SH"),
        ], ignore_index=True)
        result = closed_kline_frame(
            frame,
            now=datetime(2026, 7, 13, 10, 17, tzinfo=MARKET_TZ),
        )
        self.assertEqual(len(result), 1)
        self.assertEqual(result.iloc[0]["close"], 100)
        self.assertEqual(result.iloc[0]["datetime"].date().isoformat(), "2026-07-13")

    def test_closed_frame_excludes_after_market_flat_bars(self) -> None:
        frame = pd.DataFrame([
            {
                "datetime": pd.Timestamp("2026-07-13T15:00:00+08:00"),
                "open": 100,
                "high": 102,
                "low": 99,
                "close": 101,
                "volume": 800,
                "amount": 80800,
            },
            {
                "datetime": pd.Timestamp("2026-07-13T15:05:00+08:00"),
                "open": 101,
                "high": 101,
                "low": 101,
                "close": 101,
                "volume": 0,
                "amount": 0,
            },
            {
                "datetime": pd.Timestamp("2026-07-13T15:30:00+08:00"),
                "open": 101,
                "high": 101,
                "low": 101,
                "close": 101,
                "volume": 0,
                "amount": 0,
            },
        ])

        result = closed_kline_frame(
            frame,
            now=datetime(2026, 7, 13, 16, 0, tzinfo=MARKET_TZ),
        )

        self.assertEqual(len(result), 1)
        self.assertEqual(result.iloc[0]["datetime"].strftime("%H:%M"), "15:00")

    def test_syncs_active_holdings_and_two_benchmarks(self) -> None:
        store = _Store()
        client = _Client()
        job = TradingIntradaySyncJob(store, client)

        result = job.run(now=datetime(2026, 7, 13, 10, 17, tzinfo=MARKET_TZ))

        self.assertEqual(result.status, "success")
        self.assertEqual(result.ticker_count, 4)
        self.assertEqual(result.quote_count, 4)
        self.assertEqual(result.minute_count, 4)
        self.assertEqual(result.latency_sample_count, 4)
        requested = set(client.realtime_calls[0])
        self.assertEqual(
            requested,
            {"603986.SH", "000001.SZ", "000001.SH", "399006.SZ"},
        )
        self.assertNotIn("600000.SH", requested)
        minute_docs = store.collection(MINUTE_COLLECTION).docs
        self.assertEqual(len(minute_docs), 4)
        self.assertTrue(all(len(item["bars"]) == 1 for item in minute_docs))
        self.assertTrue(all(item["source"] == "klineshare" for item in minute_docs))
        quote_docs = store.collection(REALTIME_COLLECTION).docs
        self.assertTrue(all(item["depth_bids"] == [] for item in quote_docs))
        self.assertEqual(store.sync_states[-1]["dataset"], "trading_intraday_facts")
        self.assertEqual(result.trend_count, 4)

    def test_execution_watchlist_replaces_holdings_only_pool_and_respects_quotas(self) -> None:
        store = _Store()
        store.collection("intraday_watchlist").docs.extend(
            {
                "ticker": f"{index:06d}.SZ",
                "enabled": True,
                "updated_at": "2026-07-13T09:00:00+08:00",
            }
            for index in range(1, 207)
        )
        client = _Client()

        result = TradingIntradaySyncJob(store, client).run(
            now=datetime(2026, 7, 13, 10, 17, tzinfo=MARKET_TZ),
        )

        self.assertEqual(result.ticker_count, 200)
        self.assertEqual(sum(len(batch) for batch in client.realtime_calls), 200)
        self.assertTrue(all(len(batch) <= 20 for batch in client.realtime_calls))
        self.assertEqual(len(client.trend_calls), 60)
        requested = {ticker for batch in client.realtime_calls for ticker in batch}
        self.assertIn("000003.SZ", requested)
        self.assertIn("603986.SH", requested)

    def test_minute_bar_quota_keeps_core_tickers_and_rotates_the_rest(self) -> None:
        store = _Store()
        store.collection("intraday_watchlist").docs.extend(
            {
                "ticker": f"{index:06d}.SZ",
                "enabled": True,
                "updated_at": "2026-07-13T09:00:00+08:00",
            }
            for index in range(1, 207)
        )
        pool = collect_execution_monitor_pool(store, "20260713")

        first = minute_bar_target_tickers(
            pool,
            now=datetime(2026, 7, 13, 10, 0, tzinfo=MARKET_TZ),
        )
        second = minute_bar_target_tickers(
            pool,
            now=datetime(2026, 7, 13, 10, 1, tzinfo=MARKET_TZ),
        )

        self.assertEqual(len(first), 60)
        self.assertEqual(len(second), 60)
        self.assertTrue({"000001.SH", "399006.SZ", "603986.SH", "000001.SZ"}.issubset(first))
        self.assertNotEqual(set(first), set(second))
        self.assertGreater(len(set(first) | set(second)), 60)

    def test_fast_quote_lane_skips_slow_kline_and_trend_calls(self) -> None:
        store = _Store()
        client = _Client()

        result = TradingIntradaySyncJob(store, client).run_quotes_only(
            now=datetime(2026, 7, 13, 10, 17, tzinfo=MARKET_TZ),
        )

        self.assertEqual(result.status, "success")
        self.assertEqual(result.quote_count, 4)
        self.assertEqual(result.latency_sample_count, 4)
        self.assertEqual(client.kline_calls, [])
        self.assertEqual(client.trend_calls, [])
        self.assertEqual(store.sync_states[-1]["dataset"], "trading_realtime_quotes_fast")

    def test_realtime_batch_failure_is_bisected_to_the_bad_symbol(self) -> None:
        class IsolatingClient(_Client):
            def fetch_realtime(self, tickers):
                self.realtime_calls.append(list(tickers))
                if "920999.BJ" in tickers:
                    raise KlineShareError("/v1/realtime", "unsupported symbol")
                return pd.DataFrame([
                    {
                        "ticker": ticker,
                        "current": 100.0,
                        "quote_at": "2026-07-13T10:17:00+08:00",
                        "fetched_at": "2026-07-13T10:17:01+08:00",
                    }
                    for ticker in tickers
                ])

        store = _Store()
        client = IsolatingClient()

        records, count, returned, _, failed = TradingIntradaySyncJob(
            store,
            client,
        )._fetch_realtime_quotes(["000001.SZ", "920999.BJ", "600000.SH"])

        self.assertEqual(count, 2)
        self.assertEqual(len(records), 2)
        self.assertEqual(returned, {"000001.SZ", "600000.SH"})
        self.assertEqual(failed, ["920999.BJ"])

    def test_benchmark_quotes_and_minutes_use_dedicated_fallbacks(self) -> None:
        class PrimaryClient(_Client):
            def fetch_realtime(self, tickers):
                self.realtime_calls.append(list(tickers))
                rows = [
                    {
                        "ticker": ticker,
                        "current": 100.0,
                        "quote_at": "2026-07-13T10:17:00+08:00",
                        "fetched_at": "2026-07-13T10:17:01+08:00",
                    }
                    for ticker in tickers
                    if ticker not in {"000001.SH", "399006.SZ"}
                ]
                return pd.DataFrame(rows)

        class IndexQuoteClient:
            def fetch_index_realtime(self, tickers):
                return pd.DataFrame([
                    {
                        "ticker": ticker,
                        "current": 4000.0,
                        "quote_at": "2026-07-13T10:17:00+08:00",
                        "fetched_at": "2026-07-13T10:17:01+08:00",
                    }
                    for ticker in tickers
                ])

        class IndexMinuteClient:
            def __init__(self):
                self.calls = []

            def fetch_minute_bars(self, ticker, period="5"):
                self.calls.append((ticker, period))
                return _Client().fetch_kline(ticker)

        store = _Store()
        primary = PrimaryClient()
        minute_fallback = IndexMinuteClient()
        job = TradingIntradaySyncJob(
            store,
            primary,
            index_quote_client=IndexQuoteClient(),
            index_minute_client=minute_fallback,
        )

        records, count, returned, _, failed = job._fetch_realtime_quotes(
            ["000001.SH", "399006.SZ", "600000.SH"]
        )
        frame, source = job._fetch_minute_frame("000001.SH")

        self.assertEqual(count, 3)
        self.assertEqual({item["ticker"] for item in records}, {
            "000001.SH", "399006.SZ", "600000.SH",
        })
        self.assertEqual(returned, {"000001.SH", "399006.SZ", "600000.SH"})
        self.assertEqual(failed, [])
        self.assertFalse(frame.empty)
        self.assertEqual(source, "eastmoney")
        self.assertEqual(minute_fallback.calls, [("000001.SH", "5")])
        self.assertNotIn("000001.SH", [ticker for ticker, _ in primary.kline_calls])

    def test_scenario_scope_excludes_sector_samples_and_keeps_full_selection_pool(self) -> None:
        store = _Store()
        store.collection("intraday_watchlist").docs.extend([
            {"ticker": "000001.SZ", "enabled": True, "sector_name": "银行", "updated_at": "2026-07-13T09:00:00+08:00"},
            {"ticker": "000002.SZ", "enabled": True, "sector_name": "地产", "updated_at": "2026-07-13T09:00:00+08:00"},
        ])
        store.collection("sector_trends_cache").docs.append({
            "analysis_date": "2026-07-13",
            "computed_at": "2026-07-13T09:00:00+08:00",
            "payload": {
                "candidates": [
                    {"ticker": "688888.SH", "name": "池内股票", "sector_name": "半导体", "ranking_status": "ranked"},
                    {"ticker": "688889.SH", "name": "否决股票", "sector_name": "半导体", "ranking_status": "excluded"},
                ],
                "top_sectors": [{
                    "sector_name": "半导体",
                    "leader_candidates": [{"ticker": "688001.SH"}],
                    "candidates": [{"ticker": "688002.SH"}],
                }],
            },
        })

        pool = collect_execution_monitor_pool(store, "20260713")

        self.assertEqual(set(pool.selection_pool_tickers), {"000001.SZ", "000002.SZ", "688888.SH"})
        self.assertEqual(set(pool.scenario_tickers), {"603986.SH", "000001.SZ", "000002.SZ", "688888.SH"})
        self.assertNotIn("688889.SH", pool.scenario_tickers)
        self.assertIn("688001.SH", pool.tickers)
        self.assertNotIn("688001.SH", pool.scenario_tickers)

    def test_outside_session_skips_without_network_calls(self) -> None:
        store = _Store()
        client = _Client()
        result = TradingIntradaySyncJob(store, client).run(
            now=datetime(2026, 7, 13, 12, 0, tzinfo=MARKET_TZ),
        )
        self.assertEqual(result.status, "skipped")
        self.assertEqual(client.realtime_calls, [])
        self.assertEqual(client.kline_calls, [])

    def test_1530_closeout_expires_order_even_after_market_polling_stops(self) -> None:
        store = _Store()
        store.collection("trade_orders").docs.append({
            "order_id": "closeout-1",
            "plan_id": "plan-closeout",
            "ticker": "600000.SH",
            "side": "buy",
            "intent": "entry",
            "quantity": 100,
            "filled_quantity": 0,
            "remaining_quantity": 100,
            "limit_price": 10,
            "status": "open",
            "execution_state": "entry_pending",
            "order_revision": 1,
            "placed_at": "2026-07-13T14:00:00+08:00",
        })
        store.collection("execution_cash_reservations").docs.append({
            "plan_id": "plan-closeout", "status": "active", "reserved_cash": 1_000,
        })
        client = _Client()

        result = TradingIntradaySyncJob(store, client).run(
            now=datetime(2026, 7, 13, 15, 30, tzinfo=MARKET_TZ),
        )

        self.assertEqual(result.status, "skipped")
        self.assertEqual(result.expired_order_count, 1)
        self.assertEqual(store.collection("trade_orders").docs[0]["status"], "cancelled")
        self.assertEqual(store.collection("execution_cash_reservations").docs[0]["status"], "released")
        self.assertEqual(store.collection("trade_order_events").docs[0]["event_type"], "order_expired")
        self.assertEqual(client.realtime_calls, [])

    def test_refreshes_decisions_only_after_quote_and_minute_are_persisted(self) -> None:
        store = _Store()
        client = _Client()
        refreshed = []
        job = TradingIntradaySyncJob(
            store,
            client,
            decision_refresher=lambda ticker, now: refreshed.append((ticker, now)),
        )

        result = job.run(now=datetime(2026, 7, 13, 10, 17, tzinfo=MARKET_TZ))

        self.assertEqual(result.decision_count, 2)
        self.assertEqual(result.decision_failed_tickers, ())
        self.assertEqual({item[0] for item in refreshed}, {"603986.SH", "000001.SZ"})
        self.assertEqual(store.sync_states[-1]["decision_count"], 2)

    def test_runs_full_scenario_bridge_once_after_all_facts_are_persisted(self) -> None:
        store = _Store()
        client = _Client()
        calls = []

        def refresh_scenarios(now):
            calls.append({
                "now": now,
                "quote_count": len(store.collection(REALTIME_COLLECTION).docs),
                "minute_count": len(store.collection(MINUTE_COLLECTION).docs),
            })
            return {"decision_count": 3, "failed_tickers": []}

        result = TradingIntradaySyncJob(
            store,
            client,
            scenario_refresher=refresh_scenarios,
        ).run(now=datetime(2026, 7, 13, 10, 17, tzinfo=MARKET_TZ))

        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["quote_count"], 4)
        self.assertEqual(calls[0]["minute_count"], 4)
        self.assertEqual(result.scenario_decision_count, 3)
        self.assertEqual(store.sync_states[-1]["scenario_decision_count"], 3)


if __name__ == "__main__":
    unittest.main()
