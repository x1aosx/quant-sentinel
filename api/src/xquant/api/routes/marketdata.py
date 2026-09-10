from __future__ import annotations

import csv
import io
import re

from fastapi import APIRouter, Query, Response

from xquant.marketdata.synthetic import generate_synthetic_bars

from ..validation import required_text

router = APIRouter(tags=["marketdata"])


@router.get("/marketdata/quotes/download", summary="下载确定性演示行情 CSV")
def download_quotes(
    symbol: str = Query(...),
    timeframe: str = Query(...),
    count: int = Query(default=180, ge=60, le=10_000),
) -> Response:
    symbol_text = required_text(symbol, "品种名称")
    required_text(timeframe, "周期")

    bars = generate_synthetic_bars(symbol_text, n=count, seed=42)
    output = io.StringIO(newline="")
    writer = csv.DictWriter(
        output,
        fieldnames=["session_id", "open", "high", "low", "close", "volume"],
        extrasaction="ignore",
    )
    writer.writeheader()
    for bar in bars:
        writer.writerow(
            {
                "session_id": bar.session_id,
                "open": bar.open,
                "high": bar.high,
                "low": bar.low,
                "close": bar.close,
                "volume": bar.volume_shares,
            }
        )

    return Response(
        content=output.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": (
                f'attachment; filename="{re.sub(r"[^A-Za-z0-9._-]", "_", symbol_text)}_'
                f'{re.sub(r"[^A-Za-z0-9._-]", "_", timeframe)}.csv"'
            )
        },
    )
