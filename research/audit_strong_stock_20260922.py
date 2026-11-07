"""Read-only snapshot and as-of regression audit against the repaired archive."""
import json
from pathlib import Path

from shilun.api.review import _open_store
from shilun.review.sources import load_review_sessions
from shilun.review.strong_stock_candidates import evaluate_stock
from shilun.review.strong_stock_data import SNAPSHOTS, DETAILS, iso


def main():
    out = Path(__file__).parent / 'evidence/strong_stock_20260922'
    store = _open_store()
    try:
        snap = store.collection(SNAPSHOTS).find_one({'_id': 'strong-stock-v1:2026-09-22'}, {'_id': 0})
        names = {'600127.SH', '601086.SH', '000993.SZ', '600664.SH', '600721.SH'}
        selected = [row for row in snap['rows'] if row['ticker'] in names]
        summary = {k: snap[k] for k in ('version', 'revision', 'date', 'counts', 'scanned_stocks',
                                       'latest_known_stocks', 'history_from', 'history_sessions')}
        def save(name, value):
            (out / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')
        save('verified_samples.json', {'snapshot': summary, 'samples': selected})
        details = [store.collection(DETAILS).find_one({'_id': row['detail_id']}, {'_id': 0}) for row in selected]
        save('sample_replay.json', details)
        qualified = [row for row in snap['rows'] if row['status'] == 'qualified']
        assert len({row['ticker'] for row in qualified}) == len(qualified) == snap['counts']['qualified']
        for row in qualified:
            leg = row['leg']
            assert leg['boundary_confirmed'] and leg['initial_streak_in_window'] >= 3
            assert 5 <= leg['qualification_board_count'] <= leg['qualification_sessions'] <= 10
            assert leg['start_date'] <= leg['three_board_date'] <= leg['qualified_at'] <= leg['window_end']
            assert leg['qualified_at'] <= snap['date'] and row['tracking_complete']
        ticker = '600127.SH'
        sessions = load_review_sessions(store, '2026-09-22', '2026-04-01')
        bars = {r['date']: r for r in store.collection('market_daily_bars').find(
            {'ticker': ticker, 'date': {'$in': sessions}}, {'_id': 0})}
        limits = {iso(r['trade_date']): r for r in store.collection('daily_price_limits').find(
            {'ticker': ticker, 'trade_date': {'$in': sessions + [d.replace('-', '') for d in sessions]}}, {'_id': 0})}
        replay = []
        for day in ('2026-08-19', '2026-08-24', '2026-08-25', '2026-09-22'):
            result = evaluate_stock(ticker, sessions, day, bars, limits)
            leg, cycle = result['focus_leg'], result['current_cycle']
            replay.append({'date': day, 'status': result['status'], 'cycle_start': cycle['start_date'],
                           'leg_start': leg['start_date'], 'three_board_date': leg['three_board_date'],
                           'qualified_at': leg['qualified_at'], 'board_count': leg['board_count'],
                           'qualification_sessions': leg.get('qualification_sessions'),
                           'cumulative_return': cycle['cumulative_return']})
            assert all(e['date'] <= day for item in result['legs'] for e in item['evidence'])
        assert [r['status'] for r in replay] == ['pending', 'pending', 'qualified', 'qualified']
        assert replay[2]['qualified_at'] == '2026-08-25' and replay[2]['qualification_sessions'] == 7
        assert all(r['cycle_start'] == '2026-08-06' and r['leg_start'] == '2026-08-17' for r in replay)
        save('jinjian_asof_replay.json', replay)
        print(json.dumps({'snapshot': summary, 'samples': [
            {'name': r['name'], 'status': r['status'], 'qualified_at': r['qualified_at']} for r in selected],
            'replay': replay}, ensure_ascii=False))
    finally:
        store.close()


if __name__ == '__main__':
    main()
