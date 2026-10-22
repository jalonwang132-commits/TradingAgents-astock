"""Web 端到端：首次启动引导、邀请码注册、成员未配 Key 被拦住。

用 Streamlit 官方的 AppTest 跑真实的 ``web/app.py``，覆盖认证门、侧栏和
``start_analysis`` 消费点的接线（纯函数测试覆盖不到这些）。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

APP = Path(__file__).resolve().parent.parent / "web" / "app.py"


def _app() -> AppTest:
    return AppTest.from_file(str(APP), default_timeout=40)


def _submit(at: AppTest, key_prefix: str) -> None:
    buttons = [b for b in at.button if b.key and key_prefix in b.key]
    assert buttons, f"找不到 {key_prefix} 的提交按钮"
    buttons[0].click().run()


@pytest.mark.integration
def test_bootstrap_creates_owner_using_legacy_home(tmp_path, monkeypatch):
    monkeypatch.setenv("TRADINGAGENTS_HOME", str(tmp_path))
    at = _app()
    at.run()

    assert not at.exception
    assert any("创建 owner" in m.value for m in at.markdown)

    at.text_input(key="bootstrap_username").set_value("owner")
    at.text_input(key="bootstrap_password").set_value("owner-password-1")
    at.text_input(key="bootstrap_password2").set_value("owner-password-1")
    _submit(at, "bootstrap_form")

    assert not at.exception
    assert at.session_state["auth_user"]["username"] == "owner"
    # owner 复用旧单用户目录：CLI 与 Web 共享同一份历史。
    assert (tmp_path / "auth" / "accounts.json").exists()
    assert (tmp_path / "logs").exists()
    assert not list((tmp_path / "users").glob("u_*"))
    # 业务页面渲染出来了（侧栏「开始分析」）
    assert any(b.label == "开始分析" for b in at.button)


@pytest.mark.integration
def test_member_registration_and_missing_key_guard(tmp_path, monkeypatch):
    monkeypatch.setenv("TRADINGAGENTS_HOME", str(tmp_path))
    monkeypatch.setenv("TRADINGAGENTS_MAX_USERS", "10")

    # owner + invite
    at = _app()
    at.run()
    at.text_input(key="bootstrap_username").set_value("owner")
    at.text_input(key="bootstrap_password").set_value("owner-password-1")
    at.text_input(key="bootstrap_password2").set_value("owner-password-1")
    _submit(at, "bootstrap_form")

    [b for b in at.button if b.key == "nav_account"][0].click().run()
    at.text_input(key="admin_invite_note").set_value("朋友A")
    _submit(at, "admin_create_invite")
    code = at.session_state["new_invite_code"]
    assert code and "-" in code

    # logout → register
    [b for b in at.button if b.key == "nav_logout"][0].click().run()
    at.text_input(key="reg_invite").set_value(code)
    at.text_input(key="reg_username").set_value("friend_a")
    at.text_input(key="reg_password").set_value("friend-password-1")
    at.text_input(key="reg_password2").set_value("friend-password-1")
    _submit(at, "register_form")

    assert not at.exception
    assert at.session_state["auth_user"]["username"] == "friend_a"
    assert any("API Key" in w.value for w in at.warning)

    # 没有 Key 时，开始分析必须被消费点拦住，且不占用运行租约。
    at.text_input(key="input_ticker").set_value("600519")
    at.run()
    [b for b in at.button if b.label == "开始分析"][0].click().run()

    assert any("API Key" in e.value for e in at.error)
    assert at.session_state.get("start_analysis") is None
    assert not at.session_state.get("run_lease")


@pytest.mark.integration
def test_member_never_sees_owner_subscription_switch(tmp_path, monkeypatch):
    monkeypatch.setenv("TRADINGAGENTS_HOME", str(tmp_path))
    at = _app()
    at.run()
    at.text_input(key="bootstrap_username").set_value("owner")
    at.text_input(key="bootstrap_password").set_value("owner-password-1")
    at.text_input(key="bootstrap_password2").set_value("owner-password-1")
    _submit(at, "bootstrap_form")
    [b for b in at.button if b.key == "nav_account"][0].click().run()
    at.text_input(key="admin_invite_note").set_value("朋友A")
    _submit(at, "admin_create_invite")
    code = at.session_state["new_invite_code"]
    [b for b in at.button if b.key == "nav_logout"][0].click().run()
    at.text_input(key="reg_invite").set_value(code)
    at.text_input(key="reg_username").set_value("friend_a")
    at.text_input(key="reg_password").set_value("friend-password-1")
    at.text_input(key="reg_password2").set_value("friend-password-1")
    _submit(at, "register_form")

    labels = [s.label for s in at.selectbox]
    assert "个人 Claude 订阅覆盖 (Agent SDK)" not in labels
    assert at.session_state["subscription_scope"] == "off"
