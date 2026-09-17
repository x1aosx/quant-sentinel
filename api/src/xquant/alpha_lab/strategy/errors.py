from __future__ import annotations

from ..errors import AlphaLabError


class StrategyIntegrityError(AlphaLabError):
    """Raised when an artifact's declared hash does not match its content."""


class StrategyVersionConflictError(StrategyIntegrityError):
    """Raised when an immutable strategy version is written with new content."""


class InvalidStrategyTransitionError(StrategyIntegrityError):
    """Raised when a strategy lifecycle transition is not allowed."""
