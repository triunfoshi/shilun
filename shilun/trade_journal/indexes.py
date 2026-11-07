"""Mongo constraints shared by setup, API writes and offline journal imports."""
from threading import RLock

JOURNAL_INDEX_SPECS = [
    ("trade_journal_sources", [("source_id", 1)], "uniq_journal_source", True),
    ("trade_journal_batches", [("batch_id", 1)], "uniq_journal_batch", True),
    ("trade_journal_fills", [("fill_id", 1)], "uniq_journal_fill", True),
    ("trade_journal_financial_events", [("financial_event_id", 1)], "uniq_journal_financial", True),
    ("trade_journal_decisions", [("decision_id", 1), ("revision", 1)], "uniq_journal_decision_revision", True),
    ("trade_journal_links", [("link_id", 1)], "uniq_journal_link", True),
    ("trade_journal_expectation_checks", [("check_id", 1)], "uniq_journal_check", True),
    ("trade_journal_observations", [("observation_id", 1)], "uniq_journal_observation", True),
    ("trade_journal_snapshots", [("snapshot_id", 1)], "uniq_journal_snapshot", True),
    ("trade_journal_mode_cards", [("mode_id", 1), ("revision", 1)], "uniq_journal_mode_revision", True),
    ("trade_journal_hypotheses", [("hypothesis_id", 1), ("revision", 1)], "uniq_journal_hypothesis_revision", True),
    ("trade_journal_allocations", [("episode_id", 1), ("revision", 1)], "uniq_journal_allocation_revision", True),
    ("trade_journal_allocations", [("allocation_id", 1)], "uniq_journal_allocation", True),
    ("trade_journal_portfolio_snapshots", [("snapshot_id", 1)], "uniq_journal_portfolio_snapshot", True),
    ("trade_journal_portfolio_snapshots", [("captured_at", -1)], "idx_journal_portfolio_time", False),
    ("trade_journal_task_events", [("task_id", 1), ("revision", 1)], "uniq_journal_task_revision", True),
    ("trade_journal_task_events", [("event_id", 1)], "uniq_journal_task_event", True),
    ("trade_journal_execution_matches", [("execution_id", 1), ("revision", 1)], "uniq_journal_execution_match_revision", True),
    ("trade_journal_reviews", [("episode_id", 1), ("revision", 1)], "uniq_journal_process_review_revision", True),
    ("trade_journal_reviews", [("review_id", 1), ("revision", 1)], "uniq_journal_process_review_identity", True),
    ("trade_journal_fills", [("ticker", 1), ("date", 1)], "idx_journal_security_date", False),
    ("trade_journal_decisions", [("episode_id", 1), ("recorded_at", -1)], "idx_journal_episode_decisions", False),
    ("trade_journal_observations", [("recorded_at", -1)], "idx_journal_observation_time", False),
]

_READY = set()
_LOCK = RLock()


def ensure_journal_indexes(store):
    """Only the journal's indexes; successful setup is cached per database peer."""
    db = getattr(store, "db", None)
    client = getattr(store, "client", None)
    namespace = (getattr(client, "address", None), getattr(db, "name", None)) if db is not None else None
    with _LOCK:
        if namespace and namespace in _READY:
            return
        for collection, keys, name, unique in JOURNAL_INDEX_SPECS:
            store.collection(collection).create_index(keys, name=name, unique=unique)
        store.collection("trade_journal_fills").create_index(
            [("account_alias", 1), ("date", 1), ("ticker", 1), ("broker_execution_id", 1)],
            name="uniq_journal_broker_execution", unique=True,
            partialFilterExpression={"broker_execution_id": {"$type": "string", "$gt": ""}},
        )
        if namespace:
            _READY.add(namespace)
