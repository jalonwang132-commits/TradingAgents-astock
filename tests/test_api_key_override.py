"""每用户 API Key 的优先级：显式 Key > 环境变量；成员禁止回落 .env。"""

from __future__ import annotations

import pytest

from tradingagents.llm_clients import anthropic_client, openai_client


class _Capture:
    last_kwargs: dict | None = None

    def __init__(self, **kwargs):
        type(self).last_kwargs = kwargs


def _capture_openai(monkeypatch):
    _Capture.last_kwargs = None
    monkeypatch.setattr(openai_client, "NormalizedChatOpenAI", _Capture)
    return _Capture


@pytest.mark.unit
def test_explicit_key_wins_over_env_for_provider(monkeypatch):
    monkeypatch.setenv("DASHSCOPE_API_KEY", "env-key-should-lose")
    capture = _capture_openai(monkeypatch)

    openai_client.OpenAIClient(
        "qwen-plus", provider="qwen", api_key="user-key-wins"
    ).get_llm()

    assert capture.last_kwargs["api_key"] == "user-key-wins"


@pytest.mark.unit
def test_env_key_used_for_owner_when_allowed(monkeypatch):
    monkeypatch.setenv("DASHSCOPE_API_KEY", "env-key")
    capture = _capture_openai(monkeypatch)

    openai_client.OpenAIClient(
        "qwen-plus", provider="qwen", allow_env_api_key=True
    ).get_llm()

    assert capture.last_kwargs["api_key"] == "env-key"


@pytest.mark.unit
def test_member_without_key_cannot_fall_back_to_env(monkeypatch):
    monkeypatch.setenv("DASHSCOPE_API_KEY", "owner-env-key")

    with pytest.raises(RuntimeError) as exc:
        openai_client.OpenAIClient(
            "qwen-plus", provider="qwen", allow_env_api_key=False
        ).get_llm()

    assert "API Key" in str(exc.value)
    assert "账户" in str(exc.value)


@pytest.mark.unit
def test_member_explicit_key_still_works_with_env_fallback_off(monkeypatch):
    monkeypatch.setenv("DASHSCOPE_API_KEY", "owner-env-key")
    capture = _capture_openai(monkeypatch)

    openai_client.OpenAIClient(
        "qwen-plus",
        provider="qwen",
        api_key="member-key",
        allow_env_api_key=False,
    ).get_llm()

    assert capture.last_kwargs["api_key"] == "member-key"


@pytest.mark.unit
def test_member_openai_does_not_inherit_env_base_url(monkeypatch):
    # ChatOpenAI 会回落 OPENAI_API_BASE / OPENAI_BASE_URL；成员模式必须显式钉死。
    monkeypatch.setenv("OPENAI_API_BASE", "https://owner-relay.example/v1")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://owner-relay.example/v1")
    capture = _capture_openai(monkeypatch)

    openai_client.OpenAIClient(
        "gpt-5.4",
        provider="openai",
        api_key="member-key",
        allow_env_api_key=False,
    ).get_llm()

    assert capture.last_kwargs["base_url"] == "https://api.openai.com/v1"


@pytest.mark.unit
def test_openai_compatible_requires_explicit_key_for_member(monkeypatch):
    monkeypatch.setenv("OPENAI_COMPATIBLE_API_KEY", "owner-relay-key")
    with pytest.raises(RuntimeError) as exc:
        openai_client.OpenAIClient(
            "any-model",
            base_url="https://relay.example/v1",
            provider="openai_compatible",
            allow_env_api_key=False,
        ).get_llm()
    assert "API Key" in str(exc.value)


@pytest.mark.unit
def test_anthropic_member_without_key_rejected(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "owner-anthropic-key")
    monkeypatch.setattr(anthropic_client, "NormalizedChatAnthropic", _Capture)
    _Capture.last_kwargs = None

    with pytest.raises(RuntimeError) as exc:
        anthropic_client.AnthropicClient(
            "claude-sonnet-4-5", allow_env_api_key=False
        ).get_llm()
    assert "API Key" in str(exc.value)


@pytest.mark.unit
def test_anthropic_explicit_key_passed_through(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(anthropic_client, "NormalizedChatAnthropic", _Capture)
    _Capture.last_kwargs = None

    anthropic_client.AnthropicClient(
        "claude-sonnet-4-5",
        api_key="member-anthropic-key",
        allow_env_api_key=False,
    ).get_llm()

    assert _Capture.last_kwargs["api_key"] == "member-anthropic-key"
