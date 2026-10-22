"""「我的账户」与 owner 管理页。"""

from __future__ import annotations

import html
import os
import time
from typing import Any

import streamlit as st

from web.auth.store import (
    DEFAULT_INVITE_TTL_HOURS,
    MAX_INVITE_TTL_HOURS,
    MAX_INVITE_USES,
    ROLE_OWNER,
    STATUS_ACTIVE,
    AuthError,
    AuthStore,
)
from web.auth.ui import client_ip, current_user, is_owner
from web.llm_providers import (
    ENV_VAR_NAMES,
    PROVIDER_KEYS,
    env_var_for,
    provider_label,
    provider_needs_key,
)
from web.secrets_store import delete_api_key, load_secrets, mask_key, set_api_key
from web.user_context import current_paths
from web.user_paths import tradingagents_home


def _store() -> AuthStore:
    return AuthStore(tradingagents_home())


def _fmt_ts(ts: Any) -> str:
    try:
        value = float(ts)
    except (TypeError, ValueError):
        return "—"
    if value <= 0:
        return "—"
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(value))


def _usage_text(paths) -> str:
    used = paths.disk_usage_bytes() / 1024 / 1024
    return f"{used:.1f} MB"


def _render_account_tab(user: dict[str, Any], paths) -> None:
    cols = st.columns(4)
    cols[0].metric("账户", user.get("display_name") or user["username"])
    cols[1].metric("角色", "owner" if is_owner(user) else "member")
    cols[2].metric("数据占用", _usage_text(paths))
    cols[3].metric("上次登录", _fmt_ts(user.get("last_login_at")))

    st.caption(f"用户名：`{user['username']}` · 创建于 {_fmt_ts(user.get('created_at'))}")

    st.markdown("#### 修改密码")
    with st.form("account_change_password"):
        current = st.text_input("当前密码", type="password", key="account_pw_current")
        new = st.text_input("新密码（至少 8 位）", type="password", key="account_pw_new")
        confirm = st.text_input("确认新密码", type="password", key="account_pw_confirm")
        submitted = st.form_submit_button("更新密码", type="primary")
    if submitted:
        if new != confirm:
            st.error("两次输入的新密码不一致。")
        else:
            try:
                _store().change_password(
                    user["id"], current, new, ip=client_ip()
                )
            except (AuthError, ValueError, OSError) as exc:
                st.error(getattr(exc, "message", str(exc)))
            else:
                refreshed = _store().get_user(user["id"])
                if refreshed:
                    st.session_state["auth_user"] = refreshed
                    st.session_state["auth"]["auth_version"] = refreshed.get(
                        "password_changed_at"
                    ) or 0
                st.success("密码已更新；其它已登录会话会立即失效。")

    st.markdown("#### 数据位置")
    st.code(str(paths.root), language=None)
    if paths.legacy:
        st.caption(
            "owner 复用旧单用户根目录（CLI 与 Web 共享历史）。`auth/` 是账户库，"
            "`users/` 下是其他账户；owner 的业务读写只落在 logs/cache/memory 等子目录。"
        )
    else:
        st.caption("该目录只属于当前账户；历史报告、断点、团队配置、API Key 都在这里。")

    st.markdown("---")
    if st.button("退出登录", key="account_logout"):
        st.session_state["_logout_requested"] = True
        st.rerun()


def _render_api_keys_tab(user: dict[str, Any], paths) -> None:
    keys = load_secrets(paths.secrets)
    st.markdown("#### 我的模型 API Key")
    st.caption(
        "Key 保存在你账户目录下的 `secrets.json`（0600），页面只显示末 4 位，"
        "不会跟随团队配置导出，也不会被其他用户读取。"
    )

    provider = st.selectbox(
        "供应商",
        PROVIDER_KEYS,
        format_func=provider_label,
        key="account_key_provider",
    )
    env_name = env_var_for(provider)

    if not provider_needs_key(provider):
        st.info("Ollama 走本地服务，不需要 API Key。")
        return

    current_key = keys.get(provider)
    if current_key:
        st.success(f"已配置：`{mask_key(current_key)}`")
    else:
        st.warning("未配置 Key。开始分析前需要先保存。")

    if is_owner(user):
        env_present = bool(os.getenv(env_name))
        if env_present:
            st.caption(
                f"owner 也可直接使用服务器 `.env` 里的 `{env_name}`；"
                "账户里保存的 Key 优先级更高。"
            )
        else:
            st.caption(f"服务器 `.env` 里未检测到 `{env_name}`。")
    else:
        st.caption("普通成员只能使用自己账户里保存的 Key；服务器 `.env` 的 Key 不会借给你用。")

    with st.form(f"account_save_key_{provider}", clear_on_submit=True):
        value = st.text_input(
            f"{provider_label(provider)} API Key",
            type="password",
            key="account_key_value",
            placeholder="粘贴后保存；不会回显",
        )
        saved = st.form_submit_button("保存 Key", type="primary")
    if saved:
        try:
            set_api_key(paths.secrets, provider, value)
        except (ValueError, OSError) as exc:
            st.error(str(exc))
        else:
            st.success(f"{provider_label(provider)} 的 Key 已保存。")

    if current_key and st.button("清除这个供应商的 Key", key=f"account_key_clear_{provider}"):
        try:
            delete_api_key(paths.secrets, provider)
        except OSError as exc:
            st.error(f"删除失败：{exc}")
        else:
            st.success("已清除。")

    st.markdown("---")
    st.markdown("#### 当前已配置的供应商")
    if not keys:
        st.caption("还没有保存任何 Key。")
    else:
        rows = [
            {
                "供应商": provider_label(name),
                "Key": mask_key(value),
                "环境变量（仅 owner 兜底）": ENV_VAR_NAMES.get(name, "—"),
            }
            for name, value in sorted(keys.items())
        ]
        st.dataframe(rows, hide_index=True, use_container_width=True)


def _render_users_admin(store: AuthStore, actor: dict[str, Any]) -> None:
    users = store.list_users()
    st.caption(f"共 {len(users)} 个账户。停用账户不会删除数据，重新启用后可继续使用。")
    for target in users:
        with st.container(border=True):
            cols = st.columns([3, 2, 1.4, 1.4, 1.6])
            shown = html.escape(
                str(target.get("display_name") or target["username"])
            )
            cols[0].markdown(f"**{shown}**  \n`{target['username']}`")
            cols[1].caption(
                ("owner" if target.get("role") == ROLE_OWNER else "member")
                + " · "
                + ("正常" if target.get("status") == STATUS_ACTIVE else "已停用")
            )
            cols[2].caption(f"注册\n{_fmt_ts(target.get('created_at'))}")
            cols[3].caption(f"最近\n{_fmt_ts(target.get('last_login_at'))}")
            if target["id"] == actor["id"]:
                cols[4].caption("当前账户")
                continue
            next_status = (
                "disabled" if target.get("status") == STATUS_ACTIVE else "active"
            )
            label = "停用" if next_status == "disabled" else "启用"
            if cols[4].button(label, key=f"admin_toggle_{target['id']}", use_container_width=True):
                try:
                    store.set_status(actor["id"], target["id"], next_status, ip=client_ip())
                except (AuthError, OSError) as exc:
                    st.error(exc.message)
                else:
                    st.success(f"已{label} {target['username']}。")
                    st.rerun()

    members = [u for u in users if u["id"] != actor["id"]]
    st.markdown("#### 重置成员密码")
    if not members:
        st.caption("暂无其它账户。")
        return
    with st.form("admin_reset_password"):
        target_name = st.selectbox(
            "账户",
            [u["username"] for u in members],
            key="admin_reset_target",
        )
        new = st.text_input("新密码（至少 8 位）", type="password", key="admin_reset_pw")
        confirm = st.text_input("确认新密码", type="password", key="admin_reset_pw2")
        submitted = st.form_submit_button("重置密码")
    if submitted:
        if new != confirm:
            st.error("两次输入的密码不一致。")
            return
        target = next((u for u in members if u["username"] == target_name), None)
        if target is None:
            st.error("账户不存在。")
            return
        try:
            store.reset_password(actor["id"], target["id"], new, ip=client_ip())
        except (AuthError, ValueError, OSError) as exc:
            st.error(getattr(exc, "message", str(exc)))
        else:
            st.success(f"已重置 {target_name} 的密码；其旧会话会立即失效。")


def _render_invites_admin(store: AuthStore, actor: dict[str, Any]) -> None:
    st.caption("邀请码只显示一次（库里只存哈希）。默认 72 小时、1 次使用；过期/用完自动失效。")
    with st.form("admin_create_invite"):
        note = st.text_input("备注（给谁用）", key="admin_invite_note", placeholder="例：老王")
        c1, c2 = st.columns(2)
        ttl = c1.number_input(
            "有效期（小时）",
            min_value=1,
            max_value=MAX_INVITE_TTL_HOURS,
            value=DEFAULT_INVITE_TTL_HOURS,
            key="admin_invite_ttl",
        )
        uses = c2.number_input(
            "可用次数",
            min_value=1,
            max_value=MAX_INVITE_USES,
            value=1,
            key="admin_invite_uses",
        )
        submitted = st.form_submit_button("生成邀请码", type="primary")
    if submitted:
        try:
            _invite, code = store.create_invite(
                actor["id"],
                note=note,
                ttl_hours=int(ttl),
                max_uses=int(uses),
                ip=client_ip(),
            )
        except (AuthError, ValueError, OSError) as exc:
            st.error(getattr(exc, "message", str(exc)))
        else:
            st.session_state["new_invite_code"] = code
            st.session_state["new_invite_note"] = note

    code = st.session_state.get("new_invite_code")
    if code:
        st.success("邀请码已生成，请现在复制发给朋友（刷新或再次生成后不再显示）。")
        st.code(code, language=None)
        if st.button("我已保存，隐藏", key="admin_hide_invite"):
            st.session_state.pop("new_invite_code", None)
            st.session_state.pop("new_invite_note", None)
            st.rerun()

    st.markdown("#### 邀请码列表")
    invites = store.list_invites()
    if not invites:
        st.caption("还没有生成过邀请码。")
        return
    now = time.time()
    for invite in invites:
        active = invite.get("status") == "active" and float(invite.get("expires_at") or 0) > now
        status_label = "有效" if active else {
            "used": "已用完",
            "revoked": "已撤销",
        }.get(invite.get("status"), "已过期")
        with st.container(border=True):
            cols = st.columns([2.6, 1.2, 1.2, 1.6, 1])
            cols[0].markdown(f"**{invite.get('note') or '无备注'}**  \n`{invite['id']}`")
            cols[1].caption(f"{status_label}\n{invite.get('uses')}/{invite.get('max_uses')} 次")
            cols[2].caption(f"过期\n{_fmt_ts(invite.get('expires_at'))}")
            cols[3].caption("已注册：" + ("、".join(invite.get("redeemed_usernames") or []) or "—"))
            if active and cols[4].button("撤销", key=f"admin_revoke_{invite['id']}"):
                try:
                    store.revoke_invite(actor["id"], invite["id"], ip=client_ip())
                except (AuthError, OSError) as exc:
                    st.error(exc.message)
                else:
                    st.rerun()


def _render_settings_admin(store: AuthStore, actor: dict[str, Any]) -> None:
    try:
        settings = store.settings()
    except (AuthError, OSError) as exc:
        st.error(exc.message)
        return
    count = store.user_count()
    st.caption(
        f"当前 {count} 个账户；环境变量允许的硬上限为 {settings['max_users_cap']} "
        "（`TRADINGAGENTS_MAX_USERS`）。"
    )
    cap = max(int(settings["max_users_cap"]), count)
    with st.form("admin_settings"):
        allow = st.checkbox(
            "允许凭邀请码注册",
            value=bool(settings.get("allow_registration", True)),
            key="admin_allow_registration",
        )
        max_users = st.number_input(
            "账户数上限",
            min_value=max(1, count),
            max_value=cap,
            value=max(count, min(int(settings["max_users"]), cap)),
            key="admin_max_users",
        )
        submitted = st.form_submit_button("保存策略", type="primary")
    if submitted:
        try:
            store.update_settings(
                actor["id"],
                allow_registration=allow,
                max_users=int(max_users),
                ip=client_ip(),
            )
        except (AuthError, ValueError, OSError) as exc:
            st.error(getattr(exc, "message", str(exc)))
        else:
            st.success("注册策略已保存。")


def _render_audit_admin(store: AuthStore) -> None:
    rows = store.audit_tail(100)
    st.caption("最近 100 条账户/登录/注册事件。审计日志不含密码与 API Key。")
    if not rows:
        st.caption("暂无审计记录。")
        return
    display = [
        {
            "时间": _fmt_ts(row.get("ts")),
            "事件": row.get("event", ""),
            "用户": row.get("username", ""),
            "IP": row.get("ip", ""),
        }
        for row in reversed(rows)
    ]
    st.dataframe(display, hide_index=True, use_container_width=True)


def _render_admin_tab(store: AuthStore, actor: dict[str, Any]) -> None:
    tabs = st.tabs(["用户管理", "邀请码", "注册策略", "审计日志"])
    with tabs[0]:
        _render_users_admin(store, actor)
    with tabs[1]:
        _render_invites_admin(store, actor)
    with tabs[2]:
        _render_settings_admin(store, actor)
    with tabs[3]:
        _render_audit_admin(store)


def render_account_view() -> None:
    """账户页入口。未登录/路径未绑定时给出明确错误而不是静默回落。"""
    user = current_user()
    paths = current_paths()
    if not user or paths is None:
        st.error("账户上下文不可用，请重新登录。")
        if st.button("返回登录", key="account_relogin"):
            st.session_state["_logout_requested"] = True
            st.rerun()
        return

    st.markdown("## 👤 我的账户")
    tabs = ["账户", "API Key"]
    if is_owner(user):
        tabs.append("管理")
    rendered = st.tabs(tabs)
    with rendered[0]:
        _render_account_tab(user, paths)
    with rendered[1]:
        _render_api_keys_tab(user, paths)
    if is_owner(user):
        with rendered[2]:
            _render_admin_tab(_store(), user)
