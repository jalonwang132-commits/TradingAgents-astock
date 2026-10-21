from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from tradingagents.agents.prompt_builder import build_analyst_system
from tradingagents.agents.utils.agent_utils import (
    build_instrument_context,
    get_concept_blocks,
    get_dragon_tiger_board,
    get_fund_flow,
    get_hot_stocks,
    get_industry_comparison,
    get_insider_transactions,
    get_news,
    get_northbound_flow,
    get_stock_data,
)
from tradingagents.dataflows.config import get_config


def create_hot_money_tracker(llm, prompt_override=None):
    """A-stock hot money tracker: analyzes capital flow, volume anomalies, and major player movements."""

    def hot_money_tracker_node(state):
        current_date = state["trade_date"]
        instrument_context = build_instrument_context(state["company_of_interest"])

        tools = [
            get_stock_data,
            get_news,
            get_insider_transactions,
            get_hot_stocks,
            get_northbound_flow,
            get_concept_blocks,
            get_fund_flow,
            get_dragon_tiger_board,
            get_industry_comparison,
        ]

        system_message = build_analyst_system(
            "hot_money",
            prompt_override,
            [tool.name for tool in tools],
            current_date,
            instrument_context,
            ticker=state["company_of_interest"],
        )

        prompt = ChatPromptTemplate.from_messages(
            [
                ("system", "{system_message}"),
                MessagesPlaceholder(variable_name="messages"),
            ]
        ).partial(system_message=system_message)

        chain = prompt | llm.bind_tools(tools)
        result = chain.invoke(state["messages"])

        report = ""

        if len(result.tool_calls) == 0:
            report = result.content

        return {
            "messages": [result],
            "hot_money_report": report,
        }

    return hot_money_tracker_node
