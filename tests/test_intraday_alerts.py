from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from shilun.trading.intraday_alerts import (
    dispatch_feishu_outbox,
    feishu_card,
    record_candidate_decisions,
    replace_watchlist,
)


TZ = ZoneInfo("Asia/Shanghai")


class _Cursor:
    def __init__(self, docs):
        self.docs = [dict(item) for item in docs]

    def sort(self, key, direction=1):
        self.docs.sort(key=lambda item: item.get(key) or "", reverse=direction < 0)
        return self

    def limit(self, size):
        self.docs = self.docs[:size]
        return self

    def __iter__(self):
        return iter(self.docs)


class _Collection:
    def __init__(self):
        self.docs = []

    @staticmethod
    def _match(doc, query):
        for key, expected in query.items():
            actual = doc.get(key)
            if isinstance(expected, dict):
                if "$in" in expected and actual not in expected["$in"]:
                    return False
                if "$nin" in expected and actual in expected["$nin"]:
                    return False
                if "$lte" in expected and actual > expected["$lte"]:
                    return False
            elif actual != expected:
                return False
        return True

    def find_one(self, query, projection=None, sort=None):
        docs = [item for item in self.docs if self._match(item, query)]
        if sort:
            key, direction = sort[0]
            docs.sort(key=lambda item: item.get(key) or "", reverse=direction < 0)
        return dict(docs[0]) if docs else None

    def find(self, query, projection=None):
        return _Cursor([item for item in self.docs if self._match(item, query)])

    def update_one(self, query, update, upsert=False):
        for item in self.docs:
            if self._match(item, query):
                item.update(update.get("$set", {}))
                return
        if upsert:
            self.docs.append({**query, **update.get("$set", {}), **update.get("$setOnInsert", {})})

    def update_many(self, query, update):
        for item in self.docs:
            if self._match(item, query):
                item.update(update.get("$set", {}))


def _candidate(*, risk=False):
    signal = {
        "code": "stop_loss_broken" if risk else "support_reclaimed",
        "title": "止损触发" if risk else "支撑回收",
        "detail": "跌破冻结止损" if risk else "重新站回支撑",
        "action": "先处理风险" if risk else "等待确认",
    }
    return {
        "ticker": "603986.SH",
        "name": "兆易创新",
        "industry": "半导体",
        "realtime": {"current": 550 if risk else 620, "pct_chg": -0.08 if risk else 0.01, "quote_at": "2026-07-13T14:00:00+08:00", "source": "klineshare"},
        "primary_event": signal,
        "signal_events": [signal],
    }


def test_baseline_is_stored_but_only_semantic_change_enters_outbox():
    events = _Collection()
    outbox = _Collection()
    gate = {"state": "hold", "state_label": "持有"}

    first = record_candidate_decisions(
        events, outbox, [_candidate()], session_date="2026-07-13",
        computed_at="2026-07-13T14:00:00+08:00", market_gate=gate,
        push_tickers={"603986.SH"},
    )
    duplicate = record_candidate_decisions(
        events, outbox, [_candidate()], session_date="2026-07-13",
        computed_at="2026-07-13T14:01:00+08:00", market_gate=gate,
        push_tickers={"603986.SH"},
    )
    changed = record_candidate_decisions(
        events, outbox, [_candidate(risk=True)], session_date="2026-07-13",
        computed_at="2026-07-13T14:02:00+08:00", market_gate=gate,
        push_tickers={"603986.SH"},
    )

    assert len(first) == 1
    assert duplicate == []
    assert len(changed) == 1
    assert len(events.docs) == 2
    assert outbox.docs == []
    assert changed[0]["current"]["action_type"] == "manual_verify_risk"
    assert changed[0]["current"]["position_state"] == "flat"
    assert "持有" not in changed[0]["current"]["action_label"]


def test_dispatch_marks_outbox_sent_with_card_payload():
    events = _Collection()
    outbox = _Collection()
    gate = {"state": "hold", "state_label": "持有"}
    record_candidate_decisions(events, outbox, [_candidate()], session_date="2026-07-13", computed_at="2026-07-13T14:00:00+08:00", market_gate=gate, push_tickers={"603986.SH"})
    record_candidate_decisions(events, outbox, [_candidate(risk=True)], session_date="2026-07-13", computed_at="2026-07-13T14:02:00+08:00", market_gate=gate, push_tickers={"603986.SH"})

    class _Client:
        def __init__(self):
            self.cards = []

        def send_card(self, card):
            self.cards.append(card)
            return {"code": 0}

    client = _Client()
    result = dispatch_feishu_outbox(outbox, client, now=datetime(2026, 7, 13, 14, 3, tzinfo=TZ))

    assert result == {"selected": 0, "sent": 0, "failed": 0, "expired": 0}
    assert outbox.docs == []
    assert client.cards == []


def test_dispatch_expires_stale_backlog_and_caps_one_message_per_ticker():
    outbox = _Collection()
    outbox.docs = [
        {
            "outbox_id": "stale",
            "ticker": "600000.SH",
            "channel": "feishu",
            "status": "pending",
            "next_retry_at": "2026-07-13T14:00:00+08:00",
            "created_at": "2026-07-13T13:00:00+08:00",
            "event": {"ticker": "600000.SH", "current": {"severity": "high"}},
        },
        *[
            {
                "outbox_id": f"fresh-{index}",
                "ticker": ticker,
                "channel": "feishu",
                "status": "pending",
                "next_retry_at": "2026-07-13T14:02:00+08:00",
                "created_at": f"2026-07-13T14:02:0{index}+08:00",
                "event": {
                    "ticker": ticker,
                    "name": ticker,
                    "priority": "P0" if index == 0 else "P2",
                    "computed_at": "2026-07-13T14:02:00+08:00",
                    "current": {"severity": "high" if index == 0 else "medium", "action_label": "执行", "action_type": "prepare_entry"},
                },
            }
            for index, ticker in enumerate(["000001.SZ", "000001.SZ", "000002.SZ", "000003.SZ", "000004.SZ"])
        ],
    ]

    class _Client:
        def __init__(self):
            self.cards = []

        def send_card(self, card):
            self.cards.append(card)
            return {"code": 0}

    client = _Client()
    result = dispatch_feishu_outbox(
        outbox,
        client,
        now=datetime(2026, 7, 13, 14, 3, tzinfo=TZ),
        limit=3,
        max_age_minutes=30,
    )

    assert result == {"selected": 3, "sent": 3, "failed": 0, "expired": 1}
    assert len(client.cards) == 3
    assert next(item for item in outbox.docs if item["outbox_id"] == "stale")["status"] == "expired"


def test_feishu_card_is_compact():
    card = feishu_card({
        "ticker": "000001.SZ",
        "name": "平安银行",
        "computed_at": "2026-07-13T14:02:00+08:00",
        "previous": {"action_label": "观察"},
        "current": {
            "action_type": "prepare_entry",
            "action_label": "准备建仓",
            "severity": "medium",
            "current_price": 10.5,
            "pct_chg": 0.01,
        },
        "reasons": ["价格与量能确认"],
    })

    assert len(card["elements"]) == 3
    assert card["header"]["title"]["content"].startswith("机会确认")


def test_replace_watchlist_disables_removed_tickers():
    collection = _Collection()
    replace_watchlist(collection, [{"ticker": "603986.SH"}, {"ticker": "600584.SH"}])
    replace_watchlist(collection, [{"ticker": "600584.SH"}])
    by_ticker = {item["ticker"]: item for item in collection.docs}
    assert by_ticker["603986.SH"]["enabled"] is False
    assert by_ticker["600584.SH"]["enabled"] is True
