from __future__ import annotations

from collections.abc import Mapping

import pytest
from fastapi.testclient import TestClient

from xquant.ai import service as ai_service
from xquant.api.app import create_app


def _fake_completion(monkeypatch: pytest.MonkeyPatch, calls: list[dict[str, object]]) -> None:
    def fake_post(provider, messages):
        calls.append({"provider": provider, "messages": messages})
        return {
            "id": "req-test",
            "model": provider.model,
            "content": "pong",
            "reasoning_content": "",
            "usage": {"total_tokens": 7},
            "latency_ms": 12.5,
            "finish_reason": "stop",
        }

    monkeypatch.setattr(ai_service, "_post_chat_completion", fake_post)


def test_test_provider_requires_api_key(tmp_path) -> None:
    with TestClient(
        create_app(
            tmp_path / "xquant.db",
            system_config_path=tmp_path / "system-settings.json",
        )
    ) as client:
        response = client.post("/api/v1/system/config/test-provider", json={})

    assert response.status_code == 400
    assert "未配置 API Key" in response.json()["detail"]


def test_test_provider_uses_unsaved_form_values(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[dict[str, object]] = []
    _fake_completion(monkeypatch, calls)

    with TestClient(
        create_app(
            tmp_path / "xquant.db",
            system_config_path=tmp_path / "system-settings.json",
        )
    ) as client:
        response = client.post(
            "/api/v1/system/config/test-provider",
            json={
                "provider": {
                    "model": "deepseek-v4-flash",
                    "base_url": "https://api.deepseek.com",
                    "api_key": "sk-unsaved",
                    "timeout_seconds": 15,
                }
            },
        )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["ok"] is True
    assert payload["model"] == "deepseek-v4-flash"
    assert payload["latency_ms"] == 12.5
    assert payload["reply"] == "pong"
    assert payload["usage"] == {"total_tokens": 7}
    assert len(calls) == 1
    provider = calls[0]["provider"]
    assert provider.api_key == "sk-unsaved"
    assert provider.timeout_seconds == 15
    messages = calls[0]["messages"]
    assert isinstance(messages, list)
    assert messages[-1]["content"] == "ping"


def test_test_provider_keeps_stored_key_for_masked_value(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[dict[str, object]] = []
    _fake_completion(monkeypatch, calls)

    with TestClient(
        create_app(
            tmp_path / "xquant.db",
            system_config_path=tmp_path / "system-settings.json",
        )
    ) as client:
        saved = client.put(
            "/api/v1/system/config",
            json={"provider": {"api_key": "sk-stored"}},
        )
        assert saved.status_code == 200, saved.text
        assert saved.json()["config"]["provider"]["api_key"] == "***"

        response = client.post(
            "/api/v1/system/config/test-provider",
            json={"provider": {"api_key": "***"}},
        )

    assert response.status_code == 200, response.text
    provider = calls[0]["provider"]
    assert provider.api_key == "sk-stored"


def test_test_provider_reports_upstream_failure(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def failing_post(_provider: object, _messages: object) -> Mapping[str, object]:
        raise ValueError("模型服务返回 HTTP 401：invalid api key")

    monkeypatch.setattr(ai_service, "_post_chat_completion", failing_post)

    with TestClient(
        create_app(
            tmp_path / "xquant.db",
            system_config_path=tmp_path / "system-settings.json",
        )
    ) as client:
        response = client.post(
            "/api/v1/system/config/test-provider",
            json={"provider": {"api_key": "sk-bad"}},
        )

    assert response.status_code == 400
    assert "大模型连接测试失败" in response.json()["detail"]
    assert "HTTP 401" in response.json()["detail"]
