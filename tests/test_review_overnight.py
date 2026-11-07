"""Overseas fixtures only; these tests never contact quote providers or MongoDB."""
from datetime import datetime
import json
from unittest.mock import patch

import pytest

from shilun.api import review
from shilun.review import overnight as subject


def quotes():
    rows = {}
    for symbol, *_ in (*subject.INDICES, *subject.MAG7, *subject.AI_HARD_TECH, subject.SOXQ):
        fields = [""] * 33
        fields[3], fields[32] = "1234.56", "-1.25"
        fields[30] = "2026/09/11 16:08:00" if symbol.startswith("hk") else "2026-09-10 16:00:00"
        rows[symbol] = fields
    return rows


def test_vibe_grouping_units_and_actual_sessions():
    with patch.object(subject, "_tencent", return_value=quotes()), patch.object(subject, "_eastmoney_index") as fallback:
        result = subject.overseas_snapshot()
    fallback.assert_not_called()
    assert len(result["indices"]) == 5 and len(result["mag7"]) == 7
    assert len(result["ai_hard_tech"]) == 9 and result["ai_index"]["ticker"] == "SOXQ"
    assert [row["ticker"] for row in result["ai_hard_tech"]] == ["SNDK", "LITE", "MU", "CRWV", "AXTI", "AVGO", "SKHY", "MRVL", "COHR"]
    assert [row["ticker"] for row in result["mag7"]] == ["AAPL", "MSFT", "NVDA", "GOOGL", "AMZN", "META", "TSLA"]
    assert result["indices"][0]["price"] == 1234.56
    assert [row['ticker'] for row in result['indices']] == ['DJI', 'INX', 'IXIC', 'HSI', 'HSTECH']
    assert result["indices"][0]["change_pct"] == -1.25
    assert result["us_session"] == "2026-09-10" and result["hk_session"] == "2026-09-11"
    assert result["available"] and not result["partial"]
    assert result["snapshot_mode"] == "latest"
    assert result["historical_archive_available"] is False
    assert "尚未保存" in result["history_note"]


def test_tencent_parser_and_https_batch():
    raw = ";".join(f'v_{symbol}="' + "~".join(fields) + '"' for symbol, fields in quotes().items())
    raw += ';v_bad="~bad~";invalid;'
    with patch.object(subject, "_read", return_value=raw.encode("gbk")) as read:
        assert len(subject._tencent()) == 22
    url, timeout = read.call_args.args
    assert url.startswith("https://qt.gtimg.cn/q=") and timeout == 12
    assert "usIXIC" in url and "hkHSTECH" in url and "usTSLA" in url and "usSOXQ" in url


@pytest.mark.parametrize("invalid", ["NaN", "inf", "", "-", None, True])
def test_missing_quote_not_zero_or_nonfinite(invalid):
    raw = quotes()
    raw["usAAPL"][3] = invalid
    raw["hkHSI"][30] = "not a date"
    with patch.object(subject, "_tencent", return_value=raw):
        result = subject.overseas_snapshot()
    assert result["partial"]
    assert len(result["mag7"]) == 6
    assert len(result["ai_hard_tech"]) == 9
    assert next(r for r in result["indices"] if r["symbol"] == "hkHSI")["session"] is None
    json.dumps(result, allow_nan=False)


def test_ai_watchlist_keeps_missing_tickers_visible_without_fabricating_values():
    raw = quotes()
    for symbol, *_ in subject.AI_HARD_TECH:
        raw.pop(symbol)
    raw.pop(subject.SOXQ[0])
    with patch.object(subject, "_tencent", return_value=raw):
        result = subject.overseas_snapshot()
    assert [row["ticker"] for row in result["ai_hard_tech"]] == ["SNDK", "LITE", "MU", "CRWV", "AXTI", "AVGO", "SKHY", "MRVL", "COHR"]
    assert all(row["available"] is False and row["price"] is None for row in result["ai_hard_tech"])
    assert result["ai_index"]["ticker"] == "SOXQ" and result["ai_index"]["available"] is False
    assert result["partial"] and result["available"]


def test_complete_failure_is_unavailable_without_internal_error():
    with patch.object(subject, "_read", side_effect=OSError("SECRET connection details")):
        result = subject.overseas_snapshot()
    assert not result["available"] and not result["indices"] and not result["mag7"]
    assert all(row["available"] is False for row in result["ai_hard_tech"])
    assert "SECRET" not in json.dumps(result)


def test_eastmoney_fallback_scale_date_and_nasdaq_identity():
    with patch.object(subject, "_read", return_value=json.dumps({"data": {"f43":123456, "f59":2, "f170":-125}}).encode()):
        result = subject._eastmoney_index(subject.INDICES[2])
    assert result["price"] == 1234.56 and result["change_pct"] == -1.25
    assert result["session"] is None and result["name"] == "纳斯达克100"
    assert result['ticker'] == 'NDX'  # Never label the fallback as Nasdaq Composite.
    with patch.object(subject, "_read", return_value=b'{"data":{"f43":1234,"f59":0,"f170":0}}'):
        assert subject._eastmoney_index(subject.INDICES[0])["price"] == 1234
    with patch.object(subject, "_tencent", return_value={}), patch.object(subject, "_eastmoney_index", return_value=result):
        response = subject.overseas_snapshot()
    assert response["available"] and response["partial"]
    assert "未提供行情日期" in response["us_label"]


def test_no_quote_date_invention_and_vibe_session_labels():
    for bad in ["", "2026-02-30", "2026-13-1", "x", "20260911"]:
        assert subject._session(bad) is None
    now = datetime(2026, 9, 13, 22, 0, tzinfo=subject.CHINA)
    assert subject._market_label("美股", "2026-09-11", now).endswith("收盘")
    assert subject._market_label("美股", None, now) is None
    now = datetime(2026, 9, 11, 10, 0, tzinfo=subject.CHINA)
    assert subject._market_label("港股", "2026-09-11", now).endswith("盘中")
    assert subject._market_label("港股", "2026-09-10", now).endswith("收盘")


def test_route_does_not_open_or_modify_database():
    with patch.object(review, "overseas_snapshot", return_value={"available":False}), patch.object(review, "_open_store") as store:
        assert review.get_overseas() == {"available":False}
    store.assert_not_called()
