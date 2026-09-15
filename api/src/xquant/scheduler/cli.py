from __future__ import annotations

import argparse
import asyncio
import signal
import sys
from collections.abc import Sequence

from xquant.marketdata.tasks import register_market_tasks
from xquant.registry import Database
from xquant.scheduler.application import TaskRegistry
from xquant.storage import StorageSettings

from .runtime import SchedulerRuntime, build_scheduler_runtime


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="xquant-scheduler")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("scheduler", help="run the scheduler engine and recovery")
    worker = subparsers.add_parser("worker", help="run a Redis task worker")
    worker.add_argument(
        "--queue",
        dest="queues",
        action="append",
        help="queue to consume; repeat for multiple queues",
    )
    worker.add_argument("--concurrency", type=int)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "scheduler":
        return asyncio.run(_run_scheduler())
    return asyncio.run(
        _run_worker(
            queues=args.queues,
            concurrency=args.concurrency,
        )
    )


async def _run_scheduler() -> int:
    runtime, _db = _load_runtime()
    await runtime.start(
        start_engine=True,
        start_worker=False,
        start_recovery=True,
    )
    try:
        await _wait_for_signal()
    finally:
        await runtime.shutdown()
    return 0


async def _run_worker(
    *,
    queues: list[str] | None = None,
    concurrency: int | None = None,
) -> int:
    runtime, _db = _load_runtime()
    if runtime.worker is None:
        raise SystemExit("worker requires scheduler.dispatcher_type=redis")
    if queues:
        runtime.worker.config = runtime.worker.config.__class__(
            queues=tuple(queues),
            concurrency=(
                runtime.worker.config.concurrency
                if concurrency is None
                else concurrency
            ),
            poll_interval=runtime.worker.config.poll_interval,
            graceful_shutdown_timeout=(
                runtime.worker.config.graceful_shutdown_timeout
            ),
        )
    elif concurrency is not None:
        runtime.worker.config = runtime.worker.config.__class__(
            queues=runtime.worker.config.queues,
            concurrency=concurrency,
            poll_interval=runtime.worker.config.poll_interval,
            graceful_shutdown_timeout=(
                runtime.worker.config.graceful_shutdown_timeout
            ),
        )
    await runtime.start(
        start_engine=False,
        start_worker=True,
        start_recovery=False,
    )
    try:
        await _wait_for_signal()
    finally:
        await runtime.shutdown()
    return 0


def _load_runtime() -> tuple[SchedulerRuntime, object]:
    settings = StorageSettings.load()
    db = Database.from_settings(settings)
    registry = TaskRegistry()
    register_market_tasks(registry, db)
    runtime = build_scheduler_runtime(db, settings, registry=registry)
    if runtime is None:
        raise SystemExit("scheduler is disabled (set SCHEDULER_ENABLED=true)")
    return runtime, db


async def _wait_for_signal() -> None:
    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    installed: list[signal.Signals] = []
    for signum in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(signum, stop_event.set)
        except (NotImplementedError, RuntimeError):
            continue
        installed.append(signum)
    try:
        await stop_event.wait()
    finally:
        for signum in installed:
            loop.remove_signal_handler(signum)


def scheduler_main() -> None:
    raise SystemExit(main(["scheduler"]))


def worker_main() -> None:
    raise SystemExit(main(["worker", *sys.argv[1:]]))


if __name__ == "__main__":
    raise SystemExit(main())
