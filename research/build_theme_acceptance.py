"""CLI compatibility wrapper for the production acceptance builder."""
import argparse
from pathlib import Path
from shilun.review.theme_acceptance_builder import build, compact_candidate

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=Path('research/evidence/theme_selection_20260923'))
    parser.add_argument('--output', type=Path, default=Path('runtime/theme_acceptance'))
    args = parser.parse_args()
    build(args.source, args.output)
