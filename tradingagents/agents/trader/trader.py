"""Trader: turns the Research Manager's investment plan into a concrete transaction proposal."""

from __future__ import annotations

import functools

from langchain_core.messages import AIMessage

from tradingagents.agents.prompt_builder import build_trader_messages
from tradingagents.agents.schemas import TraderProposal, render_trader_proposal
from tradingagents.agents.utils.agent_utils import (
    build_instrument_context,
    get_language_instruction,
)
from tradingagents.agents.utils.structured import (
    bind_structured,
    invoke_structured_or_freetext,
)

def create_trader(llm, prompt_override=None):
    structured_llm = bind_structured(llm, TraderProposal, "Trader")

    def trader_node(state, name):
        company_name = state.get("company_of_interest", "")
        instrument_context = build_instrument_context(company_name)
        investment_plan = state["investment_plan"]

        # Collect A-stock specific analyst reports
        policy_report = state.get("policy_report", "")
        hot_money_report = state.get("hot_money_report", "")
        lockup_report = state.get("lockup_report", "")

        # Build optional A-stock context block
        astock_context_parts = []
        if policy_report:
            astock_context_parts.append(f"Policy Analysis Report:\n{policy_report}")
        if hot_money_report:
            astock_context_parts.append(f"Hot Money / Capital Flow Report:\n{hot_money_report}")
        if lockup_report:
            astock_context_parts.append(f"Lockup Expiry / Insider Reduction Report:\n{lockup_report}")
        astock_context = "\n\n".join(astock_context_parts)

        astock_context_block = (
            f"Additional A-Stock Analyst Context:\n{astock_context}\n\n"
            if astock_context
            else ""
        )

        messages = build_trader_messages(
            {
                "ticker": company_name,
                "date": state.get("trade_date", ""),
                "company_name": company_name,
                "instrument_context": instrument_context,
                "investment_plan": investment_plan,
                "astock_context_block": astock_context_block,
                "language_instruction": get_language_instruction(),
            },
            overrides=prompt_override,
        )

        trader_plan = invoke_structured_or_freetext(
            structured_llm,
            llm,
            messages,
            render_trader_proposal,
            "Trader",
        )

        return {
            "messages": [AIMessage(content=trader_plan)],
            "trader_investment_plan": trader_plan,
            "sender": name,
        }

    return functools.partial(trader_node, name="Trader")
