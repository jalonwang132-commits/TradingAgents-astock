"""成员自定义 Base URL 的 SSRF 防护。"""

from __future__ import annotations

import pytest

from web.components.sidebar import validate_model_selection
from web.url_safety import validate_base_url


@pytest.mark.unit
def test_public_literal_ip_is_allowed():
    assert validate_base_url("https://1.1.1.1/v1") == "https://1.1.1.1/v1"


@pytest.mark.unit
@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:11434/v1",
        "http://localhost:8501/",
        "http://169.254.169.254/latest/meta-data/",
        "http://192.168.1.1/admin",
        "http://10.0.0.5/",
        "http://[::1]:8000/",
        "http://[fe80::1]/",
        "http://0.0.0.0:8000/",
    ],
)
def test_private_and_metadata_targets_are_rejected(url):
    with pytest.raises(ValueError):
        validate_base_url(url)


@pytest.mark.unit
@pytest.mark.parametrize(
    "url",
    [
        "ftp://1.1.1.1/v1",
        "file:///etc/passwd",
        "https://user:pass@1.1.1.1/v1",
        "https://",
        "https://1.1.1.1:99999/v1",
        "https://1.1.1.1/v1\nX-Injected: 1",
    ],
)
def test_malformed_or_dangerous_urls_are_rejected(url):
    with pytest.raises(ValueError):
        validate_base_url(url)


@pytest.mark.unit
def test_owner_can_explicitly_allow_private_targets(monkeypatch):
    monkeypatch.setenv("TRADINGAGENTS_ALLOW_PRIVATE_BASE_URLS", "1")
    assert validate_base_url("http://127.0.0.1:11434/v1").startswith("http://127.0.0.1")


@pytest.mark.unit
def test_member_validation_blocks_private_base_url():
    config = {
        "quick_think_llm": "qwen-plus",
        "deep_think_llm": "qwen-plus",
        "llm_provider": "qwen",
        "llm_api_keys": {"qwen": "sk-test"},
        "allow_env_api_keys": False,
        "backend_url": "http://169.254.169.254/latest/meta-data/",
    }
    err = validate_model_selection(config)
    assert err is not None and "不安全" in err


@pytest.mark.unit
def test_owner_validation_does_not_check_private_base_url():
    config = {
        "quick_think_llm": "qwen-plus",
        "deep_think_llm": "qwen-plus",
        "llm_provider": "qwen",
        "llm_api_keys": {},
        "allow_env_api_keys": True,
        "backend_url": "http://127.0.0.1:11434/v1",
    }
    assert validate_model_selection(config) is None
