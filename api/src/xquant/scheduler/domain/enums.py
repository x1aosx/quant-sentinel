from __future__ import annotations

from enum import StrEnum


class ExecutionStatus(StrEnum):
    PENDING = "PENDING"
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    RETRYING = "RETRYING"
    TIMEOUT = "TIMEOUT"
    CANCELLED = "CANCELLED"
    SKIPPED = "SKIPPED"


class ConcurrencyPolicy(StrEnum):
    ALLOW = "ALLOW"
    FORBID = "FORBID"
    SERIAL = "SERIAL"
    REPLACE = "REPLACE"


class MisfirePolicy(StrEnum):
    SKIP = "SKIP"
    FIRE_ONCE = "FIRE_ONCE"
    CATCH_UP = "CATCH_UP"


class RetryStrategy(StrEnum):
    FIXED = "fixed"
    LINEAR = "linear"
    EXPONENTIAL = "exponential"


__all__ = [
    "ConcurrencyPolicy",
    "ExecutionStatus",
    "MisfirePolicy",
    "RetryStrategy",
]
