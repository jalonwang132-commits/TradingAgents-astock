"""提示词目录 / 渲染 / 预览的回归测试。

这套测试锁住三件事：

1. 页面展示的默认提示词与 agent 真正使用的是**同一份**；
2. 用户覆盖只替换指定片段，未覆盖的继续用默认；
3. 所有角色的模板变量在预览里都能被真实值替换，不会把 `{ticker}` 这种
   占位符漏进真实请求。
"""

from __future__ import annotations

import pytest

from tradingagents.agents.prompt_builder import (
    build_analyst_system,
    build_pm_prompt,
    build_quality_review_prompt,
    preview_prompt_parts,
    preview_values,
)
from tradingagents.agents.prompt_registry import (
    ANALYST_ROLES,
    DEFAULT_SELECTED_ANALYSTS,
    ROLE_ORDER,
    ROLE_SPECS,
    absent_variables,
    customized_parts,
    customized_roles,
    get_default_prompt,
    get_prompt,
    is_customized,
    render_prompt,
    variables_in_template,
)
from tradingagents.graph.setup import ROLE_KEYS


@pytest.mark.unit
def test_role_catalog_matches_graph_role_keys():
    """页面目录和 GraphSetup 的合法角色名必须完全一致。"""
    assert set(ROLE_SPECS) == set(ROLE_KEYS)
    assert len(ROLE_ORDER) == len(set(ROLE_ORDER)) == 16
    assert set(ANALYST_ROLES) <= set(ROLE_KEYS)
    assert list(ANALYST_ROLES) == list(DEFAULT_SELECTED_ANALYSTS)


@pytest.mark.unit
def test_every_role_has_defaults_for_every_part():
    for role, spec in ROLE_SPECS.items():
        assert spec.parts, f"{role} 没有任何提示词片段"
        for part in spec.parts:
            default = get_default_prompt(role, part.key)
            assert default.strip(), f"{role}/{part.key} 默认提示词为空"
            assert default == get_default_prompt(role, part.key)


@pytest.mark.unit
def test_default_prompts_keep_critical_rules():
    market = get_default_prompt("market", "system")
    assert "{lookback}" in market
    assert "T+1" in market
    assert "涨跌停" in market
    # 回归：get_stock_data 只接受 start_date / end_date，没有 look_back_days。
    # 旧提示词把 look_back_days 也要求传给 get_stock_data，模型照做会撞工具参数校验。
    assert "调用 get_stock_data 时按分析区间传 start_date / end_date" in market
    assert "get_stock_data 和 get_indicators 时" not in market

    trader = get_default_prompt("trader", "system")
    assert "Do NOT state entry prices" in trader
    assert "T+1 settlement" in trader

    pm = get_default_prompt("portfolio_manager", "decision")
    for tier in ("Buy", "Overweight", "Hold", "Underweight", "Sell"):
        assert f"**{tier}**" in pm
    assert "{lessons_line}" in pm

    quality = get_default_prompt("quality_gate", "review")
    for token in ("{ticker}", "{date}", "{reports}", "{analyst_count}", "{analyst_rows}"):
        assert token in quality


@pytest.mark.unit
def test_render_prompt_replaces_known_tokens_only():
    template = "标的 {ticker}，日期 {date}，自定义 {my_json}，保留 {unknown}"
    rendered = render_prompt(
        template, {"ticker": "600519", "date": "2026-10-20", "my_json": '{"a": 1}'}
    )

    assert "600519" in rendered
    assert "2026-10-20" in rendered
    assert '{"a": 1}' in rendered
    # 未知变量原样保留，方便用户自己发现拼写错误。
    assert "{unknown}" in rendered


@pytest.mark.unit
def test_render_prompt_does_not_expand_braces_from_values():
    rendered = render_prompt("{ticker}", {"ticker": "{date}", "date": "boom"})
    assert rendered == "{date}"


@pytest.mark.unit
def test_override_precedence_and_shapes():
    global_overrides = {"market": {"system": "自定义市场提示词"}}
    per_role_overrides = {"system": "另一种形状"}

    assert get_prompt("market", "system", global_overrides) == "自定义市场提示词"
    assert get_prompt("market", "system", per_role_overrides) == "另一种形状"
    assert get_prompt("market", "system", None) == get_default_prompt("market", "system")


@pytest.mark.unit
def test_customization_detection_and_reset_semantics():
    overrides = {"market": {"system": "改过的"}}
    assert is_customized("market", "system", overrides)
    assert customized_parts("market", overrides) == ["system"]
    assert customized_roles(overrides) == ["market"]

    # 与默认值完全一致不算自定义（页面的「已自定义」徽标不应误亮）。
    same = {"market": {"system": get_default_prompt("market", "system")}}
    assert not is_customized("market", "system", same)
    assert customized_roles(same) == []


@pytest.mark.unit
def test_build_analyst_system_uses_override_and_context():
    text = build_analyst_system(
        "market",
        {"system": "只做量价，不做基本面。标的 {lookback} 日。"},
        ["get_stock_data", "get_indicators"],
        "2026-10-20",
        "The instrument to analyze is `600519`.",
        lookback=45,
    )

    assert "只做量价" in text
    assert "45" in text
    assert "get_stock_data, get_indicators" in text
    assert "2026-10-20" in text
    assert "600519" in text


@pytest.mark.unit
def test_quality_prompt_embeds_reports_and_override():
    reports = {"market_report": "技术报告正文"}
    text = build_quality_review_prompt(
        reports, "2026-10-20", "600519", {"review": "复审 {ticker} {date}\n{reports}"}
    )
    assert text.startswith("复审 600519 2026-10-20")
    assert "技术报告正文" in text


@pytest.mark.unit
def test_pm_prompt_override_and_lessons():
    text = build_pm_prompt(
        {
            "instrument_context": "CTX",
            "research_plan": "PLAN",
            "trader_plan": "TRADER",
            "lessons_line": "- Lessons from prior decisions and outcomes:\nLESSON\n",
            "history": "HISTORY",
            "language_instruction": " 用中文回答。",
        },
        {"decision": "自定义 {research_plan} / {lessons_line}{language_instruction}"},
    )
    assert "PLAN" in text
    assert "LESSON" in text
    assert text.endswith(" 用中文回答。")


@pytest.mark.unit
@pytest.mark.parametrize("role", ROLE_ORDER)
def test_preview_renders_all_declared_variables(role: str):
    parts = preview_prompt_parts(role)
    assert parts
    assert len(parts) == len(ROLE_SPECS[role].parts)

    for (_, text), part in zip(parts, ROLE_SPECS[role].parts):
        assert text.strip()
        # 模板里的每个变量都应该在预览里被替换掉，不能把 `{ticker}` 漏进真实请求。
        assert not variables_in_template(text), (
            f"{role} 预览里残留变量: {variables_in_template(text)}"
        )


@pytest.mark.unit
def test_preview_values_include_all_analyst_reports():
    values = preview_values("bull")
    for spec in ROLE_SPECS.values():
        if spec.analyst:
            assert values[spec.output_key]
