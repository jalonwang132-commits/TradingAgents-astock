from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from tradingagents.agents.prompt_builder import build_analyst_system
from tradingagents.agents.utils.agent_utils import (
    build_instrument_context,
    get_global_news,
    get_news,
)
from tradingagents.dataflows.config import get_config


def create_news_analyst(llm, prompt_override=None):
    def news_analyst_node(state):
        current_date = state["trade_date"]
        instrument_context = build_instrument_context(state["company_of_interest"])

        tools = [
            get_news,
            get_global_news,
        ]

        system_message = build_analyst_system(
            "news",
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
            "news_report": report,
        }

    return news_analyst_node
