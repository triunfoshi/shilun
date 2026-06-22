import tempfile
import unittest

from shilun.common.config import AppConfig
from shilun.jobs.candidate_pool_job import (
    CandidatePoolJob,
    CandidatePoolRequest,
    build_candidate_pool_states,
    resolve_event_type,
)


class FakeMongoStore:
    def __init__(self, records: list[dict], previous_states: dict[str, dict] | None = None) -> None:
        self.records = records
        self.previous_states = previous_states or {}
        self.upserted_states: list[dict] = []
        self.upserted_events: list[dict] = []

    def find_market_snapshot_records(self, *, analysis_date: str, exclude_st: bool, limit: int | None = None) -> list[dict]:
        matched = [
            dict(record)
            for record in self.records
            if record.get("analysis_date") == analysis_date and bool(record.get("exclude_st")) == bool(exclude_st)
        ]
        matched.sort(key=lambda record: int(record.get("rank", 0) or 0))
        return matched[:limit] if limit is not None else matched

    def find_latest_candidate_pool_states_before(self, *, analysis_date: str, exclude_st: bool, tickers: list[str] | None = None) -> dict[str, dict]:
        if tickers is None:
            return dict(self.previous_states)
        return {ticker: state for ticker, state in self.previous_states.items() if ticker in tickers}

    def upsert_candidate_pool_states(self, *, analysis_date: str, exclude_st: bool, states: list[dict]) -> int:
        self.upserted_states = list(states)
        return len(states)

    def upsert_candidate_pool_events(self, *, analysis_date: str, exclude_st: bool, events: list[dict]) -> int:
        self.upserted_events = list(events)
        return len(events)

    def close(self) -> None:
        return None


class CandidatePoolJobTests(unittest.TestCase):
    def test_builds_states_and_transition_events_from_mongo_records(self) -> None:
        records = sample_records()
        previous_states = {
            "000001.SZ": {"pool_status": "watch_pool", "days_in_pool": 2, "entered_at": "2026-05-14"},
            "000002.SZ": {"pool_status": "watch_pool", "days_in_pool": 1, "entered_at": "2026-05-15"},
            "000003.SZ": {"pool_status": "candidate_pool", "days_in_pool": 3, "entered_at": "2026-05-13"},
        }

        states, events = build_candidate_pool_states(
            records=records,
            analysis_date="2026-05-16",
            exclude_st=True,
            previous_states=previous_states,
        )

        by_ticker = {state["ticker"]: state for state in states}
        self.assertEqual("buy_pool", by_ticker["000001.SZ"]["pool_status"])
        self.assertEqual("promoted", by_ticker["000001.SZ"]["event_type"])
        self.assertEqual("watch_pool", by_ticker["000002.SZ"]["pool_status"])
        self.assertEqual("unchanged", by_ticker["000002.SZ"]["event_type"])
        self.assertEqual(2, by_ticker["000002.SZ"]["days_in_pool"])
        self.assertEqual("reject_pool", by_ticker["000003.SZ"]["pool_status"])
        self.assertEqual("rejected", by_ticker["000003.SZ"]["event_type"])
        self.assertEqual(["000001.SZ", "000003.SZ"], sorted(event["ticker"] for event in events))

    def test_job_persists_states_events_and_outputs_report(self) -> None:
        store = FakeMongoStore(sample_records(), {"000001.SZ": {"pool_status": "watch_pool", "days_in_pool": 2}})
        with tempfile.TemporaryDirectory() as tmp_dir:
            job = CandidatePoolJob(config=AppConfig(), mongo_store=store)
            result = job.run(
                CandidatePoolRequest(
                    target_date="2026-05-16",
                    exclude_st=True,
                    output_dir=tmp_dir,
                )
            )

            self.assertTrue(result.report_path.exists())
            self.assertTrue(result.states_csv_path.exists())

        self.assertEqual("2026-05-16", result.analysis_date)
        self.assertEqual(3, result.state_count)
        self.assertGreaterEqual(result.event_count, 1)
        self.assertEqual(3, len(store.upserted_states))
        self.assertTrue(any(event["event_type"] == "promoted" for event in store.upserted_events))

    def test_resolve_event_type(self) -> None:
        self.assertEqual("entered", resolve_event_type(None, "candidate_pool"))
        self.assertEqual("unchanged", resolve_event_type("watch_pool", "watch_pool"))
        self.assertEqual("promoted", resolve_event_type("candidate_pool", "buy_pool"))
        self.assertEqual("demoted", resolve_event_type("buy_pool", "watch_pool"))
        self.assertEqual("rejected", resolve_event_type("watch_pool", "reject_pool"))
        self.assertEqual("reentered", resolve_event_type("reject_pool", "watch_pool"))


def sample_records() -> list[dict]:
    return [
        {
            "analysis_date": "2026-05-16",
            "exclude_st": True,
            "rank": 1,
            "ticker": "000001.SZ",
            "name": "强势一号",
            "industry": "软件",
            "action_label": "build",
            "target_position_pct": 50,
            "execution_score": 45.0,
            "risk_score": 22,
            "entry_probability": 0.72,
            "p_fail_fast_3d": 0.18,
            "p_continue_10d": 0.64,
            "candidate_tag_count": 2,
            "candidate_tags": "rps_breakout,turtle_breakout",
            "strategy_signal_count": 3,
            "strategy_ids": "shilun_v1,shilun_v1_rps_breakout_test",
        },
        {
            "analysis_date": "2026-05-16",
            "exclude_st": True,
            "rank": 2,
            "ticker": "000002.SZ",
            "name": "观察二号",
            "industry": "电子",
            "action_label": "watch",
            "target_position_pct": 10,
            "execution_score": 28.0,
            "risk_score": 35,
            "entry_probability": 0.51,
            "p_fail_fast_3d": 0.22,
            "p_continue_10d": 0.49,
            "candidate_tag_count": 1,
            "candidate_tags": "high_tight_flag",
            "strategy_signal_count": 2,
            "strategy_ids": "shilun_v1,shilun_v1_high_tight_test",
        },
        {
            "analysis_date": "2026-05-16",
            "exclude_st": True,
            "rank": 3,
            "ticker": "000003.SZ",
            "name": "淘汰三号",
            "industry": "传媒",
            "action_label": "stand_aside",
            "target_position_pct": 0,
            "execution_score": 10.0,
            "risk_score": 82,
            "entry_probability": 0.31,
            "p_fail_fast_3d": 0.5,
            "p_continue_10d": 0.2,
            "candidate_tag_count": 0,
            "strategy_signal_count": 1,
        },
    ]


if __name__ == "__main__":
    unittest.main()
