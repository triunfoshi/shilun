from __future__ import annotations

import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

from shilun.data.providers.klineshare_provider import (
    KlineShareClient,
    KlineShareConfig,
    KlineShareError,
    from_klineshare_symbol,
    to_klineshare_symbol,
)


MARKET_TZ = ZoneInfo("Asia/Shanghai")


class _Response:
    def __init__(self, payload: dict, status_code: int = 200, headers: dict | None = None):
        self.payload = payload
        self.status_code = status_code
        self.headers = headers or {}

    def json(self):
        return self.payload


class _Session:
    def __init__(self, responses: list[_Response]):
        self.responses = list(responses)
        self.calls: list[dict] = []
        self.trust_env = True

    def get(self, url, **kwargs):
        self.calls.append({"url": url, **kwargs})
        return self.responses.pop(0)


def _timestamp_ms(hour: int, minute: int) -> int:
    return int(datetime(2026, 7, 10, hour, minute, tzinfo=MARKET_TZ).timestamp() * 1000)


class KlineShareProviderTests(unittest.TestCase):
    def test_symbol_mapping_uses_ss_for_shanghai_boundary(self) -> None:
        self.assertEqual(to_klineshare_symbol("603986.SH"), "603986.SS")
        self.assertEqual(to_klineshare_symbol("000001.SH"), "000001.SS")
        self.assertEqual(from_klineshare_symbol("000001.SS"), "000001.SH")
        self.assertEqual(to_klineshare_symbol("399006.SZ"), "399006.SZ")

    def test_health_does_not_require_key(self) -> None:
        session = _Session([_Response({"success": True, "version": "1"})])
        client = KlineShareClient(KlineShareConfig(), session=session)
        result = client.health()
        self.assertTrue(result["success"])
        self.assertNotIn("X-API-Key", session.calls[0]["headers"])

    def test_authenticated_call_requires_key(self) -> None:
        client = KlineShareClient(KlineShareConfig(), session=_Session([]))
        with self.assertRaisesRegex(KlineShareError, "API_KEY"):
            client.fetch_realtime(["603986.SH"])

    def test_account_uses_header_authentication(self) -> None:
        session = _Session([_Response({
            "success": True,
            "data": {"scopes": [{"scope": "realtime"}, {"scope": "kline"}]},
        })])
        client = KlineShareClient(KlineShareConfig(api_key="secret"), session=session)
        result = client.account()
        self.assertTrue(result["success"])
        self.assertEqual(session.calls[0]["headers"]["X-API-Key"], "secret")

    def test_calendar_normalizes_date_range_without_consuming_topic_trials(self) -> None:
        session = _Session([_Response({"success": True, "data": [{"date": "20260710", "is_trade_day": True}]})])
        client = KlineShareClient(KlineShareConfig(api_key="secret"), session=session)

        result = client.fetch_calendar(start="2026-07-01", end="2026-07-31")

        self.assertTrue(result["success"])
        self.assertEqual(session.calls[0]["url"], "https://data.klineshare.cn/v1/calendar")
        self.assertEqual(session.calls[0]["params"], {"start": "20260701", "end": "20260731"})

    def test_realtime_normalizes_batch_and_source_timestamp(self) -> None:
        session = _Session([_Response({
            "success": True,
            "data": [
                {
                    "symbol": "603986.SS",
                    "name": "兆易创新",
                    "timestamp": _timestamp_ms(10, 15),
                    "date": "20260710",
                    "price": 612.0,
                    "open": 618.0,
                    "high": 620.0,
                    "low": 610.0,
                    "volume": 10000,
                    "turnover": 6120000,
                    "change_percent": -0.01,
                    "trade_status": "TRADE",
                    "quote": {"pre_close": 618.18, "volume_ratio": 1.25},
                    "depth": {
                        "bids": [{"price": 611.9, "volume": 800, "orders": 12}],
                        "asks": [{"price": 612.1, "volume": 600, "orders": 9}],
                        "inner_volume": 4000,
                        "outer_volume": 6000,
                    },
                },
                {
                    "symbol": "399006.SZ",
                    "name": "创业板指",
                    "timestamp": _timestamp_ms(10, 15),
                    "price": 3800.0,
                    "change_percent": 0.005,
                },
            ],
        })])
        client = KlineShareClient(KlineShareConfig(api_key="secret"), session=session)

        result = client.fetch_realtime(["603986.SH", "399006.SZ"], include_depth=True)

        self.assertEqual(result["ticker"].tolist(), ["603986.SH", "399006.SZ"])
        self.assertEqual(result.iloc[0]["quote_at"], "2026-07-10T10:15:00+08:00")
        self.assertEqual(result.iloc[0]["prev_close"], 618.18)
        self.assertEqual(result.iloc[0]["volume_ratio"], 1.25)
        self.assertEqual(result.iloc[0]["amount"], 6120)
        self.assertEqual(result.iloc[0]["amount_unit"], "thousand_cny")
        self.assertEqual(result.iloc[0]["outer_volume"], 6000)
        self.assertEqual(result.iloc[0]["depth_bids"][0]["price"], 611.9)
        self.assertEqual(result.iloc[0]["timestamp_kind"], "source_quote_at")
        self.assertEqual(session.calls[0]["headers"]["X-API-Key"], "secret")
        self.assertEqual(
            session.calls[0]["params"],
            [("symbol", "603986.SS"), ("symbol", "399006.SZ"), ("include_depth", "true")],
        )

    def test_kline_normalizes_five_minute_bars(self) -> None:
        session = _Session([_Response({
            "success": True,
            "data": [
                {
                    "timestamp": _timestamp_ms(10, 10),
                    "open": 610,
                    "high": 613,
                    "low": 609,
                    "close": 612,
                    "volume": 1000,
                    "turnover": 612000,
                },
                {
                    "timestamp": _timestamp_ms(10, 15),
                    "open": 612,
                    "high": 614,
                    "low": 611,
                    "close": 613,
                    "volume": 900,
                    "turnover": 551700,
                },
            ],
        })])
        client = KlineShareClient(KlineShareConfig(api_key="secret"), session=session)

        result = client.fetch_kline("603986.SH", period=300, count=100)

        self.assertEqual(len(result), 2)
        self.assertEqual(result.iloc[-1]["close"], 613)
        self.assertEqual(result.iloc[0]["amount"], 612)
        self.assertEqual(result.iloc[0]["amount_unit"], "thousand_cny")
        self.assertEqual(session.calls[0]["params"]["symbol"], "603986.SS")
        self.assertEqual(session.calls[0]["params"]["period"], 300)

    def test_trend_preserves_source_average_price_unit(self) -> None:
        session = _Session([_Response({
            "success": True,
            "data": {"data": [
                {
                    "timestamp": _timestamp_ms(10, 14),
                    "price": 612.0,
                    "avg_price": 611.25,
                    "volume": 1000,
                    "turnover": 612000,
                },
                {
                    "timestamp": _timestamp_ms(10, 15),
                    "price": 613.0,
                    "avg_price": 611.42,
                    "volume": 900,
                    "turnover": 551700,
                },
            ], "pre_close": 610.0, "symbol": "603986.SS", "total": 2},
            "meta": {"symbol": "603986.SS", "tick_count": 2},
        })])
        client = KlineShareClient(KlineShareConfig(api_key="secret"), session=session)

        result = client.fetch_trend("603986.SH")

        self.assertEqual(result.iloc[-1]["avg_price"], 611.42)
        self.assertEqual(result.iloc[-1]["price_unit"], "cny_per_share")
        self.assertEqual(result.iloc[-1]["prev_close"], 610.0)
        self.assertEqual(result.iloc[-1]["amount"], 551.7)
        self.assertEqual(session.calls[0]["url"], "https://data.klineshare.cn/v1/trend")
        self.assertEqual(session.calls[0]["params"], {"symbol": "603986.SS"})

    def test_rate_limit_exposes_retry_after(self) -> None:
        session = _Session([_Response(
            {"success": False, "message": "too many", "retry_after": 12},
            status_code=429,
            headers={"Retry-After": "12"},
        )])
        client = KlineShareClient(KlineShareConfig(api_key="secret"), session=session)

        with self.assertRaises(KlineShareError) as caught:
            client.fetch_realtime(["603986.SH"])

        self.assertEqual(caught.exception.status_code, 429)
        self.assertEqual(caught.exception.retry_after, 12)


if __name__ == "__main__":
    unittest.main()
