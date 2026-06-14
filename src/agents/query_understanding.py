"""
agents/query_understanding.py
QueryUnderstandingAgent -- first node in the research graph.

Reads:  state["user_query"]
Writes: state["query_understanding"]
"""

from __future__ import annotations

from langchain_core.messages import HumanMessage, SystemMessage

from src.llm import get_llm
from src.prompts import QUERY_UNDERSTANDING_SYSTEM, QUERY_UNDERSTANDING_USER
from src.schemas import QueryUnderstandingOutput
from src.state import AgentState
from src.tracing import tracer


def query_understanding_agent(state: AgentState) -> dict:
    """Parse user query into structured research intent."""
    tracer.log_agent_start("QueryUnderstandingAgent", state)

    llm = get_llm(tier="fast")
    structured = llm.with_structured_output(QueryUnderstandingOutput)

    messages = [
        SystemMessage(content=QUERY_UNDERSTANDING_SYSTEM),
        HumanMessage(content=QUERY_UNDERSTANDING_USER.format(
            query=state["user_query"]
        )),
    ]

    try:
        output: QueryUnderstandingOutput = structured.invoke(messages)
        tracer.log_agent_end("QueryUnderstandingAgent", output)
        return {"query_understanding": output}
    except Exception as e:
        tracer.log_error("QueryUnderstandingAgent", e)
        errors = list(state.get("errors", []))
        errors.append({"agent": "query_understanding", "error": str(e)})
        return {"errors": errors}
