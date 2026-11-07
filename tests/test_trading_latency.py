from __future__ import annotations

import unittest
from datetime import datetime

from shilun.trading.latency import record_quote_latency_samples, summarize_quote_latency


class _Collection:
    def __init__(self):
        self.docs = []

    def update_one(self, query, update, upsert=False):
        if any(item.get("sample_id") == query.get("sample_id") for item in self.docs):
            return
        self.docs.append({**query, **update.get("$setOnInsert", {})})


class TradingLatencyTests(unittest.TestCase):
    def test_records_quote_to_fetch_age_and_threshold_breach(self) -> None:
        collection = _Collection()
        count = record_quote_latency_samples(
            collection,
            [{
                "ticker": "603986.SH",
                "source": "klineshare",
                "quote_at": "2026-07-13T10:00:00+08:00",
                "fetched_at": "2026-07-13T10:00:46+08:00",
            }],
            request_latency_ms=123.45,
            decision_at=datetime.fromisoformat("2026-07-13T10:00:50+08:00"),
        )
        self.assertEqual(count, 1)
        self.assertEqual(collection.docs[0]["quote_age_at_fetch_seconds"], 46)
        self.assertEqual(collection.docs[0]["quote_age_at_decision_seconds"], 50)
        self.assertTrue(collection.docs[0]["over_threshold"])

    def test_summary_requires_fifteen_samples_per_ticker(self) -> None:
        samples = [
            {
                "ticker": "603986.SH",
                "quote_age_seconds": index,
                "request_latency_ms": 100 + index,
                "over_threshold": False,
                "observed_at": f"2026-07-13T10:{index:02d}:00+08:00",
            }
            for index in range(15)
        ]
        summary = summarize_quote_latency(samples)
        self.assertTrue(summary["calibration_ready"])
        self.assertEqual(summary["tickers"][0]["quote_age_p95_seconds"], 14)


if __name__ == "__main__":
    unittest.main()
