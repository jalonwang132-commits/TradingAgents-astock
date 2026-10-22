"""多用户账户层：注册/登录/邀请码/限流/审计/文件安全。"""

from __future__ import annotations

import json
import os
import types

import pytest

from web.auth import store as store_mod
from web.auth import ui as auth_ui
from web.auth.passwords import hash_password, verify_password
from web.auth.ratelimit import RateLimiter
from web.auth.store import AuthError, AuthStore

OWNER = "owner"
MEMBER = "friend_a"
PASSWORD = "correct-horse-1"


def _store(tmp_path, monkeypatch, *, max_users: int = 10) -> AuthStore:
    monkeypatch.setenv("TRADINGAGENTS_MAX_USERS", str(max_users))
    return AuthStore(tmp_path)


def _invite(store: AuthStore, owner_id: str, **kwargs) -> str:
    _record, code = store.create_invite(owner_id, **kwargs)
    return code


@pytest.mark.unit
def test_owner_bootstrap_then_login(tmp_path, monkeypatch):
    store = _store(tmp_path, monkeypatch)
    assert store.has_users() is False

    owner = store.create_owner(OWNER, PASSWORD, "老板")
    assert owner["role"] == "owner"
    assert store.has_users() is True

    raw = json.loads((tmp_path / "auth" / "accounts.json").read_text())
    stored = next(iter(raw["users"].values()))
    assert stored["password_hash"].startswith("scrypt$")
    assert PASSWORD not in json.dumps(raw)

    user, error = store.authenticate(OWNER, PASSWORD)
    assert error == "" and user["username"] == OWNER
    assert store.authenticate(OWNER, "wrong-password") == (None, "bad_credentials")
    assert store.authenticate("nobody", PASSWORD) == (None, "bad_credentials")

    if os.name == "posix":
        assert (tmp_path / "auth" / "accounts.json").stat().st_mode & 0o777 == 0o600


@pytest.mark.unit
def test_second_owner_is_forbidden(tmp_path, monkeypatch):
    store = _store(tmp_path, monkeypatch)
    store.create_owner(OWNER, PASSWORD)
    with pytest.raises(AuthError) as exc:
        store.create_owner("another", PASSWORD)
    assert exc.value.code == "forbidden"


@pytest.mark.unit
def test_register_with_single_use_invite(tmp_path, monkeypatch):
    store = _store(tmp_path, monkeypatch)
    owner = store.create_owner(OWNER, PASSWORD)
    code = _invite(store, owner["id"])

    with pytest.raises(AuthError) as exc:
        store.register("WRONG-CODE-XXXX", MEMBER, PASSWORD)
    assert exc.value.code == "invite_invalid"

    member = store.register(code, MEMBER, PASSWORD, "朋友A")
    assert member["role"] == "member"
    assert member["status"] == "active"

    # 一次性邀请码不能再用第二次
    with pytest.raises(AuthError) as exc:
        store.register(code, "friend_b", PASSWORD)
    assert exc.value.code == "invite_invalid"


@pytest.mark.unit
def test_register_respects_max_users(tmp_path, monkeypatch):
    store = _store(tmp_path, monkeypatch, max_users=1)
    owner = store.create_owner(OWNER, PASSWORD)
    code = _invite(store, owner["id"])
    with pytest.raises(AuthError) as exc:
        store.register(code, MEMBER, PASSWORD)
    assert exc.value.code == "full"


@pytest.mark.unit
def test_duplicate_username_case_insensitive(tmp_path, monkeypatch):
    store = _store(tmp_path, monkeypatch)
    owner = store.create_owner(OWNER, PASSWORD)
    code = _invite(store, owner["id"], max_uses=2)
    store.register(code, MEMBER, PASSWORD)
    with pytest.raises(AuthError) as exc:
        store.register(code, "FRIEND_A", PASSWORD)
    assert exc.value.code == "conflict"


@pytest.mark.unit
def test_invite_expiry(tmp_path, monkeypatch):
    store = _store(tmp_path, monkeypatch)
    owner = store.create_owner(OWNER, PASSWORD)
    now = store_mod._now()
    code = _invite(store, owner["id"], ttl_hours=1)

    monkeypatch.setattr(store_mod, "_now", lambda: now + 3601)
    with pytest.raises(AuthError) as exc:
        store.register(code, MEMBER, PASSWORD)
    assert exc.value.code == "invite_invalid"


@pytest.mark.unit
def test_password_change_and_disable_block_old_credentials(tmp_path, monkeypatch):
    store = _store(tmp_path, monkeypatch)
    owner = store.create_owner(OWNER, PASSWORD)
    code = _invite(store, owner["id"], max_uses=2)
    member = store.register(code, MEMBER, PASSWORD)

    store.change_password(member["id"], PASSWORD, "new-password-2")
    assert store.authenticate(MEMBER, PASSWORD) == (None, "bad_credentials")
    assert store.authenticate(MEMBER, "new-password-2")[0]["username"] == MEMBER

    store.set_status(owner["id"], member["id"], "disabled")
    assert store.authenticate(MEMBER, "new-password-2") == (None, "disabled")
    store.set_status(owner["id"], member["id"], "active")
    assert store.authenticate(MEMBER, "new-password-2")[0]["status"] == "active"


@pytest.mark.unit
def test_owner_cannot_disable_self(tmp_path, monkeypatch):
    store = _store(tmp_path, monkeypatch)
    owner = store.create_owner(OWNER, PASSWORD)
    with pytest.raises(AuthError) as exc:
        store.set_status(owner["id"], owner["id"], "disabled")
    assert exc.value.code == "forbidden"


@pytest.mark.unit
def test_corrupt_accounts_never_falls_back_to_bootstrap(tmp_path, monkeypatch):
    store = _store(tmp_path, monkeypatch)
    store.create_owner(OWNER, PASSWORD)
    (tmp_path / "auth" / "accounts.json").write_text("{not json", encoding="utf-8")

    with pytest.raises(AuthError) as exc:
        store.has_users()
    assert exc.value.code == "corrupt"


@pytest.mark.unit
def test_missing_accounts_after_initialization_never_allows_takeover(tmp_path, monkeypatch):
    store = _store(tmp_path, monkeypatch)
    store.create_owner(OWNER, PASSWORD)
    (tmp_path / "auth" / "accounts.json").unlink()

    with pytest.raises(AuthError) as exc:
        store.has_users()
    assert exc.value.code == "corrupt"


@pytest.mark.unit
def test_empty_users_with_marker_never_allows_takeover(tmp_path, monkeypatch):
    store = _store(tmp_path, monkeypatch)
    store.create_owner(OWNER, PASSWORD)
    path = tmp_path / "auth" / "accounts.json"
    raw = json.loads(path.read_text())
    raw["users"] = {}
    path.write_text(json.dumps(raw), encoding="utf-8")

    with pytest.raises(AuthError) as exc:
        store.has_users()
    assert exc.value.code == "corrupt"
    with pytest.raises(AuthError):
        store.create_owner("attacker", PASSWORD)


@pytest.mark.unit
def test_audit_log_does_not_contain_passwords(tmp_path, monkeypatch):
    store = _store(tmp_path, monkeypatch)
    store.create_owner(OWNER, PASSWORD)
    store.authenticate(OWNER, "wrong-password")
    store.authenticate(OWNER, PASSWORD)

    audit = (tmp_path / "auth" / "audit.log").read_text(encoding="utf-8")
    assert "login_failed" in audit and "login_ok" in audit
    assert PASSWORD not in audit
    assert "wrong-password" not in audit


@pytest.mark.unit
def test_clear_user_state_drops_strategy_and_missing_data_drafts(monkeypatch):
    state = {
        "sp_new_content": "A 的未保存战法",
        "sp_new_name": "A 的策略",
        "strategy_pending_delete": "sp_1",
        "retry_missing_600519_2026-05-12_x_result": {"secret": True},
        "missing_toggle_600519_2026-05-12": True,
        "team_config": {"prompts": {"market": "A"}},
        "auth_user": {"username": "friend_a"},
        "auth": {"user_id": "u_x"},
        "login_username": "friend_a",
    }
    fake = types.SimpleNamespace(session_state=state)
    monkeypatch.setattr(auth_ui, "st", fake)

    auth_ui._clear_user_state()

    assert "sp_new_content" not in state
    assert "sp_new_name" not in state
    assert "strategy_pending_delete" not in state
    assert "retry_missing_600519_2026-05-12_x_result" not in state
    assert "missing_toggle_600519_2026-05-12" not in state
    assert "team_config" not in state
    assert "auth_user" not in state
    assert "login_username" in state


@pytest.mark.unit
def test_password_hash_roundtrip_and_rejects_garbage():
    encoded = hash_password("secret-password")
    assert verify_password("secret-password", encoded) is True
    assert verify_password("wrong", encoded) is False
    assert verify_password("secret-password", "not-a-hash") is False
    assert verify_password("secret-password", "scrypt$99999999$8$1$AAAA$AAAA") is False


@pytest.mark.unit
def test_rate_limiter_sliding_window():
    now = [0.0]
    limiter = RateLimiter(clock=lambda: now[0])

    for _ in range(3):
        assert limiter.hit("k", limit=3, window=60).allowed
    blocked = limiter.check("k", limit=3, window=60)
    assert blocked.allowed is False and blocked.retry_after >= 1

    now[0] = 61.0
    assert limiter.check("k", limit=3, window=60).allowed is True


@pytest.mark.unit
def test_invite_code_is_hashed_on_disk(tmp_path, monkeypatch):
    store = _store(tmp_path, monkeypatch)
    owner = store.create_owner(OWNER, PASSWORD)
    code = _invite(store, owner["id"])
    raw = (tmp_path / "auth" / "accounts.json").read_text(encoding="utf-8")
    assert code not in raw
    assert code.replace("-", "") not in raw
