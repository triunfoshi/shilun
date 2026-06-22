from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path


TEMPLATE_PATH = Path(__file__).resolve().parents[1] / "examples" / "joinquant_shilun_standalone.py"


@dataclass(frozen=True)
class JoinQuantStrategyExportConfig:
    stock_pool: tuple[str, ...] = ()
    benchmark: str = "000300.XSHG"
    lookback: int = 120
    max_positions: int = 4
    target_weight: float = 0.25
    trim_weight: float = 0.125
    max_candidates: int = 300


def _render_stock_pool_literal(stock_pool: tuple[str, ...]) -> str:
    if not stock_pool:
        return "[]"
    lines = ["["]
    for ticker in stock_pool:
        lines.append(f'        "{ticker}",')
    lines.append("    ]")
    return "\n".join(lines)


def render_joinquant_strategy(config: JoinQuantStrategyExportConfig) -> str:
    rendered = TEMPLATE_PATH.read_text(encoding="utf-8")
    replacements = {
        'set_benchmark("000300.XSHG")': f'set_benchmark("{config.benchmark}")',
        'g.benchmark = "000300.XSHG"': f'g.benchmark = "{config.benchmark}"',
        "g.lookback = 120": f"g.lookback = {config.lookback}",
        "g.max_positions = 4": f"g.max_positions = {config.max_positions}",
        "g.target_weight = 0.25": f"g.target_weight = {config.target_weight}",
        "g.trim_weight = 0.125": f"g.trim_weight = {config.trim_weight}",
        "g.max_candidates = 300": f"g.max_candidates = {config.max_candidates}",
        "g.fixed_stock_pool = []": f"g.fixed_stock_pool = {_render_stock_pool_literal(config.stock_pool)}",
    }
    for source, target in replacements.items():
        rendered = rendered.replace(source, target, 1)
    return rendered


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Export a standalone JoinQuant route-A strategy script.")
    parser.add_argument("--output", default="examples/joinquant_strategy.py", help="Path to the generated script.")
    parser.add_argument(
        "--stock",
        dest="stocks",
        action="append",
        default=[],
        help="Optional JoinQuant symbol whitelist. Repeat this flag for multiple symbols.",
    )
    parser.add_argument("--benchmark", default="000300.XSHG")
    parser.add_argument("--lookback", type=int, default=120)
    parser.add_argument("--max-positions", type=int, default=4)
    parser.add_argument("--target-weight", type=float, default=0.25)
    parser.add_argument("--trim-weight", type=float, default=0.125)
    parser.add_argument("--max-candidates", type=int, default=300)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    config = JoinQuantStrategyExportConfig(
        stock_pool=tuple(args.stocks),
        benchmark=args.benchmark,
        lookback=args.lookback,
        max_positions=args.max_positions,
        target_weight=args.target_weight,
        trim_weight=args.trim_weight,
        max_candidates=args.max_candidates,
    )
    rendered = render_joinquant_strategy(config)
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(rendered, encoding="utf-8")
    print(f"exported standalone JoinQuant strategy to {output_path}")


if __name__ == "__main__":
    main()
