from __future__ import annotations


class AlphaLabError(Exception):
    """Base error for AlphaLab operations."""


class SchemaCompatibilityError(AlphaLabError):
    """Raised when a strategy artifact does not match the active schema."""


class FormulaExecutionError(AlphaLabError):
    """Raised when a token program cannot be executed safely."""


class InsufficientDataError(AlphaLabError):
    """Raised when a computation does not have enough bars."""


class StrategyNotFoundError(AlphaLabError):
    """Raised when a strategy artifact cannot be found."""
