from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime


@dataclass
class KillSwitch:
    """Process-local emergency stop checked before every execution attempt."""

    active: bool = False
    reason: str = ""
    activated_at: str = ""
    history: list[str] = field(default_factory=list)

    def activate(self, reason: str = "manual kill switch") -> None:
        self.active = True
        self.reason = str(reason or "manual kill switch")
        self.activated_at = datetime.now(UTC).isoformat()
        self.history.append(self.reason)

    def deactivate(self) -> None:
        self.active = False
        self.reason = ""
        self.activated_at = ""

    def is_active(self) -> bool:
        return self.active
