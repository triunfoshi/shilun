"""Build the decision pilot from a dated audit export, without fetching prices."""
from datetime import date
from pathlib import Path
import argparse
import gzip
import json

from shilun.decision_research.data import profile
from shilun.decision_research.engine import build_plan
from shilun.decision_research.mode_research import build_mode_research
from shilun.decision_research.service import save_plan, save_mode_research


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--publish-local', action='store_true', help='Save a frozen snapshot for the local app')
    args = parser.parse_args()
    with gzip.open(args.input, 'rt') as f:
        data = json.load(f)
    if not data.get('next_date'):
        next_dates = {r['next_date'] for r in data.get('core_snapshots', []) if r.get('date') == data['date'] and r.get('next_date')}
        if len(next_dates) == 1:
            candidate = next_dates.pop()
            if date.fromisoformat(candidate) > date.fromisoformat(data['date']):
                data['next_date'] = candidate
                data['next_date_source'] = '同日强股快照保存的下一交易日历'
    research = build_mode_research(data)
    if args.publish_local:
        research = save_mode_research(research)
    result = build_plan(data, research)
    result['provenance']['next_date_source'] = data.get('next_date_source', '已核验交易日历')
    output = args.input.parent / 'plan.json'
    output.with_name('mode-research.json').write_text(json.dumps(research, ensure_ascii=False, allow_nan=False))
    output.write_text(json.dumps(result, ensure_ascii=False, allow_nan=False))
    if args.publish_local:
        saved = save_plan(result, profile(data))
        if saved['built_at'] != result['built_at']:
            print('本地已有冻结版本，保持原预案；本次重建结果仅保存到研究目录。')
    print(json.dumps({'date': result['analysis_date'], 'next_date': result['next_date'],
        'route': result['route'], 'selected': len(result['candidates']), 'output': str(output)}, ensure_ascii=False))


if __name__ == '__main__':
    main()
