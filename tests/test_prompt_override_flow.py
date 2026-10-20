"""提示词覆盖真正进入 agent 请求（而不只是页面显示）的回归测试。"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from langchain_core.messages import HumanMessage
from langchain_core.runnables import RunnableLambda

from tradingagents.agents.analysts.market_analyst import create_market_analyst
from tradingagents.agents.managers.portfolio_manager import create_portfolio_manager
from tradingagents.agents.managers.research_manager import create_research_manager
from tradingagents.agents.quality_gate import create_quality_gate
from tradingagents.agents.researchers.bull_researcher import create_bull_researcher
from tradingagents.agents.risk_mgmt.aggressive_debator import create_aggressive_debator
from tradingagents.agents.trader.trader import create_trader
from tradingagents.graph.setup import GraphSetup
from tradingagents.graph.trading_graph import TradingAgentsGraph


class _FakeResponse:
    def __init__(self, content: str = "ok"):
        self.content = content
        self.tool_calls = []


class _CapturingLLM:
    def __init__(self, store: dict, key: str):
        self.store = store
        self.key = key

    def invoke(self, prompt):
        self.store[self.key] = prompt
        return _FakeResponse()


def _long_report() -> str:
    return "x" * 300 + "\n| 指标 | 结论 |\n|---|---|\n| a | b |"


def _all_reports() -> dict:
    return {
        "market_report": _long_report(),
        "sentiment_report": _long_report(),
        "news_report": _long_report(),
        "fundamentals_report": _long_report(),
        "policy_report": _long_report(),
        "hot_money_report": _long_report(),
        "lockup_report": _long_report(),
    }


@pytest.mark.unit
def test_market_analyst_override_reaches_system_message():
    captured: dict = {}

    class FakeLLM:
        def bind_tools(self, tools):
            def _invoke(messages):
                captured["messages"] = messages
                return _FakeResponse("技术分析报告")

            return RunnableLambda(_invoke)

    node = create_market_analyst(FakeLLM(), {"system": "CUSTOM MARKET {lookback}"})
    node(
        {
            "trade_date": "2026-10-20",
            "company_of_interest": "600519",
            "messages": [HumanMessage(content="分析一下")],
        }
    )

    system = captured["messages"].to_messages()[0].content
    assert "CUSTOM MARKET 30" in system
    assert "You are a helpful AI assistant" in system


@pytest.mark.unit
def test_quality_gate_override_reaches_llm():
    captured: dict = {}
    node = create_quality_gate(
        _CapturingLLM(captured, "prompt"),
        {"review": "CUSTOM QUALITY {ticker} {date}\n{reports}"},
    )
    node({"trade_date": "2026-10-20", "company_of_interest": "600519", **_all_reports()})

    assert captured["prompt"].startswith("CUSTOM QUALITY 600519 2026-10-20")
    assert "技术分析师" in captured["prompt"]


@pytest.mark.unit
def test_bull_override_reaches_llm():
    captured: dict = {}
    node = create_bull_researcher(
        _CapturingLLM(captured, "prompt"), {"argument": "CUSTOM BULL"}
    )
    node(
        {
            **_all_reports(),
            "data_quality_summary": "q",
            "investment_debate_state": {
                "history": "",
                "bull_history": "",
                "bear_history": "",
                "current_response": "",
                "count": 0,
            },
        }
    )
    assert captured["prompt"] == "CUSTOM BULL"


@pytest.mark.unit
def test_research_manager_override_reaches_llm():
    captured: dict = {}
    llm = MagicMock()
    llm.with_structured_output.side_effect = NotImplementedError("no structured")
    llm.invoke.return_value = MagicMock(content="plan")
    node = create_research_manager(llm, {"plan": "CUSTOM RM {history}"})
    node(
        {
            "company_of_interest": "600519",
            "investment_debate_state": {
                "history": "DEBATE",
                "bull_history": "",
                "bear_history": "",
                "current_response": "",
                "judge_decision": "",
                "count": 0,
            },
        }
    )
    assert llm.invoke.call_args.args[0] == "CUSTOM RM DEBATE"


@pytest.mark.unit
def test_trader_override_reaches_llm():
    llm = MagicMock()
    llm.with_structured_output.side_effect = NotImplementedError("no structured")
    llm.invoke.return_value = MagicMock(content="trader")
    node = create_trader(
        llm,
        {"system": "CUSTOM TRADER SYS", "user": "CUSTOM TRADER USER {company_name}"},
    )
    node({"company_of_interest": "600519", "investment_plan": "plan"})

    messages = llm.invoke.call_args.args[0]
    assert messages == [
        {"role": "system", "content": "CUSTOM TRADER SYS"},
        {"role": "user", "content": "CUSTOM TRADER USER 600519"},
    ]


@pytest.mark.unit
def test_risk_override_reaches_llm():
    captured: dict = {}
    node = create_aggressive_debator(
        _CapturingLLM(captured, "prompt"), {"argument": "CUSTOM RISK"}
    )
    node(
        {
            **_all_reports(),
            "trader_investment_plan": "trader",
            "risk_debate_state": {
                "history": "",
                "aggressive_history": "",
                "conservative_history": "",
                "neutral_history": "",
                "latest_speaker": "",
                "current_aggressive_response": "",
                "current_conservative_response": "",
                "current_neutral_response": "",
                "judge_decision": "",
                "count": 0,
            },
        }
    )
    assert captured["prompt"] == "CUSTOM RISK"


@pytest.mark.unit
def test_portfolio_manager_override_reaches_llm():
    llm = MagicMock()
    llm.with_structured_output.side_effect = NotImplementedError("no structured")
    llm.invoke.return_value = MagicMock(content="decision")
    node = create_portfolio_manager(llm, {"decision": "CUSTOM PM {research_plan}"})
    node(
        {
            "company_of_interest": "600519",
            "investment_plan": "PLAN",
            "trader_investment_plan": "TRADER",
            "past_context": "",
            "risk_debate_state": {
                "history": "HISTORY",
                "aggressive_history": "",
                "conservative_history": "",
                "neutral_history": "",
                "latest_speaker": "",
                "current_aggressive_response": "",
                "current_conservative_response": "",
                "current_neutral_response": "",
                "judge_decision": "",
                "count": 0,
            },
        }
    )
    assert llm.invoke.call_args.args[0] == "CUSTOM PM PLAN"


@pytest.mark.unit
def test_graph_setup_exposes_prompt_overrides():
    setup = GraphSetup(
        None,
        None,
        {},
        None,
        prompt_overrides={"market": {"system": "x"}},
    )
    assert setup.prompts_for("market") == {"system": "x"}
    assert setup.prompts_for("bull") is None


@pytest.mark.unit
def test_trading_graph_rejects_unknown_prompt_role():
    with pytest.raises(ValueError, match="无法识别的角色名"):
        TradingAgentsGraph(config={"agent_prompts": {"not_a_role": {"system": "x"}}})

@pytest.mark.unit
def test_quality_gate_only_checks_selected_analysts():
    """关掉部分分析师后，质量门控不能把未参与的角色记成空报告。"""
    captured: dict = {}
    node = create_quality_gate(
        _CapturingLLM(captured, "prompt"),
        selected_analysts=["market"],
    )
    state = {"trade_date": "2026-10-20", "company_of_interest": "600519"}
    state.update(_all_reports())
    # 只保留技术分析报告，其余模拟「未参与」。
    for key in (
        "sentiment_report",
        "news_report",
        "fundamentals_report",
        "policy_report",
        "hot_money_report",
        "lockup_report",
    ):
        state[key] = ""

    node(state)

    assert "prompt" in captured
    assert "1 位分析师" in captured["prompt"]
    assert "技术分析师" in captured["prompt"]
    assert "情绪分析师" not in captured["prompt"]
