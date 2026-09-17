from __future__ import annotations

import hashlib
from dataclasses import dataclass

from ..errors import SchemaCompatibilityError
from .feature_registry import FEATURE_REGISTRY
from .operator_registry import OPERATOR_REGISTRY


def compute_vocab_version(
    token_names: tuple[str, ...],
    runtime_version: str = "xqs-alpha-runtime-v1",
) -> str:
    semantic_payload = "\n".join((runtime_version, *token_names))
    digest = hashlib.sha256(semantic_payload.encode("utf-8")).hexdigest()
    return f"xqs-v{digest[:12]}"


@dataclass(frozen=True)
class FactorSchema:
    schema_version: str
    feature_registry_hash: str
    operator_registry_hash: str
    token_names: tuple[str, ...]
    runtime_version: str = "xqs-alpha-runtime-v1"

    def verify(self, artifact_version: str) -> None:
        if artifact_version != self.schema_version:
            raise SchemaCompatibilityError(
                f"factor schema mismatch: artifact={artifact_version!r} "
                f"runtime={self.schema_version!r}"
            )


def _registry_hash(values: tuple[str, ...]) -> str:
    return hashlib.sha256("\n".join(values).encode("utf-8")).hexdigest()[:16]


def _build_schema() -> FactorSchema:
    feature_names = FEATURE_REGISTRY.names
    operator_names = OPERATOR_REGISTRY.names
    overlap = set(feature_names) & set(operator_names)
    if overlap:
        raise ValueError(f"feature/operator names overlap: {sorted(overlap)}")
    token_names = feature_names + operator_names
    if len(set(token_names)) != len(token_names):
        raise ValueError("factor vocabulary contains duplicate token names")
    return FactorSchema(
        schema_version=compute_vocab_version(token_names),
        feature_registry_hash=_registry_hash(feature_names),
        operator_registry_hash=_registry_hash(operator_names),
        token_names=token_names,
    )


FORMULA_VOCAB = _build_schema()
