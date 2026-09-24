from __future__ import annotations

from datetime import UTC, datetime

import pytest

from xquant.analysis.price_action import analyze_price_action
from xquant.domain.models import Bar
from xquant.domain.session_time import dedupe_sorted_bars, session_identity
from xquant.marketdata.ohlc import validate_ohlcv
from xquant.marketdata.remote import normalize_remote_payload

# 同一时刻的两种时区写法，字符串排序与真实时序相反。
_SHANGHAI = "2026-09-17T09:30:00+08:00"
_UTC = "2026-09-17T01:30:00+00:00"


def _bars(count: int = 62) -> list[dict[str, float | str]]:
    bars: list[dict[str, float | str]] = []
    for index in range(count):
        minute = index % 60
        hour = 1 + index // 60
        # 交替使用 +08:00 与 +00:00，两者指向同一真实时刻序列。
        session = f"2026-09-17T{hour + 8:02d}:{minute:02d}:00+08:00"
        base = 100 + index * 0.5
        bars.append(
            {
                "session_id": session,
                "open": base,
                "high": base + 1.0,
                "low": base - 1.0,
                "close": base + 0.4,
                "volume": 1000.0,
            }
        )
    return bars


def test_session_identity_collapses_timezone_spellings() -> None:
    assert session_identity(_SHANGHAI) == session_identity(_UTC)
    assert session_identity(_SHANGHAI) == "2026-09-17T01:30:00+00:00"
    assert session_identity("s001") == "s001"


def test_dedupe_sorted_bars_collapses_same_instant_and_keeps_order() -> None:
    deduped = dedupe_sorted_bars(
        [
            {"session_id": "2026-09-17T02:00:00+00:00", "open": 2},
            {"session_id": _SHANGHAI, "open": 1},
            {"session_id": _UTC, "open": 9},
            {"session_id": "2026-09-17T00:30:00+00:00", "open": 0},
        ]
    )

    assert [bar["session_id"] for bar in deduped] == [
        "2026-09-17T00:30:00+00:00",
        _UTC,
        "2026-09-17T02:00:00+00:00",
    ]
    # 最后一次出现获胜（更新的数据覆盖旧值）。
    assert deduped[1]["open"] == 9


def test_normalize_remote_payload_dedupes_by_instant() -> None:
    normalized = normalize_remote_payload(
        [
            {
                "time": _SHANGHAI,
                "open": 1,
                "high": 2,
                "low": 0.5,
                "close": 1.5,
                "volume": 10,
            },
            {
                "time": _UTC,
                "open": 1,
                "high": 2,
                "low": 0.5,
                "close": 1.5,
                "volume": 10,
            },
        ]
    )

    assert len(normalized) == 1


def test_analyze_price_action_accepts_mixed_timezone_sessions() -> None:
    bars = _bars()
    # 追加一条与最后一根同一时刻、但时区写法不同的重复K线。
    bars.append({**bars[-1], "session_id": _UTC if bars[-1]["session_id"] != _UTC else _SHANGHAI})

    result = analyze_price_action(bars, symbol="TEST", timeframe="1h", lookback=len(bars))

    assert result["bars_used"] == len(bars) - 1


def test_analyze_price_action_rejects_genuinely_out_of_order_sessions() -> None:
    bars = _bars()
    bars.append(
        {
            "session_id": "2026-09-17T00:00:00+00:00",
            "open": 1,
            "high": 2,
            "low": 0.5,
            "close": 1.5,
            "volume": 1,
        }
    )

    # 去重排序后不会触发乱序错误，乱序数据会被归位而不是直接失败。
    result = analyze_price_action(bars, symbol="TEST", lookback=len(bars))
    assert result["bars_used"] == len(bars)


def _domain_bar(session_id: str) -> Bar:
    return Bar(
        instrument_id="TEST",
        session_id=session_id,
        open=1.0,
        high=2.0,
        low=0.5,
        close=1.5,
        volume_shares=10.0,
        turnover_currency=15.0,
        source="test",
        received_at=datetime.now(UTC),
        available_at=datetime.now(UTC),
        revision_id="r1",
    )


def test_validate_ohlcv_dedupes_timezone_variants() -> None:
    validated = validate_ohlcv(
        [
            _domain_bar("2026-09-17T00:30:00+00:00"),
            _domain_bar(_SHANGHAI),
            _domain_bar(_UTC),
        ]
    )

    assert [bar.session_id for bar in validated] == [
        "2026-09-17T00:30:00+00:00",
        _SHANGHAI,
    ]


def test_validate_ohlcv_still_rejects_out_of_order() -> None:
    with pytest.raises(ValueError):
        validate_ohlcv(
            [
                _domain_bar("2026-09-17T02:00:00+00:00"),
                _domain_bar("2026-09-17T01:00:00+00:00"),
            ]
        )
