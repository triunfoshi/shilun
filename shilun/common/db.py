import math
import sqlite3
from datetime import date, datetime
from pathlib import Path
from typing import Any

try:
    from pymongo import MongoClient
    from pymongo import UpdateOne
    from pymongo.collection import Collection
    from pymongo.database import Database
except ImportError:  # pragma: no cover
    MongoClient = None
    UpdateOne = None
    Collection = Any
    Database = Any


def get_connection(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def get_mongo_client(uri: str) -> MongoClient:
    if MongoClient is None:  # pragma: no cover
        raise ImportError("pymongo is required for MongoDB support")
    return MongoClient(uri, serverSelectionTimeoutMS=3000, connectTimeoutMS=3000, socketTimeoutMS=3000)


def get_mongo_database(uri: str, db_name: str) -> Database:
    return get_mongo_client(uri)[db_name]


def _normalize_mongo_value(value: Any) -> Any:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            return None
        return value
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _normalize_mongo_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_normalize_mongo_value(item) for item in value]
    item_method = getattr(value, "item", None)
    if callable(item_method):
        try:
            return _normalize_mongo_value(item_method())
        except Exception:
            pass
    isoformat = getattr(value, "isoformat", None)
    if callable(isoformat):
        try:
            return isoformat()
        except Exception:
            pass
    return value


def _strip_mongo_id(document: dict[str, Any]) -> dict[str, Any]:
    payload = dict(document)
    payload.pop("_id", None)
    return payload


def _date_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        text = value.strip()
        if len(text) == 8 and text.isdigit():
            return f"{text[:4]}-{text[4:6]}-{text[6:]}"
        return text.split("T", 1)[0].split(" ", 1)[0]
    strftime = getattr(value, "strftime", None)
    if callable(strftime):
        return strftime("%Y-%m-%d")
    return str(value)


def _trade_date_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        text = value.strip()
        if len(text) >= 10 and "-" in text:
            return text[:10].replace("-", "")
        return text
    strftime = getattr(value, "strftime", None)
    if callable(strftime):
        return strftime("%Y%m%d")
    return str(value)


def _records_from_frame_or_records(rows: Any) -> list[dict[str, Any]]:
    if rows is None:
        return []
    to_dict = getattr(rows, "to_dict", None)
    if callable(to_dict):
        return list(rows.to_dict(orient="records"))
    return [dict(row) for row in rows]


class _MongoStoreBase:
    def __init__(self, owner: "MongoSnapshotStore") -> None:
        self.owner = owner

    @property
    def raw_market(self) -> "MongoSnapshotStore":
        return self.owner

    @property
    def market_snapshots(self) -> "MongoSnapshotStore":
        return self.owner

    @property
    def candidate_pools(self) -> "MongoSnapshotStore":
        return self.owner

    def collection(self, name: str) -> Collection:
        return self.owner.collection(name)

    def _upsert_payloads(self, collection_name: str, payloads: list[dict[str, Any]], key_fields: tuple[str, ...]) -> int:
        return self.owner._upsert_payloads(collection_name, payloads, key_fields)

    def _find_records(
        self,
        collection_name: str,
        query: dict[str, Any],
        sort: list[tuple[str, int]] | str,
        *,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        return self.owner._find_records(collection_name, query, sort, limit=limit)


class RawMarketDataStore(_MongoStoreBase):
    """Mongo interface for raw synced market data.

    M5 P3 改进点：同步任务和 Mongo-first provider 只需要这层，不应依赖完整
    MongoSnapshotStore 的快照和候选池能力。
    """

    def upsert_daily_bars(self, rows: Any) -> int:
        return self.raw_market.upsert_daily_bars(rows)

    def find_daily_bars(
        self,
        *,
        start_date: str,
        end_date: str,
        tickers: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        return self.raw_market.find_daily_bars(start_date=start_date, end_date=end_date, tickers=tickers)

    def find_daily_bars_for_trade_date(self, trade_date: str) -> list[dict[str, Any]]:
        return self.raw_market.find_daily_bars_for_trade_date(trade_date)

    def upsert_stock_basic(self, rows: Any) -> int:
        return self.raw_market.upsert_stock_basic(rows)

    def find_stock_basic(self) -> list[dict[str, Any]]:
        return self.raw_market.find_stock_basic()

    def upsert_trade_calendar(self, rows: Any) -> int:
        return self.raw_market.upsert_trade_calendar(rows)

    def find_trade_calendar(self, *, start_date: str, end_date: str, exchange: str = "SSE") -> list[dict[str, Any]]:
        return self.raw_market.find_trade_calendar(start_date=start_date, end_date=end_date, exchange=exchange)

    def upsert_daily_basic(self, rows: Any) -> int:
        return self.raw_market.upsert_daily_basic(rows)

    def find_daily_basic(self, *, trade_date: str, ts_code: str | None = None) -> list[dict[str, Any]]:
        return self.raw_market.find_daily_basic(trade_date=trade_date, ts_code=ts_code)

    def upsert_moneyflow(self, rows: Any) -> int:
        return self.raw_market.upsert_moneyflow(rows)

    def find_moneyflow(
        self,
        *,
        start_date: str,
        end_date: str,
        ts_code: str | None = None,
    ) -> list[dict[str, Any]]:
        return self.raw_market.find_moneyflow(start_date=start_date, end_date=end_date, ts_code=ts_code)


class MarketSnapshotRecordStore(_MongoStoreBase):
    """Mongo interface for ranked market snapshots and analysis payloads."""

    def upsert_analysis(self, payload: dict[str, Any]) -> None:
        self.market_snapshots.upsert_analysis(payload)

    def upsert_analysis_batch(self, payloads: list[dict[str, Any]]) -> None:
        self.market_snapshots.upsert_analysis_batch(payloads)

    def upsert_market_snapshot(
        self,
        *,
        analysis_date: str,
        top_n: int,
        exclude_st: bool,
        records: list[dict[str, Any]],
    ) -> None:
        self.market_snapshots.upsert_market_snapshot(
            analysis_date=analysis_date,
            top_n=top_n,
            exclude_st=exclude_st,
            records=records,
        )

    def upsert_market_snapshot_records(
        self,
        *,
        analysis_date: str,
        exclude_st: bool,
        records: list[dict[str, Any]],
    ) -> None:
        self.market_snapshots.upsert_market_snapshot_records(
            analysis_date=analysis_date,
            exclude_st=exclude_st,
            records=records,
        )

    def find_market_snapshot_records(
        self,
        *,
        analysis_date: str,
        exclude_st: bool,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        return self.market_snapshots.find_market_snapshot_records(
            analysis_date=analysis_date,
            exclude_st=exclude_st,
            limit=limit,
        )

    def find_market_snapshot_records_between(
        self,
        *,
        start_date: str,
        end_date: str,
        exclude_st: bool,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        return self._find_records(
            "market_snapshot_records",
            {
                "analysis_date": {
                    "$gte": _date_text(start_date),
                    "$lte": _date_text(end_date),
                },
                "exclude_st": bool(exclude_st),
            },
            [("analysis_date", 1), ("rank", 1), ("ticker", 1)],
            limit=limit,
        )

    def count_market_snapshot_records(self, *, analysis_date: str, exclude_st: bool) -> int:
        return int(
            self.collection("market_snapshot_records").count_documents(
                {
                    "analysis_date": analysis_date,
                    "exclude_st": bool(exclude_st),
                }
            )
        )


class CandidatePoolStateStore(_MongoStoreBase):
    """Mongo interface for candidate-pool states and events."""

    def upsert_candidate_pool_states(
        self,
        *,
        analysis_date: str,
        exclude_st: bool,
        states: list[dict[str, Any]],
    ) -> int:
        return self.candidate_pools.upsert_candidate_pool_states(
            analysis_date=analysis_date,
            exclude_st=exclude_st,
            states=states,
        )

    def upsert_candidate_pool_events(
        self,
        *,
        analysis_date: str,
        exclude_st: bool,
        events: list[dict[str, Any]],
    ) -> int:
        return self.candidate_pools.upsert_candidate_pool_events(
            analysis_date=analysis_date,
            exclude_st=exclude_st,
            events=events,
        )

    def find_candidate_pool_states(
        self,
        *,
        analysis_date: str,
        exclude_st: bool,
        pool_status: str | None = None,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        return self.candidate_pools.find_candidate_pool_states(
            analysis_date=analysis_date,
            exclude_st=exclude_st,
            pool_status=pool_status,
            limit=limit,
        )

    def find_candidate_pool_states_between(
        self,
        *,
        start_date: str,
        end_date: str,
        exclude_st: bool,
        pool_status: str | None = None,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        return self.candidate_pools.find_candidate_pool_states_between(
            start_date=start_date,
            end_date=end_date,
            exclude_st=exclude_st,
            pool_status=pool_status,
            limit=limit,
        )

    def find_latest_candidate_pool_states_before(
        self,
        *,
        analysis_date: str,
        exclude_st: bool,
        tickers: list[str] | None = None,
    ) -> dict[str, dict[str, Any]]:
        query: dict[str, Any] = {
            "analysis_date": {"$lt": _date_text(analysis_date)},
            "exclude_st": bool(exclude_st),
        }
        if tickers:
            query["ticker"] = {"$in": [str(ticker) for ticker in tickers]}
        cursor = self.collection("candidate_pool_states").find(query, {"_id": 0}).sort([("ticker", 1), ("analysis_date", -1)])
        latest: dict[str, dict[str, Any]] = {}
        for record in cursor:
            ticker = str(record.get("ticker") or "")
            if ticker and ticker not in latest:
                latest[ticker] = _strip_mongo_id(record)
        return latest

    def find_candidate_pool_events(
        self,
        *,
        analysis_date: str,
        exclude_st: bool,
        event_type: str | None = None,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        query: dict[str, Any] = {
            "analysis_date": _date_text(analysis_date),
            "exclude_st": bool(exclude_st),
        }
        if event_type:
            query["event_type"] = event_type
        return self._find_records(
            "candidate_pool_events",
            query,
            [("event_type", 1), ("rank", 1), ("ticker", 1)],
            limit=limit,
        )


class MongoSnapshotStore:
    def __init__(self, uri: str, db_name: str) -> None:
        self.client = get_mongo_client(uri)
        self.db = self.client[db_name]
        self.ensure_indexes()
        self.raw_market = RawMarketDataStore(self)
        self.market_snapshots = MarketSnapshotRecordStore(self)
        self.candidate_pools = CandidatePoolStateStore(self)

    def close(self) -> None:
        self.client.close()

    def collection(self, name: str) -> Collection:
        return self.db[name]

    def _create_index(
        self,
        collection_name: str,
        keys: list[tuple[str, int]],
        *,
        name: str,
        unique: bool = False,
    ) -> None:
        kwargs: dict[str, Any] = {"name": name}
        if unique:
            kwargs["unique"] = True
        self.collection(collection_name).create_index(keys, **kwargs)

    def ensure_indexes(self) -> None:
        # M5 P2: declare indexes as data instead of a long sequence of
        # imperative calls. This keeps Mongo schema visibility while reducing
        # one of the largest noise blocks inside the store.
        index_specs = [
            ("analysis_snapshots", [("ticker", 1), ("analysis_date", 1)], "uniq_ticker_analysis_date", True),
            ("analysis_snapshots", [("analysis_date", -1)], "idx_analysis_date_desc", False),
            ("market_snapshots", [("analysis_date", 1), ("top_n", 1), ("exclude_st", 1)], "uniq_market_snapshot_scope", True),
            ("market_snapshots", [("analysis_date", -1)], "idx_market_snapshot_date_desc", False),
            ("market_snapshot_records", [("analysis_date", 1), ("ticker", 1), ("exclude_st", 1)], "uniq_market_snapshot_record_scope", True),
            ("market_snapshot_records", [("analysis_date", -1), ("rank", 1)], "idx_market_snapshot_rank", False),
            ("market_snapshot_records", [("ticker", 1), ("analysis_date", -1)], "idx_market_snapshot_ticker_date", False),
            ("market_snapshot_records", [("analysis_date", -1), ("conclusion_label", 1), ("rank", 1)], "idx_market_snapshot_conclusion_rank", False),
            ("market_snapshot_records", [("analysis_date", -1), ("industry", 1), ("rank", 1)], "idx_market_snapshot_industry_rank", False),
            ("market_snapshot_records", [("analysis_date", -1), ("execution_score", -1), ("risk_score", 1)], "idx_market_snapshot_execution_risk", False),
            ("market_snapshot_records", [("analysis_date", -1), ("strategy_signals.strategy_id", 1), ("rank", 1)], "idx_market_snapshot_strategy_rank", False),
            ("candidate_pool_states", [("analysis_date", 1), ("ticker", 1), ("exclude_st", 1)], "uniq_candidate_pool_state_scope", True),
            ("candidate_pool_states", [("analysis_date", -1), ("pool_status", 1), ("rank", 1)], "idx_candidate_pool_state_status_rank", False),
            ("candidate_pool_states", [("ticker", 1), ("analysis_date", -1)], "idx_candidate_pool_state_ticker_date", False),
            ("candidate_pool_events", [("analysis_date", 1), ("ticker", 1), ("exclude_st", 1)], "uniq_candidate_pool_event_scope", True),
            ("candidate_pool_events", [("analysis_date", -1), ("event_type", 1), ("pool_status", 1)], "idx_candidate_pool_event_type_status", False),
            ("market_daily_bars", [("ticker", 1), ("date", 1)], "uniq_market_daily_bar", True),
            ("market_daily_bars", [("date", 1), ("ticker", 1)], "idx_market_daily_date_ticker", False),
            ("stock_basic", [("ts_code", 1)], "uniq_stock_basic_ts_code", True),
            ("trade_calendar", [("exchange", 1), ("cal_date", 1)], "uniq_trade_calendar_date", True),
            ("daily_basic", [("ts_code", 1), ("trade_date", 1)], "uniq_daily_basic_scope", True),
            ("daily_basic", [("trade_date", 1), ("ts_code", 1)], "idx_daily_basic_date_ticker", False),
            ("moneyflow", [("ts_code", 1), ("trade_date", 1)], "uniq_moneyflow_scope", True),
            ("moneyflow", [("trade_date", 1), ("ts_code", 1)], "idx_moneyflow_date_ticker", False),
            ("sync_state", [("dataset", 1), ("scope", 1)], "uniq_sync_state_scope", True),
        ]
        for collection_name, keys, name, unique in index_specs:
            self._create_index(collection_name, keys, name=name, unique=unique)

    def _upsert_payloads(self, collection_name: str, payloads: list[dict[str, Any]], key_fields: tuple[str, ...]) -> int:
        collection = self.collection(collection_name)
        normalized_payloads = [
            payload
            for payload in _normalize_mongo_value(payloads)
            if not any(payload.get(field) in {None, ""} for field in key_fields)
        ]
        if UpdateOne is not None and hasattr(collection, "bulk_write") and normalized_payloads:
            for start in range(0, len(normalized_payloads), 1000):
                chunk = normalized_payloads[start : start + 1000]
                collection.bulk_write(
                    [
                        UpdateOne(
                            {field: payload.get(field) for field in key_fields},
                            {"$set": payload},
                            upsert=True,
                        )
                        for payload in chunk
                    ],
                    ordered=False,
                )
            return len(normalized_payloads)

        count = 0
        for payload in normalized_payloads:
            query = {field: payload.get(field) for field in key_fields}
            collection.update_one(query, {"$set": payload}, upsert=True)
            count += 1
        return count

    def _find_records(
        self,
        collection_name: str,
        query: dict[str, Any],
        sort: list[tuple[str, int]] | str,
        *,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        cursor = self.collection(collection_name).find(query, {"_id": 0}).sort(sort)
        if limit is not None:
            cursor = cursor.limit(max(1, int(limit)))
        return [_strip_mongo_id(record) for record in cursor]

    def upsert_analysis(self, payload: dict[str, Any]) -> None:
        normalized_payload = _normalize_mongo_value(payload)
        ticker = str(normalized_payload.get("ticker") or "")
        analysis_date = str(normalized_payload.get("date") or normalized_payload.get("analysis_date") or "")
        if not ticker or not analysis_date:
            return
        self.collection("analysis_snapshots").update_one(
            {"ticker": ticker, "analysis_date": analysis_date},
            {"$set": normalized_payload},
            upsert=True,
        )

    def upsert_analysis_batch(self, payloads: list[dict[str, Any]]) -> None:
        for payload in payloads:
            self.upsert_analysis(payload)

    def upsert_market_snapshot(
        self,
        *,
        analysis_date: str,
        top_n: int,
        exclude_st: bool,
        records: list[dict[str, Any]],
    ) -> None:
        normalized_records = _normalize_mongo_value(records)
        self.collection("market_snapshots").update_one(
            {
                "analysis_date": analysis_date,
                "top_n": int(top_n),
                "exclude_st": bool(exclude_st),
            },
            {
                "$set": {
                    "analysis_date": analysis_date,
                    "top_n": int(top_n),
                    "exclude_st": bool(exclude_st),
                    "record_count": len(normalized_records),
                    "records": normalized_records,
                }
            },
            upsert=True,
        )

    def upsert_market_snapshot_records(
        self,
        *,
        analysis_date: str,
        exclude_st: bool,
        records: list[dict[str, Any]],
    ) -> None:
        payloads: list[dict[str, Any]] = []
        for record in _normalize_mongo_value(records):
            ticker = str(record.get("ticker") or "")
            if not ticker:
                continue
            payloads.append({
                **record,
                "ticker": ticker,
                "analysis_date": analysis_date,
                "exclude_st": bool(exclude_st),
                "record_version": "v1",
            })
        self._upsert_payloads("market_snapshot_records", payloads, ("analysis_date", "ticker", "exclude_st"))

    def find_market_snapshot_records(
        self,
        *,
        analysis_date: str,
        exclude_st: bool,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        return self._find_records(
            "market_snapshot_records",
            {
                "analysis_date": analysis_date,
                "exclude_st": bool(exclude_st),
            },
            "rank",
            limit=limit,
        )

    def find_market_snapshot_records_between(
        self,
        *,
        start_date: str,
        end_date: str,
        exclude_st: bool,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        return self.market_snapshots.find_market_snapshot_records_between(
            start_date=start_date,
            end_date=end_date,
            exclude_st=exclude_st,
            limit=limit,
        )

    def count_market_snapshot_records(self, *, analysis_date: str, exclude_st: bool) -> int:
        return self.market_snapshots.count_market_snapshot_records(analysis_date=analysis_date, exclude_st=exclude_st)

    def upsert_candidate_pool_states(
        self,
        *,
        analysis_date: str,
        exclude_st: bool,
        states: list[dict[str, Any]],
    ) -> int:
        payloads: list[dict[str, Any]] = []
        for state in _normalize_mongo_value(states):
            ticker = str(state.get("ticker") or "")
            if not ticker:
                continue
            payloads.append({
                **state,
                "ticker": ticker,
                "analysis_date": analysis_date,
                "exclude_st": bool(exclude_st),
                "state_version": "v1",
            })
        return self._upsert_payloads("candidate_pool_states", payloads, ("analysis_date", "ticker", "exclude_st"))

    def upsert_candidate_pool_events(
        self,
        *,
        analysis_date: str,
        exclude_st: bool,
        events: list[dict[str, Any]],
    ) -> int:
        payloads: list[dict[str, Any]] = []
        for event in _normalize_mongo_value(events):
            ticker = str(event.get("ticker") or "")
            if not ticker:
                continue
            payloads.append({
                **event,
                "ticker": ticker,
                "analysis_date": analysis_date,
                "exclude_st": bool(exclude_st),
                "event_version": "v1",
            })
        return self._upsert_payloads("candidate_pool_events", payloads, ("analysis_date", "ticker", "exclude_st"))

    def find_candidate_pool_states(
        self,
        *,
        analysis_date: str,
        exclude_st: bool,
        pool_status: str | None = None,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        query: dict[str, Any] = {
            "analysis_date": _date_text(analysis_date),
            "exclude_st": bool(exclude_st),
        }
        if pool_status:
            query["pool_status"] = pool_status
        return self._find_records(
            "candidate_pool_states",
            query,
            [("pool_status", 1), ("rank", 1), ("ticker", 1)],
            limit=limit,
        )

    def find_candidate_pool_states_between(
        self,
        *,
        start_date: str,
        end_date: str,
        exclude_st: bool,
        pool_status: str | None = None,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        query: dict[str, Any] = {
            "analysis_date": {
                "$gte": _date_text(start_date),
                "$lte": _date_text(end_date),
            },
            "exclude_st": bool(exclude_st),
        }
        if pool_status:
            query["pool_status"] = pool_status
        return self._find_records(
            "candidate_pool_states",
            query,
            [("analysis_date", 1), ("pool_status", 1), ("rank", 1), ("ticker", 1)],
            limit=limit,
        )

    def find_latest_candidate_pool_states_before(
        self,
        *,
        analysis_date: str,
        exclude_st: bool,
        tickers: list[str] | None = None,
    ) -> dict[str, dict[str, Any]]:
        return self.candidate_pools.find_latest_candidate_pool_states_before(
            analysis_date=analysis_date,
            exclude_st=exclude_st,
            tickers=tickers,
        )

    def find_candidate_pool_events(
        self,
        *,
        analysis_date: str,
        exclude_st: bool,
        event_type: str | None = None,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        return self.candidate_pools.find_candidate_pool_events(
            analysis_date=analysis_date,
            exclude_st=exclude_st,
            event_type=event_type,
            limit=limit,
        )

    def upsert_daily_bars(self, rows: Any) -> int:
        payloads: list[dict[str, Any]] = []
        for record in _records_from_frame_or_records(rows):
            ticker = str(record.get("ticker") or record.get("ts_code") or "")
            date_text = _date_text(record.get("date") or record.get("trade_date"))
            if not ticker or not date_text:
                continue
            payloads.append({**record, "ticker": ticker, "date": date_text})
        return self._upsert_payloads("market_daily_bars", payloads, ("ticker", "date"))

    def find_daily_bars(
        self,
        *,
        start_date: str,
        end_date: str,
        tickers: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        query: dict[str, Any] = {
            "date": {
                "$gte": _date_text(start_date),
                "$lte": _date_text(end_date),
            }
        }
        if tickers:
            query["ticker"] = {"$in": [str(ticker) for ticker in tickers]}
        return self._find_records("market_daily_bars", query, [("ticker", 1), ("date", 1)])

    def find_daily_bars_for_trade_date(self, trade_date: str) -> list[dict[str, Any]]:
        return self._find_records("market_daily_bars", {"date": _date_text(trade_date)}, "ticker")

    def upsert_stock_basic(self, rows: Any) -> int:
        payloads: list[dict[str, Any]] = []
        for record in _records_from_frame_or_records(rows):
            ts_code = str(record.get("ts_code") or record.get("ticker") or "")
            if not ts_code:
                continue
            payloads.append({**record, "ts_code": ts_code})
        return self._upsert_payloads("stock_basic", payloads, ("ts_code",))

    def find_stock_basic(self) -> list[dict[str, Any]]:
        return self._find_records("stock_basic", {}, "ts_code")

    def upsert_trade_calendar(self, rows: Any) -> int:
        payloads: list[dict[str, Any]] = []
        for record in _records_from_frame_or_records(rows):
            exchange = str(record.get("exchange") or "SSE")
            cal_date = _trade_date_text(record.get("cal_date") or record.get("date"))
            if not cal_date:
                continue
            payloads.append({**record, "exchange": exchange, "cal_date": cal_date})
        return self._upsert_payloads("trade_calendar", payloads, ("exchange", "cal_date"))

    def find_trade_calendar(self, *, start_date: str, end_date: str, exchange: str = "SSE") -> list[dict[str, Any]]:
        return self._find_records(
            "trade_calendar",
            {
                "exchange": exchange,
                "cal_date": {"$gte": _trade_date_text(start_date), "$lte": _trade_date_text(end_date)},
            },
            "cal_date",
        )

    def upsert_daily_basic(self, rows: Any) -> int:
        payloads: list[dict[str, Any]] = []
        for record in _records_from_frame_or_records(rows):
            ts_code = str(record.get("ts_code") or record.get("ticker") or "")
            trade_date = _trade_date_text(record.get("trade_date") or record.get("date"))
            if not ts_code or not trade_date:
                continue
            payloads.append({**record, "ts_code": ts_code, "trade_date": trade_date})
        return self._upsert_payloads("daily_basic", payloads, ("ts_code", "trade_date"))

    def find_daily_basic(self, *, trade_date: str, ts_code: str | None = None) -> list[dict[str, Any]]:
        query: dict[str, Any] = {"trade_date": _trade_date_text(trade_date)}
        if ts_code:
            query["ts_code"] = ts_code
        return self._find_records("daily_basic", query, "ts_code")

    def upsert_moneyflow(self, rows: Any) -> int:
        payloads: list[dict[str, Any]] = []
        for record in _records_from_frame_or_records(rows):
            ts_code = str(record.get("ts_code") or record.get("ticker") or "")
            trade_date = _trade_date_text(record.get("trade_date") or record.get("date"))
            if not ts_code or not trade_date:
                continue
            payloads.append({**record, "ts_code": ts_code, "trade_date": trade_date})
        return self._upsert_payloads("moneyflow", payloads, ("ts_code", "trade_date"))

    def find_moneyflow(
        self,
        *,
        start_date: str,
        end_date: str,
        ts_code: str | None = None,
    ) -> list[dict[str, Any]]:
        query: dict[str, Any] = {
            "trade_date": {
                "$gte": _trade_date_text(start_date),
                "$lte": _trade_date_text(end_date),
            }
        }
        if ts_code:
            query["ts_code"] = ts_code
        return self._find_records("moneyflow", query, [("trade_date", 1), ("ts_code", 1)])

    def upsert_sync_state(self, payload: dict[str, Any]) -> int:
        dataset = str(payload.get("dataset") or "")
        scope = str(payload.get("scope") or "default")
        if not dataset:
            return 0
        state = {**payload, "dataset": dataset, "scope": scope}
        return self._upsert_payloads("sync_state", [state], ("dataset", "scope"))

    def find_sync_state(self, *, dataset: str, scope: str = "default") -> dict[str, Any] | None:
        records = self._find_records("sync_state", {"dataset": dataset, "scope": scope}, "dataset", limit=1)
        return records[0] if records else None
