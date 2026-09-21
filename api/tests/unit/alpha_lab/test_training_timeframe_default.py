from __future__ import annotations

import numpy as np

from xquant.alpha_lab.config import AlphaLabSettings
from xquant.alpha_lab.data import BarFrame
from xquant.alpha_lab.runtime import TrainingManager
from xquant.alpha_lab.strategy.repository import FileStrategyRepository


def _frame(
    symbol: str = "DEMO.RESEARCH",
    timeframe: str = "1d",
    n_bars: int = 260,
) -> BarFrame:
    x = np.arange(n_bars, dtype=np.float64)
    close = 100.0 + np.sin(x / 7.0) * 2.0 + x * 0.03
    open_ = close + np.cos(x / 5.0) * 0.2
    high = np.maximum(open_, close) + 0.5
    low = np.minimum(open_, close) - 0.5
    volume = 1_000.0 + np.sin(x / 3.0) * 50.0 + x
    return BarFrame(
        symbol=symbol,
        timeframe=timeframe,
        open=open_[None, :],
        high=high[None, :],
        low=low[None, :],
        close=close[None, :],
        volume=volume[None, :],
        time=x[None, :],
        is_closed=np.ones((1, n_bars), dtype=bool),
        source="test",
    )


class _FakeMarketData:
    """Minimal MarketDataPort stand-in that never touches a real database."""

    def __init__(self, datasets: dict[str, dict]) -> None:
        self._datasets = datasets

    def get_dataset_metadata(self, dataset_id: str) -> dict:
        return dict(self._datasets[dataset_id])

    def load_bars(
        self,
        symbol: str,
        timeframe: str,
        **kwargs: object,
    ) -> BarFrame:
        dataset_id = kwargs.get("dataset_id")
        if dataset_id:
            meta = self._datasets[dataset_id]
            return _frame(symbol=meta["symbol"], timeframe=meta["timeframe"])
        return _frame(symbol=symbol, timeframe=timeframe)


def _build_manager(
    tmp_path,
    market_data: _FakeMarketData,
) -> TrainingManager:
    settings = AlphaLabSettings(artifact_root=tmp_path / "alpha_lab")
    strategies = FileStrategyRepository(settings)
    return TrainingManager(settings, market_data, strategies)


def _noop_train(
    self: TrainingManager,
    *args: object,
    **kwargs: object,
) -> None:
    """Swallow background training so the test only asserts on create_run output."""


def test_default_timeframe_with_dataset(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(TrainingManager, "_train", _noop_train)
    market_data = _FakeMarketData(
        {"ds-1d": {"symbol": "DEMO.RESEARCH", "timeframe": "1d", "title": "Daily"}}
    )
    manager = _build_manager(tmp_path, market_data)

    created = manager.create_run(
        {
            "data_snapshot_id": "ds-1d",
            "min_bars": 60,
            "total_steps": 1,
            "batch_size": 2,
            "seed": 7,
        }
    )

    assert created["timeframe"] == "1d"


def test_default_timeframe_with_symbols_only(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(TrainingManager, "_train", _noop_train)
    market_data = _FakeMarketData({})
    manager = _build_manager(tmp_path, market_data)

    created = manager.create_run(
        {
            "symbols": ["DEMO.RESEARCH"],
            "min_bars": 60,
            "total_steps": 1,
            "batch_size": 2,
            "seed": 7,
        }
    )

    assert created["timeframe"] == "1d"


def test_dataset_timeframe_overrides_default(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(TrainingManager, "_train", _noop_train)
    market_data = _FakeMarketData(
        {
            "ds-15m": {
                "symbol": "DEMO.RESEARCH",
                "timeframe": "15m",
                "title": "Intraday",
            }
        }
    )
    manager = _build_manager(tmp_path, market_data)

    created = manager.create_run(
        {
            "data_snapshot_id": "ds-15m",
            "min_bars": 60,
            "total_steps": 1,
            "batch_size": 2,
            "seed": 7,
        }
    )

    assert created["timeframe"] == "15m"