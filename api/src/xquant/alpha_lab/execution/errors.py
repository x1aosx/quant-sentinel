from __future__ import annotations

from ..errors import AlphaLabError


class ExecutionDisabledError(AlphaLabError):
    """Raised when live execution is disabled by configuration."""


class ExecutionRejectedError(AlphaLabError):
    """Raised when an order is rejected by the execution boundary."""


class RiskRejectedError(ExecutionRejectedError):
    """Raised when the AlphaLab risk gate rejects an order."""
