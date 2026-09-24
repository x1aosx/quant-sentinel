from __future__ import annotations

from collections.abc import Iterable

from xquant.domain.models import Bar
from xquant.domain.session_time import parse_session_time


def validate_ohlcv(bars: Iterable[Bar]) -> list[Bar]:
    result: list[Bar] = []
    previous_session: str | None = None
    previous_time = None
    for bar in bars:
        bar.validate()
        session_time = parse_session_time(bar.session_id)
        if previous_session is not None:
            if session_time is not None and previous_time is not None:
                if session_time < previous_time:
                    raise ValueError(f"Out-of-order or duplicate session: {bar.session_id}")
                if session_time == previous_time:
                    continue
            elif bar.session_id <= previous_session:
                raise ValueError(f"Out-of-order or duplicate session: {bar.session_id}")
        result.append(bar)
        previous_session = bar.session_id
        if session_time is not None:
            previous_time = session_time
    return result

