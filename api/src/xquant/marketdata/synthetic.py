from __future__ import annotations

from datetime import datetime, timedelta
from typing import Iterable

import numpy as np

from xquant.domain.models import Bar


def generate_synthetic_bars(
    instrument_id: str,
    n: int = 180,
    seed: int = 42,
    start: datetime | None = None,
    source: str = "demo",
) -> list[Bar]:
    """Generate deterministic synthetic daily bars for DEMO replay only."""
    rng = np.random.default_rng(seed)
    start = start or datetime(2026, 1, 1, 8, 0, tzinfo=__import__("zoneinfo").ZoneInfo("Asia/Shanghai"))
    closes = 10.0 + np.cumsum(rng.normal(0.0, 0.12, n))
    closes = np.maximum(closes, 1.0)
    bars: list[Bar] = []
    prev_close = closes[0] * 0.98
    for i in range(n):
        open_ = float(prev_close + rng.normal(0, 0.02))
        close = float(closes[i])
        span = float(abs(rng.normal(0.05, 0.02)))
        high = float(max(open_, close) + span)
        low = float(min(open_, close) - span)
        volume = float(rng.integers(500_000, 3_000_000))
        session = f"S{i+1:04d}"
        available_at = start + timedelta(days=i)
        bars.append(
            Bar(
                instrument_id=instrument_id,
                session_id=session,
                open=open_,
                high=high,
                low=low,
                close=close,
                volume_shares=volume,
                turnover_currency=volume * close,
                source=source,
                received_at=available_at,
                available_at=available_at,
                revision_id=f"{source}-{seed}-{i}",
            )
        )
        prev_close = close
    return bars

