"""The review-context route reads the configured journal store and closes it."""
from test_trade_journal_api import setup, import_rows


def test_review_context_route_is_read_only_and_missing_episode_is_404(setup):
    client, store, payload, indexes = setup
    import_rows(client, payload)
    episode_id = client.get("/api/v1/trade-journal/episodes").json()["items"][0]["episode_id"]
    writes, indexes_count, closes = list(store.writes), len(indexes), store.close_count
    response = client.get(f"/api/v1/trade-journal/episodes/{episode_id}/review-context")
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["episode_id"] == episode_id and data["read_only"]
    assert data["buy_fills"][0]["fill_id"] == "buy"
    assert data["buy_fills"][0]["next_trading_day"] is None
    assert data["freshness"]["coverage_status"] == "unconfirmed"
    assert store.writes == writes and len(indexes) == indexes_count
    assert store.close_count == closes + 1
    assert client.get("/api/v1/trade-journal/episodes/missing/review-context").status_code == 404
