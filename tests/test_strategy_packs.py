"""「我的战法」策略包：校验、作用域、合并进 prompt 的回归测试。"""

from __future__ import annotations

import pytest

from tradingagents.agents.prompt_builder import (
    active_strategy_packs,
    build_analyst_system,
    build_bull_prompt,
    build_pm_prompt,
    build_strategy_block,
    build_trader_messages,
    preview_prompt_parts,
)
from tradingagents.agents.prompt_registry import ROLE_ORDER
from tradingagents.dataflows.config import set_config
from tradingagents.default_config import DEFAULT_CONFIG
from web.team_store import (
    add_strategy_pack,
    apply_team_config_to_run_config,
    default_team_config,
    delete_strategy_pack,
    get_strategy_pack,
    normalize_team_config,
    set_strategy_pack_enabled,
    update_strategy_pack,
)


def _pack(name="超级空头", scope=None, content="RSI > 80 且 MACD 死叉 -> 超级空头", enabled=True):
    return {
        "id": f"id_{name}",
        "name": name,
        "scope": list(scope or []),
        "content": content,
        "enabled": enabled,
    }


@pytest.mark.unit
def test_default_config_has_no_strategy_packs():
    assert default_team_config()["strategy_packs"] == []


@pytest.mark.unit
def test_normalize_strategy_packs_drops_empty_and_fixes_scope():
    cfg = normalize_team_config(
        {
            "strategy_packs": [
                {"name": "保留", "scope": ["market", "not_a_role"], "content": "x"},
                {"name": "空内容", "content": "   "},
                "不是字典",
                {"name": "重复 id", "id": "same", "content": "a"},
                {"name": "重复 id 2", "id": "same", "content": "b"},
            ]
        }
    )
    packs = cfg["strategy_packs"]
    assert len(packs) == 3
    assert packs[0]["name"] == "保留"
    assert packs[0]["scope"] == ["market"]
    assert packs[0]["enabled"] is True
    assert packs[1]["id"] != packs[2]["id"]


@pytest.mark.unit
def test_strategy_crud_roundtrip():
    cfg = default_team_config()
    cfg = add_strategy_pack(
        cfg, name="超级空头", scope=["market", "bear"], content="规则 A", enabled=True
    )
    pack = cfg["strategy_packs"][0]
    assert get_strategy_pack(cfg, pack["id"])["name"] == "超级空头"

    cfg = update_strategy_pack(
        cfg, pack["id"], name="超级空头 V2", content="规则 B", enabled=False
    )
    updated = get_strategy_pack(cfg, pack["id"])
    assert updated["name"] == "超级空头 V2"
    assert updated["content"] == "规则 B"
    assert updated["enabled"] is False

    cfg = set_strategy_pack_enabled(cfg, pack["id"], True)
    assert get_strategy_pack(cfg, pack["id"])["enabled"] is True

    cfg = delete_strategy_pack(cfg, pack["id"])
    assert cfg["strategy_packs"] == []


@pytest.mark.unit
def test_update_to_empty_content_removes_pack():
    cfg = add_strategy_pack(
        default_team_config(), name="x", scope=[], content="content"
    )
    pack_id = cfg["strategy_packs"][0]["id"]
    cfg = update_strategy_pack(cfg, pack_id, content="  ")
    assert cfg["strategy_packs"] == []


@pytest.mark.unit
def test_active_packs_respect_role_scope_and_enabled():
    packs = [
        _pack("全角色", scope=[], content="全局"),
        _pack("技术专用", scope=["market"], content="技术"),
        _pack("空方专用", scope=["bear"], content="空方"),
        _pack("停用", scope=[], content="不该出现", enabled=False),
    ]

    market = active_strategy_packs("market", packs)
    assert [p["name"] for p in market] == ["全角色", "技术专用"]

    bear = active_strategy_packs("bear", packs)
    assert [p["name"] for p in bear] == ["全角色", "空方专用"]

    assert active_strategy_packs("bull", packs) == [
        {"id": "id_全角色", "name": "全角色", "content": "全局"}
    ]


@pytest.mark.unit
def test_strategy_block_has_header_and_order():
    packs = [_pack("第一条", content="规则一"), _pack("第二条", content="规则二")]
    block = build_strategy_block("market", packs)
    assert "用户自定义战法" in block
    assert block.index("第一条") < block.index("第二条")
    assert "规则一" in block and "规则二" in block


@pytest.mark.unit
def test_market_prompt_includes_strategy_and_renders_variables():
    packs = [
        _pack(
            "超级空头",
            scope=["market"],
            content="标的 {ticker}，日期 {date}。RSI > 80 => 超级空头。",
        )
    ]
    text = build_analyst_system(
        "market",
        None,
        ["get_stock_data"],
        "2026-10-21",
        "CTX",
        lookback=60,
        strategy_packs=packs,
        ticker="600519",
    )
    assert "超级空头" in text
    assert "标的 600519" in text
    assert "2026-10-21" in text
    assert "{ticker}" not in text


@pytest.mark.unit
def test_strategy_respects_role_scope():
    packs = [_pack("只给技术", scope=["market"], content="技术规则")]
    bull = build_bull_prompt(
        {
            "ticker": "600519",
            "date": "2026-10-21",
            "market_report": "m",
            "sentiment_report": "s",
            "news_report": "n",
            "fundamentals_report": "f",
            "policy_report": "p",
            "hot_money_report": "h",
            "lockup_report": "l",
            "data_quality_summary": "q",
            "history": "",
        },
        strategy_packs=packs,
    )
    assert "只给技术" not in bull


@pytest.mark.unit
def test_pm_prompt_includes_global_strategy():
    packs = [_pack("全局纪律", scope=[], content="每条结论必须引用数值。")]
    text = build_pm_prompt(
        {
            "instrument_context": "CTX",
            "research_plan": "PLAN",
            "trader_plan": "TRADER",
            "lessons_line": "",
            "history": "HISTORY",
            "language_instruction": "",
        },
        strategy_packs=packs,
    )
    assert "全局纪律" in text
    assert "每条结论必须引用数值" in text


@pytest.mark.unit
def test_trader_strategy_only_in_system_message():
    packs = [_pack("交易纪律", scope=["trader"], content="不要追高。")]
    messages = build_trader_messages(
        {
            "ticker": "600519",
            "date": "2026-10-21",
            "company_name": "600519",
            "instrument_context": "CTX",
            "investment_plan": "PLAN",
            "astock_context_block": "",
            "language_instruction": "",
        },
        strategy_packs=packs,
    )
    assert "交易纪律" in messages[0]["content"]
    assert "交易纪律" not in messages[1]["content"]


@pytest.mark.unit
def test_preview_includes_strategy():
    packs = [_pack("预览战法", scope=["market"], content="预览内容")]
    parts = preview_prompt_parts("market", strategy_packs=packs)
    assert "预览战法" in parts[0][1]
    assert "预览内容" in parts[0][1]


@pytest.mark.unit
def test_apply_team_config_carries_strategy_packs():
    cfg = add_strategy_pack(
        default_team_config(), name="x", scope=["market"], content="y"
    )
    run = apply_team_config_to_run_config({}, cfg)
    assert run["strategy_packs"] == cfg["strategy_packs"]


@pytest.mark.unit
def test_agent_runtime_reads_strategy_packs_from_global_config():
    cfg = add_strategy_pack(
        default_team_config(), name="运行期战法", scope=["market"], content="运行期规则"
    )
    run = apply_team_config_to_run_config({}, cfg)
    original = dict(DEFAULT_CONFIG)
    try:
        set_config(run)
        text = build_analyst_system(
            "market", None, ["get_stock_data"], "2026-10-21", "CTX", ticker="600519"
        )
        assert "运行期战法" in text
    finally:
        set_config(original)


@pytest.mark.unit
@pytest.mark.parametrize("role", ROLE_ORDER)
def test_global_strategy_reaches_every_role(role: str):
    """全角色战法必须进入每个角色的主提示词；trader 的 user 消息不重复注入。"""
    packs = [_pack("全局规则", scope=[], content="这是全局规则")]
    parts = preview_prompt_parts(role, strategy_packs=packs)
    assert parts
    assert "全局规则" in parts[0][1]
