"""运行配置把「每用户 Key」送进 graph 的回归测试。

客户端层的优先级已有 test_api_key_override.py；这里锁的是**上层接线**：
`TradingAgentsGraph._get_provider_kwargs` / `_build_role_llms` 是否真的按目标
provider 取 `llm_api_keys`，而不是把主 provider 的 Key 发给另一家。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from tradingagents.default_config import DEFAULT_CONFIG
from tradingagents.graph import trading_graph as tg


def _graph(overrides: dict) -> tg.TradingAgentsGraph:
    graph = tg.TradingAgentsGraph.__new__(tg.TradingAgentsGraph)
    config = DEFAULT_CONFIG.copy()
    config.update(overrides)
    graph.config = config
    graph.callbacks = []
    return graph


@pytest.mark.unit
def test_primary_provider_gets_user_key_and_env_flag():
    graph = _graph(
        {
            "llm_provider": "deepseek",
            "llm_api_keys": {"deepseek": "sk-user-deepseek"},
            "allow_env_api_keys": False,
        }
    )
    kwargs = graph._get_provider_kwargs()
    assert kwargs["api_key"] == "sk-user-deepseek"
    assert kwargs["allow_env_api_key"] is False


@pytest.mark.unit
def test_role_llms_use_key_for_their_own_provider(monkeypatch):
    captured: list[dict] = []

    def fake_create(**kwargs):
        captured.append(kwargs)
        return SimpleNamespace(get_llm=lambda: object())

    monkeypatch.setattr(tg, "create_llm_client", fake_create)
    graph = _graph(
        {
            "llm_provider": "deepseek",
            "llm_api_keys": {"deepseek": "sk-user-deepseek", "qwen": "sk-user-qwen"},
            "allow_env_api_keys": False,
            "role_llms": {
                "bull": {"provider": "qwen", "model": "qwen-plus"},
                "bear": {"model": "deepseek-chat"},
            },
        }
    )

    # 模拟主 provider 的 kwargs 里已带 main key；角色必须按目标 provider 重新取。
    graph._build_role_llms(
        {"api_key": "sk-main-must-not-leak", "allow_env_api_key": False},
        subscription_on=False,
        selected_analysts=None,
    )

    by_provider = {item["provider"]: item for item in captured}
    assert by_provider["qwen"]["api_key"] == "sk-user-qwen"
    assert by_provider["deepseek"]["api_key"] == "sk-user-deepseek"
    assert all(
        item.get("api_key") != "sk-main-must-not-leak" for item in captured
    )
    assert all(item["allow_env_api_key"] is False for item in captured)


@pytest.mark.unit
def test_role_llm_without_user_key_does_not_borrow_main_key(monkeypatch):
    captured: list[dict] = []

    def fake_create(**kwargs):
        captured.append(kwargs)
        return SimpleNamespace(get_llm=lambda: object())

    monkeypatch.setattr(tg, "create_llm_client", fake_create)
    graph = _graph(
        {
            "llm_provider": "deepseek",
            "llm_api_keys": {"deepseek": "sk-user-deepseek"},
            "allow_env_api_keys": False,
            "role_llms": {"bull": {"provider": "qwen", "model": "qwen-plus"}},
        }
    )
    graph._build_role_llms(
        {"api_key": "sk-main-must-not-leak"}, subscription_on=False, selected_analysts=None
    )

    assert captured[0]["provider"] == "qwen"
    assert "api_key" not in captured[0]
    assert captured[0]["allow_env_api_key"] is False
