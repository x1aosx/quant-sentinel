from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return float(value)
    except ValueError:
        return default


@dataclass(frozen=True)
class MiningSettings:
    """Deterministic defaults used by the clean-room AlphaLab implementation."""

    training_min_bars: int = 3_000
    realtime_min_bars: int = 800
    max_formula_length: int = 12
    min_formula_length: int = 3
    min_exposure: float = 0.05
    learning_rate: float = 0.02
    entropy_weight: float = 0.01
    elite_fraction: float = 0.2
    walk_forward_train_bars: int = 360
    walk_forward_validation_bars: int = 90
    walk_forward_gap_bars: int = 5
    walk_forward_folds: int = 3
    holdout_bars: int = 120
    seed: int = 42


@dataclass(frozen=True)
class AlphaLabSettings:
    enabled: bool = True
    execution_enabled: bool = False
    artifact_root: Path = Path("data/alpha_lab")
    snapshot_root: Path = Path("data/alpha_lab/snapshots")
    mining: MiningSettings = MiningSettings()

    @classmethod
    def from_env(cls) -> AlphaLabSettings:
        return cls(
            enabled=_env_bool("ALPHA_LAB_ENABLED", True),
            execution_enabled=_env_bool("ALPHA_LAB_EXECUTION_ENABLED", False),
            artifact_root=Path(
                os.getenv("ALPHA_LAB_ARTIFACT_ROOT", "data/alpha_lab")
            ),
            snapshot_root=Path(
                os.getenv("ALPHA_LAB_SNAPSHOT_ROOT", "data/alpha_lab/snapshots")
            ),
            mining=MiningSettings(
                training_min_bars=_env_int("ALPHA_LAB_TRAINING_MIN_BARS", 3_000),
                realtime_min_bars=_env_int("ALPHA_LAB_REALTIME_MIN_BARS", 800),
                max_formula_length=_env_int("ALPHA_LAB_MAX_FORMULA_LENGTH", 12),
                min_formula_length=_env_int("ALPHA_LAB_MIN_FORMULA_LENGTH", 3),
                min_exposure=_env_float("ALPHA_LAB_MIN_EXPOSURE", 0.05),
                learning_rate=_env_float("ALPHA_LAB_LEARNING_RATE", 0.02),
                entropy_weight=_env_float("ALPHA_LAB_ENTROPY_WEIGHT", 0.01),
                elite_fraction=_env_float("ALPHA_LAB_ELITE_FRACTION", 0.2),
                seed=_env_int("ALPHA_LAB_SEED", 42),
            ),
        )
