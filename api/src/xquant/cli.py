from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from xquant.execution.replay import run_replay
from xquant.marketdata.repair import DEFAULT_LOOKBACK_FLOOR, repair_dataset_bars
from xquant.marketdata.synthetic import generate_synthetic_bars


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="xquant")
    sub = parser.add_subparsers(dest="command")
    replay = sub.add_parser("replay")
    replay.add_argument("--output", default="artifacts/demo/srpa", type=Path)
    replay.add_argument("--n", default=180, type=int)
    repair = sub.add_parser(
        "repair-bars",
        help="回源重建行情存储（InfluxDB）中丢失的 K 线",
    )
    repair.add_argument(
        "--dataset-id",
        action="append",
        default=None,
        help="只重建指定数据集，可重复；默认重建全部数据集",
    )
    repair.add_argument(
        "--symbol",
        action="append",
        default=None,
        help="只重建指定品种，可重复",
    )
    repair.add_argument(
        "--lookback-floor",
        default=DEFAULT_LOOKBACK_FLOOR,
        type=int,
        help=f"数据集 bar_count 缺失时的最小回源数量（默认 {DEFAULT_LOOKBACK_FLOOR}）",
    )
    repair.add_argument(
        "--dry-run",
        action="store_true",
        help="只打印将要发起的同步请求，不写行情存储",
    )
    args = parser.parse_args(argv)
    if args.command == "replay":
        bars = generate_synthetic_bars("DEMO.EXAMPLE", n=args.n, seed=42)
        result = run_replay(bars, run_id="cli-demo")
        args.output.mkdir(parents=True, exist_ok=True)
        (args.output / "report.json").write_text(
            json.dumps(
                {
                    "events": result.events,
                    "plans": result.plans,
                    "equity_curve": result.equity_curve,
                    "summary": result.summary,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        print(json.dumps(result.summary, ensure_ascii=False, indent=2))
        return 0
    if args.command == "repair-bars":
        return _repair_bars(args)
    parser.print_help()
    return 1


def _repair_bars(args: argparse.Namespace) -> int:
    # 只在真正用到时才导入存储层，保持 replay 等本地命令的启动开销。
    from xquant.registry import Database
    from xquant.storage import StorageSettings

    database = Database.from_settings(StorageSettings.load())
    try:
        if getattr(database, "influx", None) is None:
            print("当前是 legacy_sqlite 后端，K 线不在 InfluxDB 中，无需重建。")
            return 0
        report = repair_dataset_bars(
            database,
            dataset_ids=args.dataset_id,
            symbols=args.symbol,
            lookback_floor=args.lookback_floor,
            dry_run=args.dry_run,
        )
    finally:
        close = getattr(database, "close", None)
        if callable(close):
            close()
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["failed"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
