from __future__ import annotations

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from ..errors import SchemaCompatibilityError

CHECKPOINT_FORMAT_VERSION = "alpha-lab-mining-checkpoint-v1"


def encode_payload(value: Any) -> Any:
    if isinstance(value, np.ndarray):
        contiguous = np.ascontiguousarray(value)
        return {
            "__type__": "ndarray",
            "dtype": str(contiguous.dtype),
            "shape": list(contiguous.shape),
            "data": contiguous.reshape(-1).tolist(),
        }
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, Mapping):
        return {str(key): encode_payload(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return {"__type__": "tuple", "items": [encode_payload(item) for item in value]}
    if isinstance(value, list):
        return [encode_payload(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise TypeError(f"checkpoint payload contains unsupported type: {type(value).__name__}")


def decode_payload(value: Any) -> Any:
    if isinstance(value, list):
        return [decode_payload(item) for item in value]
    if not isinstance(value, Mapping):
        return value
    tagged_type = value.get("__type__")
    if tagged_type == "ndarray":
        array = np.asarray(value["data"], dtype=np.dtype(str(value["dtype"])))
        return array.reshape(tuple(int(item) for item in value["shape"]))
    if tagged_type == "tuple":
        return tuple(decode_payload(item) for item in value.get("items", ()))
    return {str(key): decode_payload(item) for key, item in value.items()}


@dataclass(frozen=True)
class TrainingCheckpoint:
    step: int
    model_state: Mapping[str, Any] = field(default_factory=dict)
    optimizer_state: Mapping[str, Any] = field(default_factory=dict)
    elite_pool: Mapping[str, Any] = field(default_factory=dict)
    factor_pool: Mapping[str, Any] = field(default_factory=dict)
    random_state: Mapping[str, Any] = field(default_factory=dict)
    best_formula_tokens: tuple[int, ...] = ()
    best_score: float | None = None
    metrics: Mapping[str, Any] = field(default_factory=dict)
    training_run: Mapping[str, Any] = field(default_factory=dict)
    schema_version: str = ""
    data_snapshot_id: str = ""
    config_snapshot: Mapping[str, Any] = field(default_factory=dict)
    format_version: str = CHECKPOINT_FORMAT_VERSION

    def __post_init__(self) -> None:
        if self.step < 0:
            raise ValueError("checkpoint step cannot be negative")
        if self.format_version != CHECKPOINT_FORMAT_VERSION:
            raise ValueError(f"unsupported checkpoint format: {self.format_version}")

    def to_payload(self) -> dict[str, Any]:
        payload = {
            "format_version": self.format_version,
            "step": self.step,
            "model_state": self.model_state,
            "optimizer_state": self.optimizer_state,
            "elite_pool": self.elite_pool,
            "factor_pool": self.factor_pool,
            "random_state": self.random_state,
            "best_formula_tokens": self.best_formula_tokens,
            "best_score": self.best_score,
            "metrics": self.metrics,
            "training_run": self.training_run,
            "schema_version": self.schema_version,
            "data_snapshot_id": self.data_snapshot_id,
            "config_snapshot": self.config_snapshot,
        }
        encoded = encode_payload(payload)
        # Validate that the complete payload is data-only and JSON serializable.
        json.dumps(encoded, ensure_ascii=True, allow_nan=False)
        return encoded

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> TrainingCheckpoint:
        decoded = decode_payload(dict(payload))
        return cls(
            format_version=str(decoded.get("format_version", "")),
            step=int(decoded.get("step", 0)),
            model_state=dict(decoded.get("model_state", {})),
            optimizer_state=dict(decoded.get("optimizer_state", {})),
            elite_pool=dict(decoded.get("elite_pool", {})),
            factor_pool=dict(decoded.get("factor_pool", {})),
            random_state=dict(decoded.get("random_state", {})),
            best_formula_tokens=tuple(decoded.get("best_formula_tokens", ())),
            best_score=decoded.get("best_score"),
            metrics=dict(decoded.get("metrics", {})),
            training_run=dict(decoded.get("training_run", {})),
            schema_version=str(decoded.get("schema_version", "")),
            data_snapshot_id=str(decoded.get("data_snapshot_id", "")),
            config_snapshot=dict(decoded.get("config_snapshot", {})),
        )

    def verify_schema(self, schema_version: str) -> None:
        if self.schema_version and self.schema_version != schema_version:
            raise SchemaCompatibilityError(
                "checkpoint factor schema mismatch: "
                f"artifact={self.schema_version!r} runtime={schema_version!r}"
            )

    @property
    def key(self) -> str:
        run_id = str(self.training_run.get("id", "unknown"))
        return f"{run_id}:{self.step}"

    def save(self, path: str | Path) -> Path:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(f".{target.name}.tmp")
        with temporary.open("w", encoding="utf-8") as handle:
            json.dump(
                self.to_payload(),
                handle,
                ensure_ascii=True,
                allow_nan=False,
                separators=(",", ":"),
                sort_keys=True,
            )
        os.replace(temporary, target)
        return target

    @classmethod
    def load(cls, path: str | Path) -> TrainingCheckpoint:
        with Path(path).open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
        if not isinstance(payload, Mapping):
            raise TypeError("checkpoint payload must be an object")
        return cls.from_payload(payload)

    def to_dict(self) -> dict[str, Any]:
        return self.to_payload()


def save_checkpoint(checkpoint: TrainingCheckpoint, path: str | Path) -> Path:
    return checkpoint.save(path)


def load_checkpoint(path: str | Path) -> TrainingCheckpoint:
    return TrainingCheckpoint.load(path)


def restore_numpy_generator(
    generator: np.random.Generator,
    random_state: Mapping[str, Any],
) -> None:
    numpy_state = random_state.get("numpy")
    if numpy_state is None:
        raise ValueError("checkpoint does not contain a numpy random state")
    generator.bit_generator.state = decode_payload(numpy_state)


__all__ = [
    "CHECKPOINT_FORMAT_VERSION",
    "TrainingCheckpoint",
    "decode_payload",
    "encode_payload",
    "load_checkpoint",
    "restore_numpy_generator",
    "save_checkpoint",
]
