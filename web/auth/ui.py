"""Streamlit 认证门、账户页与管理员页。

调用顺序很重要：``web/app.py`` 必须在渲染侧栏/页面之前调用 :func:`require_login`。
登录成功（或已有有效会话）后，本模块会把当前线程绑定到该用户的目录
（``web.user_context.activate``），之后所有历史/团队/模型配置读写都会自动落到
该用户目录。

**会话不落 cookie**：Streamlit 的页面刷新会开新会话，需要重新登录。这是有意的
安全取舍（浏览器关掉即失效、服务端可随时停用账户）。需要「刷新不掉登录」的部署
方式见 MULTIUSER.md 的 trusted-header 模式（由反向代理负责持久会话）。
"""

from __future__ import annotations

import os
import time
from typing import Any

import streamlit as st

from web.auth.ratelimit import LIMITER, login_keys, registration_key
from web.auth.store import (
    ROLE_OWNER,
    STATUS_ACTIVE,
    AuthError,
    AuthStore,
)
from web.branding import APP_DESCRIPTION, APP_MARK_LEFT, APP_MARK_RIGHT
from web.run_registry import release_run
from web.user_context import activate, deactivate
from web.user_paths import UserPaths, tradingagents_home

# ── 会话 / 限流参数 ─────────────────────────────────────────────────────────

SESSION_TTL_SECONDS = 12 * 3600
SESSION_IDLE_SECONDS = 2 * 3600
LOGIN_LIMIT_PER_IDENTITY = 5
LOGIN_WINDOW_SECONDS = 15 * 60
LOGIN_LIMIT_PER_IP = 50
REGISTER_LIMIT_PER_IP = 10
REGISTER_WINDOW_SECONDS = 60 * 60

# 这些前缀/键属于上一个账户，切换/退出时必须清掉，防止 A 的团队配置、
# 正在看的报告、模型选择被 B 复用。
_USER_STATE_PREFIXES = (
    "team_",
    "llm_",
    "quick_",
    "deep_",
    "custom_",
    "subscription_",
    "agent_sdk_",
    "input_",
    "nav_",
    "hist_",
    "resume_",
    "api_key_",
    "account_",
    "admin_",
    "reanalyze_",
    # 团队页未保存的战法草稿 / 报告页缺失数据重试结果：都含用户内容，
    # 切换账户必须清掉，否则 B 会看到 A 的策略草稿。
    "sp_",
    "strategy_",
    "missing_",
    "retry_missing_",
)
# 登录/注册/改密表单里的密码字段。登录成功后要在下一次 run 开头清掉，
# 避免明文密码一直留在 session_state 里。
_FORM_SECRET_KEYS = {
    "login_password",
    "reg_password",
    "reg_password2",
    "bootstrap_password",
    "bootstrap_password2",
    "account_pw_current",
    "account_pw_new",
    "account_pw_confirm",
    "admin_reset_pw",
    "admin_reset_pw2",
}
_USER_STATE_KEYS = {
    "tracker",
    "viewing_history",
    "start_analysis",
    "current_view",
    "run_lease",
    "auth_notice",
    "new_invite_code",
    "new_invite_note",
}


def _store() -> AuthStore:
    return AuthStore(tradingagents_home())


def current_user() -> dict[str, Any] | None:
    user = st.session_state.get("auth_user")
    return user if isinstance(user, dict) else None


def is_owner(user: dict[str, Any] | None = None) -> bool:
    target = user if user is not None else current_user()
    return bool(target and target.get("role") == ROLE_OWNER)


def client_ip() -> str:
    headers = getattr(st.context, "headers", {}) or {}
    if os.environ.get("TRADINGAGENTS_TRUST_PROXY") == "1":
        forwarded = None
        try:
            forwarded = headers.get("X-Forwarded-For") or headers.get("x-forwarded-for")
        except AttributeError:
            forwarded = None
        if forwarded:
            # nginx 的 $proxy_add_x_forwarded_for 会把真实客户端 IP **追加在最后**；
            # 最左边可能是客户端自己伪造的。只信任最后一跳。
            hops = [part.strip() for part in str(forwarded).split(",") if part.strip()]
            if hops:
                return hops[-1][:64]
    ip = getattr(st.context, "ip_address", None)
    return str(ip or "unknown")[:64]


def _drop_form_secret_keys() -> None:
    for key in _FORM_SECRET_KEYS:
        st.session_state.pop(key, None)


def _clear_user_state() -> None:
    """删除上一个账户留在 session_state 里的所有业务状态。

    只在没有实例化过业务 widget 的时点调用（认证门最开头）。保留登录/注册表单的
    key，否则 Streamlit 会抛「不能删除已实例化 widget」。
    """
    for key in list(st.session_state.keys()):
        if key in _USER_STATE_KEYS or any(str(key).startswith(p) for p in _USER_STATE_PREFIXES):
            try:
                del st.session_state[key]
            except (KeyError, RuntimeError):
                pass
    # 账户相关的非 widget 状态也一并清掉。
    for key in ("auth", "auth_user", "_logout_requested"):
        st.session_state.pop(key, None)


def _paths_for(user: dict[str, Any]) -> UserPaths:
    return UserPaths.for_user(user).ensure()


def _login_success(user: dict[str, Any]) -> None:
    """建立会话、清理旧账户状态、绑定路径。调用方随后应 ``st.rerun()``。"""
    _clear_user_state()
    now = time.time()
    st.session_state["auth"] = {
        "user_id": user["id"],
        "issued_at": now,
        "last_seen": now,
        "auth_version": user.get("password_changed_at") or 0,
    }
    st.session_state["auth_user"] = user
    st.session_state["auth_notice"] = f"欢迎回来，{user.get('display_name') or user['username']}。"
    st.session_state["_clear_form_secrets"] = True
    activate(_paths_for(user))


def _session_valid(user: dict[str, Any] | None, session: dict[str, Any]) -> bool:
    if not user or user.get("status") != STATUS_ACTIVE:
        return False
    if str(session.get("user_id") or "") != str(user.get("id") or ""):
        return False
    if float(session.get("auth_version") or 0) != float(user.get("password_changed_at") or 0):
        # 密码被修改/重置后旧会话立即失效。
        return False
    now = time.time()
    issued = float(session.get("issued_at") or 0)
    last_seen = float(session.get("last_seen") or 0)
    if not issued or now - issued > SESSION_TTL_SECONDS:
        return False
    if not last_seen or now - last_seen > SESSION_IDLE_SECONDS:
        return False
    session["last_seen"] = now
    return True


def do_logout(*, reason: str = "") -> None:
    """清理会话与路径绑定。不调用 st.rerun（调用方决定）。"""
    tracker = st.session_state.get("tracker")
    running = bool(tracker is not None and getattr(tracker, "is_running", False))
    try:
        if running:
            tracker.request_stop()
    except Exception:
        pass
    token = st.session_state.pop("run_lease", None)
    if token and not running:
        # 正在跑的任务由 runner 线程在 finally 里释放；提前释放会让同一账户在
        # 退出后立刻再起一个并发分析。没有任务在跑时，这里的 token 是残留，
        # 释放掉避免把用户锁死。
        release_run(str(token))
    _clear_user_state()
    # 不在这里清表单密码：登出后的下一次 run 用户可能正在输入注册表单，
    # 在 run 开头删 widget key 会把刚提交的密码一起清掉。登录成功时清即可。
    deactivate()
    if reason:
        st.session_state["auth_notice"] = reason


# ── trusted header（反向代理认证）─────────────────────────────────────────


def _trusted_header_user(store: AuthStore) -> dict[str, Any] | None:
    """反向代理已认证时，用可信 header 自动登录。

    必须同时满足：显式开启 ``TRADINGAGENTS_TRUSTED_HEADER``、配置共享密钥
    ``TRADINGAGENTS_TRUSTED_SECRET``、请求头密钥匹配。只应在应用**只被代理访问**
    （绑定 127.0.0.1 / 内网）时启用，否则 header 可被伪造。
    """
    header_name = os.environ.get("TRADINGAGENTS_TRUSTED_HEADER", "").strip()
    secret = os.environ.get("TRADINGAGENTS_TRUSTED_SECRET", "")
    if not header_name or not secret:
        return None
    headers = getattr(st.context, "headers", {}) or {}

    def _get(name: str) -> str:
        try:
            return str(headers.get(name) or headers.get(name.lower()) or "")
        except AttributeError:
            return ""

    import hmac as _hmac

    provided = _get(os.environ.get("TRADINGAGENTS_TRUSTED_SECRET_HEADER", "X-Auth-Secret"))
    if not provided or not _hmac.compare_digest(provided, secret):
        return None
    username = _get(header_name).strip().lower()
    if not username:
        return None
    user = store.get_user_by_username(username)
    if not user or user.get("status") != STATUS_ACTIVE:
        return None
    return user


# ── 页面 ────────────────────────────────────────────────────────────────────


def _auth_shell(title: str, subtitle: str) -> None:
    st.markdown(
        f"""
        <div style="text-align:center; margin: 2.5rem 0 1.5rem;">
            <div style="font-size:2.6rem; font-weight:900; letter-spacing:0.02em;">
                <span style="color:#ff5a1f;">{APP_MARK_LEFT}</span>&nbsp;
                <span style="color:#f5f1eb;">{APP_MARK_RIGHT}</span>
            </div>
            <div style="color:#888; font-size:0.95rem; margin-top:0.5rem;">{APP_DESCRIPTION}</div>
            <div style="color:#f5f1eb; font-size:1.15rem; font-weight:700; margin-top:1.4rem;">{title}</div>
            <div style="color:#666; font-size:0.82rem; margin-top:0.35rem;">{subtitle}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _render_login_tab(store: AuthStore) -> None:
    with st.form("login_form", clear_on_submit=False):
        username = st.text_input("用户名", key="login_username", autocomplete="username")
        password = st.text_input(
            "密码", key="login_password", type="password", autocomplete="current-password"
        )
        submitted = st.form_submit_button("登录", type="primary", use_container_width=True)
    if not submitted:
        return

    ip = client_ip()
    identity_key, ip_key = login_keys(ip, username or "-")
    # 先原子记录再认证。之前的 check-then-act 在并发登录时所有请求都能在
    # 第一次 hit 落库前通过检查，等于把 5 次/15 分钟放大成「并发数 × 5」。
    identity_check = LIMITER.hit(
        identity_key, limit=LOGIN_LIMIT_PER_IDENTITY, window=LOGIN_WINDOW_SECONDS
    )
    if not identity_check.allowed:
        st.error(f"尝试过于频繁，请 {identity_check.retry_after} 秒后再试。")
        return
    ip_check = LIMITER.hit(ip_key, limit=LOGIN_LIMIT_PER_IP, window=LOGIN_WINDOW_SECONDS)
    if not ip_check.allowed:
        st.error(f"尝试过于频繁，请 {ip_check.retry_after} 秒后再试。")
        return

    try:
        user, error = store.authenticate(username, password, ip=ip)
    except AuthError as exc:
        st.error(exc.message)
        return
    except OSError as exc:
        st.error(f"账户数据写入失败：{exc}")
        return

    if user is None:
        if error == "disabled":
            st.error("该账户已被管理员停用。")
        else:
            st.error("用户名或密码错误。")
        return

    # 成功登录把两个计数都清掉：刷新页面会丢登录，朋友一天要登录很多次，
    # 让成功登录占用 IP 预算会导致 50 次/15 分钟后全员被锁。
    LIMITER.reset(identity_key)
    LIMITER.reset(ip_key)
    _login_success(user)
    st.rerun()


def _render_register_tab(store: AuthStore) -> None:
    try:
        settings = store.settings()
    except AuthError as exc:
        st.error(exc.message)
        return
    if not settings.get("allow_registration", True):
        st.info("管理员已关闭注册。请让 owner 生成邀请码或直接为你开通账户。")
        return

    st.caption(
        f"注册需要 owner 提供的邀请码。当前账户数上限 {settings.get('max_users')}，"
        "仅限认识的亲友使用。"
    )
    with st.form("register_form", clear_on_submit=False):
        code = st.text_input("邀请码", key="reg_invite", placeholder="例: ABCD-EFGH-JKLM")
        username = st.text_input(
            "用户名", key="reg_username", help="3~24 位字母/数字/下划线/连字符，登录用"
        )
        display_name = st.text_input("昵称（可选）", key="reg_display_name")
        password = st.text_input("密码", key="reg_password", type="password", help="至少 8 位")
        confirm = st.text_input("确认密码", key="reg_password2", type="password")
        submitted = st.form_submit_button("注册并登录", type="primary", use_container_width=True)
    if not submitted:
        return

    ip = client_ip()
    limit = LIMITER.hit(
        registration_key(ip), limit=REGISTER_LIMIT_PER_IP, window=REGISTER_WINDOW_SECONDS
    )
    if not limit.allowed:
        st.error(f"注册尝试过于频繁，请 {limit.retry_after} 秒后再试。")
        return
    if password != confirm:
        st.error("两次输入的密码不一致。")
        return
    try:
        user = store.register(code, username, password, display_name, ip=ip)
    except AuthError as exc:
        st.error(exc.message)
        return
    except ValueError as exc:
        st.error(str(exc))
        return
    except OSError as exc:
        st.error(f"账户数据写入失败：{exc}")
        return

    _login_success(user)
    st.rerun()


def _render_bootstrap(store: AuthStore) -> None:
    _auth_shell("首次启动：创建 owner 账户", "这台服务器还没有任何账户。owner 拥有管理权限。")
    legacy = tradingagents_home()
    if (legacy / "logs").exists() or (legacy / "team_config.json").exists():
        st.info(
            "检测到旧版单用户数据。创建 owner 后，owner 会直接继续使用这个目录，"
            "已有历史报告 / 团队配置 / 缓存不会丢失。",
            icon="📦",
        )
    st.warning(
        "创建后，owner 继续使用旧的 `~/.tradingagents/` 目录，CLI 与 Web 看到同一份"
        "历史，不会搬动/复制你的报告和缓存；其他朋友需要邀请码注册，各自隔离在"
        "`users/<id>/` 下。",
        icon="🔐",
    )
    with st.form("bootstrap_form", clear_on_submit=False):
        username = st.text_input("owner 用户名", key="bootstrap_username")
        display_name = st.text_input("昵称（可选）", key="bootstrap_display_name")
        password = st.text_input(
            "密码", key="bootstrap_password", type="password", help="至少 8 位"
        )
        confirm = st.text_input("确认密码", key="bootstrap_password2", type="password")
        submitted = st.form_submit_button("创建 owner", type="primary", use_container_width=True)
    if not submitted:
        st.caption("⚠️ 仅供学习研究，不构成投资建议。")
        return
    if password != confirm:
        st.error("两次输入的密码不一致。")
        return
    try:
        user = store.create_owner(username, password, display_name, ip=client_ip())
    except (AuthError, ValueError, OSError) as exc:
        st.error(getattr(exc, "message", str(exc)))
        return

    _paths_for(user)
    _login_success(user)
    st.session_state["auth_notice"] = (
        "owner 账户已创建。owner 继续使用旧的 ~/.tradingagents/ 目录，"
        "CLI 与 Web 看到同一份历史；朋友的数据隔离在 users/<id>/ 下。"
    )
    st.rerun()


def _render_auth_page(store: AuthStore) -> None:
    _auth_shell("登录", "只有被邀请的朋友可以创建账户。")
    tab_login, tab_register = st.tabs(["登录", "注册（需要邀请码）"])
    with tab_login:
        _render_login_tab(store)
    with tab_register:
        _render_register_tab(store)
    st.markdown(
        """
        <div style="text-align:center; color:#555; font-size:0.72rem; margin-top:2rem; line-height:1.7;">
            ⚠️ 本工具仅供学习研究与技术演示，不构成任何投资建议。<br>
            账户数据按用户隔离；发现异常请立即联系 owner。
        </div>
        """,
        unsafe_allow_html=True,
    )


def require_login() -> dict[str, Any]:
    """认证门。未登录时渲染登录/注册页并 ``st.stop()``；成功返回 public_user。"""
    # 退出登录 / 登录成功后的密码字段清理必须在最开头处理：此时业务 widget
    # 还没有实例化，删除 session_state 里的 widget key 不会被 Streamlit 拒绝。
    if st.session_state.pop("_clear_form_secrets", False):
        _drop_form_secret_keys()
    if st.session_state.pop("_logout_requested", False):
        do_logout(reason="已退出登录。")

    store = _store()
    try:
        has_users = store.has_users()
    except AuthError as exc:
        st.error(f"❌ {exc.message}")
        st.caption(
            "为防账户被重置，系统不会在账户文件损坏时进入「创建 owner」流程。"
            "请从备份恢复 `~/.tradingagents/auth/accounts.json`。"
        )
        st.stop()

    if not has_users:
        _render_bootstrap(store)
        st.stop()

    # 1) 已有会话：校验状态、密码版本、TTL。
    session = st.session_state.get("auth")
    user = current_user()
    if isinstance(session, dict) and isinstance(user, dict):
        try:
            fresh = store.get_user(user.get("id", ""))
        except AuthError as exc:
            st.error(f"❌ {exc.message}")
            st.stop()
        if _session_valid(fresh, session):
            st.session_state["auth_user"] = fresh
            paths = _paths_for(fresh)
            activate(paths)
            return fresh
        do_logout(reason="登录状态已失效，请重新登录。")

    # 2) 反向代理可信 header（可选）。
    try:
        trusted = _trusted_header_user(store)
    except AuthError as exc:
        st.error(f"❌ {exc.message}")
        st.stop()
    if trusted is not None:
        try:
            trusted = store.record_proxy_login(trusted["id"], ip=client_ip())
        except (AuthError, OSError):
            trusted = None
        if trusted is not None:
            _login_success(trusted)
            st.rerun()

    # 3) 登录/注册页。
    notice = st.session_state.pop("auth_notice", "")
    if notice:
        st.info(notice)
    _render_auth_page(store)
    st.stop()
