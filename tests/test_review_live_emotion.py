"""Live board-emotion adapter tests; no network or Mongo access."""

from datetime import datetime
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from shilun.api import review
from shilun.review import live_emotion as subject


def _reset_cache():
    subject._cache.clear()


def test_dt_request_uses_fund_sort_and_validates_count_date_and_codes():
    session = MagicMock()
    session.__enter__.return_value = session
    response = session.get.return_value
    row = {"c":"600706", "n":"曲江文旅", "zdp":-9.98937}
    valid = {"rc":0,"data":{"qdate":20260914,"tc":1,"pool":[row]}}
    with patch.object(subject.requests,"Session",return_value=session):
        response.json.return_value=valid
        assert subject._fetch_pool("getTopicDTPool","20260914")==[row]
        assert session.get.call_args.kwargs["params"]["sort"]=="fund:asc"
        for malformed in [
            {"rc":0,"data":{"qdate":20260914,"tc":16,"pool":[]}},
            {"rc":0,"data":{"qdate":20260911,"tc":0,"pool":[]}},
            {"rc":1,"data":{"qdate":20260914,"tc":0,"pool":[]}},
            {"rc":0,"data":{"qdate":20260914,"tc":2,"pool":[row,row]}},
        ]:
            response.json.return_value=malformed
            assert subject._fetch_pool("getTopicDTPool","20260914") is None
        response.json.return_value={"rc":0,"data":{"qdate":20260914,"tc":0,"pool":[]}}
        assert subject._fetch_pool("getTopicDTPool","20260914")==[]


def test_original_scope_keeps_all_raw_pool_members_and_promotion_denominator():
    _reset_cache()
    now=datetime(2026,9,14,10,8,tzinfo=subject.CHINA)
    pools={
        ("getTopicZTPool","20260914"):[{"c":"000001","lbc":2},{"c":"300001","lbc":6},{"c":"688001","lbc":8},{"c":"600001","n":"ST甲","lbc":9}],
        ("getTopicZBPool","20260914"):[{"c":"000002"},{"c":"300002"}],
        ("getTopicDTPool","20260914"):[{"c":"000003","zdp":-9.98},{"c":"688002","zdp":-20}],
        ("getTopicZTPool","20260911"):[{"c":"000001"},{"c":"300001"},{"c":"000004"}],
    }
    with patch.object(subject,"_now",return_value=now),patch.object(subject,"_fetch_pool",side_effect=lambda e,d:pools.get((e,d))):
        result=subject.snapshot()
    assert result["zt_count"]==4 and result["dt_count"]==2 and result["zb_count"]==2
    assert result["seal_rate"]==.6667 and result["max_boards"]==9
    assert result["promotion_base"]==3 and result["promotion_rate"]==.6667
    assert result["scope"]=="eastmoney_raw_pool"
    assert result["raw_pool_counts"]["zt_count"]==4
    assert result["down_stocks"][0]["pct"]==-9.98  # Rounded price does not imply no limit-down.


def test_explicit_refresh_invalidates_settled_cache():
    _reset_cache()
    key="getTopicZTPool:20260914"
    subject._cache[key]=(subject.monotonic_time.monotonic(),[{"c":"000001"}])
    with patch.object(subject,"_now",return_value=datetime(2026,9,14,16,tzinfo=subject.CHINA)), patch.object(subject,"_fetch_pool",return_value=[{"c":"000002"}]) as fetch:
        result=subject.snapshot(force=True)
    assert fetch.called
    assert subject._cache[key][1]==[{"c":"000002"}]


def test_actual_september_14_scope_and_limit_rounding():
    _reset_cache()
    fixture=json.loads((Path(__file__).parent/"fixtures/live_emotion_20260914.json").read_text())
    mapping={"getTopicZTPool":"up","getTopicZBPool":"broken","getTopicDTPool":"down", "getYesterdayZTPool":"previous"}
    def fetch(endpoint, day):
        return fixture[mapping[endpoint]]["data"]["pool"] if day=="20260914" else None
    with patch.object(subject,"_now",return_value=datetime(2026,9,14,16,tzinfo=subject.CHINA)),patch.object(subject,"_fetch_pool",side_effect=fetch):
        result=subject.snapshot()
    assert result["zt_count"]==55
    assert result["raw_pool_counts"]["zt_count"]==55
    assert result["dt_count"]==16
    assert result["zb_count"]==30 and result["seal_rate"]==.6471
    assert result["promotion_base"]==41 and result["promotion_rate"]==.2683
    assert result["promotion_source"]=="current_day_yesterday_pool"
    assert result["promotion_base_date"] is None  # The endpoint identifies yesterday, not a dated calendar entry.
    assert sum(r["pct"]<=-10 for r in result["down_stocks"])==9
    assert len([r for r in result["down_stocks"] if r["pct"]>-10])==7


def test_live_snapshot_matches_vibe_metrics_and_keeps_empty_dt_as_zero():
    _reset_cache()
    now = datetime(2026, 9, 14, 10, 8, tzinfo=subject.CHINA)  # Monday
    pools = {
        ("getTopicZTPool", "20260914"): [{"c": "000001", "lbc": 1}, {"c": "000002", "lbc": 3}, {"c": "000003", "lbc": 2}],
        ("getTopicZBPool", "20260914"): [{"c": "000004"}],
        ("getTopicDTPool", "20260914"): [],
        ("getTopicZTPool", "20260911"): [{"c": "000001"}, {"c": "000009"}],
    }

    def fake_fetch(endpoint, ymd):
        return pools.get((endpoint, ymd))

    with patch.object(subject, "_now", return_value=now), patch.object(subject, "_fetch_pool", side_effect=fake_fetch):
        result = subject.snapshot()

    assert result["available"]
    assert result["zt_count"] == 3 and result["zb_count"] == 1 and result["dt_count"] == 0
    assert result["max_boards"] == 3 and result["lianban_count"] == 2
    assert result["seal_rate"] == 0.75 and result["break_rate"] == 0.25
    assert result["promotion_rate"] == 0.5 and result["promotion_base"] == 2
    assert result["promotion_base_date"] == "2026-09-11"
    assert result["data_quality"]["跌停池"] == "ok"


def test_live_snapshot_never_turns_failed_zt_pool_into_zero():
    _reset_cache()
    now = datetime(2026, 9, 14, 10, 8, tzinfo=subject.CHINA)
    with patch.object(subject, "_now", return_value=now), patch.object(subject, "_fetch_pool", return_value=None):
        result = subject.snapshot()
    assert result["available"] is False
    assert "zt_count" not in result and "零涨停" in result["reason"]
    assert result["data_quality"]["涨停池"] == "unavailable"


def test_live_snapshot_uses_latest_settled_session_outside_continuous_session():
    _reset_cache()
    now = datetime(2026, 9, 13, 10, 8, tzinfo=subject.CHINA)  # Sunday
    pools = {
        ("getTopicZTPool", "20260911"): [{"c": "000001", "lbc": 2}],
        ("getTopicZBPool", "20260911"): [],
        ("getTopicDTPool", "20260911"): [{"c": "000002"}],
        ("getTopicZTPool", "20260910"): [{"c": "000003"}],
    }

    def fake_fetch(endpoint, ymd):
        return pools.get((endpoint, ymd))

    with patch.object(subject, "_now", return_value=now), patch.object(subject, "_fetch_pool", side_effect=fake_fetch):
        result = subject.snapshot()
    assert result["available"] is True and result["snapshot_mode"] == "settled"
    assert result["snapshot_date"] == "2026-09-11"
    assert result["zt_count"] == 1 and result["dt_count"] == 1
    assert result["phase_key"] == "non_trading_settled"


def test_live_emotion_route_does_not_open_database():
    with patch.object(review, "live_emotion_snapshot", return_value={"available": False}) as snapshot:
        assert review.get_live_emotion() == {"available": False}
        snapshot.assert_called_with(force=False)
        review.get_live_emotion(force=True)
        snapshot.assert_called_with(force=True)
