"""Explicit dated P2 upgrade. Credentials never printed; old snapshots retained."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from shilun.api.review import _open_store
from shilun.review.service import DailyReviewService
from shilun.review.sources import load_review_sessions, sync_review_limit_pools
from shilun.review.dated_pools import sync_heat_summaries

if __name__ == '__main__':
    store = _open_store()
    try:
        day = sys.argv[1] if len(sys.argv) > 1 else '2026-09-09'
        sessions = load_review_sessions(store, day)
        print(json.dumps({'summaries': sync_heat_summaries(store, sessions)}, ensure_ascii=False), flush=True)
        # Only these audited missing dates; never overwrite existing pools.
        for gap in (() if '--skip-gap-check' in sys.argv else ('2026-08-12', '2026-08-13')):
            if gap in sessions and not store.collection('limit_up_pool').find_one({'trade_date': {'$in': [gap, gap.replace('-', '')]}}):
                print(json.dumps({'gap_repair': sync_review_limit_pools(store, [gap])}, ensure_ascii=False), flush=True)
        print('正在重建所选日期快照；保留旧版本。', flush=True)
        result = DailyReviewService(store).build_and_save(day, sync_sources=False)
        s = result['sentiment']
        print(json.dumps({'date': result['analysis_date'], 'version': result['engine_version'],
            'wave': s['limit_up_wave'], 'risk': s['downside_risk']['penalty'],
            'heat': [{**{k: d[k] for k in ('date', 'limit_up', 'highest_consec', 'broken_rate')},
                'leaders': [r['name'] for r in d['leaders']]} for d in s['heat_history']['days']],
            'themes': [{'name': t['name'], 'peak': t['round']['max_board'], 'start': t['round']['window_start'],
                'leaders': [{k: r.get(k) for k in ('name', 'start_date', 'startup_return', 'price_basis')}
                            for r in t['round']['leaders']]} for t in s['theme_evidence']['themes'][:3]]}, ensure_ascii=False, default=str), flush=True)
    except Exception as error:
        raise SystemExit(f'Materialization failed: {type(error).__name__}') from None
    finally:
        store.close()
