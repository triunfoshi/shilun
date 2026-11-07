from unittest.mock import Mock

import pytest

from shilun.data.providers.eastmoney_provider import EastmoneyClient, EastmoneyFetchError


def test_minute_contract_preserves_unadjusted_prices_converts_lots_and_sorts():
    client = EastmoneyClient()
    client._get = Mock(return_value={"code": "000001", "klines": [
        "2026-09-18 09:40,10,11,12,9,500,550000,0,0,0,0",
        "2026-09-18 09:35,9,10,11,8,400,400000,0,0,0,0"]})
    df = client.fetch_minute_bars("000001.SZ", trade_date="20260918")
    assert df.iloc[0]["volume"] == 40000
    assert df.iloc[0]["amount"] == 400000
    assert df.iloc[0]["close"] == 10
    assert client._get.call_args.args[1]["fqt"] == "0"


def test_future_or_invalid_ohlc_is_rejected():
    client = EastmoneyClient()
    client._get = Mock(return_value={"code": "000001", "klines": ["2026-09-21 09:35,10,11,12,9,400,4000"]})
    with pytest.raises(EastmoneyFetchError):
        client.fetch_minute_bars("000001.SH", trade_date="20260918")


def test_duplicate_numeric_code_keeps_stock_and_index_separate():
    client = EastmoneyClient()
    client._get = Mock(return_value={"diff": [
        {"f12": "000001", "f13": 1, "f2": 3900, "f124": 1789719092},
        {"f12": "000001", "f13": 0, "f2": 11.7, "f124": 1789719092}]})
    frame = client.fetch_stock_realtime_batch(["000001.SH", "000001.SZ"])
    assert list(frame["ticker"]) == ["000001.SH", "000001.SZ"]
    assert set(frame["timestamp_kind"]) == {"provider"}
