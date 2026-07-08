from __future__ import annotations

from langgraph.graph import StateGraph, END
from state import AgentState
from agents.orchestrator_agent import (
    compare_node,
    document_query_node,
    document_store_node,
    finalize_node,
    route_node,
    web_search_node,
)


def compile_app():
    g = StateGraph(AgentState)
    g.add_node("route", route_node)
    g.add_node("document_store", document_store_node)
    g.add_node("document_query", document_query_node)
    g.add_node("compare", compare_node)
    g.add_node("web", web_search_node)
    g.add_node("finalize", finalize_node)

    g.set_entry_point("route")

    def decide_after_route(s: AgentState) -> str:
        return s.get("route", "question")

    g.add_conditional_edges(
        "route",
        decide_after_route,
        {
            "document": "document_store",
            "document_question": "document_store",
            "question": "document_query",
            "compare_documents": "compare",
            "conversation_followup": "finalize",
        },
    )

    def decide_after_ingest(s: AgentState) -> str:
        if s.get("route") == "document":
            return "finalize"
        return "document_query"

    g.add_conditional_edges(
        "document_store",
        decide_after_ingest,
        {
            "finalize": "finalize",
            "document_query": "document_query",
        },
    )

    def decide_after_document_query(s: AgentState) -> str:
        if s.get("route") == "document_question":
            return "finalize"
        return "finalize" if s.get("rag_confident") else "web"

    g.add_conditional_edges(
        "document_query",
        decide_after_document_query,
        {
            "finalize": "finalize",
            "web": "web",
        },
    )

    g.add_edge("compare", "finalize")
    g.add_edge("web", "finalize")
    g.add_edge("finalize", END)
    return g.compile()
