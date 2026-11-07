"""Materialize the shared cycle archive after input collection, without a new timer.

Source inputs must span a stable start/warmup through the requested end; rebuilding
only the last day would lose incumbents and is deliberately unsupported here.
"""
import argparse
from pathlib import Path
from shilun.review.theme_cycle_store import build, ROOT


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--output', type=Path, default=ROOT)
    args = parser.parse_args(argv)
    result = build(args.source, args.output)
    print(f'theme_cycles revision={result["revision"]} days={len(result["dates"])} last={result["dates"][-1]}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
