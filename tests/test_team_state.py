"""角色状态推导测试：流水线每个阶段是否点亮正确的角色。"""

from __future__ import annotations

import pytest

from tradingagents.agents.prompt_registry import ANALYST_ROLES
from web.team_state import (
    STATUS_ACTIVE,
    STATUS_DONE,
    STATUS_OFF,
    STATUS_PENDING,
    derive_role_states,
    empty_role_states,
    summarize_role_states,
)


def _all_reports() -> dict[str, str]:
    return {
        "market_report": "m",
        "sentiment_report": "s",
        "news_report": "n",
        "fundamentals_report": "f",
        "policy_report": "p",
        "hot_money_report": "h",
        "lockup_report": "l",
    }


def _debate(*, count: int = 0, bull: str = "", bear: str = "", plan: str = "") -> dict:
    return {
        "investment_debate_state": {
            "history": "",
            "bull_history": bull,
            "bear_history": bear,
            "current_response": "",
            "judge_decision": plan,
            "count": count,
        }
    }


def _risk(
    *,
    count: int = 0,
    latest: str = "",
    aggressive: str = "",
    conservative: str = "",
    neutral: str = "",
) -> dict:
    return {
        "risk_debate_state": {
            "history": "",
            "aggressive_history": aggressive,
            "conservative_history": conservative,
            "neutral_history": neutral,
            "latest_speaker": latest,
            "current_aggressive_response": "",
            "current_conservative_response": "",
            "current_neutral_response": "",
            "judge_decision": "",
            "count": count,
        }
    }


@pytest.mark.unit
def test_empty_state_marks_first_analyst_active():
    states = derive_role_states({}, selected_analysts=["news", "market"])
    assert states["news"] == STATUS_ACTIVE
    assert states["market"] == STATUS_PENDING
    assert states["social"] == STATUS_OFF
    assert states["quality_gate"] == STATUS_PENDING


@pytest.mark.unit
def test_analyst_progress_walks_in_selected_order():
    selected = ["news", "market"]
    states = derive_role_states({"news_report": "done"}, selected)
    assert states["news"] == STATUS_DONE
    assert states["market"] == STATUS_ACTIVE

    states = derive_role_states(
        {"news_report": "done", "market_report": "done"}, selected
    )
    assert states["market"] == STATUS_DONE
    assert states["quality_gate"] == STATUS_ACTIVE


@pytest.mark.unit
def test_downstream_start_marks_empty_analyst_reports_done():
    # 分析师报告为空（失败/空输出）但流程已经推进到质量门控，页面不能永远停在
    # 「第一位缺报告的分析师进行中」。
    states = derive_role_states({"data_quality_summary": "reviewed"})
    for role in ANALYST_ROLES:
        assert states[role] == STATUS_DONE
    assert states["quality_gate"] == STATUS_DONE
    assert states["bull"] == STATUS_ACTIVE


@pytest.mark.unit
def test_debate_progression_and_research_manager():
    state = _all_reports() | {"data_quality_summary": "q"} | _debate(count=0)
    states = derive_role_states(state, max_debate_rounds=1)
    assert states["bull"] == STATUS_ACTIVE
    assert states["bear"] == STATUS_PENDING

    state = _all_reports() | {"data_quality_summary": "q"} | _debate(
        count=1, bull="bull"
    )
    states = derive_role_states(state, max_debate_rounds=1)
    assert states["bull"] == STATUS_DONE
    assert states["bear"] == STATUS_ACTIVE

    state = _all_reports() | {"data_quality_summary": "q"} | _debate(
        count=2, bull="bull", bear="bear"
    )
    states = derive_role_states(state, max_debate_rounds=1)
    assert states["bear"] == STATUS_DONE
    assert states["research_manager"] == STATUS_ACTIVE

    state = _all_reports() | {"data_quality_summary": "q"} | _debate(
        count=2, bull="bull", bear="bear", plan="plan"
    )
    states = derive_role_states(state, max_debate_rounds=1)
    assert states["research_manager"] == STATUS_DONE
    assert states["trader"] == STATUS_ACTIVE


@pytest.mark.unit
def test_long_debate_round_two_goes_back_to_bull():
    state = _all_reports() | {"data_quality_summary": "q"} | _debate(
        count=3, bull="bull", bear="bear"
    )
    states = derive_role_states(state, max_debate_rounds=2)
    assert states["bull"] == STATUS_DONE
    assert states["bear"] == STATUS_ACTIVE

    state = _all_reports() | {"data_quality_summary": "q"} | _debate(
        count=4, bull="bull", bear="bear"
    )
    states = derive_role_states(state, max_debate_rounds=2)
    assert states["research_manager"] == STATUS_ACTIVE


@pytest.mark.unit
def test_trader_and_risk_progression():
    base = _all_reports() | {"data_quality_summary": "q"} | _debate(
        count=2, bull="bull", bear="bear", plan="plan"
    )
    state = base | {"trader_investment_plan": "trader"}
    states = derive_role_states(state, max_risk_rounds=1)
    assert states["trader"] == STATUS_DONE
    assert states["risk_aggressive"] == STATUS_ACTIVE

    state = base | {"trader_investment_plan": "trader"} | _risk(count=1, latest="Aggressive", aggressive="a")
    states = derive_role_states(state, max_risk_rounds=1)
    assert states["risk_aggressive"] == STATUS_DONE
    assert states["risk_conservative"] == STATUS_ACTIVE

    state = base | {"trader_investment_plan": "trader"} | _risk(
        count=2, latest="Conservative", aggressive="a", conservative="c"
    )
    states = derive_role_states(state, max_risk_rounds=1)
    assert states["risk_neutral"] == STATUS_ACTIVE

    state = base | {"trader_investment_plan": "trader"} | _risk(
        count=3, latest="Neutral", aggressive="a", conservative="c", neutral="n"
    )
    states = derive_role_states(state, max_risk_rounds=1)
    assert states["portfolio_manager"] == STATUS_ACTIVE


@pytest.mark.unit
def test_final_decision_completes_everything():
    state = {"final_trade_decision": "Buy"}
    states = derive_role_states(state, max_debate_rounds=2, max_risk_rounds=2)
    assert all(value == STATUS_DONE for value in states.values())


@pytest.mark.unit
def test_unselected_analysts_are_off_and_do_not_block_quality():
    selected = ["market", "social"]
    state = {"market_report": "m", "sentiment_report": "s"}
    states = derive_role_states(state, selected_analysts=selected)
    assert states["market"] == STATUS_DONE
    assert states["social"] == STATUS_DONE
    assert states["quality_gate"] == STATUS_ACTIVE
    for role in ANALYST_ROLES:
        if role not in selected:
            assert states[role] == STATUS_OFF


@pytest.mark.unit
def test_empty_role_states_and_summary():
    states = empty_role_states(["market"])
    assert states["market"] == STATUS_PENDING
    assert states["social"] == STATUS_OFF
    summary = summarize_role_states(states)
    assert summary["等待"] == 10
    assert summary["未参与"] == 6
