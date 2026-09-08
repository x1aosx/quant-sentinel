from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from xquant.execution.replay import run_replay
from xquant.marketdata.synthetic import generate_synthetic_bars


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="xquant")
    sub = parser.add_subparsers(dest="command")
    replay = sub.add_parser("replay")
    replay.add_argument("--output", default="artifacts/demo/srpa", type=Path)
    replay.add_argument("--n", default=180, type=int)
    args = parser.parse_args(argv)
    if args.command == "replay":
        bars = generate_synthetic_bars("DEMO.EXAMPLE", n=args.n, seed=42)
        result = run_replay(bars, run_id="cli-demo")
        args.output.mkdir(parents=True, exist_ok=True)
        (args.output / "report.json").write_text(
            json.dumps({"events": result.events, "plans": result.plans, "equity_curve": result.equity_curve, "summary": result.summary}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(json.dumps(result.summary, ensure_ascii=False, indent=2))
        return 0
    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())

