from __future__ import annotations

from typing import Iterable

from xquant.domain.models import Bar


def validate_ohlcv(bars: Iterable[Bar]) -> list[Bar]:
    result: list[Bar] = []
    previous_session: str | None = None
    for bar in bars:
        bar.validate()
        if previous_session is not None and bar.session_id <= previous_session:
            raise ValueError(f"Out-of-order or duplicate session: {bar.session_id}")
        result.append(bar)
        previous_session = bar.session_id
    return result

