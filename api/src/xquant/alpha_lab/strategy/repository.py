from __future__ import annotations

import json
import os
import re
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import replace
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from ..config import AlphaLabSettings
from ..domain import StrategyArtifact, StrategyStatus
from .artifact import (
    artifact_from_alphamaster_json,
    artifact_to_alphamaster_json,
    ensure_artifact_hash,
    validate_artifact_schema,
)
from .errors import InvalidStrategyTransitionError, StrategyVersionConflictError

_SAFE_COMPONENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_TRANSITIONS: dict[StrategyStatus, frozenset[StrategyStatus]] = {
    StrategyStatus.CANDIDATE: frozenset({StrategyStatus.VALIDATED, StrategyStatus.RETIRED}),
    StrategyStatus.VALIDATED: frozenset({StrategyStatus.PRODUCTION, StrategyStatus.RETIRED}),
    StrategyStatus.PRODUCTION: frozenset({StrategyStatus.RETIRED}),
    StrategyStatus.RETIRED: frozenset(),
}


def _validate_component(value: str, label: str) -> str:
    normalized = str(value or "").strip()
    if not _SAFE_COMPONENT.fullmatch(normalized):
        raise ValueError(f"invalid {label}: {value!r}")
    return normalized


def _version_key(value: str) -> tuple[int, ...]:
    parts = re.split(r"[._-]", value)
    numbers = tuple(int(part) for part in parts if part.isdigit())
    return numbers or (0,)


@runtime_checkable
class StrategyRepository(Protocol):
    def save(self, artifact: StrategyArtifact, **kwargs: Any) -> StrategyArtifact: ...

    def get(self, strategy_id: str, version: str | None = None) -> StrategyArtifact: ...

    def list(
        self,
        *,
        status: StrategyStatus | str | None = None,
    ) -> list[StrategyArtifact]: ...

    def promote(
        self,
        strategy_id: str,
        version: str | StrategyStatus | None = None,
        target_status: StrategyStatus | str = StrategyStatus.PRODUCTION,
    ) -> StrategyArtifact: ...


class FileStrategyRepository:
    """Immutable, file-backed strategy version repository."""

    def __init__(
        self,
        settings: AlphaLabSettings | None = None,
        *,
        root: str | Path | None = None,
    ) -> None:
        self.settings = settings or AlphaLabSettings()
        self.root = Path(root) if root is not None else self.settings.artifact_root / "strategies"

    def _path(self, strategy_id: str, version: str) -> Path:
        safe_id = _validate_component(strategy_id, "strategy_id")
        safe_version = _validate_component(version, "version")
        return self.root / safe_id / f"{safe_version}.json"

    def _write(self, path: Path, artifact: StrategyArtifact) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = artifact_to_alphamaster_json(artifact)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{path.name}.",
            suffix=".tmp",
            dir=path.parent,
        )
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_name, path)
        finally:
            if os.path.exists(temporary_name):
                os.unlink(temporary_name)

    def _read(self, path: Path) -> StrategyArtifact:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"cannot load strategy artifact {path}: {exc}") from exc
        artifact = artifact_from_alphamaster_json(payload)
        if not path.stem == artifact.version:
            raise StrategyVersionConflictError(
                f"strategy file version mismatch: path={path.stem!r} payload={artifact.version!r}"
            )
        return ensure_artifact_hash(artifact)

    def save(
        self,
        artifact: StrategyArtifact,
        *,
        allow_status_update: bool = False,
    ) -> StrategyArtifact:
        artifact = ensure_artifact_hash(artifact)
        validate_artifact_schema(artifact)
        path = self._path(artifact.strategy_id, artifact.version)
        if path.exists():
            existing = self._read(path)
            if existing.content_hash != artifact.content_hash:
                raise StrategyVersionConflictError(
                    f"strategy version is immutable: {artifact.strategy_id}@{artifact.version}"
                )
            if existing.status is artifact.status:
                return existing
            if not allow_status_update:
                return existing
            artifact = replace(
                existing,
                status=artifact.status,
                created_at=existing.created_at,
                content_hash=existing.content_hash,
            )
        self._write(path, artifact)
        return artifact

    def get(self, strategy_id: str, version: str | None = None) -> StrategyArtifact:
        safe_id = _validate_component(strategy_id, "strategy_id")
        if version is not None:
            path = self._path(safe_id, version)
            if not path.exists():
                raise KeyError(f"strategy not found: {safe_id}@{version}")
            return self._read(path)

        directory = self.root / safe_id
        files = sorted(directory.glob("*.json"), key=lambda item: _version_key(item.stem))
        if not files:
            raise KeyError(f"strategy not found: {safe_id}")
        return self._read(files[-1])

    def list(
        self,
        *,
        status: StrategyStatus | str | None = None,
    ) -> list[StrategyArtifact]:
        expected = status
        if isinstance(expected, str):
            expected = StrategyStatus(expected.lower())
        artifacts: list[StrategyArtifact] = []
        if not self.root.exists():
            return artifacts
        for path in sorted(self.root.glob("*/*.json")):
            artifact = self._read(path)
            if expected is None or artifact.status is expected:
                artifacts.append(artifact)
        return sorted(
            artifacts,
            key=lambda item: (item.strategy_id, _version_key(item.version), item.created_at),
        )

    def promote(
        self,
        strategy_id: str,
        version: str | StrategyStatus | None = None,
        target_status: StrategyStatus | str = StrategyStatus.PRODUCTION,
    ) -> StrategyArtifact:
        resolved_version: str | None
        if isinstance(version, StrategyStatus):
            resolved_version = None
            target_status = version
        else:
            resolved_version = version
        target = (
            target_status
            if isinstance(target_status, StrategyStatus)
            else StrategyStatus(str(target_status).lower())
        )
        artifact = self.get(strategy_id, resolved_version)
        if target not in _TRANSITIONS[artifact.status]:
            raise InvalidStrategyTransitionError(
                f"invalid strategy transition: {artifact.status.value} -> {target.value}"
            )
        promoted = replace(artifact, status=target)
        self._write(self._path(artifact.strategy_id, artifact.version), promoted)
        return promoted

    def validate(
        self,
        strategy_id: str,
        version: str | None = None,
    ) -> StrategyArtifact:
        return self.promote(strategy_id, version, StrategyStatus.VALIDATED)

    def import_alphamaster(
        self,
        payload: Mapping[str, Any] | Sequence[int],
        *,
        strategy_id: str | None = None,
        version: str = "1.0.0",
        name: str | None = None,
        schema_version: str | None = None,
        status: StrategyStatus | str = StrategyStatus.CANDIDATE,
        metadata: Mapping[str, Any] | None = None,
    ) -> StrategyArtifact:
        artifact = artifact_from_alphamaster_json(
            payload,
            strategy_id=strategy_id,
            version=version,
            name=name,
            schema_version=schema_version,
            status=status,
            metadata=metadata,
        )
        return self.save(artifact)

    def export_alphamaster(
        self,
        strategy_id: str,
        version: str | None = None,
    ) -> dict[str, Any]:
        return artifact_to_alphamaster_json(self.get(strategy_id, version))


FileStrategyArtifactRepository = FileStrategyRepository
