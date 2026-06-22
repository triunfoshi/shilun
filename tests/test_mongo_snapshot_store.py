import unittest
from unittest.mock import patch

from shilun.common.db import MongoSnapshotStore


class FakeCollection:
    def __init__(self) -> None:
        self.index_calls: list[dict] = []
        self.update_calls: list[dict] = []
        self.documents: list[dict] = []

    def create_index(self, keys, **kwargs) -> None:
        self.index_calls.append({"keys": list(keys), **kwargs})

    def update_one(self, query, update, upsert=False) -> None:
        self.update_calls.append(
            {
                "query": query,
                "update": update,
                "upsert": upsert,
            }
        )

    def find(self, query, projection=None):
        matched = []
        for document in self.documents:
            if all(self._matches(document, key, value) for key, value in query.items()):
                payload = dict(document)
                if projection and projection.get("_id") == 0:
                    payload.pop("_id", None)
                matched.append(payload)
        return FakeCursor(matched)

    def count_documents(self, query) -> int:
        return len([document for document in self.documents if all(self._matches(document, key, value) for key, value in query.items())])

    @staticmethod
    def _matches(document: dict, key: str, expected) -> bool:
        actual = document.get(key)
        if isinstance(expected, dict):
            if "$gte" in expected and actual < expected["$gte"]:
                return False
            if "$lte" in expected and actual > expected["$lte"]:
                return False
            if "$lt" in expected and actual >= expected["$lt"]:
                return False
            if "$in" in expected and actual not in expected["$in"]:
                return False
            return True
        return actual == expected


class FakeCursor:
    def __init__(self, documents: list[dict]) -> None:
        self.documents = documents

    def sort(self, key, direction: int | None = None):
        sort_keys = key if isinstance(key, list) else [(key, direction or 1)]
        for field, order in reversed(sort_keys):
            reverse = order < 0
            self.documents.sort(key=lambda document: document.get(field, 0), reverse=reverse)
        return self

    def limit(self, count: int):
        self.documents = self.documents[:count]
        return self

    def __iter__(self):
        return iter(self.documents)


class FakeDatabase:
    def __init__(self) -> None:
        self.collections: dict[str, FakeCollection] = {}

    def __getitem__(self, name: str) -> FakeCollection:
        if name not in self.collections:
            self.collections[name] = FakeCollection()
        return self.collections[name]


class FakeClient:
    def __init__(self) -> None:
        self.databases: dict[str, FakeDatabase] = {}

    def __getitem__(self, name: str) -> FakeDatabase:
        if name not in self.databases:
            self.databases[name] = FakeDatabase()
        return self.databases[name]

    def close(self) -> None:
        return None


class MongoSnapshotStoreTests(unittest.TestCase):
    @patch("shilun.common.db.get_mongo_client")
    def test_store_creates_expected_indexes_and_record_version(self, mock_get_mongo_client) -> None:
        fake_client = FakeClient()
        mock_get_mongo_client.return_value = fake_client

        store = MongoSnapshotStore("mongodb://unit-test", "shilun")
        market_snapshot_records = fake_client["shilun"]["market_snapshot_records"]

        index_names = {call["name"] for call in market_snapshot_records.index_calls}
        self.assertIn("uniq_market_snapshot_record_scope", index_names)
        self.assertIn("idx_market_snapshot_rank", index_names)
        self.assertIn("idx_market_snapshot_ticker_date", index_names)
        self.assertIn("idx_market_snapshot_conclusion_rank", index_names)
        self.assertIn("idx_market_snapshot_industry_rank", index_names)
        self.assertIn("idx_market_snapshot_execution_risk", index_names)
        self.assertIn("idx_market_snapshot_strategy_rank", index_names)
        candidate_state_index_names = {call["name"] for call in fake_client["shilun"]["candidate_pool_states"].index_calls}
        candidate_event_index_names = {call["name"] for call in fake_client["shilun"]["candidate_pool_events"].index_calls}
        self.assertIn("uniq_candidate_pool_state_scope", candidate_state_index_names)
        self.assertIn("idx_candidate_pool_state_ticker_date", candidate_state_index_names)
        self.assertIn("uniq_candidate_pool_event_scope", candidate_event_index_names)

        store.upsert_market_snapshot_records(
            analysis_date="2026-03-30",
            exclude_st=True,
            records=[
                {
                    "ticker": "000001.SZ",
                    "rank": 1,
                    "execution_score": 40.5,
                    "risk_score": 28,
                    "industry": "银行",
                    "conclusion_label": "high_quality_continuation",
                }
            ],
        )

        self.assertEqual(1, len(market_snapshot_records.update_calls))
        update_call = market_snapshot_records.update_calls[0]
        self.assertEqual("v1", update_call["update"]["$set"]["record_version"])
        self.assertEqual("2026-03-30", update_call["update"]["$set"]["analysis_date"])
        self.assertTrue(update_call["update"]["$set"]["exclude_st"])

        market_snapshot_records.documents = [
            {"_id": "a", "analysis_date": "2026-03-30", "exclude_st": True, "ticker": "000002.SZ", "rank": 2},
            {"_id": "b", "analysis_date": "2026-03-30", "exclude_st": True, "ticker": "000001.SZ", "rank": 1},
            {"_id": "c", "analysis_date": "2026-03-31", "exclude_st": True, "ticker": "000003.SZ", "rank": 1},
        ]
        records = store.find_market_snapshot_records(analysis_date="2026-03-30", exclude_st=True, limit=2)

        self.assertEqual(["000001.SZ", "000002.SZ"], [record["ticker"] for record in records])
        self.assertNotIn("_id", records[0])
        self.assertEqual(2, store.count_market_snapshot_records(analysis_date="2026-03-30", exclude_st=True))

        ranged_records = store.find_market_snapshot_records_between(
            start_date="2026-03-30",
            end_date="2026-03-31",
            exclude_st=True,
        )
        self.assertEqual(["000001.SZ", "000002.SZ", "000003.SZ"], [record["ticker"] for record in ranged_records])

        candidate_pool_states = fake_client["shilun"]["candidate_pool_states"]
        candidate_pool_events = fake_client["shilun"]["candidate_pool_events"]
        store.upsert_candidate_pool_states(
            analysis_date="2026-03-31",
            exclude_st=True,
            states=[
                {"ticker": "000001.SZ", "pool_status": "watch_pool", "rank": 1},
                {"ticker": "000002.SZ", "pool_status": "buy_pool", "rank": 2},
            ],
        )
        store.upsert_candidate_pool_events(
            analysis_date="2026-03-31",
            exclude_st=True,
            events=[{"ticker": "000002.SZ", "event_type": "promoted", "pool_status": "buy_pool", "rank": 2}],
        )
        self.assertEqual(2, len(candidate_pool_states.update_calls))
        self.assertEqual("v1", candidate_pool_states.update_calls[0]["update"]["$set"]["state_version"])
        self.assertEqual(1, len(candidate_pool_events.update_calls))
        self.assertEqual("v1", candidate_pool_events.update_calls[0]["update"]["$set"]["event_version"])

        candidate_pool_states.documents = [
            {"_id": "state-a", "analysis_date": "2026-03-30", "exclude_st": True, "ticker": "000001.SZ", "pool_status": "candidate_pool", "rank": 3},
            {"_id": "state-b", "analysis_date": "2026-03-31", "exclude_st": True, "ticker": "000001.SZ", "pool_status": "watch_pool", "rank": 1},
            {"_id": "state-c", "analysis_date": "2026-03-31", "exclude_st": True, "ticker": "000002.SZ", "pool_status": "buy_pool", "rank": 2},
        ]
        states = store.find_candidate_pool_states(analysis_date="2026-03-31", exclude_st=True, pool_status="watch_pool")
        self.assertEqual(["000001.SZ"], [state["ticker"] for state in states])
        ranged_states = store.find_candidate_pool_states_between(
            start_date="2026-03-30",
            end_date="2026-03-31",
            exclude_st=True,
            pool_status="buy_pool",
        )
        self.assertEqual(["000002.SZ"], [state["ticker"] for state in ranged_states])
        latest_before = store.find_latest_candidate_pool_states_before(
            analysis_date="2026-04-01",
            exclude_st=True,
            tickers=["000001.SZ", "000002.SZ"],
        )
        self.assertEqual("watch_pool", latest_before["000001.SZ"]["pool_status"])
        self.assertNotIn("_id", latest_before["000001.SZ"])

        candidate_pool_events.documents = [
            {"_id": "event-a", "analysis_date": "2026-03-31", "exclude_st": True, "ticker": "000002.SZ", "event_type": "promoted", "pool_status": "buy_pool", "rank": 2}
        ]
        events = store.find_candidate_pool_events(analysis_date="2026-03-31", exclude_st=True, event_type="promoted")
        self.assertEqual(["000002.SZ"], [event["ticker"] for event in events])

    @patch("shilun.common.db.get_mongo_client")
    def test_store_reads_raw_market_data_collections(self, mock_get_mongo_client) -> None:
        fake_client = FakeClient()
        mock_get_mongo_client.return_value = fake_client

        store = MongoSnapshotStore("mongodb://unit-test", "shilun")
        daily_bars = fake_client["shilun"]["market_daily_bars"]
        stock_basic = fake_client["shilun"]["stock_basic"]
        trade_calendar = fake_client["shilun"]["trade_calendar"]
        daily_basic = fake_client["shilun"]["daily_basic"]
        moneyflow = fake_client["shilun"]["moneyflow"]

        index_names = {call["name"] for call in daily_bars.index_calls}
        self.assertIn("uniq_market_daily_bar", index_names)
        self.assertIn("idx_market_daily_date_ticker", index_names)
        self.assertIn("uniq_stock_basic_ts_code", {call["name"] for call in stock_basic.index_calls})
        self.assertIn("uniq_trade_calendar_date", {call["name"] for call in trade_calendar.index_calls})
        self.assertIn("uniq_daily_basic_scope", {call["name"] for call in daily_basic.index_calls})
        self.assertIn("uniq_moneyflow_scope", {call["name"] for call in moneyflow.index_calls})

        store.upsert_daily_bars([{"ticker": "000001.SZ", "date": "2026-03-30", "close": 10.0}])
        self.assertEqual({"ticker": "000001.SZ", "date": "2026-03-30"}, daily_bars.update_calls[0]["query"])

        daily_bars.documents = [
            {"ticker": "000002.SZ", "date": "2026-03-31", "close": 8.1},
            {"ticker": "000001.SZ", "date": "2026-03-30", "close": 10.0},
        ]
        self.assertEqual(
            ["000001.SZ", "000002.SZ"],
            [record["ticker"] for record in store.find_daily_bars(start_date="20260330", end_date="20260331")],
        )

        stock_basic.documents = [{"ts_code": "000001.SZ", "name": "甲公司"}]
        trade_calendar.documents = [{"exchange": "SSE", "cal_date": "20260330", "is_open": 1}]
        daily_basic.documents = [{"ts_code": "000001.SZ", "trade_date": "20260330", "pe": 10.0}]
        moneyflow.documents = [{"ts_code": "000001.SZ", "trade_date": "20260330", "net_mf_amount": 88.0}]

        self.assertEqual("甲公司", store.find_stock_basic()[0]["name"])
        self.assertEqual(1, store.find_trade_calendar(start_date="20260330", end_date="20260330")[0]["is_open"])
        self.assertEqual(10.0, store.find_daily_basic(trade_date="2026-03-30", ts_code="000001.SZ")[0]["pe"])
        self.assertEqual(88.0, store.find_moneyflow(start_date="2026-03-30", end_date="2026-03-30")[0]["net_mf_amount"])


if __name__ == "__main__":
    unittest.main()
