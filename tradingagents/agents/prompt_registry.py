"""投研团队角色目录 & 提示词覆盖工具。

「投研团队」页面、agent 节点、提示词预览都读这一份元数据，保证：

* 前端展示的角色 / 工具 / 输出字段和真实图里的一致；
* 用户改了提示词后，真正发出去的文本就是页面上预览的那份。

覆盖值的形状::

    {
        "market": {"system": "……"},
        "trader": {"system": "……", "user": "……"},
    }

只存用户改过的片段；没出现的角色/片段继续用 :mod:`prompt_defaults` 的默认值。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping

from tradingagents.agents.prompt_defaults import DEFAULTS


@dataclass(frozen=True)
class PromptPart:
    """一个角色可能有多个提示词片段（如交易员的 system / user）。"""

    key: str
    label: str
    variables: tuple[str, ...] = ()
    description: str = ""


@dataclass(frozen=True)
class RoleSpec:
    role: str
    name: str
    icon: str
    group: str
    model_tier: str  # "quick" | "deep"
    output_key: str
    description: str
    tools: tuple[str, ...] = ()
    parts: tuple[PromptPart, ...] = ()
    analyst: bool = False
    stage: str = ""


GROUP_ORDER: tuple[tuple[str, str, str], ...] = (
    ("analysts", "① 分析师团队", "7 位工具型分析师，按顺序各自取数、写报告"),
    ("quality", "② 质量门控", "硬检查 + LLM 复审，给 7 份报告打分并提示数据缺口"),
    ("debate", "③ 多空辩论", "Bull / Bear 各自立论，研究经理把辩论收敛成投资计划"),
    ("decision", "④ 交易决策", "交易员把研究计划翻译成可执行的交易观点"),
    ("risk", "⑤ 风险委员会", "激进 / 保守 / 中性三方风险辩论，组合经理做最终决策"),
)

GROUP_LABELS = {key: label for key, label, _ in GROUP_ORDER}

TOOL_LABELS: dict[str, str] = {
    "get_stock_data": "K线行情",
    "get_indicators": "技术指标",
    "get_fundamentals": "基本面快照",
    "get_balance_sheet": "资产负债表",
    "get_cashflow": "现金流量表",
    "get_income_statement": "利润表",
    "get_profit_forecast": "机构一致预期",
    "get_industry_comparison": "行业横向对比",
    "get_news": "个股新闻",
    "get_global_news": "宏观新闻",
    "get_insider_transactions": "股东/内部人交易",
    "get_fund_flow": "资金流向",
    "get_hot_stocks": "强势股/题材归因",
    "get_northbound_flow": "北向资金",
    "get_concept_blocks": "概念板块归属",
    "get_dragon_tiger_board": "龙虎榜",
    "get_lockup_expiry": "解禁日历",
}

VARIABLE_LABELS: dict[str, str] = {
    "ticker": "股票代码",
    "date": "分析日期",
    "lookback": "回溯天数",
    "company_name": "股票代码/名称",
    "instrument_context": "标的约束说明",
    "reports": "7 份分析师报告（自动拼装）",
    "analyst_count": "参与分析的分析师数量",
    "analyst_rows": "质量审核表格的分析师行",
    "market_report": "技术分析报告",
    "sentiment_report": "情绪分析报告",
    "news_report": "新闻分析报告",
    "fundamentals_report": "基本面报告",
    "policy_report": "政策分析报告",
    "hot_money_report": "游资/资金流报告",
    "lockup_report": "解禁/减持报告",
    "data_quality_summary": "质量门控结论",
    "history": "辩论历史（已压缩）",
    "language_instruction": "输出语言指令",
    "investment_plan": "研究经理投资计划",
    "trader_plan": "交易员交易方案",
    "trader_decision": "交易员交易方案",
    "astock_context_block": "A股补充报告块（可为空）",
    "lessons_line": "历史决策教训（可为空）",
    "research_plan": "研究经理投资计划",
}

_REPORT_VARS = (
    "market_report",
    "sentiment_report",
    "news_report",
    "fundamentals_report",
    "policy_report",
    "hot_money_report",
    "lockup_report",
)


def _parts(*items: PromptPart) -> tuple[PromptPart, ...]:
    return tuple(items)


ROLE_SPECS: dict[str, RoleSpec] = {
    # ── 7 位分析师 ──────────────────────────────────────────────────────────
    "market": RoleSpec(
        role="market",
        name="技术分析师",
        icon="📊",
        group="analysts",
        model_tier="quick",
        output_key="market_report",
        stage="market",
        analyst=True,
        description="读 K 线与技术指标，判断趋势、支撑阻力和量价关系。",
        tools=("get_stock_data", "get_indicators"),
        parts=_parts(
            PromptPart(
                "system",
                "分析指令",
                ("lookback",),
                "lookback 会替换成侧栏「分析窗口」算出的天数。",
            )
        ),
    ),
    "social": RoleSpec(
        role="social",
        name="情绪分析师",
        icon="💬",
        group="analysts",
        model_tier="quick",
        output_key="sentiment_report",
        stage="social",
        analyst=True,
        description="用资金流、量价、强势股榜和新闻推断市场情绪与背离。",
        tools=("get_news", "get_fund_flow", "get_hot_stocks", "get_stock_data"),
        parts=_parts(PromptPart("system", "分析指令")),
    ),
    "news": RoleSpec(
        role="news",
        name="新闻分析师",
        icon="📰",
        group="analysts",
        model_tier="quick",
        output_key="news_report",
        stage="news",
        analyst=True,
        description="追踪个股与宏观新闻，区分利好/利空并评估持续性。",
        tools=("get_news", "get_global_news"),
        parts=_parts(PromptPart("system", "分析指令")),
    ),
    "fundamentals": RoleSpec(
        role="fundamentals",
        name="基本面分析师",
        icon="📋",
        group="analysts",
        model_tier="quick",
        output_key="fundamentals_report",
        stage="fundamentals",
        analyst=True,
        description="看估值、三张表、一致预期与行业横向对比。",
        tools=(
            "get_fundamentals",
            "get_balance_sheet",
            "get_cashflow",
            "get_income_statement",
            "get_profit_forecast",
            "get_industry_comparison",
        ),
        parts=_parts(PromptPart("system", "分析指令")),
    ),
    "policy": RoleSpec(
        role="policy",
        name="政策分析师",
        icon="🏛️",
        group="analysts",
        model_tier="quick",
        output_key="policy_report",
        stage="policy",
        analyst=True,
        description="解读宏观、监管、产业与地方政策对公司的传导。",
        tools=("get_news", "get_global_news"),
        parts=_parts(PromptPart("system", "分析指令")),
    ),
    "hot_money": RoleSpec(
        role="hot_money",
        name="游资追踪师",
        icon="🔥",
        group="analysts",
        model_tier="quick",
        output_key="hot_money_report",
        stage="hot_money",
        analyst=True,
        description="追踪龙虎榜、主力资金、北向资金和题材轮动。",
        tools=(
            "get_stock_data",
            "get_news",
            "get_insider_transactions",
            "get_hot_stocks",
            "get_northbound_flow",
            "get_concept_blocks",
            "get_fund_flow",
            "get_dragon_tiger_board",
            "get_industry_comparison",
        ),
        parts=_parts(PromptPart("system", "分析指令")),
    ),
    "lockup": RoleSpec(
        role="lockup",
        name="解禁监控师",
        icon="🔒",
        group="analysts",
        model_tier="quick",
        output_key="lockup_report",
        stage="lockup",
        analyst=True,
        description="评估限售解禁、大股东减持和供给端压力。",
        tools=(
            "get_insider_transactions",
            "get_news",
            "get_fundamentals",
            "get_lockup_expiry",
        ),
        parts=_parts(PromptPart("system", "分析指令")),
    ),
    # ── 质量门控 ────────────────────────────────────────────────────────────
    "quality_gate": RoleSpec(
        role="quality_gate",
        name="质量门控",
        icon="✅",
        group="quality",
        model_tier="quick",
        output_key="data_quality_summary",
        stage="quality_gate",
        description="先做代码硬检查，再让 LLM 逐份复审 7 份报告的数据质量。",
        parts=_parts(
            PromptPart(
                "review",
                "复审提示词",
                ("ticker", "date", "analyst_count", "reports", "analyst_rows"),
                "reports 会自动拼成「分析师名 + 报告正文」；analyst_rows 只列出本次参与的分析师。",
            )
        ),
    ),
    # ── 多空辩论 ────────────────────────────────────────────────────────────
    "bull": RoleSpec(
        role="bull",
        name="多方研究员",
        icon="🐂",
        group="debate",
        model_tier="quick",
        output_key="investment_debate_state.bull_history",
        stage="debate",
        description="基于 7 份报告构建看多论证，并回应空方。",
        parts=_parts(
            PromptPart(
                "argument",
                "立论提示词",
                _REPORT_VARS + ("data_quality_summary", "history"),
            )
        ),
    ),
    "bear": RoleSpec(
        role="bear",
        name="空方研究员",
        icon="🐻",
        group="debate",
        model_tier="quick",
        output_key="investment_debate_state.bear_history",
        stage="debate",
        description="基于 7 份报告构建看空论证，并回应多方。",
        parts=_parts(
            PromptPart(
                "argument",
                "立论提示词",
                _REPORT_VARS + ("data_quality_summary", "history"),
            )
        ),
    ),
    "research_manager": RoleSpec(
        role="research_manager",
        name="研究经理",
        icon="🧑‍⚖️",
        group="debate",
        model_tier="deep",
        output_key="investment_plan",
        stage="debate",
        description="主持多空辩论，输出五档评级的投资计划。",
        parts=_parts(
            PromptPart(
                "plan",
                "决策提示词",
                ("instrument_context", "history", "language_instruction"),
            )
        ),
    ),
    # ── 交易决策 ────────────────────────────────────────────────────────────
    "trader": RoleSpec(
        role="trader",
        name="交易员",
        icon="💹",
        group="decision",
        model_tier="quick",
        output_key="trader_investment_plan",
        stage="trader",
        description="把投资计划翻译成带 A 股交易约束的交易观点。",
        parts=_parts(
            PromptPart("system", "系统指令"),
            PromptPart(
                "user",
                "用户消息模板",
                (
                    "company_name",
                    "instrument_context",
                    "investment_plan",
                    "astock_context_block",
                    "language_instruction",
                ),
            ),
        ),
    ),
    # ── 风险委员会 ──────────────────────────────────────────────────────────
    "risk_aggressive": RoleSpec(
        role="risk_aggressive",
        name="激进风控",
        icon="🚀",
        group="risk",
        model_tier="quick",
        output_key="risk_debate_state.aggressive_history",
        stage="risk",
        description="强调涨停动量、政策托底与游资接力带来的上行空间。",
        parts=_parts(
            PromptPart(
                "argument",
                "风险辩论提示词",
                ("trader_decision",) + _REPORT_VARS + ("history",),
            )
        ),
    ),
    "risk_neutral": RoleSpec(
        role="risk_neutral",
        name="中性风控",
        icon="⚖️",
        group="risk",
        model_tier="quick",
        output_key="risk_debate_state.neutral_history",
        stage="risk",
        description="在激进与保守之间做仓位与估值区间的平衡。",
        parts=_parts(
            PromptPart(
                "argument",
                "风险辩论提示词",
                ("trader_decision",) + _REPORT_VARS + ("history",),
            )
        ),
    ),
    "risk_conservative": RoleSpec(
        role="risk_conservative",
        name="保守风控",
        icon="🛡️",
        group="risk",
        model_tier="quick",
        output_key="risk_debate_state.conservative_history",
        stage="risk",
        description="强调 T+1、跌停流动性、解禁与政策反转风险。",
        parts=_parts(
            PromptPart(
                "argument",
                "风险辩论提示词",
                ("trader_decision",) + _REPORT_VARS + ("history",),
            )
        ),
    ),
    "portfolio_manager": RoleSpec(
        role="portfolio_manager",
        name="组合经理",
        icon="👔",
        group="risk",
        model_tier="deep",
        output_key="final_trade_decision",
        stage="pm",
        description="汇总风险辩论，给出最终 Buy / Overweight / Hold / Underweight / Sell。",
        parts=_parts(
            PromptPart(
                "decision",
                "最终决策提示词",
                (
                    "instrument_context",
                    "research_plan",
                    "trader_plan",
                    "lessons_line",
                    "history",
                    "language_instruction",
                ),
            )
        ),
    ),
}


ROLE_ORDER: tuple[str, ...] = (
    "market",
    "social",
    "news",
    "fundamentals",
    "policy",
    "hot_money",
    "lockup",
    "quality_gate",
    "bull",
    "bear",
    "research_manager",
    "trader",
    "risk_aggressive",
    "risk_neutral",
    "risk_conservative",
    "portfolio_manager",
)

ANALYST_ROLES: tuple[str, ...] = tuple(
    role for role in ROLE_ORDER if ROLE_SPECS[role].analyst
)

# 默认参与分析的分析师。团队页的开关会覆盖它，但未配置时保持原行为。
DEFAULT_SELECTED_ANALYSTS: tuple[str, ...] = ANALYST_ROLES


def role_spec(role: str) -> RoleSpec:
    try:
        return ROLE_SPECS[role]
    except KeyError as exc:  # pragma: no cover - 防御性分支
        raise KeyError(f"未知的投研角色: {role}") from exc


def roles_in_group(group: str) -> list[RoleSpec]:
    return [ROLE_SPECS[r] for r in ROLE_ORDER if ROLE_SPECS[r].group == group]


def get_default_prompt(role: str, part: str) -> str:
    try:
        return DEFAULTS[role][part]
    except KeyError as exc:  # pragma: no cover - 目录与默认值必须同步
        raise KeyError(f"角色 {role!r} 没有提示词片段 {part!r}") from exc


def _override_for(
    role: str,
    part: str,
    overrides: Mapping[str, Any] | None,
) -> str:
    if not overrides:
        return ""
    value = overrides.get(role)
    # 兼容早期/手写的扁平形状：{"market": "……"} 视为改 system。
    if isinstance(value, str):
        return value if part == "system" else ""
    if isinstance(value, Mapping):
        raw = value.get(part, "")
        return raw if isinstance(raw, str) else ""
    # 兼容「只传该角色」的形状：{"argument": "……"}。GraphSetup 传的是
    # {role: {part: text}}；测试或脚本直接调 agent 工厂时更可能只传片段字典。
    raw = overrides.get(part)
    return raw if isinstance(raw, str) else ""


def get_prompt(
    role: str,
    part: str,
    overrides: Mapping[str, Any] | None = None,
) -> str:
    """取某个角色片段的生效提示词：用户覆盖优先，否则默认值。"""
    override = _override_for(role, part, overrides).strip()
    return override or get_default_prompt(role, part)


def is_customized(
    role: str,
    part: str,
    overrides: Mapping[str, Any] | None = None,
) -> bool:
    override = _override_for(role, part, overrides).strip()
    return bool(override) and override != get_default_prompt(role, part).strip()


def customized_parts(
    role: str,
    overrides: Mapping[str, Any] | None = None,
) -> list[str]:
    spec = role_spec(role)
    return [p.key for p in spec.parts if is_customized(role, p.key, overrides)]


def is_role_customized(
    role: str,
    overrides: Mapping[str, Any] | None = None,
) -> bool:
    return bool(customized_parts(role, overrides))


def customized_roles(
    overrides: Mapping[str, Any] | None = None,
) -> list[str]:
    return [role for role in ROLE_ORDER if is_role_customized(role, overrides)]


_VAR_RE = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")


def render_prompt(template: str, values: Mapping[str, Any]) -> str:
    """替换模板里的 `{变量}`。

    只替换**已知变量名**，未知的花括号原样保留——用户可以写自己的 JSON 示例或
    数学花括号，不会被这里误伤。值里的花括号也不会被二次展开。
    """

    def _replace(match: re.Match[str]) -> str:
        key = match.group(1)
        if key not in values:
            return match.group(0)
        value = values[key]
        return "" if value is None else str(value)

    return _VAR_RE.sub(_replace, template)


def absent_variables(template: str, declared: Iterable[str]) -> list[str]:
    """返回声明过、但当前模板里已经被删掉的变量。"""
    present = set(_VAR_RE.findall(template))
    return [name for name in declared if name not in present]


def variables_in_template(template: str) -> list[str]:
    """返回模板里仍以 `{name}` 形式存在的变量（用于预览完整性检查）。"""
    return _VAR_RE.findall(template)


def unknown_variables(template: str) -> list[str]:
    known = set(VARIABLE_LABELS) | {"no_levels_instruction", "no_levels_rule"}
    return sorted({name for name in _VAR_RE.findall(template) if name not in known})


def prompt_char_stats(
    role: str, overrides: Mapping[str, Any] | None = None
) -> tuple[int, int]:
    """返回 (默认字符数, 生效字符数)，用于页面上的「省了多少/加了多少」。"""
    spec = role_spec(role)
    default_len = sum(len(get_default_prompt(role, p.key)) for p in spec.parts)
    effective_len = sum(len(get_prompt(role, p.key, overrides)) for p in spec.parts)
    return default_len, effective_len
