from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from shilun.trading.execution_production import FullScenarioProductionService


TZ = ZoneInfo("Asia/Shanghai")


class _Cursor:
    def __init__(self, docs):
        self.docs = [dict(item) for item in docs]

    def sort(self, key, direction=1):
        self.docs.sort(key=lambda item: item.get(key) or "", reverse=direction < 0)
        return self

    def __iter__(self):
        return iter([dict(item) for item in self.docs])


class _Collection:
    def __init__(self, docs=None):
        self.docs = [dict(item) for item in (docs or [])]

    @staticmethod
    def _match(document, query):
        for key, expected in query.items():
            actual = document.get(key)
            if isinstance(expected, dict) and "$in" in expected:
                if actual not in expected["$in"]:
                    return False
            elif actual != expected:
                return False
        return True

    def find(self, query, projection=None):
        return _Cursor(item for item in self.docs if self._match(item, query))

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
            self.docs.append({**query, **update.get("$setOnInsert", {}), **update.get("$set", {})})


class _RawMarket:
    def __init__(self, daily):
        self.daily = list(daily)

    def find_daily_bars(self, *, start_date, end_date, tickers=None):
        allowed = set(tickers or [])
        return [
            dict(item) for item in self.daily
            if start_date <= item["date"] <= end_date
            and (not allowed or item["ticker"] in allowed)
        ]

    def find_stock_basic(self):
        return [{"ts_code": "000001.SZ", "name": "生产桥样例"}]


class _Store:
    def __init__(self, collections, daily):
        self.collections = dict(collections)
        self.raw_market = _RawMarket(daily)

    def collection(self, name):
        return self.collections.setdefault(name, _Collection())


def _daily_rows(ticker, *, base):
    start = date(2026, 5, 25)
    rows = []
    for index in range(36):
        day = start + timedelta(days=index)
        close = base + index * 0.02
        rows.append({
            "ticker": ticker,
            "date": day.isoformat(),
            "open": close - 0.03,
            "high": close + 0.12,
            "low": close - 0.15,
            "close": close,
        })
    return rows


def _bars(ticker, price):
    return {
        "ticker": ticker,
        "trade_date": "20260715",
        "interval": "5",
        "bars": [
            {
                "datetime": f"2026-07-15T14:{minute:02d}:00+08:00",
                "open": price - 0.03,
                "high": price + 0.06,
                "low": price - 0.08 + index * 0.01,
                "close": price + index * 0.01,
                "volume": 1000,
                "amount": price * 1000,
            }
            for index, minute in enumerate((5, 10, 15))
        ],
    }


def test_full_scenario_bridge_persists_plan_facts_event_and_e7_evaluation():
    now = datetime(2026, 7, 15, 14, 20, tzinfo=TZ)
    quote_at = now.isoformat(timespec="seconds")
    collections = {
        "holdings": _Collection([]),
        "intraday_watchlist": _Collection([{
            "ticker": "000001.SZ", "sector_name": "样例板块", "enabled": True,
            "updated_at": quote_at,
        }]),
        "sector_trends_cache": _Collection([{
            "analysis_date": "2026-07-15", "computed_at": quote_at,
            "payload": {"top_sectors": [{
                "sector_name": "样例板块",
                "leader_candidates": [{"ticker": "000001.SZ"}],
            }]},
        }]),
        "trading_realtime_quotes": _Collection([
            {"ticker": "000001.SZ", "current": 10.0, "open": 9.95, "prev_close": 9.9, "quote_at": quote_at},
            {"ticker": "000001.SH", "current": 3900, "prev_close": 3910, "quote_at": quote_at},
            {"ticker": "399006.SZ", "current": 2500, "prev_close": 2510, "quote_at": quote_at},
        ]),
        "minute_bars_daily": _Collection([
            _bars("000001.SZ", 10.0), _bars("000001.SH", 3900), _bars("399006.SZ", 2500),
        ]),
        "execution_market_facts": _Collection([{
            "trade_date": "20260715", "sector_name": "样例板块", "state": "divergence",
            "up_ratio": 0.5, "computed_at": quote_at,
        }]),
        "execution_market_fact_snapshots": _Collection([{
            "snapshot_id": "s1", "trade_date": "20260715", "sector_name": "样例板块",
            "up_ratio": 0.5, "computed_at": quote_at,
        }]),
        "market_part1_cache": _Collection([{
            "cached_at": quote_at, "payload": {"market_gate": {"state": "hold"}},
        }]),
        "account_risk_profiles": _Collection([{
            "profile_id": "default", "total_assets": 86_000, "available_cash": 78_000,
            "max_loss_per_trade": 6_880, "source": "user_configured",
        }]),
        "trade_calendar": _Collection([{"cal_date": "20260716", "is_open": 1}]),
    }
    daily = [
        *_daily_rows("000001.SZ", base=9.0),
        *_daily_rows("000001.SH", base=3800),
        *_daily_rows("399006.SZ", base=2400),
    ]
    store = _Store(collections, daily)

    result = FullScenarioProductionService(store).refresh_all(now=now)

    assert result["evaluated_count"] == 1
    assert result["e7_evaluation_count"] == 1
    assert len(store.collection("trading_watch_plans").docs) == 1
    assert len(store.collection("execution_scenario_fact_snapshots").docs) == 1
    assert len(store.collection("trading_decision_events").docs) == 1
    frozen_plan = deepcopy(store.collection("trading_watch_plans").docs[0]["plan"])
    assert store.collection("trading_watch_plans").docs[0]["frozen_at"] == quote_at
    fact_payload = store.collection("execution_scenario_fact_snapshots").docs[0]["facts"]
    assert len(fact_payload["source_timestamps"]) >= 5
    e7 = store.collection("execution_e7_evaluations").docs[0]
    assert e7["status"] in {"not_triggered", "data_blocked"}
    assert e7["reason"]

    # 基础决策时间线与全场景执行共用 trading_decision_events。基础事件即使
    # 时间更新，也不能打断同一 plan_id 内的语义去重。
    store.collection("trading_decision_events").docs.append({
        "event_id": "timeline-between-scenario-runs",
        "ticker": "000001.SZ",
        "plan_id": "20260715:000001.SZ:ma5_trend_v0_2:0.2.0",
        "computed_at": "2026-07-15T14:21:00+08:00",
        "fingerprint": "timeline-fingerprint",
    })
    FullScenarioProductionService(store).refresh_all(
        now=datetime(2026, 7, 15, 14, 22, tzinfo=TZ),
    )
    assert len(store.collection("trading_decision_events").docs) == 2
    assert store.collection("trading_watch_plans").docs[0]["plan"] == frozen_plan
