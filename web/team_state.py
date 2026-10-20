"""从 LangGraph 状态推导 16 个角色的实时状态。

图是串行推进的（7 位分析师 → 质量门控 → 多空辩论 → 研究经理 → 交易员 →
风险三方 → 组合经理），但状态里没有「当前节点」字段。这里按各阶段产物是否
出现，推导出 ``pending / active / done / off``，供「投研团队」页面做实时
可视化；runner 每收到一个 stream chunk 就调用一次。
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping

from tradingagents.agents.prompt_registry import (
    ANALYST_ROLES,
    DEFAULT_SELECTED_ANALYSTS,
    ROLE_ORDER,
    ROLE_SPECS,
)

STATUS_PENDING = "pending"
STATUS_ACTIVE = "active"
STATUS_DONE = "done"
STATUS_OFF = "off"

STATUS_LABELS = {
    STATUS_PENDING: "等待",
    STATUS_ACTIVE: "进行中",
    STATUS_DONE: "已完成",
    STATUS_OFF: "未参与",
}


def empty_role_states(
    selected_analysts: Iterable[str] | None = None,
) -> dict[str, str]:
    selected = set(selected_analysts or DEFAULT_SELECTED_ANALYSTS)
    states = {role: STATUS_PENDING for role in ROLE_ORDER}
    for role in ANALYST_ROLES:
        if role not in selected:
            states[role] = STATUS_OFF
    return states


def _debate_active_role(count: int, rounds: int) -> str:
    if count >= 2 * max(1, rounds):
        return "research_manager"
    return "bull" if count % 2 == 0 else "bear"


def _risk_active_role(count: int, latest_speaker: str) -> str:
    if count <= 0:
        return "risk_aggressive"
    speaker = (latest_speaker or "").strip().lower()
    if speaker.startswith("aggressive"):
        return "risk_conservative"
    if speaker.startswith("conservative"):
        return "risk_neutral"
    if speaker.startswith("neutral"):
        return "risk_aggressive"
    # 状态里万一没有 latest_speaker，按轮次兜底（顺序：激进→保守→中性）。
    return ("risk_aggressive", "risk_conservative", "risk_neutral")[count % 3]


def derive_role_states(
    state: Mapping[str, Any] | None,
    selected_analysts: Iterable[str] | None = None,
    max_debate_rounds: int = 1,
    max_risk_rounds: int = 1,
) -> dict[str, str]:
    """返回 ``{role: status}``，角色顺序与 ROLE_ORDER 一致。"""
    selected = list(selected_analysts or DEFAULT_SELECTED_ANALYSTS)
    selected_set = set(selected)
    states = empty_role_states(selected)

    # 还没有任何状态：第一位参与的分析师是 active。
    if not state:
        if selected:
            states[selected[0]] = STATUS_ACTIVE
        return states

    reports = {
        role: str(state.get(ROLE_SPECS[role].output_key) or "")
        for role in ANALYST_ROLES
    }
    debate = state.get("investment_debate_state") or {}
    risk = state.get("risk_debate_state") or {}

    data_quality = str(state.get("data_quality_summary") or "")
    investment_plan = str(
        state.get("investment_plan") or debate.get("judge_decision") or ""
    )
    trader_plan = str(state.get("trader_investment_plan") or "")
    final_decision = str(
        state.get("final_trade_decision") or risk.get("judge_decision") or ""
    )

    downstream_started = bool(
        data_quality or investment_plan or trader_plan or final_decision
    )

    # ── 7 位分析师 ──────────────────────────────────────────────────────────
    for role in ANALYST_ROLES:
        if role not in selected_set:
            continue
        states[role] = STATUS_DONE if reports[role] else STATUS_PENDING

    if downstream_started:
        # 流程已经走到下游，没写出报告的分析师也视为已结束（失败/空输出），
        # 否则页面会永远停在「第一位缺报告的分析师进行中」。
        for role in selected:
            states[role] = STATUS_DONE
    else:
        for role in selected:
            if not reports[role]:
                states[role] = STATUS_ACTIVE
                break

    # ── 质量门控 ────────────────────────────────────────────────────────────
    all_reports_ready = all(bool(reports[role]) for role in selected)
    if data_quality:
        states["quality_gate"] = STATUS_DONE
    elif all_reports_ready and not downstream_started:
        states["quality_gate"] = STATUS_ACTIVE

    # ── 多空辩论 / 研究经理 ─────────────────────────────────────────────────
    bull_done = bool(str(debate.get("bull_history") or "").strip())
    bear_done = bool(str(debate.get("bear_history") or "").strip())
    debate_count = int(debate.get("count") or 0)

    if investment_plan:
        # 能产出投资计划，说明质量门控和多空辩论都已经结束。
        states["quality_gate"] = STATUS_DONE
        states["bull"] = STATUS_DONE
        states["bear"] = STATUS_DONE
        states["research_manager"] = STATUS_DONE
    elif data_quality:
        states["bull"] = STATUS_DONE if bull_done else STATUS_PENDING
        states["bear"] = STATUS_DONE if bear_done else STATUS_PENDING
        active = _debate_active_role(debate_count, max_debate_rounds)
        # 多轮辩论里，上一轮发过言的角色会再次轮到：active 必须覆盖 done，
        # 否则第二轮 Bull 会被显示成「已完成」而不是「进行中」。
        states[active] = STATUS_ACTIVE

    # ── 交易员 ──────────────────────────────────────────────────────────────
    if trader_plan:
        states["quality_gate"] = STATUS_DONE
        states["bull"] = STATUS_DONE
        states["bear"] = STATUS_DONE
        states["research_manager"] = STATUS_DONE
        states["trader"] = STATUS_DONE
    elif investment_plan:
        states["trader"] = STATUS_ACTIVE

    # ── 风险委员会 ──────────────────────────────────────────────────────────
    if final_decision:
        states["quality_gate"] = STATUS_DONE
        states["bull"] = STATUS_DONE
        states["bear"] = STATUS_DONE
        states["research_manager"] = STATUS_DONE
        states["trader"] = STATUS_DONE
        states["risk_aggressive"] = STATUS_DONE
        states["risk_conservative"] = STATUS_DONE
        states["risk_neutral"] = STATUS_DONE
        states["portfolio_manager"] = STATUS_DONE
    elif trader_plan:
        risk_count = int(risk.get("count") or 0)
        for role, key in (
            ("risk_aggressive", "aggressive_history"),
            ("risk_conservative", "conservative_history"),
            ("risk_neutral", "neutral_history"),
        ):
            if str(risk.get(key) or "").strip():
                states[role] = STATUS_DONE

        if risk_count >= 3 * max(1, max_risk_rounds):
            states["portfolio_manager"] = STATUS_ACTIVE
        else:
            active = _risk_active_role(
                risk_count, str(risk.get("latest_speaker") or "")
            )
            # 同辩论：多轮风控里上一轮发过言的人会再次轮到，active 覆盖 done。
            states[active] = STATUS_ACTIVE

    return states


def summarize_role_states(states: Mapping[str, str]) -> dict[str, int]:
    summary = {label: 0 for label in STATUS_LABELS.values()}
    for status in states.values():
        summary[STATUS_LABELS.get(status, status)] += 1
    return summary
