from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from tradingagents.agents.prompt_builder import build_analyst_system
from tradingagents.agents.utils.agent_utils import (
    build_instrument_context,
    get_balance_sheet,
    get_cashflow,
    get_fundamentals,
    get_income_statement,
    get_industry_comparison,
    get_insider_transactions,
    get_profit_forecast,
)
from tradingagents.dataflows.config import get_config


def create_fundamentals_analyst(llm, prompt_override=None):
    def fundamentals_analyst_node(state):
        current_date = state["trade_date"]
        instrument_context = build_instrument_context(state["company_of_interest"])

        tools = [
            get_fundamentals,
            get_balance_sheet,
            get_cashflow,
            get_income_statement,
            get_profit_forecast,
            get_industry_comparison,
        ]

        system_message = build_analyst_system(
            "fundamentals",
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
            "fundamentals_report": report,
        }

    return fundamentals_analyst_node
