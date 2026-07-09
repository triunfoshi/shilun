"""`breakout_events` 数据层的单元测试。

用一个 in-memory fake collection 覆盖 upsert / append_post_bar / 状态迁移 /
breakout_quality 分档 / expired 清理，避免依赖真实 Mongo。
"""

from __future__ import annotations

import unittest

from shilun.market.breakout_events import (
    TRACK_DAYS,
    BreakoutBaseline,
    PostBreakoutBar,
    append_post_bar,
    bulk_insert_baselines,
    classify_breakout_quality,
    find_events_needing_backfill,
    get_breakout_event,
    upsert_breakout_event,
)


class FakeCollection:
    """最小可用的 in-memory Mongo collection 桩，只实现测试用到的接口。"""

    def __init__(self) -> None:
        self._docs: list[dict] = []

    @staticmethod
    def _match(doc: dict, query: dict) -> bool:
        for key, expect in query.items():
            actual = doc.get(key)
            if isinstance(expect, dict):
                for op, op_val in expect.items():
                    if op == "$in" and actual not in op_val:
                        return False
                    if op == "$lte" and not (actual is not None and actual <= op_val):
                        return False
                    if op == "$lt" and not (actual is not None and actual < op_val):
                        return False
                    if op == "$gte" and not (actual is not None and actual >= op_val):
                        return False
                    if op == "$gt" and not (actual is not None and actual > op_val):
                        return False
            else:
                if actual != expect:
                    return False
        return True

    def find_one(self, query: dict, projection: dict | None = None) -> dict | None:
        for doc in self._docs:
            if self._match(doc, query):
                return dict(doc)
        return None

    def update_one(self, query: dict, update: dict, upsert: bool = False) -> None:
        for doc in self._docs:
            if self._match(doc, query):
                doc.update(update.get("$set", {}))
                return
        if upsert:
            new_doc = dict(query)
            new_doc.update(update.get("$set", {}))
            self._docs.append(new_doc)

    class _Cursor:
        def __init__(self, docs: list[dict]) -> None:
            self._docs = docs

        def sort(self, key: str, direction: int = 1) -> "FakeCollection._Cursor":
            self._docs = sorted(self._docs, key=lambda d: d.get(key) or "", reverse=direction < 0)
            return self

        def limit(self, n: int) -> "FakeCollection._Cursor":
            self._docs = self._docs[:n]
            return self

        def __iter__(self):
            return iter([dict(d) for d in self._docs])

    def find(self, query: dict, projection: dict | None = None) -> "FakeCollection._Cursor":
        matched = [doc for doc in self._docs if self._match(doc, query)]
        return self._Cursor(matched)


def _baseline(ticker: str = "600519.SH", date: str = "2026-06-20") -> BreakoutBaseline:
    return BreakoutBaseline(
        ticker=ticker,
        breakout_date=date,
        breakout_close=100.0,
        breakout_ma5=98.0,
        breakout_volume=1_000_000,
        breakout_volume_ratio=1.8,
        previous_high_20=99.0,
        previous_high_60=105.0,
        box_upper=99.0,
        close_position=0.85,
        real_body_ratio=0.7,
        industry="白酒",
    )


def _bar(n: int, *, close: float, low: float, high: float | None = None, volume: float = 700_000) -> PostBreakoutBar:
    return PostBreakoutBar(
        date=f"2026-06-{20 + n:02d}",
        n=n,
        close=close,
        low=low,
        high=high if high is not None else close * 1.005,
        volume=volume,
    )


class UpsertBaselineTest(unittest.TestCase):
    def test_first_insert_returns_inserted(self) -> None:
        col = FakeCollection()
        result = upsert_breakout_event(col, _baseline())
        self.assertEqual(result, "inserted")
        doc = get_breakout_event(col, "600519.SH", "2026-06-20")
        self.assertIsNotNone(doc)
        self.assertEqual(doc["status"], "pending")
        self.assertEqual(doc["tracked_days"], 0)
        self.assertEqual(doc["post_bars"], [])

    def test_second_upsert_does_not_wipe_post_bars(self) -> None:
        col = FakeCollection()
        upsert_breakout_event(col, _baseline())
        append_post_bar(col, "600519.SH", "2026-06-20", _bar(1, close=100.5, low=99.5))
        # 重跑基线（例如脚本重复执行）
        result = upsert_breakout_event(col, _baseline())
        self.assertEqual(result, "existing")
        doc = get_breakout_event(col, "600519.SH", "2026-06-20")
        self.assertEqual(len(doc["post_bars"]), 1, "重跑基线时不能清空已回填的 post_bars。")

    def test_bulk_insert_counts(self) -> None:
        col = FakeCollection()
        counts = bulk_insert_baselines(
            col,
            [_baseline("A"), _baseline("B"), _baseline("A")],
        )
        self.assertEqual(counts["inserted"], 2)
        self.assertEqual(counts["existing"], 1)


class AppendPostBarTest(unittest.TestCase):
    def test_append_advances_status_from_pending_to_tracking(self) -> None:
        col = FakeCollection()
        upsert_breakout_event(col, _baseline())
        ok = append_post_bar(col, "600519.SH", "2026-06-20", _bar(1, close=100.5, low=99.5))
        self.assertTrue(ok)
        doc = get_breakout_event(col, "600519.SH", "2026-06-20")
        self.assertEqual(doc["status"], "tracking")
        self.assertEqual(doc["tracked_days"], 1)
        self.assertTrue(doc["next_day_hold_flag"])

    def test_settled_after_five_bars(self) -> None:
        col = FakeCollection()
        upsert_breakout_event(col, _baseline())
        for n in range(1, TRACK_DAYS + 1):
            append_post_bar(col, "600519.SH", "2026-06-20", _bar(n, close=101.0, low=99.3, volume=600_000))
        doc = get_breakout_event(col, "600519.SH", "2026-06-20")
        self.assertEqual(doc["status"], "settled")
        self.assertEqual(doc["tracked_days"], TRACK_DAYS)
        # 前高守住 + 缩量 + 次日守住 → valid
        self.assertEqual(doc["breakout_quality"], "valid")
        self.assertLess(doc["post_breakout_shrink_ratio"], 1.0)

    def test_duplicate_n_is_rejected(self) -> None:
        col = FakeCollection()
        upsert_breakout_event(col, _baseline())
        append_post_bar(col, "600519.SH", "2026-06-20", _bar(1, close=100.5, low=99.5))
        ok = append_post_bar(col, "600519.SH", "2026-06-20", _bar(1, close=102.0, low=99.5))
        self.assertFalse(ok, "同一 T+n 不能重复追加。")
        doc = get_breakout_event(col, "600519.SH", "2026-06-20")
        self.assertEqual(len(doc["post_bars"]), 1)

    def test_settled_event_rejects_further_bars(self) -> None:
        col = FakeCollection()
        upsert_breakout_event(col, _baseline())
        for n in range(1, TRACK_DAYS + 1):
            append_post_bar(col, "600519.SH", "2026-06-20", _bar(n, close=101.0, low=99.5))
        ok = append_post_bar(col, "600519.SH", "2026-06-20", _bar(6, close=105.0, low=100.0))
        self.assertFalse(ok)


class BreakoutQualityClassificationTest(unittest.TestCase):
    def test_failed_when_fall_back_into_box(self) -> None:
        self.assertEqual(
            classify_breakout_quality(
                previous_high_hold_ratio=0.01,
                next_day_hold_flag=True,
                post_breakout_shrink_ratio=0.8,
                fall_back_into_box_flag=True,
                tracked_days=5,
            ),
            "failed",
        )

    def test_failed_when_hold_ratio_below_minus_2pct(self) -> None:
        self.assertEqual(
            classify_breakout_quality(
                previous_high_hold_ratio=-0.03,
                next_day_hold_flag=True,
                post_breakout_shrink_ratio=0.8,
                fall_back_into_box_flag=False,
                tracked_days=5,
            ),
            "failed",
        )

    def test_suspicious_when_next_day_lost(self) -> None:
        self.assertEqual(
            classify_breakout_quality(
                previous_high_hold_ratio=0.005,
                next_day_hold_flag=False,
                post_breakout_shrink_ratio=0.8,
                fall_back_into_box_flag=False,
                tracked_days=5,
            ),
            "suspicious",
        )

    def test_pending_when_tracking_incomplete(self) -> None:
        self.assertEqual(
            classify_breakout_quality(
                previous_high_hold_ratio=0.005,
                next_day_hold_flag=True,
                post_breakout_shrink_ratio=0.8,
                fall_back_into_box_flag=False,
                tracked_days=2,
            ),
            "pending_confirmation",
        )

    def test_valid_when_everything_holds(self) -> None:
        self.assertEqual(
            classify_breakout_quality(
                previous_high_hold_ratio=0.005,
                next_day_hold_flag=True,
                post_breakout_shrink_ratio=0.7,
                fall_back_into_box_flag=False,
                tracked_days=5,
            ),
            "valid",
        )


class ExpirationTest(unittest.TestCase):
    def test_events_older_than_30_days_are_expired_and_dropped(self) -> None:
        col = FakeCollection()
        upsert_breakout_event(col, _baseline(date="2026-05-01"))  # 50 天前
        upsert_breakout_event(col, _baseline(ticker="000001.SZ", date="2026-06-15"))  # 近期

        to_backfill = find_events_needing_backfill(col, up_to_date="2026-06-20", max_age_days=30)

        self.assertEqual(len(to_backfill), 1)
        self.assertEqual(to_backfill[0]["ticker"], "000001.SZ")

        expired = get_breakout_event(col, "600519.SH", "2026-05-01")
        self.assertEqual(expired["status"], "expired")


class DetectDailyBreakoutsTest(unittest.TestCase):
    """Job 3：全市场 pandas 扫描的正/反例。"""

    @staticmethod
    def _base_series(ticker: str, days: int = 30, base_close: float = 10.0) -> list[dict]:
        """温和上涨、量能中性、MA 多头排列的一段基础日线，作为背景。"""
        bars = []
        for i in range(days):
            close = base_close + i * 0.02
            bars.append({
                "ticker": ticker,
                "date": f"2026-05-{i + 1:02d}" if i < 28 else f"2026-06-{i - 27:02d}",
                "open": close * 0.998,
                "high": close * 1.005,
                "low": close * 0.995,
                "close": close,
                "volume": 1_000_000,
                "ma5": close * 0.995,
                "ma10": close * 0.99,
                "ma20": close * 0.985,
                "volume_ratio_20": 1.0,
                "close_position": 0.5,
                "real_body_ratio": 0.3,
                "industry": "白酒",
            })
        return bars

    def _frame(self, extras: list[dict]) -> "object":
        import pandas as pd
        base = self._base_series("600519.SH", days=28)
        return pd.DataFrame(base + extras)

    def test_positive_breakout_is_detected(self) -> None:
        prev = {
            "ticker": "600519.SH",
            "date": "2026-06-04",
            "open": 10.55, "high": 10.62, "low": 10.53, "close": 10.60,
            "volume": 1_100_000,
            "ma5": 10.55, "ma10": 10.48, "ma20": 10.40,
            "volume_ratio_20": 1.0,
            "close_position": 0.7, "real_body_ratio": 0.6,
            "industry": "白酒",
        }
        breakout = {
            "ticker": "600519.SH",
            "date": "2026-06-05",
            "open": 10.62, "high": 10.90, "low": 10.60, "close": 10.85,
            "volume": 2_000_000,
            "ma5": 10.60, "ma10": 10.50, "ma20": 10.42,
            "volume_ratio_20": 1.8,
            "close_position": 0.85, "real_body_ratio": 0.75,
            "industry": "白酒",
        }
        frame = self._frame([prev, breakout])
        baselines = __import__("shilun.market.breakout_events", fromlist=["detect_daily_breakouts"]).detect_daily_breakouts(
            frame, "2026-06-05"
        )
        self.assertEqual(len(baselines), 1)
        b = baselines[0]
        self.assertEqual(b.ticker, "600519.SH")
        self.assertEqual(b.breakout_date, "2026-06-05")
        self.assertAlmostEqual(b.breakout_close, 10.85)
        self.assertAlmostEqual(b.breakout_volume_ratio, 1.8)
        self.assertGreater(b.previous_high_20, 0)
        self.assertEqual(b.industry, "白酒")

    def test_rejects_when_volume_ratio_too_low(self) -> None:
        prev = {
            "ticker": "600519.SH", "date": "2026-06-04",
            "open": 10.55, "high": 10.62, "low": 10.53, "close": 10.60,
            "volume": 1_100_000, "ma5": 10.55, "ma10": 10.48, "ma20": 10.40,
            "volume_ratio_20": 1.0, "close_position": 0.7, "real_body_ratio": 0.6,
            "industry": "白酒",
        }
        breakout = {
            "ticker": "600519.SH", "date": "2026-06-05",
            "open": 10.62, "high": 10.90, "low": 10.60, "close": 10.85,
            "volume": 1_100_000, "ma5": 10.60, "ma10": 10.50, "ma20": 10.42,
            "volume_ratio_20": 1.05,  # 量能不足 1.2
            "close_position": 0.85, "real_body_ratio": 0.75,
            "industry": "白酒",
        }
        frame = self._frame([prev, breakout])
        baselines = __import__("shilun.market.breakout_events", fromlist=["detect_daily_breakouts"]).detect_daily_breakouts(
            frame, "2026-06-05"
        )
        self.assertEqual(baselines, [])

    def test_rejects_when_prev_close_below_ma5(self) -> None:
        """昨日破位后回来 → reclaim 而非 breakout，Job 3 不应记录。"""
        prev = {
            "ticker": "600519.SH", "date": "2026-06-04",
            "open": 10.55, "high": 10.60, "low": 10.40, "close": 10.45,
            "volume": 1_100_000, "ma5": 10.60, "ma10": 10.48, "ma20": 10.40,
            "volume_ratio_20": 1.0, "close_position": 0.3, "real_body_ratio": 0.6,
            "industry": "白酒",
        }
        breakout = {
            "ticker": "600519.SH", "date": "2026-06-05",
            "open": 10.50, "high": 10.90, "low": 10.48, "close": 10.85,
            "volume": 2_000_000, "ma5": 10.62, "ma10": 10.50, "ma20": 10.42,
            "volume_ratio_20": 1.8, "close_position": 0.9, "real_body_ratio": 0.8,
            "industry": "白酒",
        }
        frame = self._frame([prev, breakout])
        baselines = __import__("shilun.market.breakout_events", fromlist=["detect_daily_breakouts"]).detect_daily_breakouts(
            frame, "2026-06-05"
        )
        self.assertEqual(baselines, [])

    def test_rejects_when_history_too_short(self) -> None:
        import pandas as pd
        # 只喂 3 根，达不到 BREAKOUT_MIN_HISTORY_BARS=25
        frame = pd.DataFrame([
            {"ticker": "T", "date": "2026-06-03", "close": 10, "ma5": 9.9, "high": 10.05, "low": 9.9,
             "open": 9.95, "volume": 1_000_000, "volume_ratio_20": 1.5, "close_position": 0.6,
             "real_body_ratio": 0.5, "industry": "白酒"},
            {"ticker": "T", "date": "2026-06-04", "close": 10.5, "ma5": 10.0, "high": 10.6, "low": 10.0,
             "open": 10.05, "volume": 1_800_000, "volume_ratio_20": 1.8, "close_position": 0.9,
             "real_body_ratio": 0.7, "industry": "白酒"},
            {"ticker": "T", "date": "2026-06-05", "close": 10.8, "ma5": 10.2, "high": 10.9, "low": 10.4,
             "open": 10.5, "volume": 2_000_000, "volume_ratio_20": 1.9, "close_position": 0.9,
             "real_body_ratio": 0.75, "industry": "白酒"},
        ])
        baselines = __import__("shilun.market.breakout_events", fromlist=["detect_daily_breakouts"]).detect_daily_breakouts(
            frame, "2026-06-05"
        )
        self.assertEqual(baselines, [])

    def test_skips_tickers_whose_latest_is_not_analysis_date(self) -> None:
        """票 A 停牌到 2026-06-03，票 B 有 2026-06-05 数据。只应扫到 B。"""
        import pandas as pd
        base_a = self._base_series("A", days=28)
        base_b = self._base_series("B", days=28)
        prev_b = {
            "ticker": "B", "date": "2026-06-04",
            "open": 10.55, "high": 10.62, "low": 10.53, "close": 10.60,
            "volume": 1_100_000, "ma5": 10.55, "ma10": 10.48, "ma20": 10.40,
            "volume_ratio_20": 1.0, "close_position": 0.7, "real_body_ratio": 0.6,
            "industry": "白酒",
        }
        breakout_b = {
            "ticker": "B", "date": "2026-06-05",
            "open": 10.62, "high": 10.90, "low": 10.60, "close": 10.85,
            "volume": 2_000_000, "ma5": 10.60, "ma10": 10.50, "ma20": 10.42,
            "volume_ratio_20": 1.8, "close_position": 0.85, "real_body_ratio": 0.75,
            "industry": "白酒",
        }
        frame = pd.DataFrame(base_a + base_b + [prev_b, breakout_b])
        baselines = __import__("shilun.market.breakout_events", fromlist=["detect_daily_breakouts"]).detect_daily_breakouts(
            frame, "2026-06-05"
        )
        self.assertEqual(len(baselines), 1)
        self.assertEqual(baselines[0].ticker, "B")

    def test_detected_baselines_can_be_bulk_inserted(self) -> None:
        col = FakeCollection()
        prev = {
            "ticker": "600519.SH", "date": "2026-06-04",
            "open": 10.55, "high": 10.62, "low": 10.53, "close": 10.60,
            "volume": 1_100_000, "ma5": 10.55, "ma10": 10.48, "ma20": 10.40,
            "volume_ratio_20": 1.0, "close_position": 0.7, "real_body_ratio": 0.6,
            "industry": "白酒",
        }
        breakout = {
            "ticker": "600519.SH", "date": "2026-06-05",
            "open": 10.62, "high": 10.90, "low": 10.60, "close": 10.85,
            "volume": 2_000_000, "ma5": 10.60, "ma10": 10.50, "ma20": 10.42,
            "volume_ratio_20": 1.8, "close_position": 0.85, "real_body_ratio": 0.75,
            "industry": "白酒",
        }
        frame = self._frame([prev, breakout])
        detect = __import__("shilun.market.breakout_events", fromlist=["detect_daily_breakouts", "bulk_insert_baselines"])
        baselines = detect.detect_daily_breakouts(frame, "2026-06-05")
        counts = detect.bulk_insert_baselines(col, baselines)
        self.assertEqual(counts["inserted"], 1)
        # 重跑同一天不再插入新记录
        counts2 = detect.bulk_insert_baselines(col, baselines)
        self.assertEqual(counts2["inserted"], 0)
        self.assertEqual(counts2["existing"], 1)


class FakeRawMarket:
    """模拟 `store.raw_market.find_daily_bars` 的接口。"""

    def __init__(self, bars: list[dict]) -> None:
        self._bars = bars

    def find_daily_bars(self, *, start_date: str, end_date: str, tickers: list[str] | None = None) -> list[dict]:
        result = []
        allowed = set(tickers or [])
        for bar in self._bars:
            if allowed and str(bar.get("ticker") or "") not in allowed:
                continue
            date_str = str(bar.get("date") or "")
            if start_date <= date_str <= end_date:
                result.append(dict(bar))
        return result


class FakeStore:
    """把 collection 和 raw_market 打包成 backfill_post_bars 期望的形态。"""

    def __init__(self, collection: FakeCollection, raw_market: FakeRawMarket) -> None:
        self._collection = collection
        self.raw_market = raw_market

    def collection(self, name: str) -> FakeCollection:
        assert name == "breakout_events"
        return self._collection


def _daily_bar(ticker: str, date: str, close: float, low: float, high: float, volume: float) -> dict:
    return {
        "ticker": ticker,
        "date": date,
        "close": close,
        "low": low,
        "high": high,
        "volume": volume,
    }


class BuildLatestEventsLookupTest(unittest.TestCase):
    """Job 6：批量拉最近事件，返回 {ticker: latest_event}。"""

    def _seed(self, col: FakeCollection, ticker: str, date: str) -> None:
        upsert_breakout_event(
            col,
            BreakoutBaseline(
                ticker=ticker,
                breakout_date=date,
                breakout_close=100.0,
                breakout_ma5=98.0,
                breakout_volume=1_000_000,
                breakout_volume_ratio=1.8,
                previous_high_20=99.0,
                previous_high_60=105.0,
                box_upper=99.0,
                close_position=0.85,
                real_body_ratio=0.7,
                industry="白酒",
            ),
        )

    def test_returns_latest_event_per_ticker(self) -> None:
        from shilun.market.breakout_events import build_latest_events_lookup
        col = FakeCollection()
        self._seed(col, "A", "2026-06-10")
        self._seed(col, "A", "2026-06-20")  # 更新
        self._seed(col, "B", "2026-06-18")

        lookup = build_latest_events_lookup(col, on_or_before="2026-06-25", lookback_days=30)
        self.assertEqual(set(lookup.keys()), {"A", "B"})
        self.assertEqual(lookup["A"]["breakout_date"], "2026-06-20")
        self.assertEqual(lookup["B"]["breakout_date"], "2026-06-18")

    def test_excludes_events_beyond_lookback(self) -> None:
        from shilun.market.breakout_events import build_latest_events_lookup
        col = FakeCollection()
        self._seed(col, "OLD", "2026-04-01")  # 60+ 天前
        self._seed(col, "NEW", "2026-06-20")
        lookup = build_latest_events_lookup(col, on_or_before="2026-06-25", lookback_days=30)
        self.assertNotIn("OLD", lookup)
        self.assertIn("NEW", lookup)

    def test_ticker_filter_restricts_result(self) -> None:
        from shilun.market.breakout_events import build_latest_events_lookup
        col = FakeCollection()
        self._seed(col, "A", "2026-06-20")
        self._seed(col, "B", "2026-06-20")
        lookup = build_latest_events_lookup(col, tickers=["A"], on_or_before="2026-06-25")
        self.assertEqual(set(lookup.keys()), {"A"})


class BackfillPostBarsTest(unittest.TestCase):
    """Job 4：把 pending/tracking 事件的 T+1..T+5 从日线补齐。"""

    def _seed_baseline(self, col: FakeCollection, *, ticker: str = "600519.SH", breakout_date: str = "2026-06-20") -> None:
        from shilun.market.breakout_events import upsert_breakout_event, BreakoutBaseline
        upsert_breakout_event(
            col,
            BreakoutBaseline(
                ticker=ticker,
                breakout_date=breakout_date,
                breakout_close=100.0,
                breakout_ma5=98.0,
                breakout_volume=1_000_000,
                breakout_volume_ratio=1.8,
                previous_high_20=99.0,
                previous_high_60=105.0,
                box_upper=99.0,
                close_position=0.85,
                real_body_ratio=0.7,
                industry="白酒",
            ),
        )

    def test_backfill_from_pending_to_settled_in_one_pass(self) -> None:
        from shilun.market.breakout_events import backfill_post_bars, get_breakout_event
        col = FakeCollection()
        self._seed_baseline(col)
        # 5 根 T+n 日线，全部守住前高、缩量、次日站稳
        bars = [
            _daily_bar("600519.SH", "2026-06-23", 101.0, 99.5, 101.5, 700_000),
            _daily_bar("600519.SH", "2026-06-24", 100.5, 99.4, 101.0, 650_000),
            _daily_bar("600519.SH", "2026-06-25", 102.0, 100.0, 102.5, 720_000),
            _daily_bar("600519.SH", "2026-06-26", 101.8, 100.2, 102.2, 680_000),
            _daily_bar("600519.SH", "2026-06-27", 102.5, 100.5, 103.0, 700_000),
        ]
        store = FakeStore(col, FakeRawMarket(bars))
        result = backfill_post_bars(store, up_to_date="2026-06-27")
        self.assertEqual(result["scanned"], 1)
        self.assertEqual(result["appended"], 5)
        self.assertEqual(result["settled"], 1)
        doc = get_breakout_event(col, "600519.SH", "2026-06-20")
        self.assertEqual(doc["status"], "settled")
        self.assertEqual(doc["tracked_days"], 5)
        self.assertEqual(doc["breakout_quality"], "valid")

    def test_backfill_is_idempotent(self) -> None:
        from shilun.market.breakout_events import backfill_post_bars, get_breakout_event
        col = FakeCollection()
        self._seed_baseline(col)
        bars = [
            _daily_bar("600519.SH", "2026-06-23", 101.0, 99.5, 101.5, 700_000),
            _daily_bar("600519.SH", "2026-06-24", 100.5, 99.4, 101.0, 650_000),
        ]
        store = FakeStore(col, FakeRawMarket(bars))

        first = backfill_post_bars(store, up_to_date="2026-06-24")
        self.assertEqual(first["appended"], 2)
        # 同一天再跑：不应重复追加
        second = backfill_post_bars(store, up_to_date="2026-06-24")
        self.assertEqual(second["appended"], 0)
        doc = get_breakout_event(col, "600519.SH", "2026-06-20")
        self.assertEqual(len(doc["post_bars"]), 2)

    def test_partial_backfill_advances_status_but_not_settled(self) -> None:
        from shilun.market.breakout_events import backfill_post_bars, get_breakout_event
        col = FakeCollection()
        self._seed_baseline(col)
        bars = [
            _daily_bar("600519.SH", "2026-06-23", 101.0, 99.5, 101.5, 700_000),
            _daily_bar("600519.SH", "2026-06-24", 100.5, 99.4, 101.0, 650_000),
        ]
        store = FakeStore(col, FakeRawMarket(bars))
        result = backfill_post_bars(store, up_to_date="2026-06-24")
        self.assertEqual(result["appended"], 2)
        self.assertEqual(result["settled"], 0)
        doc = get_breakout_event(col, "600519.SH", "2026-06-20")
        self.assertEqual(doc["status"], "tracking")
        self.assertEqual(doc["tracked_days"], 2)
        self.assertEqual(doc["breakout_quality"], "pending_confirmation")

    def test_bars_beyond_t5_are_ignored(self) -> None:
        """就算 Mongo 里有 6 根日线，也只写 T+1..T+5。"""
        from shilun.market.breakout_events import backfill_post_bars, get_breakout_event
        col = FakeCollection()
        self._seed_baseline(col)
        bars = [
            _daily_bar("600519.SH", f"2026-06-{22 + i:02d}", 101.0, 99.5, 101.5, 700_000)
            for i in range(1, 8)  # 7 根，超过 T+5
        ]
        store = FakeStore(col, FakeRawMarket(bars))
        result = backfill_post_bars(store, up_to_date="2026-06-30")
        doc = get_breakout_event(col, "600519.SH", "2026-06-20")
        self.assertEqual(doc["tracked_days"], 5)
        self.assertEqual(result["appended"], 5)

    def test_multiple_events_batched_together(self) -> None:
        """多张事件、多只票在一次 backfill 里都能推进。"""
        from shilun.market.breakout_events import backfill_post_bars, get_breakout_event
        col = FakeCollection()
        self._seed_baseline(col, ticker="A", breakout_date="2026-06-20")
        self._seed_baseline(col, ticker="B", breakout_date="2026-06-21")
        bars = [
            _daily_bar("A", "2026-06-23", 101.0, 99.5, 101.5, 700_000),
            _daily_bar("A", "2026-06-24", 100.5, 99.4, 101.0, 650_000),
            _daily_bar("B", "2026-06-23", 101.0, 99.5, 101.5, 700_000),
            _daily_bar("B", "2026-06-24", 100.5, 99.4, 101.0, 650_000),
        ]
        store = FakeStore(col, FakeRawMarket(bars))
        result = backfill_post_bars(store, up_to_date="2026-06-24")
        self.assertEqual(result["scanned"], 2)
        self.assertEqual(result["appended"], 4)
        doc_a = get_breakout_event(col, "A", "2026-06-20")
        doc_b = get_breakout_event(col, "B", "2026-06-21")
        self.assertEqual(doc_a["tracked_days"], 2)
        self.assertEqual(doc_b["tracked_days"], 2)

    def test_no_bars_available_yet_leaves_event_pending(self) -> None:
        from shilun.market.breakout_events import backfill_post_bars, get_breakout_event
        col = FakeCollection()
        self._seed_baseline(col)
        store = FakeStore(col, FakeRawMarket([]))
        result = backfill_post_bars(store, up_to_date="2026-06-21")
        self.assertEqual(result["appended"], 0)
        doc = get_breakout_event(col, "600519.SH", "2026-06-20")
        self.assertEqual(doc["status"], "pending")

    def test_expired_events_are_skipped_and_marked(self) -> None:
        """突破日超龄 30 天时，find_events_needing_backfill 会把它置为 expired 并跳过。"""
        from shilun.market.breakout_events import backfill_post_bars, get_breakout_event
        col = FakeCollection()
        self._seed_baseline(col, ticker="OLD", breakout_date="2026-05-01")  # 50 天前
        store = FakeStore(col, FakeRawMarket([]))
        result = backfill_post_bars(store, up_to_date="2026-06-25")
        self.assertEqual(result["scanned"], 0)
        doc = get_breakout_event(col, "OLD", "2026-05-01")
        self.assertEqual(doc["status"], "expired")


if __name__ == "__main__":
    unittest.main()
