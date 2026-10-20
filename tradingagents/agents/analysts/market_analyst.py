from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from tradingagents.agents.prompt_builder import build_analyst_system
from tradingagents.agents.utils.agent_utils import (
    build_instrument_context,
    get_indicators,
    get_stock_data,
)
from tradingagents.dataflows.config import get_config


def create_market_analyst(llm, prompt_override=None):
    def market_analyst_node(state):
        current_date = state["trade_date"]
        instrument_context = build_instrument_context(state["company_of_interest"])
        # User-configurable analysis window (#16). None → previous default (~30).
        lookback = get_config().get("market_lookback_days") or 30

        tools = [
            get_stock_data,
            get_indicators,
        ]

        system_message = build_analyst_system(
            "market",
            prompt_override,
            [tool.name for tool in tools],
            current_date,
            instrument_context,
            lookback=lookback,
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
            "market_report": report,
        }

    return market_analyst_node
