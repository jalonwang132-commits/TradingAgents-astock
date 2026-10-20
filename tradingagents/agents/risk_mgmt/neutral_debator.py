

from tradingagents.agents.prompt_builder import build_risk_prompt
from tradingagents.agents.utils.context import compact_history


def create_neutral_debator(llm, prompt_override=None):
    def neutral_node(state) -> dict:
        risk_debate_state = state["risk_debate_state"]
        history = risk_debate_state.get("history", "")
        neutral_history = risk_debate_state.get("neutral_history", "")

        market_research_report = state["market_report"]
        sentiment_report = state["sentiment_report"]
        news_report = state["news_report"]
        fundamentals_report = state["fundamentals_report"]
        policy_report = state.get("policy_report", "")
        hot_money_report = state.get("hot_money_report", "")
        lockup_report = state.get("lockup_report", "")

        trader_decision = state["trader_investment_plan"]

        prompt = build_risk_prompt(
            "risk_neutral",
            {
                "trader_decision": trader_decision,
                "market_report": market_research_report,
                "sentiment_report": sentiment_report,
                "news_report": news_report,
                "fundamentals_report": fundamentals_report,
                "policy_report": policy_report,
                "hot_money_report": hot_money_report,
                "lockup_report": lockup_report,
                "history": compact_history(history),
            },
            overrides=prompt_override,
        )

        response = llm.invoke(prompt)

        argument = f"Neutral Analyst: {response.content}"

        new_risk_debate_state = {
            "history": history + "\n" + argument,
            "aggressive_history": risk_debate_state.get("aggressive_history", ""),
            "conservative_history": risk_debate_state.get("conservative_history", ""),
            "neutral_history": neutral_history + "\n" + argument,
            "latest_speaker": "Neutral",
            "current_aggressive_response": risk_debate_state.get(
                "current_aggressive_response", ""
            ),
            "current_conservative_response": risk_debate_state.get("current_conservative_response", ""),
            "current_neutral_response": argument,
            "count": risk_debate_state["count"] + 1,
        }

        return {"risk_debate_state": new_risk_debate_state}

    return neutral_node
