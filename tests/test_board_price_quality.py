import unittest
from unittest.mock import MagicMock

import pandas as pd

from shilun.review.board_effects import build_effects
from shilun.review.board_price_quality import annotate_prices, sync_cohort_prices
from shilun.review.suspensions import load_suspensions

DAY = '2026-09-22'
TICKER = '002860.SZ'


class Store:
    def __init__(self, bars=(), suspensions=(), limits=()):
        self.rows = {'market_daily_bars': list(bars), 'review_daily_suspensions': list(suspensions),
                     'daily_price_limits': list(limits)}
        self.writes = []

    def collection(self, name):
        collection = MagicMock()
        def find(query, projection):
            return [dict(r) for r in self.rows.get(name, []) if all(
                r.get(k) in value['$in'] if isinstance(value, dict) else r.get(k) == value
                for k, value in query.items())]
        collection.find.side_effect = find
        return collection

    def upsert_daily_bars(self, frame):
        self.writes.extend(frame.to_dict('records'))
        for r in frame.to_dict('records'):
            self.rows['market_daily_bars'] = [b for b in self.rows['market_daily_bars']
                if (b['date'], b['ticker']) != (r['date'], r['ticker'])] + [r]


def suspension(ticker=TICKER, timing=None, kind='S', day=DAY):
    return dict(ticker=ticker, date=day, suspend_timing=timing, suspend_type=kind)


class BoardPriceQualityTests(unittest.TestCase):
    def test_suspensions_require_exact_day_and_full_day_stop(self):
        store = Store(suspensions=[suspension(timing=float('nan')),
            suspension('000001.SZ', timing='09:30-10:30'),
            suspension('000002.SZ', kind='R'), suspension('000003.SZ', day='2026-09-21')])
        self.assertEqual({TICKER: {DAY}}, load_suspensions(store, [DAY]))

    def test_no_price_is_not_zero_or_resumption_return_and_groups_deduplicate(self):
        records = {DAY: [dict(code='002860', name='星帅尔', ret=None, prev_boards=1,
                              seal_time='093000', sector='电子')]}
        annotate_prices([records], {}, {TICKER: {DAY}})
        result = build_effects(dates=['2026-09-21', DAY], per_day=records)
        g = result['strategies'][0]
        self.assertIsNone(g['overall']['avg'])
        self.assertEqual((0, 1), (g['overall']['sample'], g['overall']['sample_total']))
        self.assertEqual(1, g['price_coverage']['suspended'])
        self.assertEqual(0, g['price_coverage']['unresolved'])
        self.assertEqual(1, len(result['price_exceptions']))
        self.assertGreater(len(result['price_exceptions'][0]['groups']), 1)
        self.assertIsNone(result['price_exceptions'][0]['ret'])

    def test_missing_close_reference_and_conflict_are_distinct(self):
        rows = {DAY: [dict(code=code, ret=ret) for code, ret in
                      [('000001', None), ('000002', None), ('002860', 3.0)]]}
        annotate_prices([rows], {(DAY, '000002'): {'close': 10}}, {TICKER: {DAY}})
        self.assertEqual(['missing_bar', 'missing_reference', 'conflict'],
                         [r['price_status'] for r in rows[DAY]])
        self.assertEqual(3, rows[DAY][-1]['ret'])

    def test_verified_suspension_does_not_fetch_or_write_synthetic_bar(self):
        store = Store(suspensions=[suspension()]); client = MagicMock()
        result = sync_cohort_prices(store, {DAY: {TICKER}}, client=client)
        self.assertEqual('ok', result['status'])
        self.assertEqual([{'date': DAY, 'ticker': TICKER}], result['suspended'])
        self.assertEqual([], result['unresolved'])
        client.fetch_daily_for_trade_date.assert_not_called()
        self.assertEqual([], store.writes)

    def test_repair_is_targeted_and_repeat_run_is_noop(self):
        store = Store(suspensions=[suspension()]); client = MagicMock()
        client.fetch_daily_for_trade_date.return_value = pd.DataFrame([
            dict(ticker='000001.SZ', date=DAY, close=11, pre_close=10),
            dict(ticker='000002.SZ', date=DAY, close=9, pre_close=10)])
        needed = {DAY: {TICKER, '000001.SZ'}}
        result = sync_cohort_prices(store, needed, client=client)
        self.assertEqual(1, result['repaired'])
        self.assertEqual(['000001.SZ'], [r['ticker'] for r in store.writes])
        again = sync_cohort_prices(store, needed, client=client)
        self.assertEqual('ok', again['status'])
        self.assertEqual(0, again['repaired'])
        client.fetch_daily_for_trade_date.assert_called_once_with('20260922', include_reference=True)

    def test_no_suspension_evidence_keeps_gap_unresolved(self):
        client = MagicMock(); client.fetch_daily_for_trade_date.return_value = pd.DataFrame()
        result = sync_cohort_prices(Store(), {DAY: {TICKER}}, client=client)
        self.assertEqual('unavailable', result['status'])
        self.assertEqual(1, len(result['unresolved']))
        self.assertEqual([], result['suspended'])

    def test_wrong_day_duplicate_and_invalid_prices_cannot_be_saved(self):
        for rows in ([dict(ticker=TICKER, date='2026-09-23', close=11, pre_close=10)],
                     [dict(ticker=TICKER, date=DAY, close=11, pre_close=10)]*2,
                     [dict(ticker=TICKER, date=DAY, close=float('nan'), pre_close=10)],
                     [dict(ticker=TICKER, date=DAY, close=11, pre_close=0)]):
            with self.subTest(rows=rows):
                store = Store(); client = MagicMock()
                client.fetch_daily_for_trade_date.return_value = pd.DataFrame(rows)
                result = sync_cohort_prices(store, {DAY: {TICKER}}, client=client)
                self.assertEqual('unavailable', result['status'])
                self.assertEqual([], store.writes)

    def test_same_day_price_limit_reference_is_valid(self):
        store = Store(bars=[dict(ticker=TICKER, date=DAY, close=11)],
                      limits=[dict(ticker=TICKER, trade_date='20260922', pre_close=10)])
        client = MagicMock()
        self.assertEqual('ok', sync_cohort_prices(store, {DAY: {TICKER}}, client=client)['status'])
        client.fetch_daily_for_trade_date.assert_not_called()


if __name__ == '__main__':
    unittest.main()
