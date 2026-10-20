

from tradingagents.agents.prompt_builder import build_bull_prompt
from tradingagents.agents.utils.context import compact_history


def create_bull_researcher(llm, prompt_override=None):
    def bull_node(state) -> dict:
        investment_debate_state = state["investment_debate_state"]
        history = investment_debate_state.get("history", "")
        bull_history = investment_debate_state.get("bull_history", "")

        market_research_report = state["market_report"]
        sentiment_report = state["sentiment_report"]
        news_report = state["news_report"]
        fundamentals_report = state["fundamentals_report"]
        policy_report = state.get("policy_report", "")
        hot_money_report = state.get("hot_money_report", "")
        lockup_report = state.get("lockup_report", "")
        data_quality_summary = state.get("data_quality_summary", "")

        prompt = build_bull_prompt(
            {
                "market_report": market_research_report,
                "sentiment_report": sentiment_report,
                "news_report": news_report,
                "fundamentals_report": fundamentals_report,
                "policy_report": policy_report,
                "hot_money_report": hot_money_report,
                "lockup_report": lockup_report,
                "data_quality_summary": data_quality_summary,
                "history": compact_history(history),
            },
            overrides=prompt_override,
        )

        response = llm.invoke(prompt)

        argument = f"Bull Analyst: {response.content}"

        new_investment_debate_state = {
            "history": history + "\n" + argument,
            "bull_history": bull_history + "\n" + argument,
            "bear_history": investment_debate_state.get("bear_history", ""),
            "current_response": argument,
            "count": investment_debate_state["count"] + 1,
        }

        return {"investment_debate_state": new_investment_debate_state}

    return bull_node
