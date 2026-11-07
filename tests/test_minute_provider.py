from unittest.mock import Mock

import pandas as pd
import pytest

from shilun.data.providers.minute_provider import MinuteClient, MinuteFetchError


def bars(ticker="000001.SH", day="2026-09-18"):
    return pd.DataFrame([{"ts_code": ticker, "trade_time": day+" 15:00:00",
        "open": 3912, "close": 3911.8713, "high": 3914, "low": 3910,
        "vol": 2011820400, "amount": 35979854000}])


@pytest.mark.parametrize("index_api", ["idx_mins", "stk_mins"])
def test_index_endpoint_is_explicit_and_preserves_minute_units(index_api):
    pro, fallback = Mock(), Mock()
    pro._call_api.return_value = bars()
    frame = MinuteClient(pro, fallback, index_api=index_api).fetch_minute_bars("000001.SH", trade_date="20260918")
    assert pro._call_api.call_args.args[0] == index_api
    assert frame.iloc[0]["volume"] == 2011820400
    assert frame.iloc[0]["amount"] == 35979854000
    assert frame.attrs["source"] == "tushare_"+index_api
    fallback.fetch_minute_bars.assert_not_called()


def test_wrong_security_never_enters_minute_archive():
    pro, fallback = Mock(), Mock()
    pro._call_api.return_value = bars("000001.SZ")
    fallback.fetch_minute_bars.side_effect = RuntimeError("offline")
    with pytest.raises(MinuteFetchError):
        MinuteClient(pro, fallback).fetch_minute_bars("000001.SH", trade_date="20260918")


def test_old_fallback_date_is_not_target_date():
    pro, fallback = Mock(), Mock()
    pro._call_api.side_effect = RuntimeError("unavailable")
    fallback.fetch_minute_bars.return_value = pd.DataFrame([{"datetime": pd.Timestamp("2026-09-17 15:00:00")}])
    with pytest.raises(MinuteFetchError):
        MinuteClient(pro, fallback).fetch_minute_bars("000001.SH", trade_date="20260918")
