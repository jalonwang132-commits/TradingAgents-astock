from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from tradingagents.agents.prompt_builder import build_analyst_system
from tradingagents.agents.utils.agent_utils import (
    build_instrument_context,
    get_fundamentals,
    get_insider_transactions,
    get_lockup_expiry,
    get_news,
)
from tradingagents.dataflows.config import get_config


def create_lockup_watcher(llm, prompt_override=None):
    """A-stock lockup expiry and insider reduction watcher."""

    def lockup_watcher_node(state):
        current_date = state["trade_date"]
        instrument_context = build_instrument_context(state["company_of_interest"])

        tools = [
            get_insider_transactions,
            get_news,
            get_fundamentals,
            get_lockup_expiry,
        ]

        system_message = build_analyst_system(
            "lockup",
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
            "lockup_report": report,
        }

    return lockup_watcher_node
