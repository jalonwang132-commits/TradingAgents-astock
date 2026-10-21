from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from tradingagents.agents.prompt_builder import build_analyst_system
from tradingagents.agents.utils.agent_utils import (
    build_instrument_context,
    get_fund_flow,
    get_hot_stocks,
    get_news,
    get_stock_data,
)
from tradingagents.dataflows.config import get_config


def create_social_media_analyst(llm, prompt_override=None):
    def social_media_analyst_node(state):
        current_date = state["trade_date"]
        instrument_context = build_instrument_context(state["company_of_interest"])

        # 情绪不能只靠读新闻推断（#61）：光看文本，模型很容易把"这条新闻听起来利好"
        # 当成"市场情绪乐观"，而资金可能正在流出。加上资金流 / 热度榜 / 量价三样
        # 可核对的硬数据，让情绪判断有据可依，也能暴露"消息面与资金面背离"。
        tools = [
            get_news,
            get_fund_flow,
            get_hot_stocks,
            get_stock_data,
        ]

        system_message = build_analyst_system(
            "social",
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
            "sentiment_report": report,
        }

    return social_media_analyst_node
