"""Sidebar: stock input, LLM config, API keys, account nav, and history list."""

from __future__ import annotations

import html
import json
import logging
import os
from datetime import date
from pathlib import Path

import streamlit as st

from tradingagents.default_config import DEFAULT_CONFIG
from tradingagents.graph.checkpointer import clear_checkpoint
from tradingagents.llm_clients.model_catalog import MODEL_OPTIONS
from web.branding import (
    APP_MARK_LEFT,
    APP_MARK_RIGHT,
    APP_TAGLINE,
    AUTHOR,
    AUTHOR_URL,
    VERSION,
)
from web.history import (
    clear_incomplete_task,
    get_history,
    get_incomplete_history,
    record_incomplete_task,
)
from web.llm_providers import (
    PROVIDER_DISPLAY as _PROVIDER_DISPLAY,
    PROVIDER_KEYS as _PROVIDER_KEYS,
    PROVIDERS as _PROVIDERS,
    env_var_candidates,
    env_var_for,
    provider_label,
    provider_needs_key,
)
from web.secrets_store import delete_api_key, load_secrets, mask_key, set_api_key
from web.url_safety import validate_base_url
from web.user_context import current_paths

logger = logging.getLogger(__name__)

# ── LLM config persistence ────────────────────────────────────────────────────
# Saves model selection to a JSON file so it survives browser tab close/reopen.
# 多用户模式：路径由 web.user_context 绑定到当前账户；未登录（CLI / 测试 / 旧
# 单用户模式）时回落到 ~/.tradingagents/llm_config.json。
_LLM_CONFIG_PATH = Path(os.path.expanduser("~")) / ".tradingagents" / "llm_config.json"
# 侧栏「个人 Claude 订阅覆盖」选项的取值；selectbox 的 widget 键是 subscription_scope_idx，
# 恢复时必须写这个索引，只写派生值 subscription_scope 会在渲染时被覆盖回默认。
_SCOPE_VALUES = ["off", "deep", "all"]


def _llm_config_path() -> Path:
    paths = current_paths()
    return paths.llm_config if paths is not None else _LLM_CONFIG_PATH


def _analysis_cache_dir() -> str:
    paths = current_paths()
    return str(paths.cache) if paths is not None else DEFAULT_CONFIG["data_cache_dir"]


def _current_user() -> dict | None:
    user = st.session_state.get("auth_user")
    return user if isinstance(user, dict) else None


def _is_owner() -> bool:
    """当前账户是否 owner。

    没有登录用户时（CLI / 测试 / 旧单用户模式）按 owner 处理，保持旧行为；
    但**只要已经绑定了 Web 用户上下文**（current_paths 非空），缺少 auth_user
    就按 member 处理（fail-closed），避免渲染顺序回归时泄漏 owner 权限。
    """
    user = _current_user()
    if user is not None:
        return user.get("role") == "owner"
    return current_paths() is None


# `MODEL_OPTIONS` 里每个供应商都带一条哨兵项 `("Custom model ID", "custom")`，含义是
# "自己填模型 ID"。CLI 会就此追问输入（cli/utils.py::_select_model），Web 却把这个
# 字符串**当成模型名直接发给 API**，于是用户拿到的是一句看不懂的服务端错误：
#   The supported API model names are deepseek-flash, deepseek-v4-pro,
#   but you passed custom.
_CUSTOM_MODEL_SENTINEL = "custom"


def _resolve_model_value(value: str, text_key: str, label: str) -> str:
    """把下拉选中的模型值翻成真正要发给 API 的模型 ID。

    选中哨兵项时渲染一个文本输入框、用它填的 ID——补上 CLI 已有的那一步。输入框复用
    `custom_quick_model` / `custom_deep_model` 两个键，而这两个键本来就在配置持久化
    清单里（`_save_llm_config` / `_load_saved_llm_config`），所以填过的 ID 会被记住。
    """
    if value != _CUSTOM_MODEL_SENTINEL:
        return value
    return st.text_input(
        label,
        key=text_key,
        placeholder="填该供应商实际支持的模型 ID，例: deepseek-v4-pro",
    ).strip()


def validate_model_selection(config: dict | None = None) -> str | None:
    """拦住「模型 ID 没填」，别让它变成服务端的一句 400。

    模型配置在折叠面板里，选成「Custom model ID」却没填输入框时用户看不到任何提示，
    请求会带着 'custom' 或空串打出去，报回来的是「你传了 custom」「你传了 .」这种与
    界面完全对不上号的错误。

    **必须同时挂在唯一的消费点**（web/app.py 拿到 start_analysis 之后、真正起线程之前）：
    `start_analysis` 有 4 个生产者——侧栏「开始分析」、侧栏「未完成任务」续跑、
    报告页「重新分析」、错误页「继续未完成任务」（历史记录只把它清空）——只在按钮里守一个，其余三条路径照样
    把空模型发出去（实测踩过：深模型选「Custom model ID」且留空，走续跑路径，
    收到 `but you passed .`）。

    `config` 传入**真正要发出去的配置**时校验的就是最终值；不传则退回读 session_state，
    供侧栏按钮做即时反馈。
    """
    source = config if config is not None else st.session_state
    for label, key in (
        ("快速思考模型", "quick_think_llm"),
        ("深度思考模型", "deep_think_llm"),
    ):
        model = str(source.get(key) or "").strip()
        if not model or model == _CUSTOM_MODEL_SENTINEL:
            return (
                f"{label}的模型 ID 没填。请在「⚙️ 模型配置」里选一个模型，"
                "或选「Custom model ID」后填上该供应商实际支持的 ID。"
            )

    # 多用户模式下，普通成员的 Key 只来自自己的 secrets.json；没有就当场拦住，
    # 不要等请求打到上游才收到 401（更糟的是 owner 的 .env Key 被悄悄用掉）。
    # `allow_env_api_keys` 只在 Web 多用户配置里出现，CLI / 旧测试不受影响。
    if "allow_env_api_keys" in source:
        provider = str(source.get("llm_provider") or "").strip().lower()
        allow_env = bool(source.get("allow_env_api_keys"))
        base_url = str(
            source.get("backend_url") or source.get("llm_base_url") or ""
        ).strip()
        if provider == "openai_compatible" and not base_url:
            return "「OpenAI 兼容」供应商需要填写 API Base URL（你的网关地址）。"
        # 普通成员可以自定义端点，但**不能让服务器去请求内网/元数据地址**。
        # owner（allow_env=True）跳过：owner 可能故意接本机/内网中转。
        if base_url and not allow_env:
            try:
                validate_base_url(base_url)
            except ValueError as exc:
                return f"API Base URL 不安全：{exc}"
        if provider_needs_key(provider):
            keys = source.get("llm_api_keys") or {}
            has_key = isinstance(keys, dict) and bool(str(keys.get(provider) or "").strip())
            env_name = env_var_for(provider)
            has_env = allow_env and any(
                os.environ.get(name) for name in env_var_candidates(provider)
            )
            if not has_key and not has_env and not allow_env:
                return (
                    f"{provider_label(provider)} 还没配置 API Key。"
                    f"请在「⚙️ 模型配置」或「👤 账户 → API Key」里保存你自己的 Key"
                    f"（服务器不会借用 owner 的 {env_name}）。"
                )
            # owner 缺 Key 不在这里硬拦：owner 可能开着 Claude 订阅覆盖（走本机
            # 凭据，不需要 API Key）。真需要 Key 时客户端会给出明确错误。
    return None


def _load_saved_llm_config() -> None:
    """Restore user's last model selection into session_state defaults."""
    try:
        cfg = json.loads(_llm_config_path().read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return
    provider_key = cfg.get("llm_provider", "")
    try:
        idx = _PROVIDER_KEYS.index(provider_key)
    except ValueError:
        idx = 0
    st.session_state.setdefault("llm_provider_idx", idx)
    st.session_state.setdefault("quick_model_idx", cfg.get("quick_model_idx", 0))
    st.session_state.setdefault("deep_model_idx", cfg.get("deep_model_idx", 0))
    st.session_state.setdefault("llm_base_url", cfg.get("llm_base_url", ""))
    # 普通成员不允许使用 owner 的 Claude 订阅额度：即使旧配置里存了 scope，
    # 加载时也强制关掉（渲染层还会再挡一次）。
    if not _is_owner():
        # 显式覆盖，不用 setdefault：登录前残留的 deep/all 不能让成员继承。
        st.session_state["subscription_scope"] = "off"
        st.session_state["subscription_scope_idx"] = 0
    else:
        scope = cfg.get("subscription_scope", "off")
        st.session_state.setdefault("subscription_scope", scope)
        st.session_state.setdefault(
            "subscription_scope_idx",
            _SCOPE_VALUES.index(scope) if scope in _SCOPE_VALUES else 0,
        )
    if cfg.get("agent_sdk_model"):
        st.session_state.setdefault("agent_sdk_model", cfg["agent_sdk_model"])
    for key in ("custom_quick_model", "custom_deep_model"):
        if key in cfg and cfg[key]:
            st.session_state.setdefault(key, cfg[key])


def _save_llm_config() -> None:
    """Persist current LLM config to disk (called before analysis)."""
    cfg = {
        "llm_provider": st.session_state.get("llm_provider", "minimax"),
        "quick_model_idx": st.session_state.get("quick_model_idx", 0),
        "deep_model_idx": st.session_state.get("deep_model_idx", 0),
        "llm_base_url": st.session_state.get("llm_base_url", ""),
        "subscription_scope": (
            st.session_state.get("subscription_scope", "off") if _is_owner() else "off"
        ),
    }
    if st.session_state.get("agent_sdk_model"):
        cfg["agent_sdk_model"] = st.session_state["agent_sdk_model"]
    for key in ("custom_quick_model", "custom_deep_model"):
        val = st.session_state.get(key)
        if val:
            cfg[key] = val
    # 持久化失败（目录只读 / 磁盘满）只记 warning，不得打断「开始分析」主流程
    try:
        target = _llm_config_path()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(cfg, ensure_ascii=False, indent=2))
    except OSError as exc:
        logger.warning("LLM 配置持久化失败（不影响本次分析）: %s", exc)


# Provider display names / keys 来自 web.llm_providers（账户页共用同一份清单）。


def _resolve_user_input(raw: str) -> tuple[str, str | None]:
    """Resolve raw user input to (ticker_code, error_msg).

    Accepts 6-digit codes or Chinese stock names (e.g. '宝光股份').
    Returns (code, None) on success or ("", error_msg) on failure.
    """
    from tradingagents.dataflows.a_stock import resolve_ticker

    try:
        code = resolve_ticker(raw)
        return code, None
    except ValueError as e:
        return "", str(e)


def _clear_analysis_artifacts(ticker: str, trade_date: str) -> None:
    clear_incomplete_task(ticker, trade_date)
    clear_checkpoint(_analysis_cache_dir(), ticker, trade_date)


def _render_analysis_controls(raw_ticker: str, trade_date_value: date) -> None:
    tracker = st.session_state.get("tracker")
    is_running = tracker is not None and tracker.is_running
    trade_date = trade_date_value.strftime("%Y-%m-%d")

    pause_col, resume_col, stop_col = st.columns(3)

    pause_disabled = not is_running or tracker.is_paused or tracker.stop_requested
    if pause_col.button(
        "暂停",
        key="sidebar_pause_analysis",
        use_container_width=True,
        disabled=pause_disabled,
    ):
        if tracker.pause():
            record_incomplete_task(
                tracker.ticker,
                tracker.trade_date,
                status="paused",
                completed_stages=tracker.completed_stages,
            )
        st.rerun()

    resume_disabled = not is_running or not tracker.is_paused or tracker.stop_requested
    if resume_col.button(
        "恢复",
        key="sidebar_resume_analysis",
        use_container_width=True,
        disabled=resume_disabled,
    ):
        if tracker.resume():
            record_incomplete_task(
                tracker.ticker,
                tracker.trade_date,
                status="running",
                completed_stages=tracker.completed_stages,
            )
        st.rerun()

    can_stop = tracker is not None or bool(raw_ticker.strip())
    if stop_col.button(
        "停止",
        key="sidebar_stop_analysis",
        use_container_width=True,
        disabled=not can_stop,
    ):
        target_ticker = tracker.ticker if tracker is not None and tracker.ticker else ""
        target_date = (
            tracker.trade_date
            if tracker is not None and tracker.trade_date
            else trade_date
        )

        if not target_ticker:
            target_ticker, err = _resolve_user_input(raw_ticker)
            if err:
                st.error(f"❌ {err}")
                return

        if tracker is not None and tracker.is_running:
            tracker.request_stop()
            clear_incomplete_task(target_ticker, target_date)
        else:
            if tracker is not None:
                tracker.mark_stopped()
                st.session_state["tracker"] = None
            _clear_analysis_artifacts(target_ticker, target_date)

        st.session_state["viewing_history"] = None
        st.success("已清空当前进度；下一次开始分析会从头生成。")
        st.rerun()

    if tracker is not None and tracker.stop_requested:
        st.caption("正在停止并清空，收尾完成后可重新开始。")


def _render_api_key_control(provider_key: str) -> None:
    """按账户保存当前供应商的 API Key（不回显、不写入 llm_config.json）。"""
    if not provider_needs_key(provider_key):
        if provider_key == "ollama":
            st.caption("Ollama 走本地服务，不需要 API Key。")
        return
    paths = current_paths()
    if paths is None:
        return

    owner = _is_owner()
    keys = load_secrets(paths.secrets)
    # session_state 只放「有没有 Key」，不复制 Key 明文；真正的 Key 在渲染配置时
    # 才从 secrets.json 读到局部变量里。
    st.session_state["llm_api_keys"] = {name: True for name, value in keys.items() if value}
    st.session_state["allow_env_api_keys"] = owner
    current_key = keys.get(provider_key)
    env_name = env_var_for(provider_key)

    if current_key:
        st.success(f"本账户已配置 Key：`{mask_key(current_key)}`", icon="🔑")
    elif owner and any(os.getenv(name) for name in env_var_candidates(provider_key)):
        st.caption(f"本账户未保存 Key，将使用服务器 `.env` 的 `{env_name}`（仅 owner）。")
    else:
        st.warning(f"未配置 {provider_label(provider_key)} 的 API Key；开始分析会被拦住。")

    input_key = f"api_key_input_{provider_key}"
    value = st.text_input(
        f"{provider_label(provider_key)} API Key",
        type="password",
        key=input_key,
        placeholder="粘贴后点保存；页面不会回显",
        help=(
            "Key 保存在你账户目录的 secrets.json（0600），不会跟随团队配置导出，"
            "其他用户读不到。"
        ),
    )
    save_col, clear_col = st.columns(2)
    if save_col.button("保存 Key", key=f"api_key_save_{provider_key}", use_container_width=True):
        try:
            updated = set_api_key(paths.secrets, provider_key, value)
        except (ValueError, OSError) as exc:
            st.error(str(exc))
        else:
            st.session_state["llm_api_keys"] = {
                name: True for name, value in updated.items() if value
            }
            st.success("已保存。")
            st.rerun()
    if current_key and clear_col.button(
        "清除 Key", key=f"api_key_clear_{provider_key}", use_container_width=True
    ):
        try:
            updated = delete_api_key(paths.secrets, provider_key)
        except OSError as exc:
            st.error(f"清除失败：{exc}")
        else:
            st.session_state["llm_api_keys"] = {
                name: True for name, value in updated.items() if value
            }
            st.success("已清除。")
            st.rerun()


def _render_llm_config() -> None:
    """Render LLM provider and model selection controls."""

    provider_idx = st.selectbox(
        "LLM 供应商",
        range(len(_PROVIDERS)),
        format_func=lambda i: _PROVIDER_DISPLAY[i],
        key="llm_provider_idx",
        help="选择你配置了 API Key 的供应商",
    )
    provider_key = _PROVIDER_KEYS[provider_idx]
    st.session_state["llm_provider"] = provider_key
    st.session_state["allow_env_api_keys"] = _is_owner()

    if provider_key in MODEL_OPTIONS:
        quick_options = MODEL_OPTIONS[provider_key]["quick"]
        deep_options = MODEL_OPTIONS[provider_key]["deep"]

        quick_labels = [label for label, _ in quick_options]
        quick_values = [value for _, value in quick_options]
        deep_labels = [label for label, _ in deep_options]
        deep_values = [value for _, value in deep_options]

        quick_idx = st.selectbox(
            "快速思考模型",
            range(len(quick_options)),
            format_func=lambda i: quick_labels[i],
            key="quick_model_idx",
            help="用于常规分析任务，速度优先",
        )
        st.session_state["quick_think_llm"] = _resolve_model_value(
            quick_values[quick_idx], "custom_quick_model", "快速思考模型 ID"
        )

        deep_idx = st.selectbox(
            "深度思考模型",
            range(len(deep_options)),
            format_func=lambda i: deep_labels[i],
            key="deep_model_idx",
            help="用于辩论/决策等需要深度推理的任务",
        )
        st.session_state["deep_think_llm"] = _resolve_model_value(
            deep_values[deep_idx], "custom_deep_model", "深度思考模型 ID"
        )
    else:
        custom_quick = st.text_input("快速思考模型 ID", key="custom_quick_model")
        custom_deep = st.text_input("深度思考模型 ID", key="custom_deep_model")
        st.session_state["quick_think_llm"] = custom_quick
        st.session_state["deep_think_llm"] = custom_deep

    base_url_required = provider_key == "openai_compatible"
    st.text_input(
        "API Base URL（第三方/代理" + ("·必填" if base_url_required else "，可选") + "）",
        key="llm_base_url",
        placeholder="例: https://your-relay.example/v1",
        help=(
            "通过第三方中转/代理访问模型时填写网关地址；留空则用所选供应商的官方地址。"
            "API Key 在下方按账户保存（每个供应商一把）；"
            "owner 也可以继续用服务器 .env 里的各家环境变量兜底。"
        ),
    )
    if base_url_required:
        st.caption(
            "已选「OpenAI 兼容（自定义）」：**Base URL 必填**（你的网关，走标准 Chat "
            "Completions），模型 ID 手动填写，Key 在下方保存。"
        )

    _render_api_key_control(provider_key)

    # ── 个人 Claude 订阅额度（仅 owner；使用服务器本机登录凭据）────────────
    if not _is_owner():
        # 订阅额度是**服务器级**凭据（本机 claude 登录 / CLAUDE_CODE_OAUTH_TOKEN），
        # 让普通成员打开等于让所有人共用 owner 的额度，且无法按人计费/限流。
        st.session_state["subscription_scope"] = "off"
        st.session_state["subscription_scope_idx"] = 0
        st.caption("Claude 订阅覆盖仅 owner 可用（使用服务器本机登录凭据），普通成员走自己的 API Key。")
        return

    _scope_labels = [
        "关闭（走上面选的供应商）",
        "仅深度节点（Research/Portfolio）",
        "所有节点（含 7 个工具分析师）",
    ]
    scope_idx = st.selectbox(
        "个人 Claude 订阅覆盖 (Agent SDK)",
        range(len(_scope_labels)),
        format_func=lambda i: _scope_labels[i],
        key="subscription_scope_idx",
        help=(
            "让部分/全部节点经 Claude Agent SDK 走你个人 Pro/Max 订阅额度，"
            "而非按 token 计费。「所有节点」含 7 个工具分析师（其工具调用已桥接到订阅）。"
            "需装 [agentsdk] 依赖，且本机 claude 已登录（或设 CLAUDE_CODE_OAUTH_TOKEN）。"
        ),
    )
    scope = _SCOPE_VALUES[scope_idx]
    st.session_state["subscription_scope"] = scope
    if scope != "off":
        # 用别名而非写死版本号：claude CLI 的 opus/sonnet 恒指向最新模型。
        st.session_state.setdefault("agent_sdk_model", "opus")
        st.text_input(
            "订阅使用的 Claude 模型",
            key="agent_sdk_model",
            help=(
                "填别名 opus / sonnet（恒指向最新模型，推荐）或完整模型 id。"
                "撞额度/失败时自动降级到上面选的供应商 + 对应模型。"
            ),
        )
        if scope == "all":
            st.caption(
                "⚠️ 「所有节点」会把 7 个分析师 + 多空/交易员/风险辩手全部压到订阅上，"
                "订阅是按额度限流的，跑几轮就可能撞上限。可在 config 里把 "
                "`agent_sdk_quick_model` 设为 `sonnet` 降低消耗（默认已是）。"
            )
        if os.getenv("ANTHROPIC_API_KEY"):
            st.info(
                "检测到 ANTHROPIC_API_KEY。它**不会**泄进 Agent SDK 子进程"
                "（已在子进程环境显式置空），所以订阅额度照常生效；"
                "父进程保留它，是为了让 `anthropic` 仍能作为撞额度后的降级 provider。"
                "如果你并不打算保留付费降级，可在 .env 里清掉它。"
            )


def render_sidebar() -> None:
    """Render the sidebar with input controls and history."""
    # Restore saved LLM config on every render so selectboxes start at the user's last selection.
    _load_saved_llm_config()

    # 用两个按钮而不是 radio：radio 的 key 一旦实例化就不能在按钮回调里写，
    # 否则 StreamlitAPIException；而「开始分析 / 历史记录」又必须能把视图切回
    # 工作台。按钮只写一个普通 session 变量，没有这个限制。
    st.session_state.setdefault("current_view", "workspace")

    user = _current_user()
    if user:
        role_label = "owner" if user.get("role") == "owner" else "member"
        # display_name 是用户可控的；这里用了 unsafe_allow_html，必须转义。
        shown_name = html.escape(
            str(user.get("display_name") or user.get("username") or "")
        )
        st.markdown(
            f"<div style='color:#888;font-size:0.78rem;margin-bottom:0.4rem;'>"
            f"👤 <b style='color:#f5f1eb;'>{shown_name}</b>"
            f" · {role_label}</div>",
            unsafe_allow_html=True,
        )
    notice = st.session_state.pop("auth_notice", None)
    if notice:
        st.info(notice, icon="✅")

    nav_workspace, nav_team = st.columns(2)
    if nav_workspace.button(
        "📈 工作台",
        key="nav_workspace",
        use_container_width=True,
        type="primary"
        if st.session_state["current_view"] == "workspace"
        else "secondary",
    ):
        st.session_state["current_view"] = "workspace"
        st.rerun()
    if nav_team.button(
        "🧑‍💼 投研团队",
        key="nav_team",
        use_container_width=True,
        type="primary" if st.session_state["current_view"] == "team" else "secondary",
    ):
        st.session_state["current_view"] = "team"
        st.rerun()

    nav_account, nav_logout = st.columns(2)
    if nav_account.button(
        "👤 账户",
        key="nav_account",
        use_container_width=True,
        type="primary" if st.session_state["current_view"] == "account" else "secondary",
    ):
        st.session_state["current_view"] = "account"
        st.rerun()
    if nav_logout.button("退出登录", key="nav_logout", use_container_width=True):
        # 不在按钮回调里直接删 session_state（会撞上已实例化 widget 的限制）；
        # 交给下一次 run 最开头的认证门清理。
        st.session_state["_logout_requested"] = True
        st.rerun()

    st.markdown(
        f"""
        <div style="text-align:center; margin-bottom:1.5rem;">
            <span style="font-size:2rem; font-weight:800; color:#ff5a1f;">{APP_MARK_LEFT}</span>&nbsp;<span style="font-size:2rem; font-weight:800; color:#f5f1eb;">{APP_MARK_RIGHT}</span>
            <div style="font-size:0.85rem; color:#888; margin-top:0.2rem;">
                {APP_TAGLINE}
            </div>
            <div style="font-size:0.7rem; color:#555; margin-top:0.3rem;">
                by <a href="{AUTHOR_URL}" style="color:#ff5a1f; text-decoration:none;">{AUTHOR}</a>
                · v{VERSION}
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    st.markdown("---")
    st.markdown("#### 新建分析")

    ticker = st.text_input(
        "股票代码",
        placeholder="例: 300750 或 宁德时代",
        key="input_ticker",
        help="输入6位A股代码或中文股票全称",
    )

    trade_date = st.date_input(
        "分析日期",
        value=date.today(),
        key="input_date",
    )

    start_date = st.date_input(
        "数据起始日期",
        value=trade_date.replace(day=1),   # 默认本月第一天
        key="input_start_date",
        help="技术分析回溯到该日期（默认本月第一天）。分析区间 = 起始日期 → 分析日期，"
             "用于「按月」或自定义时段分析；留默认即分析当月至今。",
    )
    # 分析窗口天数 → market_lookback_days（下限 5 天，保证指标有意义）
    st.session_state["market_lookback_days"] = max((trade_date - start_date).days, 5)
    if start_date >= trade_date:
        st.caption("⚠️ 起始日期应早于分析日期，已按最小窗口（5 天）处理。")

    with st.expander("⚙️ 模型配置", expanded=False):
        _render_llm_config()

    tracker = st.session_state.get("tracker")
    is_busy = tracker is not None and tracker.is_running
    is_stopping = is_busy and tracker.stop_requested

    if st.button(
        "开始分析" if not is_busy else "停止中..." if is_stopping else "分析进行中...",
        use_container_width=True,
        disabled=is_busy or not ticker,
        type="primary",
    ):
        _save_llm_config()  # persist model choice before running
        model_err = validate_model_selection()
        resolved_code, err = _resolve_user_input(ticker)
        if model_err:
            st.error(f"❌ {model_err}")
        elif err:
            st.error(f"❌ {err}")
        else:
            if resolved_code != ticker.strip():
                st.success(f"✅ {ticker.strip()} → {resolved_code}")
            st.session_state["start_analysis"] = {
                "ticker": resolved_code,
                "trade_date": trade_date.strftime("%Y-%m-%d"),
                "fresh": True,
            }
            st.session_state["viewing_history"] = None
            st.session_state["current_view"] = "workspace"

    _render_analysis_controls(ticker, trade_date)

    st.markdown("---")
    st.markdown("#### 未完成任务")

    incomplete = get_incomplete_history()
    if not incomplete:
        st.caption("暂无未完成任务")
    else:
        for entry in incomplete[:10]:
            t, d = entry["ticker"], entry["trade_date"]
            status_label = {
                "error": "出错",
                "paused": "已暂停",
                "running": "进行中",
            }.get(entry.get("status"), "可继续")
            step = entry.get("checkpoint_step")
            step_label = f" · step {step}" if step is not None else ""
            label = f"{t}  ·  {d}  ·  {status_label}{step_label}"
            if st.button(
                label,
                key=f"resume_{t}_{d}",
                use_container_width=True,
                disabled=is_busy,
            ):
                st.session_state["start_analysis"] = {
                    "ticker": t,
                    "trade_date": d,
                }
                st.session_state["viewing_history"] = None
                st.session_state["current_view"] = "workspace"

    st.markdown("---")
    st.markdown("#### 历史记录")

    history = get_history()
    if not history:
        st.caption("暂无历史记录")
        return

    for entry in history[:20]:
        t, d = entry["ticker"], entry["date"]
        label = f"{t}  ·  {d}"
        if st.button(label, key=f"hist_{t}_{d}", use_container_width=True):
            st.session_state["viewing_history"] = entry["path"]
            st.session_state["start_analysis"] = None
            st.session_state["current_view"] = "workspace"

    st.markdown("---")
    st.caption("⚠️ 仅供学习研究，不构成投资建议")
