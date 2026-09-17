from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

from ..domain import StrategyArtifact, StrategyStatus
from ..errors import SchemaCompatibilityError
from ..factor import FORMULA_VOCAB, FactorRuntime, SignalKernel
from .errors import StrategyIntegrityError

ALPHAMASTER_SCHEMA_VERSION = "alpha-lab-strategy-v1"
_MUTABLE_FIELDS = {"content_hash", "status", "created_at"}


def _canonical_json(payload: Mapping[str, Any]) -> str:
    return json.dumps(
        payload,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def compute_content_hash(artifact: StrategyArtifact) -> str:
    """Hash immutable strategy semantics while allowing lifecycle transitions."""

    payload = artifact.to_dict()
    for field_name in _MUTABLE_FIELDS:
        payload.pop(field_name, None)
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def ensure_artifact_hash(artifact: StrategyArtifact) -> StrategyArtifact:
    expected = compute_content_hash(artifact)
    if artifact.content_hash and artifact.content_hash != expected:
        raise StrategyIntegrityError(
            f"strategy content hash mismatch: declared={artifact.content_hash!r} "
            f"computed={expected!r}"
        )
    return replace(artifact, content_hash=expected)


def validate_artifact_schema(
    artifact: StrategyArtifact,
    *,
    expected_schema_version: str | None = None,
) -> None:
    expected = expected_schema_version or FORMULA_VOCAB.schema_version
    FORMULA_VOCAB.verify(artifact.factor_schema_version)
    if artifact.factor_schema_version != expected:
        raise SchemaCompatibilityError(
            "factor schema mismatch: "
            f"artifact={artifact.factor_schema_version!r} expected={expected!r}"
        )
    if artifact.signal_kernel != SignalKernel.version:
        raise SchemaCompatibilityError(
            "signal kernel mismatch: "
            f"artifact={artifact.signal_kernel!r} runtime={SignalKernel.version!r}"
        )
    if not artifact.formula_tokens:
        raise StrategyIntegrityError("strategy formula cannot be empty")


def _normalize_status(value: Any) -> StrategyStatus:
    if isinstance(value, StrategyStatus):
        return value
    normalized = str(value or StrategyStatus.CANDIDATE.value).strip().lower()
    try:
        return StrategyStatus(normalized)
    except ValueError as exc:
        raise StrategyIntegrityError(f"unknown strategy status: {value!r}") from exc


def _metadata_value(payload: Mapping[str, Any], key: str, default: Any) -> Any:
    metadata = payload.get("metadata")
    if isinstance(metadata, Mapping) and key in metadata:
        return metadata[key]
    return payload.get(key, default)


def _load_payload(payload: Any) -> Any:
    if isinstance(payload, (str, bytes, bytearray)):
        try:
            return json.loads(payload)
        except json.JSONDecodeError as exc:
            raise StrategyIntegrityError(f"invalid AlphaMaster strategy JSON: {exc}") from exc
    return payload


def artifact_from_alphamaster_json(
    payload: Any,
    *,
    strategy_id: str | None = None,
    version: str = "1.0.0",
    name: str | None = None,
    schema_version: str | None = None,
    status: StrategyStatus | str = StrategyStatus.CANDIDATE,
    metadata: Mapping[str, Any] | None = None,
) -> StrategyArtifact:
    """Import legacy AlphaMaster list/dict strategy JSON with strict schema checks."""

    data = _load_payload(payload)
    if isinstance(data, Mapping):
        raw_formula = data.get("formula_tokens") or data.get("formula")
        imported_status = data.get("status", status)
        artifact_schema = (
            data.get("factor_schema_version")
            or data.get("vocab_version")
            or data.get("schema_version")
        )
        resolved_id = str(
            strategy_id
            or data.get("strategy_id")
            or data.get("id")
            or data.get("name")
            or ""
        ).strip()
        resolved_version = str(data.get("version") or version).strip()
        resolved_name = str(name or data.get("name") or resolved_id).strip()
        symbol = str(data.get("symbol") or _metadata_value(data, "symbol", "UNKNOWN"))
        timeframe = str(
            data.get("timeframe") or _metadata_value(data, "timeframe", "1d")
        )
        signal_kernel = str(
            data.get("signal_kernel_version")
            or data.get("signal_kernel")
            or SignalKernel.version
        )
        min_exposure = float(
            data.get("min_exposure")
            or _metadata_value(data, "min_exposure", 0.05)
        )
        imported_metadata = dict(metadata or {})
        raw_metadata = data.get("metadata")
        if isinstance(raw_metadata, Mapping):
            imported_metadata.update(raw_metadata)
        for field_name in ("best_score", "train_step", "data_file", "mode", "market"):
            if field_name in data:
                imported_metadata.setdefault(field_name, data[field_name])
    elif isinstance(data, Sequence) and not isinstance(data, (str, bytes, bytearray)):
        raw_formula = data
        artifact_schema = None
        resolved_id = str(strategy_id or "").strip()
        resolved_version = str(version).strip()
        resolved_name = str(name or resolved_id).strip()
        symbol = str(_metadata_value(metadata or {}, "symbol", "UNKNOWN"))
        timeframe = str(_metadata_value(metadata or {}, "timeframe", "1d"))
        signal_kernel = SignalKernel.version
        min_exposure = float(_metadata_value(metadata or {}, "min_exposure", 0.05))
        imported_metadata = dict(metadata or {})
    else:
        raise StrategyIntegrityError("AlphaMaster strategy JSON must be a list or object")

    if not resolved_id:
        raise StrategyIntegrityError("strategy_id is required for AlphaMaster import")
    if not resolved_version:
        raise StrategyIntegrityError("strategy version cannot be empty")
    if not isinstance(raw_formula, Sequence) or isinstance(
        raw_formula, (str, bytes, bytearray)
    ):
        raise StrategyIntegrityError("strategy formula must be a token sequence")
    formula_tokens = tuple(int(token) for token in raw_formula)
    if not formula_tokens:
        raise StrategyIntegrityError("strategy formula cannot be empty")

    expected_schema = schema_version or FORMULA_VOCAB.schema_version
    if artifact_schema is None:
        artifact_schema = expected_schema
    if str(artifact_schema) != expected_schema:
        raise SchemaCompatibilityError(
            f"factor schema mismatch: artifact={artifact_schema!r} "
            f"runtime={expected_schema!r}"
        )

    artifact = StrategyArtifact(
        strategy_id=resolved_id,
        version=resolved_version,
        name=resolved_name or resolved_id,
        symbol=symbol,
        timeframe=timeframe,
        formula_tokens=formula_tokens,
        factor_schema_version=str(artifact_schema),
        signal_kernel=signal_kernel,
        min_exposure=min_exposure,
        status=_normalize_status(imported_status),
        data_snapshot_id=str(
            _metadata_value(data, "data_snapshot_id", "")
            if isinstance(data, Mapping)
            else ""
        ),
        training_run_id=str(
            _metadata_value(data, "training_run_id", "")
            if isinstance(data, Mapping)
            else ""
        ),
        created_at=str(
            data.get("created_at", datetime.now(UTC).isoformat())
            if isinstance(data, Mapping)
            else datetime.now(UTC).isoformat()
        ),
        metadata=imported_metadata,
    )
    artifact = ensure_artifact_hash(artifact)
    validate_artifact_schema(artifact, expected_schema_version=expected_schema)
    return artifact


def artifact_to_alphamaster_json(
    artifact: StrategyArtifact,
    *,
    include_metadata: bool = True,
) -> dict[str, Any]:
    """Export an artifact in a format understood by AlphaMaster and AlphaLab."""

    artifact = ensure_artifact_hash(artifact)
    try:
        formula_decoded = FactorRuntime().expression(artifact.formula_tokens)
    except Exception:  # noqa: BLE001 - export remains useful for diagnostics
        formula_decoded = ""
    payload: dict[str, Any] = {
        "schema_version": ALPHAMASTER_SCHEMA_VERSION,
        "id": artifact.strategy_id,
        "strategy_id": artifact.strategy_id,
        "name": artifact.name,
        "version": artifact.version,
        "status": artifact.status.value,
        "symbol": artifact.symbol,
        "timeframe": artifact.timeframe,
        "formula": list(artifact.formula_tokens),
        "formula_tokens": list(artifact.formula_tokens),
        "formula_decoded": formula_decoded,
        "vocab_version": artifact.factor_schema_version,
        "factor_schema_version": artifact.factor_schema_version,
        "signal_kernel": artifact.signal_kernel,
        "signal_kernel_version": artifact.signal_kernel,
        "min_exposure": artifact.min_exposure,
        "data_snapshot_id": artifact.data_snapshot_id,
        "training_run_id": artifact.training_run_id,
        "content_hash": artifact.content_hash,
        "created_at": artifact.created_at,
    }
    if include_metadata:
        payload["metadata"] = dict(artifact.metadata)
        if "best_score" in artifact.metadata:
            payload["best_score"] = artifact.metadata["best_score"]
        if "train_step" in artifact.metadata:
            payload["train_step"] = artifact.metadata["train_step"]
    return payload
