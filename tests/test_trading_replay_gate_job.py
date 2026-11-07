from __future__ import annotations

import unittest

from shilun.jobs.trading_replay_gate_job import TradingReplayGateJob, collect_replay_inventory


class _Collection:
    def __init__(self, docs=None):
        self.docs = list(docs or [])
        self.updated = []

    def find(self, *_args, **_kwargs):
        return list(self.docs)

    def update_one(self, query, update, upsert=False):
        self.updated.append((query, update, upsert))


class _Store:
    def __init__(self, collections):
        self.collections = collections

    def collection(self, name):
        return self.collections.setdefault(name, _Collection())


def _store() -> _Store:
    return _Store({
        "minute_bars_daily": _Collection([
            {"ticker": "000001.SH", "trade_date": "20260710", "interval": "5m", "bars": []},
            {"ticker": "399006.SZ", "trade_date": "20260710", "interval": "5m", "bars": []},
            {"ticker": "000001.SZ", "trade_date": "20260710", "interval": "5m", "bars": []},
        ]),
        "execution_market_facts": _Collection(),
        "trading_watch_plans": _Collection(),
        "trading_decision_events": _Collection(),
        "execution_scenario_replays": _Collection(),
        "trading_replay_gate_reports": _Collection(),
    })


class TradingReplayGateJobTests(unittest.TestCase):
    def test_inventory_distinguishes_raw_minutes_from_full_fact_sessions(self) -> None:
        inventory = collect_replay_inventory(_store())

        self.assertEqual(inventory["minute_bar_document_count"], 3)
        self.assertEqual(inventory["paired_stock_session_count"], 1)
        self.assertEqual(inventory["full_fact_session_count"], 0)
        self.assertIn("缺少 execution_market_facts 历史板块事实", inventory["missing_requirements"])

    def test_job_persists_a_stable_closed_gate(self) -> None:
        store = _store()
        report = TradingReplayGateJob(store).run(persist=True)

        self.assertFalse(report["ready_for_launch"])
        self.assertEqual(report["decision"], "hold_launch")
        self.assertTrue(report["persisted"])
        self.assertTrue(report["report_id"].startswith("job12h:"))
        updates = store.collection("trading_replay_gate_reports").updated
        self.assertEqual(len(updates), 1)
        self.assertTrue(updates[0][2])


if __name__ == "__main__":
    unittest.main()
