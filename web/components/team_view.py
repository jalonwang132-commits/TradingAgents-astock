"""「投研团队」页面：可视化 16 个角色 + 在线编辑提示词。

页面分三块：

1. **流水线视图** —— 7 位分析师 → 质量门控 → 多空辩论 → 交易员 → 风险委员会，
   每张卡片显示该角色本次分析的实时状态（等待/进行中/已完成/未参与）。
2. **角色提示词编辑器** —— 完整展示真正会发出去的提示词，支持变量、全文预览、
   保存/恢复默认。覆盖值写入 ``~/.tradingagents/team_config.json``，下一次
   「开始分析」立即生效。
3. **团队编排** —— 选择参与的分析师、辩论轮数；改动同样持久化。

提示词不是「附加一句指令」而是完整模板：页面里看到的就是请求里的 system /
user 文本（运行时变量以示例值展示）。删掉某个 `{变量}` 会导致该内容不再注入，
编辑器会明确警告。
"""

from __future__ import annotations

import json
import time
from datetime import date
from typing import Any

import streamlit as st

from tradingagents.agents.prompt_builder import (
    preview_prompt_parts,
    variable_hints,
)
from tradingagents.agents.prompt_registry import (
    ANALYST_ROLES,
    GROUP_LABELS,
    GROUP_ORDER,
    ROLE_SPECS,
    TOOL_LABELS,
    absent_variables,
    customized_parts,
    customized_roles,
    get_default_prompt,
    get_prompt,
    is_role_customized,
    prompt_char_stats,
    unknown_variables,
)
from web.history import load_analysis
from web.team_state import (
    STATUS_ACTIVE,
    STATUS_DONE,
    STATUS_LABELS,
    STATUS_OFF,
    STATUS_PENDING,
    derive_role_states,
    empty_role_states,
)
from web.team_store import (
    load_team_config,
    normalize_team_config,
    reset_all_prompts,
    reset_prompt,
    save_team_config,
    set_team_options,
    update_prompt,
)

_TEAM_CSS = """
<style>
.team-hero {
    background: radial-gradient(circle at 12% 20%, rgba(255,90,31,.20), transparent 40%),
                linear-gradient(135deg, #141414 0%, #101722 100%);
    border: 1px solid #2a2a2a;
    border-radius: 18px;
    padding: 1.4rem 1.8rem 1.2rem;
    margin-bottom: 1rem;
}
.team-kicker {
    color: #ff5a1f;
    font-size: .72rem;
    font-weight: 800;
    letter-spacing: .22em;
}
.team-hero h1 {
    margin: .15rem 0 .25rem;
    font-size: 2.1rem;
    font-weight: 900;
    color: #f5f1eb;
}
.team-hero p {
    margin: 0;
    color: #9a9a9a;
    font-size: .92rem;
    line-height: 1.65;
    max-width: 900px;
}
.team-group {
    display: flex;
    align-items: baseline;
    gap: .55rem;
    margin: 1.25rem 0 .15rem;
    padding-bottom: .35rem;
    border-bottom: 1px solid #202020;
}
.team-group .idx {
    color: #ff5a1f;
    font-weight: 900;
    font-size: .9rem;
}
.team-group .name {
    color: #f5f1eb;
    font-weight: 800;
    font-size: 1.02rem;
}
.team-group .desc {
    color: #777;
    font-size: .78rem;
}
.team-flow {
    text-align: center;
    color: #ff5a1f;
    font-size: 1.15rem;
    line-height: 1.2;
    margin: .15rem 0 .1rem;
    opacity: .85;
}
.team-flow span {
    color: #555;
    font-size: .68rem;
    letter-spacing: .12em;
    margin-left: .5rem;
}
.team-status {
    text-align: center;
    font-size: .74rem;
    margin-top: .25rem;
    letter-spacing: .02em;
}
.team-status.active {
    animation: teamPulse 1.5s ease-in-out infinite;
}
@keyframes teamPulse {
    0%, 100% { opacity: 1; }
    50% { opacity: .35; }
}
.team-legend {
    color: #888;
    font-size: .76rem;
    margin: .2rem 0 .8rem;
}
.team-legend b { font-weight: 700; }
.team-tip {
    color: #777;
    font-size: .78rem;
    line-height: 1.6;
}
@media (prefers-reduced-motion: reduce) {
    .team-status.active { animation: none; }
}
</style>
"""

_STATUS_COLORS = {
    STATUS_PENDING: "#6b7280",
    STATUS_ACTIVE: "#ff5a1f",
    STATUS_DONE: "#22c55e",
    STATUS_OFF: "#4b5563",
}
_STATUS_DOTS = {
    STATUS_PENDING: "○",
    STATUS_ACTIVE: "◉",
    STATUS_DONE: "●",
    STATUS_OFF: "—",
}


# ── 配置读写 ────────────────────────────────────────────────────────────────


def _team_config() -> dict[str, Any]:
    if "team_config" not in st.session_state:
        st.session_state["team_config"] = load_team_config()
    return st.session_state["team_config"]


def _persist(cfg: dict[str, Any]) -> dict[str, Any]:
    try:
        saved = save_team_config(cfg)
    except OSError as exc:
        st.error(f"团队配置写入失败：{exc}")
        return st.session_state["team_config"]
    st.session_state["team_config"] = saved
    return saved


def _notice(message: str) -> None:
    st.session_state["team_notice"] = message


def _editor_key(role: str, part: str) -> str:
    return f"team_prompt_{role}_{part}"


def _clear_editor_keys() -> None:
    for role, spec in ROLE_SPECS.items():
        for part in spec.parts:
            st.session_state.pop(_editor_key(role, part.key), None)
    for key in ("team_analysts", "team_debate_rounds", "team_risk_rounds"):
        st.session_state.pop(key, None)


# ── 回调：在 widget 实例化之前改 session_state，避免 StreamlitAPIException ──


def _save_options_cb() -> None:
    cfg = st.session_state.get("team_config") or load_team_config()
    cfg = set_team_options(
        cfg,
        selected_analysts=st.session_state.get("team_analysts"),
        max_debate_rounds=st.session_state.get("team_debate_rounds"),
        max_risk_rounds=st.session_state.get("team_risk_rounds"),
    )
    saved = _persist(cfg)
    # set_team_options 会拒绝空选择；把 widget 状态同步回实际生效值。
    st.session_state["team_analysts"] = list(saved["selected_analysts"])
    st.session_state["team_debate_rounds"] = saved["max_debate_rounds"]
    st.session_state["team_risk_rounds"] = saved["max_risk_rounds"]
    _notice("团队编排已保存；下一次分析生效。")


def _reset_options_cb() -> None:
    cfg = st.session_state.get("team_config") or load_team_config()
    cfg = set_team_options(
        cfg,
        selected_analysts=list(ANALYST_ROLES),
        max_debate_rounds=1,
        max_risk_rounds=1,
    )
    saved = _persist(cfg)
    st.session_state["team_analysts"] = list(saved["selected_analysts"])
    st.session_state["team_debate_rounds"] = saved["max_debate_rounds"]
    st.session_state["team_risk_rounds"] = saved["max_risk_rounds"]
    _notice("团队编排已恢复默认：7 位分析师全部参与，多空 / 风险各 1 轮。")


def _reset_role_cb(role: str, part_keys: tuple[str, ...]) -> None:
    cfg = st.session_state.get("team_config") or load_team_config()
    cfg = reset_prompt(cfg, role)
    st.session_state["team_config"] = _persist(cfg)
    for part in part_keys:
        st.session_state[_editor_key(role, part)] = get_default_prompt(role, part)
    _notice(f"{ROLE_SPECS[role].icon} {ROLE_SPECS[role].name} 已恢复内置提示词。")


def _reset_all_cb() -> None:
    cfg = st.session_state.get("team_config") or load_team_config()
    cfg = reset_all_prompts(cfg)
    st.session_state["team_config"] = _persist(cfg)
    _clear_editor_keys()
    st.session_state["team_confirm_reset_all"] = False
    _notice("全部角色已恢复内置提示词。")


# ── 状态 / 拓扑 ─────────────────────────────────────────────────────────────


def _resolve_statuses(cfg: dict[str, Any]):
    tracker = st.session_state.get("tracker")
    if tracker is not None and (
        tracker.is_running or tracker.is_complete or tracker.error
    ):
        states = dict(tracker.role_state_snapshot())
        if not states and tracker.final_state:
            states = derive_role_states(
                tracker.final_state,
                tracker.selected_analysts or cfg["selected_analysts"],
                tracker.max_debate_rounds,
                tracker.max_risk_rounds,
            )
        if not states:
            states = empty_role_states(
                tracker.selected_analysts or cfg["selected_analysts"]
            )
        return states, tracker, "live"

    viewing = st.session_state.get("viewing_history")
    if viewing:
        try:
            state = load_analysis(viewing)
            # v0.5.22 起报告里会记录当时的参与分析师和辩论轮数；旧报告没有这些
            # 字段时回落到当前团队配置。
            selected = state.get("selected_analysts") or cfg["selected_analysts"]
            debate_rounds = int(
                state.get("max_debate_rounds") or cfg["max_debate_rounds"]
            )
            risk_rounds = int(
                state.get("max_risk_discuss_rounds") or cfg["max_risk_rounds"]
            )
            return (
                derive_role_states(
                    state,
                    selected,
                    debate_rounds,
                    risk_rounds,
                ),
                None,
                "history",
            )
        except Exception:  # noqa: BLE001 — 历史文件坏了不该让团队页崩掉
            return empty_role_states(cfg["selected_analysts"]), None, "idle"

    return empty_role_states(cfg["selected_analysts"]), None, "idle"


def _status_html(status: str, *, has_error: bool = False) -> str:
    if has_error and status == STATUS_ACTIVE:
        return (
            '<div class="team-status" style="color:#ef4444">'
            "⚠ 出错 / 中断</div>"
        )
    color = _STATUS_COLORS.get(status, "#6b7280")
    label = STATUS_LABELS.get(status, status)
    active_class = " active" if status == STATUS_ACTIVE else ""
    dot = _STATUS_DOTS.get(status, "○")
    return (
        f'<div class="team-status{active_class}" style="color:{color}">'
        f"{dot} {label}</div>"
    )


def _role_card(
    role: str,
    states: dict[str, str],
    overrides: dict[str, Any],
    *,
    has_error: bool,
) -> None:
    spec = ROLE_SPECS[role]
    selected = st.session_state.get("team_selected_role") == role
    status = states.get(role, STATUS_PENDING)
    with st.container(border=True):
        st.markdown(
            f"<div style='text-align:center;font-size:1.55rem;line-height:1.1'>"
            f"{spec.icon}</div>",
            unsafe_allow_html=True,
        )
        if st.button(
            spec.name,
            key=f"team_card_{role}",
            use_container_width=True,
            type="primary" if selected else "secondary",
        ):
            st.session_state["team_selected_role"] = role
            st.rerun()
        st.markdown(_status_html(status, has_error=has_error), unsafe_allow_html=True)
        if is_role_customized(role, overrides):
            st.caption("✏️ 已自定义")


def _flow(label: str = "") -> None:
    suffix = f"<span>{label}</span>" if label else ""
    st.markdown(f'<div class="team-flow">↓{suffix}</div>', unsafe_allow_html=True)


def _group_header(group: str) -> None:
    label = GROUP_LABELS.get(group, group)
    desc = next((d for key, _, d in GROUP_ORDER if key == group), "")
    # label 已经带序号，拆出来单独上色。
    idx, _, name = label.partition(" ")
    st.markdown(
        f'<div class="team-group"><span class="idx">{idx}</span>'
        f'<span class="name">{name}</span>'
        f'<span class="desc">{desc}</span></div>',
        unsafe_allow_html=True,
    )


def _render_topology(
    states: dict[str, str],
    overrides: dict[str, Any],
    *,
    has_error: bool,
) -> None:
    _group_header("analysts")
    cols = st.columns(len(ANALYST_ROLES))
    for col, role in zip(cols, ANALYST_ROLES):
        with col:
            _role_card(role, states, overrides, has_error=has_error)

    _flow("7 份报告")

    _group_header("quality")
    left, middle, right = st.columns([2, 1, 2])
    with middle:
        _role_card("quality_gate", states, overrides, has_error=has_error)

    _flow("质量结论")

    _group_header("debate")
    cols = st.columns(3)
    for col, role in zip(cols, ("bull", "bear", "research_manager")):
        with col:
            _role_card(role, states, overrides, has_error=has_error)

    _flow("投资计划")

    _group_header("decision")
    left, middle, right = st.columns([2, 1, 2])
    with middle:
        _role_card("trader", states, overrides, has_error=has_error)

    _flow("交易方案")

    _group_header("risk")
    cols = st.columns(3)
    for col, role in zip(
        cols,
        ("risk_aggressive", "risk_conservative", "risk_neutral"),
    ):
        with col:
            _role_card(role, states, overrides, has_error=has_error)
    _flow("三方辩论")
    left, middle, right = st.columns([2, 1, 2])
    with middle:
        _role_card("portfolio_manager", states, overrides, has_error=has_error)


# ── 提示词编辑器 ────────────────────────────────────────────────────────────


def _preview_ticker() -> str:
    raw = str(st.session_state.get("input_ticker") or "").strip()
    return raw if len(raw) == 6 and raw.isdigit() else "600519"


def _preview_date() -> str:
    value = st.session_state.get("input_date")
    if isinstance(value, date):
        return value.strftime("%Y-%m-%d")
    return date.today().strftime("%Y-%m-%d")


def _render_part_editor(
    cfg: dict[str, Any],
    role: str,
    part,
) -> str:
    key = _editor_key(role, part.key)
    if key not in st.session_state:
        st.session_state[key] = get_prompt(role, part.key, cfg["prompts"])

    draft = st.session_state[key]
    default = get_default_prompt(role, part.key)
    changed = (draft or "").strip() != default.strip()
    st.markdown(f"**{part.label}**")
    if part.description:
        st.caption(part.description)

    st.text_area(
        part.label,
        key=key,
        height=420,
        label_visibility="collapsed",
        placeholder="留空则使用内置默认提示词。",
    )

    draft = st.session_state[key]
    st.caption(
        f"{len(draft):,} 字 · 内置默认 {len(default):,} 字"
        + (" · ✏️ 使用自定义版本" if changed else " · 使用内置默认")
    )

    hints = variable_hints(role)
    if hints:
        joined = " · ".join(f"`{{{name}}}` {label}" for name, label in hints)
        st.caption(f"可用变量：{joined}")

    missing = absent_variables(draft, part.variables)
    if missing:
        st.warning(
            "模板里已删除变量："
            + "、".join(f"`{{{name}}}`" for name in missing)
            + "。保存后对应内容将不再注入本次请求。",
            icon="⚠️",
        )

    custom_vars = unknown_variables(draft)
    if custom_vars:
        st.info(
            "这些变量不在已知清单里，会原样保留在提示词中："
            + "、".join(f"`{{{name}}}`" for name in custom_vars),
            icon="ℹ️",
        )

    return key


def _render_editor(cfg: dict[str, Any], states: dict[str, str]) -> None:
    role = st.session_state.get("team_selected_role") or "market"
    if role not in ROLE_SPECS:
        role = "market"
    st.session_state["team_selected_role"] = role
    spec = ROLE_SPECS[role]

    st.markdown("---")
    header_left, header_right = st.columns([3, 2])
    with header_left:
        st.markdown(f"### {spec.icon} {spec.name}  `{role}`")
        st.caption(spec.description)
    with header_right:
        custom_parts = customized_parts(role, cfg["prompts"])
        status = states.get(role, STATUS_PENDING)
        status_label = (
            "出错 / 中断"
            if st.session_state.get("tracker") is not None
            and st.session_state["tracker"].error
            and status == STATUS_ACTIVE
            else STATUS_LABELS.get(status, status)
        )
        st.markdown(
            f"当前状态：**{status_label}** · "
            + (
                f"✏️ 已自定义 {len(custom_parts)} 段"
                if custom_parts
                else "内置默认提示词"
            )
        )
        st.caption(
            f"输出字段 `{spec.output_key}` · "
            f"{'深度模型' if spec.model_tier == 'deep' else '快速模型'}"
        )

    tab_edit, tab_preview, tab_detail = st.tabs(
        ["✏️ 编辑提示词", "👁️ 完整提示词预览", "ℹ️ 角色详情"]
    )

    with tab_edit:
        if len(spec.parts) == 1:
            _render_part_editor(cfg, role, spec.parts[0])
        else:
            part_tabs = st.tabs([p.label for p in spec.parts])
            for part_tab, part in zip(part_tabs, spec.parts):
                with part_tab:
                    _render_part_editor(cfg, role, part)

        st.caption(
            "`{变量}` 会在运行时自动替换；分析师还会自动附加通用协作头、工具列表、"
            "当前日期、标的说明和输出语言指令。"
        )
        col_save, col_reset, col_spacer = st.columns([1, 1, 2])
        with col_save:
            if st.button(
                "💾 保存提示词",
                key=f"team_save_{role}",
                type="primary",
                use_container_width=True,
            ):
                new_cfg = cfg
                for part in spec.parts:
                    new_cfg = update_prompt(
                        new_cfg,
                        role,
                        part.key,
                        st.session_state.get(_editor_key(role, part.key), ""),
                    )
                st.session_state["team_config"] = _persist(new_cfg)
                _notice(f"{spec.icon} {spec.name} 的提示词已保存；下一次分析生效。")
                st.rerun()
        with col_reset:
            st.button(
                "↩️ 恢复本角色默认",
                key=f"team_reset_{role}",
                use_container_width=True,
                on_click=_reset_role_cb,
                args=(role, tuple(p.key for p in spec.parts)),
            )
        st.caption(
            "保存只影响**下一次**分析；正在跑的分析已经拿走了旧提示词。"
            " 留空并保存 = 恢复该片段的默认值。"
        )

    with tab_preview:
        ticker = _preview_ticker()
        trade_date = _preview_date()
        lookback = int(st.session_state.get("market_lookback_days") or 30)
        st.caption(
            f"示例标的 `{ticker}` · 日期 `{trade_date}` · 回溯 `{lookback}` 天。"
            " 报告、辩论历史等运行时数据用占位文本代替；真正发出去时会替换成真实内容。"
        )
        preview = preview_prompt_parts(
            role,
            cfg["prompts"],
            ticker=ticker,
            trade_date=trade_date,
            lookback=lookback,
            # Web 端的 _build_config() 固定 output_language=Chinese；预览与之一致。
            language_instruction=" Write your entire response in Chinese.",
            selected_analysts=cfg["selected_analysts"],
        )
        for label, text in preview:
            st.markdown(f"**{label}** · `{len(text):,}` 字")
            st.code(text, language="text")
        if preview:
            st.download_button(
                "⬇️ 下载完整提示词",
                data="\n\n".join(f"## {label}\n\n{text}" for label, text in preview),
                file_name=f"prompt_{role}_{trade_date}.md",
                mime="text/markdown",
                key=f"team_download_prompt_{role}",
            )

    with tab_detail:
        st.markdown(f"- **角色 ID**：`{role}`")
        st.markdown(f"- **分组**：{GROUP_LABELS.get(spec.group, spec.group)}")
        st.markdown(
            f"- **模型档位**：{'深度（Research / Portfolio 档）' if spec.model_tier == 'deep' else '快速（工具型 Agent 档）'}"
        )
        st.markdown(f"- **输出字段**：`{spec.output_key}`")
        st.markdown(f"- **流水线阶段**：`{spec.stage}`")
        default_len, effective_len = prompt_char_stats(role, cfg["prompts"])
        st.markdown(
            f"- **提示词长度**：内置 {default_len:,} 字 → 当前 {effective_len:,} 字"
        )

        if spec.tools:
            st.markdown("**可用工具**")
            for tool in spec.tools:
                st.markdown(f"- `{tool}` · {TOOL_LABELS.get(tool, tool)}")
        else:
            st.caption("该角色不调用数据工具，只做综合判断。")

        hints = variable_hints(role)
        if hints:
            st.markdown("**模板变量**")
            for name, label in hints:
                st.markdown(f"- `{{{name}}}` · {label}")

        custom = customized_parts(role, cfg["prompts"])
        if custom:
            st.markdown("**已覆盖的片段**：" + "、".join(custom))
        else:
            st.caption("当前全部使用内置提示词。")


# ── 页面主体 ────────────────────────────────────────────────────────────────


def render_team_view() -> None:
    st.markdown(_TEAM_CSS, unsafe_allow_html=True)
    cfg = _team_config()

    notice = st.session_state.pop("team_notice", None)
    if notice:
        st.success(notice)

    states, tracker, source = _resolve_statuses(cfg)
    has_error = bool(tracker is not None and tracker.error)
    custom_roles = customized_roles(cfg["prompts"])

    st.markdown(
        """
        <div class="team-hero">
            <div class="team-kicker">RESEARCH TEAM · CONTROL ROOM</div>
            <h1>🧑‍💼 投研团队</h1>
            <p>
                16 位 Agent 的完整流水线：7 位工具型分析师各自动取数写报告 →
                质量门控复审 → 多空辩论 → 研究经理收敛 → 交易员落地 →
                激进 / 保守 / 中性三方风控 → 组合经理最终决策。
                这里既能看团队现在跑到哪一步，也能直接改每个角色的提示词。
            </p>
        </div>
        """,
        unsafe_allow_html=True,
    )

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("团队角色", len(ROLE_SPECS))
    m2.metric("已自定义提示词", len(custom_roles))
    m3.metric("参与分析", f"{len(cfg['selected_analysts'])} / {len(ANALYST_ROLES)}")
    m4.metric(
        "辩论轮数",
        f"{cfg['max_debate_rounds']} / {cfg['max_risk_rounds']}",
        help="多空辩论轮数 / 风险辩论轮数",
    )

    # ── 当前运行状态 ────────────────────────────────────────────────────────
    if tracker is not None and tracker.is_running:
        st.info(
            f"🟠 正在分析 **{tracker.ticker}** · {tracker.trade_date} · "
            f"已用 {int(tracker.elapsed)} 秒。团队状态随流水线实时点亮。"
        )
        refresh_col, live_col, _ = st.columns([1, 1, 2])
        with refresh_col:
            if st.button("🔄 刷新状态", use_container_width=True):
                st.rerun()
        with live_col:
            st.session_state.setdefault("team_live_refresh", True)
            st.toggle(
                "实时刷新（2 秒）",
                key="team_live_refresh",
                help="开启后页面每 2 秒自动刷新；编辑提示词时建议关闭。",
            )
        if st.session_state.get("team_live_refresh"):
            time.sleep(2)
            st.rerun()
    elif tracker is not None and tracker.error:
        st.error(f"上次分析失败：{tracker.error}")
    elif tracker is not None and tracker.is_complete:
        st.success(
            f"✅ **{tracker.ticker}** · {tracker.trade_date} 分析完成 · "
            f"最终信号 **{tracker.signal or '—'}**"
        )
    elif source == "history":
        st.info("正在查看历史分析的角色状态。")
    else:
        st.caption(
            "尚未运行分析。左侧输入股票代码开始分析后，这里会实时点亮每个角色；"
            "下方的提示词编辑不依赖运行，随时可改。"
        )

    st.markdown(
        '<div class="team-legend">'
        "<b style='color:#22c55e'>● 已完成</b> &nbsp; "
        "<b style='color:#ff5a1f'>◉ 进行中</b> &nbsp; "
        "<b style='color:#6b7280'>○ 等待</b> &nbsp; "
        "<b style='color:#4b5563'>— 未参与</b> &nbsp; "
        "· 点击任意角色卡片编辑它的提示词"
        "</div>",
        unsafe_allow_html=True,
    )

    # ── 团队编排 ────────────────────────────────────────────────────────────
    with st.expander("⚙️ 团队编排：谁参与、辩论几轮", expanded=False):
        st.caption(
            "改动会写入 `~/.tradingagents/team_config.json`，下一次「开始分析」生效；"
            "不会打断正在跑的任务。"
        )
        st.session_state.setdefault("team_analysts", list(cfg["selected_analysts"]))
        st.session_state.setdefault("team_debate_rounds", cfg["max_debate_rounds"])
        st.session_state.setdefault("team_risk_rounds", cfg["max_risk_rounds"])

        st.multiselect(
            "参与本次分析的分析师",
            options=list(ANALYST_ROLES),
            format_func=lambda role: f"{ROLE_SPECS[role].icon} {ROLE_SPECS[role].name}",
            key="team_analysts",
            on_change=_save_options_cb,
            help="至少保留一位。未选中的分析师不会进图，质量门控会把对应报告标为缺失。",
        )
        round_col, risk_col = st.columns(2)
        with round_col:
            st.slider(
                "多空辩论轮数",
                min_value=1,
                max_value=5,
                key="team_debate_rounds",
                on_change=_save_options_cb,
                help="每轮 = Bull 立论 + Bear 反驳。轮数越多越深，token 也越多。",
            )
        with risk_col:
            st.slider(
                "风险辩论轮数",
                min_value=1,
                max_value=5,
                key="team_risk_rounds",
                on_change=_save_options_cb,
                help="每轮 = 激进 → 保守 → 中性 各发言一次。",
            )
        st.button(
            "↩️ 恢复默认编排",
            key="team_reset_options",
            on_click=_reset_options_cb,
        )

    # ── 拓扑 ────────────────────────────────────────────────────────────────
    _render_topology(states, cfg["prompts"], has_error=has_error)

    # ── 编辑器 ──────────────────────────────────────────────────────────────
    _render_editor(cfg, states)

    # ── 导入 / 导出 ─────────────────────────────────────────────────────────
    with st.expander("📦 导入 / 导出团队配置", expanded=False):
        st.caption(
            "导出的 JSON 含提示词、参与的分析师和辩论轮数；换机器/备份时可直接导入。"
        )
        st.download_button(
            "⬇️ 导出 team_config.json",
            data=json.dumps(cfg, ensure_ascii=False, indent=2),
            file_name="team_config.json",
            mime="application/json",
            key="team_export_config",
        )
        uploaded = st.file_uploader(
            "导入团队配置 JSON",
            type=["json"],
            key="team_import_file",
            help="导入后会覆盖当前的提示词与团队编排，并清空编辑器草稿。",
        )
        if uploaded is not None and uploaded.name != st.session_state.get(
            "team_last_import"
        ):
            try:
                raw = json.loads(uploaded.getvalue().decode("utf-8"))
                imported = normalize_team_config(raw)
                st.session_state["team_config"] = _persist(imported)
                _clear_editor_keys()
                st.session_state["team_last_import"] = uploaded.name
                _notice(f"已导入 {uploaded.name}。")
                st.rerun()
            except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
                st.error(f"导入失败：{exc}")

        st.markdown("---")
        st.session_state.setdefault("team_confirm_reset_all", False)
        st.checkbox(
            "我确认要清空所有角色的自定义提示词",
            key="team_confirm_reset_all",
        )
        st.button(
            "🗑️ 恢复全部默认提示词",
            key="team_reset_all",
            disabled=not st.session_state.get("team_confirm_reset_all"),
            on_click=_reset_all_cb,
        )
