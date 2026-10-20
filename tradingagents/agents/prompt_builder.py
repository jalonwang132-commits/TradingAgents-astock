"""提示词组装 —— agent 节点与 Web 预览共用的唯一入口。

在这里集中三件事：

1. 默认模板 + 用户覆盖的渲染；
2. 运行期变量注入（报告、辩论历史、标的说明……）；
3. 「完整提示词预览」所需的示例数据。

> 新增 agent 或改提示词结构时，**只改这里和 prompt_defaults.py**；不要在 agent
> 节点里再拼一份，否则页面预览和真实请求会悄悄分叉。
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping

from tradingagents.agents.prompt_registry import (
    ANALYST_ROLES,
    ROLE_SPECS,
    VARIABLE_LABELS,
    get_prompt,
    render_prompt,
)


# 与各分析师原本的通用协作 system prompt 保持一致。
ANALYST_SYSTEM_HEADER = (
    "You are a helpful AI assistant, collaborating with other assistants."
    " Use the provided tools to progress towards answering the question."
    " If you are unable to fully answer, that's OK; another assistant with different tools"
    " will help where you left off. Execute what you can to make progress."
    " If you or any other assistant has the FINAL TRANSACTION PROPOSAL: **BUY/HOLD/SELL** or deliverable,"
    " prefix your response with FINAL TRANSACTION PROPOSAL: **BUY/HOLD/SELL** so the team knows to stop."
    " You have access to the following tools: {tool_names}.\n{system_message}"
    "For your reference, the current date is {current_date}. {instrument_context}"
)


def _language_instruction() -> str:
    # 延迟导入：避免 prompt 模块在数据层初始化前被 import。
    from tradingagents.agents.utils.agent_utils import get_language_instruction

    return get_language_instruction()


def render_role_prompt(
    role: str,
    part: str,
    overrides: Mapping[str, Any] | None,
    values: Mapping[str, Any],
) -> str:
    """取生效模板并注入变量。"""
    return render_prompt(get_prompt(role, part, overrides), values)


def build_analyst_system(
    role: str,
    overrides: Mapping[str, Any] | None,
    tool_names: Iterable[str],
    current_date: str,
    instrument_context: str,
    lookback: int | None = None,
    language_instruction: str | None = None,
) -> str:
    """组装分析师完整的 system prompt（通用协作头 + 角色指令 + 日期/标的）。"""
    instruction = render_role_prompt(
        role,
        "system",
        overrides,
        {"lookback": lookback if lookback is not None else 30},
    )
    instruction += (
        _language_instruction()
        if language_instruction is None
        else language_instruction
    )
    # 先填通用头里的变量，最后再塞角色指令：否则角色指令里恰好出现的
    # {current_date} / {instrument_context} 会被二次替换。
    header = (
        ANALYST_SYSTEM_HEADER.replace("{tool_names}", ", ".join(tool_names))
        .replace("{current_date}", str(current_date))
        .replace("{instrument_context}", instrument_context)
    )
    return header.replace("{system_message}", instruction)


def selected_analyst_roles(selected: Iterable[str] | None = None) -> list[str]:
    """按固定目录顺序返回本次参与的分析师角色；None = 全部 7 位。"""
    if selected is None:
        return list(ANALYST_ROLES)
    chosen = set(selected)
    return [role for role in ANALYST_ROLES if role in chosen]


def build_reports_block(
    reports: Mapping[str, str],
    max_chars: int = 3000,
    selected_analysts: Iterable[str] | None = None,
) -> str:
    """把参与分析的分析师报告拼成质量门控用的段落。"""
    sections: list[str] = []
    for role in selected_analyst_roles(selected_analysts):
        spec = ROLE_SPECS[role]
        content = reports.get(spec.output_key, "") or "（报告为空）"
        if len(content) > max_chars:
            content = content[:max_chars] + "\n... (truncated for review)"
        sections.append(f"### {spec.name} ({role})\n{content}")
    return "\n\n".join(sections)


def build_analyst_rows(selected_analysts: Iterable[str] | None = None) -> str:
    """质量门控输出表格里只列出本次参与的分析师。"""
    rows = [
        f"| {ROLE_SPECS[role].name} | A/B/C/D/F | 是否匹配交易日 | 列出缺失的必采项 | 简要说明 |"
        for role in selected_analyst_roles(selected_analysts)
    ]
    return "\n".join(rows)


def build_quality_review_prompt(
    reports: Mapping[str, str] | str,
    trade_date: str,
    ticker: str,
    overrides: Mapping[str, Any] | None = None,
    selected_analysts: Iterable[str] | None = None,
) -> str:
    roles = selected_analyst_roles(selected_analysts)
    reports_block = (
        reports
        if isinstance(reports, str)
        else build_reports_block(reports, selected_analysts=roles)
    )
    return render_role_prompt(
        "quality_gate",
        "review",
        overrides,
        {
            "ticker": ticker,
            "date": trade_date,
            "analyst_count": len(roles),
            "reports": reports_block,
            "analyst_rows": build_analyst_rows(roles),
        },
    )


def build_bull_prompt(
    values: Mapping[str, Any],
    overrides: Mapping[str, Any] | None = None,
) -> str:
    return render_role_prompt("bull", "argument", overrides, values)


def build_bear_prompt(
    values: Mapping[str, Any],
    overrides: Mapping[str, Any] | None = None,
) -> str:
    return render_role_prompt("bear", "argument", overrides, values)


def build_research_manager_prompt(
    values: Mapping[str, Any],
    overrides: Mapping[str, Any] | None = None,
) -> str:
    return render_role_prompt("research_manager", "plan", overrides, values)


def build_trader_messages(
    values: Mapping[str, Any],
    overrides: Mapping[str, Any] | None = None,
) -> list[dict[str, str]]:
    """交易员走 system + user 两条消息，和原实现保持一致。"""
    system = render_role_prompt("trader", "system", overrides, values)
    user = render_role_prompt("trader", "user", overrides, values)
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


def build_risk_prompt(
    role: str,
    values: Mapping[str, Any],
    overrides: Mapping[str, Any] | None = None,
) -> str:
    if role not in ("risk_aggressive", "risk_neutral", "risk_conservative"):
        raise ValueError(f"不是风险委员会角色: {role}")
    return render_role_prompt(role, "argument", overrides, values)


def build_pm_prompt(
    values: Mapping[str, Any],
    overrides: Mapping[str, Any] | None = None,
) -> str:
    return render_role_prompt("portfolio_manager", "decision", overrides, values)


# ── 预览用示例数据 ──────────────────────────────────────────────────────────

_SAMPLE_REPORT = "（示例）本次分析中该分析师生成的报告正文会自动注入到这里。"
_SAMPLE_HISTORY = (
    "Bull Analyst: （示例）多方第一轮论点……\n"
    "Bear Analyst: （示例）空方第一轮论点……"
)
_SAMPLE_PLAN = (
    "**Recommendation**: Overweight\n"
    "**Rationale**: （示例）研究经理综合多空辩论后的理由。\n"
    "**Strategic Actions**: （示例）分批建仓，控制单一标的风险敞口。"
)
_SAMPLE_TRADER_PLAN = (
    "**Action**: Buy\n"
    "**Reasoning**: （示例）交易员基于研究计划的交易观点。"
)


def preview_values(
    role: str,
    ticker: str = "600519",
    trade_date: str = "2026-10-20",
    lookback: int = 30,
    language_instruction: str | None = None,
) -> dict[str, Any]:
    """给「完整提示词预览」用的示例变量。"""
    from tradingagents.agents.utils.agent_utils import build_instrument_context

    instrument_context = build_instrument_context(ticker)
    reports = {spec.output_key: _SAMPLE_REPORT for spec in ROLE_SPECS.values() if spec.analyst}
    analyst_count = len(ANALYST_ROLES)
    values: dict[str, Any] = {
        "ticker": ticker,
        "date": trade_date,
        "lookback": lookback,
        "company_name": ticker,
        "instrument_context": instrument_context,
        "language_instruction": (
            _language_instruction()
            if language_instruction is None
            else language_instruction
        ),
        "history": _SAMPLE_HISTORY,
        "data_quality_summary": "（示例）质量门控结论：整体 B，基本面报告缺少一致预期。",
        "investment_plan": _SAMPLE_PLAN,
        "research_plan": _SAMPLE_PLAN,
        "trader_plan": _SAMPLE_TRADER_PLAN,
        "trader_decision": _SAMPLE_TRADER_PLAN,
        "lessons_line": (
            "- Lessons from prior decisions and outcomes:\n"
            "（示例）历史上同标的的复盘教训会注入到这里。\n"
        ),
        "astock_context_block": (
            "Additional A-Stock Analyst Context:\n"
            "（示例）政策 / 游资 / 解禁报告会注入到这里。\n\n"
        ),
        "reports": build_reports_block(reports),
        "analyst_count": analyst_count,
        "analyst_rows": build_analyst_rows(),
    }
    values.update(reports)
    return values


def preview_prompt_parts(
    role: str,
    overrides: Mapping[str, Any] | None = None,
    ticker: str = "600519",
    trade_date: str = "2026-10-20",
    lookback: int = 30,
    language_instruction: str | None = None,
    selected_analysts: Iterable[str] | None = None,
) -> list[tuple[str, str]]:
    """返回 [(片段名, 完整提示词)]，供 Web 页面展示真正会发出去的内容。"""
    spec = ROLE_SPECS[role]
    values = preview_values(
        role, ticker, trade_date, lookback, language_instruction
    )

    if spec.analyst:
        full = build_analyst_system(
            role,
            overrides,
            spec.tools,
            trade_date,
            values["instrument_context"],
            lookback,
            language_instruction=values["language_instruction"],
        )
        return [(spec.parts[0].label, full)]

    if role == "quality_gate":
        full = build_quality_review_prompt(
            values["reports"],
            trade_date,
            ticker,
            overrides,
            selected_analysts=selected_analysts,
        )
        return [(spec.parts[0].label, full)]

    if role == "trader":
        messages = build_trader_messages(values, overrides)
        return [
            (spec.parts[0].label, messages[0]["content"]),
            (spec.parts[1].label, messages[1]["content"]),
        ]

    if role == "bull":
        return [(spec.parts[0].label, build_bull_prompt(values, overrides))]
    if role == "bear":
        return [(spec.parts[0].label, build_bear_prompt(values, overrides))]
    if role == "research_manager":
        return [
            (spec.parts[0].label, build_research_manager_prompt(values, overrides))
        ]
    if role.startswith("risk_"):
        return [(spec.parts[0].label, build_risk_prompt(role, values, overrides))]
    if role == "portfolio_manager":
        return [(spec.parts[0].label, build_pm_prompt(values, overrides))]

    # 兜底：按片段逐一渲染，保证新增角色也能预览。
    return [
        (part.label, render_role_prompt(role, part.key, overrides, values))
        for part in spec.parts
    ]


def variable_hints(role: str) -> list[tuple[str, str]]:
    """列出某角色模板里可用的变量及中文说明。"""
    spec = ROLE_SPECS[role]
    names: list[str] = []
    for part in spec.parts:
        for name in part.variables:
            if name not in names:
                names.append(name)
    return [(name, VARIABLE_LABELS.get(name, "运行时自动注入")) for name in names]
