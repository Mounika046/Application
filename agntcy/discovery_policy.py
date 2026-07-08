from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from state import Route


DiscoveryIntent = Literal["document_ingest", "document_query", "web_search", "comparison"]
WorkflowPhase = Literal["ingest", "query", "web_search", "comparison"]


@dataclass(frozen=True)
class DiscoveryPolicy:
    intent: DiscoveryIntent
    primary_skill: str
    secondary_skills: tuple[str, ...] = ()


DOCUMENT_INGEST_POLICY = DiscoveryPolicy(
    intent="document_ingest",
    primary_skill="retrieval_augmented_generation/retrieval_of_information/indexing",
    secondary_skills=("multi_modal/image_processing/image_to_text",),
)

DOCUMENT_QUERY_POLICY = DiscoveryPolicy(
    intent="document_query",
    primary_skill="retrieval_augmented_generation/document_or_database_question_answering",
    secondary_skills=("natural_language_processing/information_retrieval_synthesis/question_answering",),
)

WEB_SEARCH_POLICY = DiscoveryPolicy(
    intent="web_search",
    primary_skill="retrieval_augmented_generation/retrieval_of_information/retrieval_of_information_search",
    secondary_skills=(
        "natural_language_processing/information_retrieval_synthesis/information_retrieval_synthesis_search",
    ),
)

COMPARISON_POLICY = DiscoveryPolicy(
    intent="comparison",
    primary_skill="natural_language_processing/information_retrieval_synthesis/knowledge_synthesis",
    secondary_skills=("natural_language_processing/natural_language_generation/summarization",),
)


ROUTE_PHASE_POLICIES: dict[tuple[Route, WorkflowPhase], DiscoveryPolicy] = {
    ("document", "ingest"): DOCUMENT_INGEST_POLICY,
    ("document_question", "ingest"): DOCUMENT_INGEST_POLICY,
    ("document_question", "query"): DOCUMENT_QUERY_POLICY,
    ("question", "query"): DOCUMENT_QUERY_POLICY,
    ("question", "web_search"): WEB_SEARCH_POLICY,
    ("compare_documents", "ingest"): DOCUMENT_INGEST_POLICY,
    ("compare_documents", "comparison"): COMPARISON_POLICY,
}


def get_policy_for_route(route: Route, phase: WorkflowPhase) -> DiscoveryPolicy:
    try:
        return ROUTE_PHASE_POLICIES[(route, phase)]
    except KeyError as exc:
        raise KeyError(f"No ADS discovery policy defined for route={route!r} phase={phase!r}.") from exc
