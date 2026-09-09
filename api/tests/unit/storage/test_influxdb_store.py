from __future__ import annotations

from datetime import UTC, datetime

from xquant.storage.influxdb import InfluxDBStore


def test_point_to_line_protocol() -> None:
    point = {
        "measurement": "market bar",
        "tags": {"dataset_id": "dataset,1", "symbol": "TEST"},
        "fields": {
            "session_id": "2026-01-01",
            "source_seq": 1,
            "open": 10.0,
            "complete": True,
        },
        "time": datetime(2026, 1, 1, tzinfo=UTC),
    }

    line = InfluxDBStore.point_to_line_protocol(point)

    assert line.startswith('market\\ bar,dataset_id=dataset\\,1,symbol=TEST ')
    assert 'session_id="2026-01-01"' in line
    assert "source_seq=1i" in line
    assert "open=10.0" in line
    assert "complete=true" in line
    assert line.endswith(" 1767225600000000000")
