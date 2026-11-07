from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from shilun.trading.workbench import ExecutionWorkbenchService, queue_workbench_notifications


TZ = ZoneInfo("Asia/Shanghai")


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
        return [dict(item) for item in self.docs if self._match(item, query)]

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


class _Store:
    def __init__(self, collections=None):
        self.collections = dict(collections or {})
        if "intraday_watchlist" not in self.collections:
            event_tickers = {
                str(item.get("ticker") or "").upper()
                for name in ("trading_decision_events", "intraday_decision_events")
                for item in self.collections.get(name, _Collection()).docs
                if item.get("ticker")
            }
            self.collections["intraday_watchlist"] = _Collection([
                {"ticker": ticker, "enabled": True}
                for ticker in sorted(event_tickers)
            ])

    def collection(self, name):
        return self.collections.setdefault(name, _Collection())


def _decision_event(*, ticker="000001.SZ", action="prepare_entry", state="entry_ready"):
    return {
        "event_id": f"decision-{ticker}",
        "ticker": ticker,
        "computed_at": "2026-07-15T14:40:00+08:00",
        "fingerprint": f"fingerprint-{ticker}",
        "previous": {"action_label": "等待确认"},
        "current": {
            "canonical_action_type": action,
            "action_label": "可建仓 500 股" if action == "prepare_entry" else "立即清仓",
            "position_state": "flat" if action == "prepare_entry" else "positioned",
            "execution_state": state,
            "scenario_id": "E3" if action == "prepare_entry" else "R1",
            "scenario_title": "区间支撑反转" if action == "prepare_entry" else "硬止损退出",
        },
        "reasons": ["确认已完成，价格仍在最高可接受价内"],
        "decision": {
            "position_state": "flat" if action == "prepare_entry" else "positioned",
            "execution_state": state,
            "action": {"action_type": action, "target_quantity": 500, "change_quantity": 500},
            "switch_conditions": ["价格不得超过 10.80"],
            "presentation": {
                "headline": "可建仓 500 股" if action == "prepare_entry" else "立即清仓",
                "reason": "确认已完成，价格仍在最高可接受价内",
                "next_condition": "价格不得超过 10.80",
                "target_quantity": 500,
                "change_quantity": 500,
            },
        },
    }


def test_workbench_layers_primary_waiting_and_never_calls_flat_a_holding():
    store = _Store({
        "trading_decision_events": _Collection([_decision_event()]),
        "intraday_decision_events": _Collection([{
            "event_id": "candidate-2",
            "ticker": "000002.SZ",
            "name": "候选二",
            "computed_at": "2026-07-15T14:39:00+08:00",
            "current": {
                "action_type": "hold_observe",
                "action_label": "继续持有并观察确认",
                "scenario_title": "支撑回收",
                "quote_at": "2026-07-15T14:39:00+08:00",
            },
            "reasons": ["重新站回支撑"],
            "notification_eligible": False,
        }]),
        "holdings": _Collection([]),
        "account_risk_profiles": _Collection([{
            "profile_id": "default", "total_assets": 86_000, "available_cash": 78_000, "max_loss_per_trade": 6_880,
        }]),
    })

    payload = ExecutionWorkbenchService(
        store,
        now_factory=lambda: datetime(2026, 7, 15, 14, 45, tzinfo=TZ),
    ).build()

    assert payload["queues"]["primary_opportunity"]["ticker"] == "000001.SZ"
    assert payload["queues"]["waiting_confirmation"][0]["ticker"] == "000002.SZ"
    assert payload["queues"]["waiting_confirmation"][0]["action_name_cn"] == "等待确认"
    assert payload["queues"]["holding_unchanged"] == []
    assert payload["account"]["available_cash"] == 78_000
    assert {item["event_id"] for item in payload["timeline"]} >= {"decision-000001.SZ", "candidate-2"}


def test_workbench_puts_cross_collection_mismatch_first_in_risk_queue():
    store = _Store({
        "trade_orders": _Collection([{
            "order_id": "buy-1", "plan_id": "plan-1", "ticker": "000001.SZ", "side": "buy", "intent": "entry",
            "quantity": 100, "filled_quantity": 0, "remaining_quantity": 100, "limit_price": 10,
            "status": "open", "execution_state": "entry_pending", "placed_at": "2026-07-15T14:00:00+08:00",
        }]),
        "execution_cash_reservations": _Collection([]),
        "holdings": _Collection([]),
    })

    payload = ExecutionWorkbenchService(
        store,
        now_factory=lambda: datetime(2026, 7, 15, 14, 45, tzinfo=TZ),
    ).build()

    assert payload["summary"]["audit_issue_count"] == 1
    assert payload["queues"]["risk"][0]["priority"] == "P0"
    assert payload["queues"]["risk"][0]["action_name_cn"] == "核验现金预留"


def test_flat_candidate_stop_break_is_not_reported_as_holding_risk():
    store = _Store({
        "intraday_decision_events": _Collection([{
            "event_id": "candidate-risk",
            "ticker": "600172.SH",
            "name": "黄河旋风",
            "computed_at": "2026-07-15T14:39:00+08:00",
            "current": {
                "action_type": "risk_exit",
                "scenario_title": "止损触发",
                "quote_at": "2026-07-15T14:39:00+08:00",
            },
            "reasons": ["空仓候选已低于观察止损"],
        }]),
        "holdings": _Collection([]),
    })

    payload = ExecutionWorkbenchService(
        store,
        now_factory=lambda: datetime(2026, 7, 15, 14, 45, tzinfo=TZ),
    ).build()

    assert payload["summary"]["risk_count"] == 0
    assert payload["queues"]["risk"] == []


def test_workbench_explains_e7_from_production_evaluation_not_missing_bridge_copy():
    store = _Store({
        "execution_e7_evaluations": _Collection([{
            "trade_date": "20260715",
            "ticker": "600538.SH",
            "status": "not_triggered",
            "reason": "T-2 大盘、板块和个股共同超跌门槛不足",
            "next_condition": "等待板块上涨比例三组维稳",
            "computed_at": "2026-07-15T14:40:00+08:00",
        }]),
    })

    payload = ExecutionWorkbenchService(
        store,
        now_factory=lambda: datetime(2026, 7, 15, 14, 45, tzinfo=TZ),
    ).build()

    e7 = payload["opportunity_discovery"]["oversold_arbitrage"]
    assert e7["status"] == "not_triggered"
    assert e7["ticker"] == "600538.SH"
    assert "共同超跌" in e7["reason"]


def test_yesterday_candidate_is_not_presented_as_today_opportunity():
    store = _Store({
        "intraday_decision_events": _Collection([{
            "event_id": "candidate-old",
            "ticker": "002185.SZ",
            "computed_at": "2026-07-14T14:39:00+08:00",
            "current": {
                "action_type": "watch_trigger",
                "scenario_title": "昨日候选",
                "quote_at": "2026-07-14T14:39:00+08:00",
            },
        }]),
    })

    payload = ExecutionWorkbenchService(
        store,
        now_factory=lambda: datetime(2026, 7, 15, 14, 45, tzinfo=TZ),
    ).build()

    assert payload["summary"]["alternative_count"] == 0
    assert payload["queues"]["alternatives"] == []


def test_oversold_arbitrage_reports_not_evaluated_instead_of_false_no_trigger():
    payload = ExecutionWorkbenchService(
        _Store(),
        now_factory=lambda: datetime(2026, 7, 15, 14, 45, tzinfo=TZ),
    ).build()

    status = payload["opportunity_discovery"]["oversold_arbitrage"]
    assert status["status"] == "not_evaluated"
    assert "有效实时价" in status["reason"]
    assert "同步行情并重算" in status["next_condition"]


def test_oversold_arbitrage_reports_real_e7_event_when_present():
    event = _decision_event(ticker="600132.SH")
    event["current"]["scenario_id"] = "E7"
    event["current"]["scenario_title"] = "超跌反弹隔夜"
    event["decision"]["current_scenario"] = {"scenario_id": "E7", "title": "超跌反弹隔夜"}
    payload = ExecutionWorkbenchService(
        _Store({"trading_decision_events": _Collection([event])}),
        now_factory=lambda: datetime(2026, 7, 15, 14, 45, tzinfo=TZ),
    ).build()

    status = payload["opportunity_discovery"]["oversold_arbitrage"]
    assert status["status"] == "triggered"
    assert status["ticker"] == "600132.SH"


def test_same_execution_event_is_queued_once_for_feishu():
    store = _Store({"trading_decision_events": _Collection([_decision_event()])})
    current = datetime(2026, 7, 15, 14, 45, tzinfo=TZ)

    assert queue_workbench_notifications(store, now=current) == 1
    assert queue_workbench_notifications(store, now=current) == 0
    outbox = store.collection("trading_notification_outbox").docs
    assert len(outbox) == 1
    assert outbox[0]["event_id"] == "decision-000001.SZ"
    assert outbox[0]["event"]["presentation"]["headline"] == "可建仓 500 股"


def test_workbench_timeline_excludes_stocks_outside_checked_watchlist_and_holdings():
    store = _Store({
        "trading_decision_events": _Collection([
            _decision_event(ticker="000001.SZ"),
            _decision_event(ticker="600000.SH"),
        ]),
        "intraday_watchlist": _Collection([{"ticker": "000001.SZ", "enabled": True}]),
    })

    payload = ExecutionWorkbenchService(
        store,
        now_factory=lambda: datetime(2026, 7, 15, 14, 45, tzinfo=TZ),
    ).build()

    assert {item["ticker"] for item in payload["timeline"]} == {"000001.SZ"}


def test_workbench_enriches_event_name_from_stock_basic():
    store = _Store({
        "trading_decision_events": _Collection([_decision_event(ticker="000001.SZ")]),
        "intraday_watchlist": _Collection([{"ticker": "000001.SZ", "enabled": True}]),
        "stock_basic": _Collection([{"ts_code": "000001.SZ", "name": "平安银行"}]),
    })
    payload = ExecutionWorkbenchService(
        store,
        now_factory=lambda: datetime(2026, 7, 15, 14, 45, tzinfo=TZ),
    ).build()
    assert payload["timeline"][0]["name"] == "平安银行"


def test_queue_workbench_notifications_skips_stale_and_low_priority_events():
    current = datetime(2026, 7, 15, 14, 30, tzinfo=TZ)
    stale = _decision_event(ticker="603986.SH", action="risk_exit", state="exit_pending")
    stale["computed_at"] = "2026-07-15T13:30:00+08:00"
    low_priority = _decision_event(ticker="603986.SH", action="wait_confirmation", state="confirming")
    low_priority["event_id"] = "fresh-wait"
    low_priority["computed_at"] = "2026-07-15T14:20:00+08:00"
    store = _Store({
        "holdings": _Collection([{"ticker": "603986.SH", "status": "active", "quantity": 100}]),
        "trading_decision_events": _Collection([stale, low_priority]),
    })

    assert queue_workbench_notifications(store, now=current, max_age_minutes=30) == 0
