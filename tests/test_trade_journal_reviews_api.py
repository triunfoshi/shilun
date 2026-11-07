"""Real review/task domain exercised through the configured HTTP adapter."""
from copy import deepcopy

import pytest

from test_trade_journal_api import setup, import_rows

BASE = "/api/v1/trade-journal"


def episode(client, payload):
    import_rows(client, payload)
    return client.get(BASE+"/episodes").json()["items"][0]["episode_id"]


def save_body(current, **changes):
    return {"state": "confirmed", "summary": "用户已实际保存过程复盘；无法还原部分保持未知",
        "expected_revision": current["latest_revision"], "expected_fact_digest": current["fact_digest"],
        "revision_reason": "独立核对本次原文", **changes}


def test_http_empty_draft_confirmed_and_exact_old_version_preserve_real_facts(setup):
    client, store, payload, _ = setup
    eid = episode(client, payload); path = BASE+"/episodes/"+eid+"/review"
    summary = client.get(BASE+"/summary").json()
    empty = client.get(path)
    assert empty.status_code == 200 and empty.json()["record"] is None
    assert empty.json()["current_state"] == "missing" and empty.json()["latest_revision"] == 0
    draft = client.post(path, json=save_body(empty.json(), state="draft", summary=""))
    assert draft.status_code == 200, draft.text
    assert draft.json()["record"]["state"] == "draft" and draft.json()["record"]["revision"] == 1
    current = client.get(path).json()
    confirmed = client.post(path, json=save_body(current, captured_at="2000-01-01T00:00:00+08:00",
        judgments={"entry": {"judgment": "deviation", "reason": "用户明确：盈利也不代表遵守原确认"}}))
    assert confirmed.status_code == 200, confirmed.text
    record = confirmed.json()["record"]
    assert record["state"] == "confirmed" and record["revision"] == 2
    assert not record["captured_at"].startswith("2000")
    assert record["judgments"]["entry"]["judgment"] == "deviation"
    assert record["judgments"]["exit"]["judgment"] == "unknown" and not record["judgments"]["exit"]["user_supplied"]
    old = client.get(path, params={"revision": 1})
    assert old.status_code == 200 and old.json()["record"]["state"] == "draft"
    history = client.get(path+"/history")
    assert history.status_code == 200 and history.json()["total"] == 2
    assert client.get(BASE+"/summary").json() == summary
    assert all(name.startswith("trade_journal_") for name, _, _ in store.writes)


def test_http_stale_revision_and_fact_digest_are_separate_conflicts(setup):
    client, _, payload, _ = setup
    eid = episode(client, payload); path = BASE+"/episodes/"+eid+"/review"
    empty = client.get(path).json()
    first = client.post(path, json=save_body(empty))
    assert first.status_code == 200, first.text
    assert client.post(path, json=save_body(empty)).status_code == 409
    old = client.get(path).json()
    decision = client.post(BASE+"/episodes/"+eid+"/decisions", json={"action": "补记原意", "reason": "用户找到新原话",
        "observed_at": "2026-10-19T10:00:00+08:00", "evidence_class": "retrospective_recall"})
    assert decision.status_code == 200, decision.text
    current = client.get(path).json()
    assert current["current_state"] == "stale" and not current["current_confirmed"]
    stale = client.post(path, json=save_body(old))
    assert stale.status_code == 409, stale.text
    assert client.get(path+"/history").json()["total"] == 1
    revised = client.post(path, json=save_body(current))
    assert revised.status_code == 200 and revised.json()["review"]["current_confirmed"]
    assert revised.json()["record"]["decision_refs"][0]["revision"] == 1


@pytest.mark.parametrize("operation", ["get", "history", "save"])
def test_http_unknown_episode_is_not_an_empty_existing_review(setup, operation):
    client, _, _, _ = setup
    path = BASE+"/episodes/ep-missing/review"
    if operation == "get": response = client.get(path)
    elif operation == "history": response = client.get(path+"/history")
    else: response = client.post(path, json={"state": "draft", "expected_revision": 0, "expected_fact_digest": "missing"})
    assert response.status_code == 404, response.text


@pytest.mark.parametrize("revision", [0, -1, "not-integer"])
def test_http_invalid_review_revision_is_validation_error(setup, revision):
    client, _, payload, _ = setup
    eid = episode(client, payload)
    result = client.get(BASE+"/episodes/"+eid+"/review", params={"revision": revision})
    assert result.status_code == 422, result.text


def test_http_positive_missing_exact_version_is_not_silently_latest(setup):
    client, _, payload, _ = setup
    eid = episode(client, payload); path = BASE+"/episodes/"+eid+"/review"
    current = client.get(path).json()
    assert client.post(path, json=save_body(current)).status_code == 200
    missing = client.get(path, params={"revision": 999})
    assert missing.status_code == 404, missing.text


def test_http_unknown_source_is_not_a_recoverable_review_reference(setup):
    client, store, payload, _ = setup
    eid = episode(client, payload); path = BASE+"/episodes/"+eid+"/review"
    current = client.get(path).json()
    response = client.post(path, json=save_body(current,
        evidence_refs=[{"text": "声称有原件", "source_ref": {"source_id": "not-archived", "row": 1}}]))
    assert response.status_code == 422, response.text
    assert client.get(path+"/history").json()["total"] == 0
    assert not store.collection("trade_journal_reviews").documents


def test_confirm_unknown_task_does_not_create_or_confirm_a_process_review(setup):
    client, _, payload, _ = setup
    eid = episode(client, payload); path = BASE+"/episodes/"+eid+"/review"
    tasks = client.get(BASE+"/tasks", params={"episode_id": eid}).json()
    task = next(row for row in tasks["items"] if row["root_cause"] == "process_review")
    result = client.post(BASE+"/tasks/"+task["task_id"]+"/resolutions", json={"expected_revision": task["revision"],
        "expected_fingerprint": task["fact_fingerprint"], "action": "confirm_unknown", "reason": "当时过程无法回忆，确认缺失"})
    assert result.status_code == 200, result.text
    assert result.json()["task"]["state"] == "confirmed_unknown"
    current = client.get(path).json()
    assert current["record"] is None and not current["current_confirmed"]
    assert client.get(path+"/history").json()["total"] == 0
    saved = client.post(path, json=save_body(current))
    assert saved.status_code == 200, saved.text
    refreshed = client.get(BASE+"/tasks/"+task["task_id"]).json()["task"]
    assert refreshed["state"] == "resolved"
