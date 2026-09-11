"""Persistent system settings for AI analysis and notifications."""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Mapping
from copy import deepcopy
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit, urlunsplit

from pydantic import BaseModel, ConfigDict, Field, model_validator

DecisionStance = Literal["conservative", "balanced", "aggressive", "extreme_aggressive"]
ReasoningEffort = Literal["low", "medium", "high", "max"]

DEFAULT_SYSTEM_CONFIG_PATH = Path("data/system-settings.json")
SYSTEM_CONFIG_ENV = "XQUANT_SYSTEM_CONFIG_FILE"

_PROVIDER_ENV_FIELDS = {
    "XQUANT_AI_API_KEY": "api_key",
    "XQUANT_AI_PROXY": "proxy_url",
}
_FEISHU_ENV_FIELDS = {
    "XQUANT_FEISHU_WEBHOOK": "webhook_url",
    "XQUANT_FEISHU_SECRET": "secret",
}


class ProviderSettings(BaseModel):
    """AI provider connection and model behaviour settings."""

    model_config = ConfigDict(extra="ignore")

    model: str = "deepseek-v4-flash"
    base_url: str = "https://api.deepseek.com"
    api_key: str = ""
    thinking: bool = True
    reasoning_effort: ReasoningEffort = "high"
    context_window: int = Field(default=2_000_000, ge=1)
    proxy_url: str = ""
    timeout_seconds: float = Field(default=60.0, gt=0)


class AnalysisSettings(BaseModel):
    """General AI analysis and realtime monitor settings."""

    model_config = ConfigDict(extra="ignore")

    analysis_bar_count: int = Field(default=100, ge=2, le=5000)
    decision_stance: DecisionStance = "balanced"
    enable_next_bar_prediction: bool = False
    keep_analysis: bool = False
    incremental_max_new_bars: int = Field(default=10, ge=0, le=500)
    monitor_interval_seconds: int = Field(default=60, ge=1, le=86400)
    concurrency: int = Field(default=3, ge=1, le=64)


class FeishuSettings(BaseModel):
    """Feishu notification credentials and delivery rules."""

    model_config = ConfigDict(extra="ignore")

    enabled: bool = True
    webhook_url: str = ""
    secret: str = ""
    app_id: str = ""
    app_secret: str = ""
    notify_on_order_only: bool = True
    confidence_threshold: int = Field(default=0, ge=0, le=100)


class MonitorWatchlistItem(BaseModel):
    """One monitored product with an independent analysis configuration."""

    model_config = ConfigDict(extra="ignore")

    symbol: str = ""
    timeframe: str = "15m"
    source: str = ""
    dataset_id: str = ""
    enabled: bool = True
    analysis: AnalysisSettings = Field(default_factory=AnalysisSettings)

    @model_validator(mode="before")
    @classmethod
    def _accept_flattened_analysis_fields(cls, value: Any) -> Any:
        if not isinstance(value, Mapping):
            return value
        data = dict(value)
        nested = data.get("analysis")
        analysis = dict(nested) if isinstance(nested, Mapping) else {}
        analysis_keys = set(AnalysisSettings.model_fields)
        for key in analysis_keys:
            if key in data:
                analysis[key] = data.pop(key)
        if analysis:
            data["analysis"] = analysis
        return data


class SystemSettings(BaseModel):
    """Root system settings object persisted as JSON."""

    model_config = ConfigDict(extra="ignore")

    provider: ProviderSettings = Field(default_factory=ProviderSettings)
    analysis: AnalysisSettings = Field(default_factory=AnalysisSettings)
    feishu: FeishuSettings = Field(default_factory=FeishuSettings)
    monitor_watchlist: list[MonitorWatchlistItem] = Field(default_factory=list)


AIProviderSettings = ProviderSettings
WatchlistItem = MonitorWatchlistItem


def resolve_config_path(path: str | Path | None = None) -> Path:
    """Resolve an explicit path, then the environment override, then the default."""

    configured = path or os.environ.get(SYSTEM_CONFIG_ENV) or DEFAULT_SYSTEM_CONFIG_PATH
    resolved = Path(configured).expanduser()
    if not resolved.is_absolute():
        resolved = Path.cwd() / resolved
    return resolved.resolve(strict=False)


def _mask_secret(value: str) -> str:
    return "***" if value else ""


def _mask_proxy_url(value: str) -> str:
    """Keep proxy host details while removing authentication credentials."""

    if not value:
        return ""
    try:
        parts = urlsplit(value)
    except ValueError:
        return "***"
    if not parts.username and not parts.password:
        return value
    username = "***" if parts.username else ""
    password = "***" if parts.password else ""
    auth = username
    if password:
        auth = f"{username}:{password}"
    host = parts.hostname or ""
    if parts.port:
        host = f"{host}:{parts.port}"
    netloc = f"{auth}@{host}" if auth else host
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))


def _mask_webhook_url(value: str) -> str:
    if not value:
        return ""
    try:
        parts = urlsplit(value)
    except ValueError:
        return "***"
    path = parts.path.rstrip("/")
    if path:
        path = f"{path.rsplit('/', 1)[0]}/***"
    return urlunsplit((parts.scheme, parts.netloc, path, "", ""))


def masked_payload(settings: SystemSettings) -> dict[str, Any]:
    """Return a JSON-safe payload without credential material."""

    provider = settings.provider.model_dump()
    provider.update(
        {
            "api_key": _mask_secret(provider["api_key"]),
            "proxy_url": _mask_proxy_url(provider["proxy_url"]),
            "configured": bool(settings.provider.api_key),
            "api_key_configured": bool(settings.provider.api_key),
            "proxy_configured": bool(settings.provider.proxy_url),
        }
    )

    feishu = settings.feishu.model_dump()
    feishu.update(
        {
            "webhook_url": _mask_webhook_url(feishu["webhook_url"]),
            "secret": _mask_secret(feishu["secret"]),
            "app_secret": _mask_secret(feishu["app_secret"]),
            "configured": bool(settings.feishu.webhook_url),
            "webhook_configured": bool(settings.feishu.webhook_url),
            "secret_configured": bool(settings.feishu.secret),
            "app_configured": bool(
                settings.feishu.app_id and settings.feishu.app_secret
            ),
        }
    )
    return {
        "provider": provider,
        "analysis": settings.analysis.model_dump(),
        "feishu": feishu,
        "monitor_watchlist": [
            item.model_dump() for item in settings.monitor_watchlist
        ],
    }


public_payload = masked_payload


def _apply_environment_overrides(settings: SystemSettings) -> SystemSettings:
    data = settings.model_dump()
    for env_name, field_name in _PROVIDER_ENV_FIELDS.items():
        value = os.environ.get(env_name)
        if value is not None:
            data["provider"][field_name] = value.strip()
    for env_name, field_name in _FEISHU_ENV_FIELDS.items():
        value = os.environ.get(env_name)
        if value is not None:
            data["feishu"][field_name] = value.strip()
    return SystemSettings.model_validate(data)


def _read_settings(path: Path) -> SystemSettings:
    if not path.exists():
        return SystemSettings()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return SystemSettings()
    if not isinstance(raw, Mapping):
        return SystemSettings()
    return SystemSettings.model_validate(raw)


def _atomic_write(path: Path, settings: SystemSettings) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary_path = Path(handle.name)
            json.dump(settings.model_dump(), handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink(missing_ok=True)


def load_system_settings(path: str | Path | None = None) -> SystemSettings:
    """Load settings with environment overrides applied."""

    resolved = resolve_config_path(path)
    stored = _read_settings(resolved)
    if not resolved.exists():
        _atomic_write(resolved, stored)
    return _apply_environment_overrides(stored)


def save_system_settings(
    settings: SystemSettings,
    path: str | Path | None = None,
) -> SystemSettings:
    """Persist settings and return the effective configuration."""

    resolved = resolve_config_path(path)
    _atomic_write(resolved, settings)
    return _apply_environment_overrides(settings)


def _deep_merge(base: dict[str, Any], update: Mapping[str, Any]) -> dict[str, Any]:
    merged = deepcopy(base)
    for key, value in update.items():
        if isinstance(value, Mapping) and isinstance(merged.get(key), Mapping):
            merged[key] = _deep_merge(dict(merged[key]), value)
        else:
            merged[key] = deepcopy(value)
    return merged


def _preserve_masked_values(base: dict[str, Any], merged: dict[str, Any]) -> None:
    for section, key in (
        ("provider", "api_key"),
        ("provider", "proxy_url"),
        ("feishu", "webhook_url"),
        ("feishu", "secret"),
        ("feishu", "app_secret"),
    ):
        value = merged.get(section, {}).get(key)
        if isinstance(value, str) and "***" in value:
            merged[section][key] = base.get(section, {}).get(key, "")


def update_system_settings(
    payload: Mapping[str, Any] | SystemSettings,
    path: str | Path | None = None,
) -> SystemSettings:
    """Merge a partial payload into the persisted settings."""

    resolved = resolve_config_path(path)
    stored = _read_settings(resolved)
    if isinstance(payload, SystemSettings):
        updated = payload
    else:
        merged = _deep_merge(stored.model_dump(), payload)
        _preserve_masked_values(stored.model_dump(), merged)
        updated = SystemSettings.model_validate(
            merged
        )
    _atomic_write(resolved, updated)
    return _apply_environment_overrides(updated)


def reset_system_settings(path: str | Path | None = None) -> SystemSettings:
    """Replace persisted settings with defaults."""

    resolved = resolve_config_path(path)
    defaults = SystemSettings()
    _atomic_write(resolved, defaults)
    return _apply_environment_overrides(defaults)


def merge_provider_payload(
    settings: SystemSettings,
    payload: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Combine stored provider settings with a route request payload."""

    incoming = dict(payload or {})
    provider_update = incoming.get("provider")
    provider_update = dict(provider_update) if isinstance(provider_update, Mapping) else {}

    provider = settings.provider.model_dump()
    for key in ("model", "base_url", "thinking", "reasoning_effort", "context_window",
                "proxy_url", "timeout_seconds"):
        if key in provider_update and provider_update[key] is not None:
            if key == "proxy_url" and "***" in str(provider_update[key]):
                continue
            provider[key] = provider_update[key]
    incoming_key = provider_update.get("api_key")
    if incoming_key not in (None, "", "***"):
        provider["api_key"] = incoming_key

    analysis = settings.analysis.model_dump()
    analysis_update = incoming.get("analysis")
    if isinstance(analysis_update, Mapping):
        analysis.update(analysis_update)
    for key in AnalysisSettings.model_fields:
        if key in incoming and incoming[key] is not None:
            analysis[key] = incoming[key]

    merged: dict[str, Any] = {
        "provider": provider,
        "analysis": analysis,
        **analysis,
    }
    if "monitor_watchlist" in incoming:
        merged["monitor_watchlist"] = incoming["monitor_watchlist"]
    elif settings.monitor_watchlist:
        merged["monitor_watchlist"] = [
            item.model_dump() for item in settings.monitor_watchlist
        ]
    return merged


class SystemConfigStore:
    """Stateful facade over the JSON-backed system settings."""

    def __init__(self, path: str | Path | None = None) -> None:
        self.path = resolve_config_path(path)
        self._stored = _read_settings(self.path)
        if not self.path.exists():
            _atomic_write(self.path, self._stored)
        self._settings = _apply_environment_overrides(self._stored)

    @property
    def settings(self) -> SystemSettings:
        return self._settings

    def load(self) -> SystemSettings:
        self._stored = _read_settings(self.path)
        self._settings = _apply_environment_overrides(self._stored)
        return self._settings

    def save(self, settings: SystemSettings | None = None) -> SystemSettings:
        if settings is not None:
            self._stored = settings
        _atomic_write(self.path, self._stored)
        self._settings = _apply_environment_overrides(self._stored)
        return self._settings

    def update(
        self,
        payload: Mapping[str, Any] | SystemSettings,
    ) -> SystemSettings:
        if isinstance(payload, SystemSettings):
            self._stored = payload
        else:
            merged = _deep_merge(self._stored.model_dump(), payload)
            _preserve_masked_values(self._stored.model_dump(), merged)
            self._stored = SystemSettings.model_validate(
                merged
            )
        return self.save()

    def reset(self) -> SystemSettings:
        self._stored = SystemSettings()
        return self.save()

    def masked_payload(self) -> dict[str, Any]:
        return masked_payload(self._settings)

    def public_payload(self) -> dict[str, Any]:
        return self.masked_payload()

    def merge_provider_payload(
        self,
        payload: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        return merge_provider_payload(self._settings, payload)


SystemSettingsStore = SystemConfigStore


__all__ = [
    "DEFAULT_SYSTEM_CONFIG_PATH",
    "SYSTEM_CONFIG_ENV",
    "AIProviderSettings",
    "AnalysisSettings",
    "DecisionStance",
    "FeishuSettings",
    "MonitorWatchlistItem",
    "ProviderSettings",
    "ReasoningEffort",
    "SystemConfigStore",
    "SystemSettings",
    "SystemSettingsStore",
    "WatchlistItem",
    "load_system_settings",
    "masked_payload",
    "merge_provider_payload",
    "public_payload",
    "reset_system_settings",
    "resolve_config_path",
    "save_system_settings",
    "update_system_settings",
]
