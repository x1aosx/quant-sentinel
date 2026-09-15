from __future__ import annotations

import random
from collections.abc import Sequence
from dataclasses import dataclass

from .enums import RetryStrategy


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    """Retry delays for failed task attempts."""

    max_attempts: int = 3
    strategy: RetryStrategy | str = RetryStrategy.EXPONENTIAL
    initial_delay_seconds: float = 5.0
    max_delay_seconds: float = 300.0
    multiplier: float = 2.0
    jitter: bool = True
    retry_on: Sequence[str] | None = None
    no_retry_on: Sequence[str] | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "strategy", RetryStrategy(self.strategy))
        if self.retry_on is not None:
            object.__setattr__(self, "retry_on", tuple(self.retry_on))
        if self.no_retry_on is not None:
            object.__setattr__(self, "no_retry_on", tuple(self.no_retry_on))

        if self.max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")
        if self.initial_delay_seconds < 0:
            raise ValueError("initial_delay_seconds cannot be negative")
        if self.max_delay_seconds < 0:
            raise ValueError("max_delay_seconds cannot be negative")
        if self.multiplier <= 0:
            raise ValueError("multiplier must be positive")

    def can_retry(self, failed_attempts: int) -> bool:
        """Return whether another attempt is allowed after ``failed_attempts`` failures."""

        if failed_attempts < 0:
            raise ValueError("failed_attempts cannot be negative")
        return failed_attempts < self.max_attempts

    def delay_for(self, attempt: int) -> float:
        """Return the bounded delay before retry number ``attempt``.

        ``attempt`` is one-based. A value of 1 is the delay before the first
        retry. Jitter samples a uniform delay in ``[0, bounded_delay]`` so a
        large number of failed tasks do not wake up together.
        """

        if attempt < 1:
            raise ValueError("attempt must be at least 1")

        if self.strategy is RetryStrategy.FIXED:
            delay = self.initial_delay_seconds
        elif self.strategy is RetryStrategy.LINEAR:
            delay = self.initial_delay_seconds * attempt
        else:
            delay = self.initial_delay_seconds * (self.multiplier ** (attempt - 1))

        bounded_delay = min(max(delay, 0.0), self.max_delay_seconds)
        if not self.jitter:
            return bounded_delay
        return min(random.uniform(0.0, bounded_delay), self.max_delay_seconds)


__all__ = ["RetryPolicy"]
