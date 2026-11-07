from __future__ import annotations

import unittest
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from shilun.trading.contracts import (
    ActionIntent,
    DataFreshnessReport,
    DataState,
    DecisionResult,
    MatchLevel,
    ScenarioCandidate,
    ScenarioState,
    TradingAction,
)
from shilun.trading.timeline import list_decision_events, persist_decision_if_changed


TZ = ZoneInfo("Asia/Shanghai")


class _Cursor:
    def __init__(self, docs):
        self.docs = list(docs)

    def sort(self, key, direction=1):
        self.docs.sort(key=lambda item: item.get(key) or "", reverse=direction < 0)
        return self

    def limit(self, limit):
        self.docs = self.docs[:limit]
        return self

    def __iter__(self):
        return iter([dict(item) for item in self.docs])


class _Collection:
    def __init__(self):
        self.docs = []

    @staticmethod
    def _match(doc, query):
        return all(doc.get(key) == expected for key, expected in query.items())

    def find_one(self, query, projection=None, sort=None):
        docs = [item for item in self.docs if self._match(item, query)]
        if sort:
            key, direction = sort[0]
            docs.sort(key=lambda item: item.get(key) or "", reverse=direction < 0)
        return dict(docs[0]) if docs else None

    def find(self, query, projection=None):
        return _Cursor([item for item in self.docs if self._match(item, query)])

    def update_one(self, query, update, upsert=False):
        if any(self._match(item, query) for item in self.docs):
            return
        if upsert:
            self.docs.append({**query, **update.get("$setOnInsert", {})})


def _decision(at: datetime, *, action_type: str = "hold", scenario_id: str = "base_hold") -> DecisionResult:
    scenario = ScenarioCandidate(
        scenario_id=scenario_id,
        title=scenario_id,
        priority=1,
        state=ScenarioState.TRIGGERED,
        match_level=MatchLevel.HIGH,
        traces=(),
    )
    return DecisionResult(
        ticker="603986.SH",
        computed_at=at,
        data_freshness=DataFreshnessReport(DataState.NORMAL, True, (), ()),
        action=TradingAction(action_type, action_type, 100, 0),
        action_intent=ActionIntent.ACTIVE,
        reasons=("rule",),
        current_scenario=scenario,
    )


class TradingTimelineTests(unittest.TestCase):
    def test_same_semantic_decision_does_not_create_price_tick_noise(self) -> None:
        collection = _Collection()
        now = datetime(2026, 7, 13, 10, 17, tzinfo=TZ)

        first, _ = persist_decision_if_changed(
            collection,
            _decision(now),
            playbook_id="ma5",
            playbook_version="0.2",
            facts_snapshot={"current_price": 100},
        )
        second, stored = persist_decision_if_changed(
            collection,
            _decision(now + timedelta(minutes=1)),
            playbook_id="ma5",
            playbook_version="0.2",
            facts_snapshot={"current_price": 100.2},
        )

        self.assertTrue(first)
        self.assertFalse(second)
        self.assertEqual(len(collection.docs), 1)
        self.assertEqual(stored["facts_snapshot"]["current_price"], 100)

    def test_action_change_creates_event_with_previous_summary(self) -> None:
        collection = _Collection()
        now = datetime(2026, 7, 13, 10, 17, tzinfo=TZ)
        persist_decision_if_changed(collection, _decision(now), playbook_id="ma5", playbook_version="0.2")
        created, event = persist_decision_if_changed(
            collection,
            _decision(now + timedelta(minutes=5), action_type="prepare_reduce", scenario_id="ma5_break"),
            playbook_id="ma5",
            playbook_version="0.2",
        )

        self.assertTrue(created)
        self.assertEqual(event["previous"]["action_type"], "hold")
        self.assertEqual(event["current"]["scenario_id"], "ma5_break")
        events = list_decision_events(collection, "603986.SH", limit=10)
        self.assertEqual([item["current"]["action_type"] for item in events], ["prepare_reduce", "hold"])

    def test_event_namespace_separates_trade_date_and_plan(self) -> None:
        collection = _Collection()
        now = datetime(2026, 7, 13, 10, 17, tzinfo=TZ)
        _, first = persist_decision_if_changed(
            collection,
            _decision(now),
            playbook_id="ma5",
            playbook_version="0.2",
            trade_date="20260713",
            plan_id="plan-20260713",
            plan_revision=1,
        )
        _, second = persist_decision_if_changed(
            collection,
            _decision(now + timedelta(days=1)),
            playbook_id="ma5",
            playbook_version="0.2",
            trade_date="20260714",
            plan_id="plan-20260714",
            plan_revision=1,
        )

        self.assertIsNone(first["previous"])
        self.assertIsNone(second["previous"])
        self.assertEqual(second["trade_date"], "20260714")
        self.assertEqual(second["plan_id"], "plan-20260714")
        filtered = list_decision_events(
            collection,
            "603986.SH",
            trade_date="20260714",
            plan_id="plan-20260714",
        )
        self.assertEqual(len(filtered), 1)

    def test_position_revision_creates_semantic_event(self) -> None:
        collection = _Collection()
        now = datetime(2026, 7, 13, 10, 17, tzinfo=TZ)
        persist_decision_if_changed(
            collection,
            _decision(now),
            playbook_id="ma5",
            playbook_version="0.2",
            trade_date="20260713",
            plan_id="plan-1",
            plan_revision=1,
            position_revision=1,
        )
        created, event = persist_decision_if_changed(
            collection,
            _decision(now + timedelta(minutes=1)),
            playbook_id="ma5",
            playbook_version="0.2",
            trade_date="20260713",
            plan_id="plan-1",
            plan_revision=1,
            position_revision=2,
        )

        self.assertTrue(created)
        self.assertEqual(event["position_revision"], 2)
        self.assertEqual(len(collection.docs), 2)

        with self.assertRaisesRegex(ValueError, "position_revision 不能倒退"):
            persist_decision_if_changed(
                collection,
                _decision(now + timedelta(minutes=2)),
                playbook_id="ma5",
                playbook_version="0.2",
                trade_date="20260713",
                plan_id="plan-1",
                plan_revision=1,
                position_revision=1,
            )

    def test_legacy_and_canonical_action_do_not_duplicate_event(self) -> None:
        collection = _Collection()
        now = datetime(2026, 7, 13, 10, 17, tzinfo=TZ)
        persist_decision_if_changed(
            collection,
            _decision(now, action_type="risk_reduce"),
            playbook_id="ma5",
            playbook_version="0.2",
        )
        created, event = persist_decision_if_changed(
            collection,
            _decision(now + timedelta(minutes=1), action_type="prepare_reduce"),
            playbook_id="ma5",
            playbook_version="0.2",
        )

        self.assertFalse(created)
        self.assertEqual(event["current"]["canonical_action_type"], "prepare_reduce")
        self.assertEqual(len(collection.docs), 1)


if __name__ == "__main__":
    unittest.main()
