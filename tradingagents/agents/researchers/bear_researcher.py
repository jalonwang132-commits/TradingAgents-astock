

from tradingagents.agents.prompt_builder import build_bear_prompt
from tradingagents.agents.utils.context import compact_history


def create_bear_researcher(llm, prompt_override=None):
    def bear_node(state) -> dict:
        investment_debate_state = state["investment_debate_state"]
        history = investment_debate_state.get("history", "")
        bear_history = investment_debate_state.get("bear_history", "")

        market_research_report = state["market_report"]
        sentiment_report = state["sentiment_report"]
        news_report = state["news_report"]
        fundamentals_report = state["fundamentals_report"]
        policy_report = state.get("policy_report", "")
        hot_money_report = state.get("hot_money_report", "")
        lockup_report = state.get("lockup_report", "")
        data_quality_summary = state.get("data_quality_summary", "")

        prompt = build_bear_prompt(
            {
                "ticker": state.get("company_of_interest", ""),
                "date": state.get("trade_date", ""),
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

        argument = f"Bear Analyst: {response.content}"

        new_investment_debate_state = {
            "history": history + "\n" + argument,
            "bear_history": bear_history + "\n" + argument,
            "bull_history": investment_debate_state.get("bull_history", ""),
            "current_response": argument,
            "count": investment_debate_state["count"] + 1,
        }

        return {"investment_debate_state": new_investment_debate_state}

    return bear_node
