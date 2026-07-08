from __future__ import annotations

import re
from typing import Any, Dict, List

from agntcy.directory_resolver import DirectoryResolver
from agntcy.discovery_policy import get_policy_for_route
from agntcy.identity_node_verifier import IdentityNodeVerifier
from agents.generic_a2a_client import invoke_a2a_json
from config import (
    COMPARISON_AGENT_TIMEOUT_S,
    DOC_INGEST_AGENT_TIMEOUT_S,
    DOCUMENT_QUERY_AGENT_TIMEOUT_S,
    WEB_SEARCH_AGENT_TIMEOUT_S,
)
from file_input import file_input_display_label, make_doc_id_for_input
from flow_trace import skip_if_empty, trace_event
from oracle_enterprise_ai.responses_client import create_text_response
from state import AgentState

DOCQA_CMD = re.compile(r'^\s*docqa\s+"?(.+?)"?\s*\|\s*(.+)\s*$', re.IGNORECASE)
OCR_CMD = re.compile(r'^\s*ocr\s+"?(.+?)"?\s*$', re.IGNORECASE)
COMPARE_DOCS_CMD = re.compile(
    r'^\s*compare\s+"(?P<left>.+?)"\s+with\s+"(?P<right>.+?)"\s*$',
    re.IGNORECASE,
)
RAW_DOC_AND_QUERY = re.compile(r'^\s*"?(?P<path>[A-Za-z]:\\[^"|]+?\.(?:pdf|png|jpg|jpeg|tif|tiff|bmp))"?\s*\|\s*(?P<query>.+)\s*$', re.IGNORECASE)
RAW_DOC_ONLY = re.compile(r'^\s*"?(?P<path>[A-Za-z]:\\[^"]+?\.(?:pdf|png|jpg|jpeg|tif|tiff|bmp))"?\s*$', re.IGNORECASE)
_directory_resolver = DirectoryResolver()
_identity_verifier = IdentityNodeVerifier()


def _trace(stage: str, message: str) -> None:
    trace_event(stage, message)


def _ensure_errors(state: AgentState) -> None:
    if "errors" not in state or state["errors"] is None:
        state["errors"] = []


def _normalize_final_answer_text(text: str) -> str:
    cleaned = str(text or "").replace("【", "[").replace("】", "]")
    cleaned = re.sub(r"\[\s*RAG(?:_|\s+)CONTEXT\s*\]", "[RAG]", cleaned, flags=re.IGNORECASE)
    return re.sub(r"\[\s*(\d+)\s*\]", r"[\1]", cleaned)


def _is_conversation_followup(*, user_input: str, conversation_id: str | None) -> bool:
    if not str(conversation_id or "").strip():
        return False

    text = str(user_input or "").strip().lower()
    if not text:
        return False

    explicit_reference_patterns = (
        r"\babove answer\b",
        r"\bprevious answer\b",
        r"\bprevious response\b",
        r"\babove response\b",
        r"\bthis answer\b",
        r"\bthat answer\b",
        r"\bprevious one\b",
        r"\bthe above\b",
        r"\bthe previous\b",
    )
    if any(re.search(pattern, text, re.IGNORECASE) for pattern in explicit_reference_patterns):
        return True

    followup_starts = (
        "summarize",
        "shorten",
        "rewrite",
        "rephrase",
        "translate",
        "convert",
        "make it",
        "put it",
        "give it",
        "list it",
        "explain it",
    )
    if text.startswith(followup_starts) and any(token in text for token in ("it", "that", "above", "previous")):
        return True

    return False


def _has_active_document_context(state: AgentState) -> bool:
    document_store = state.get("document_store") or {}
    if not isinstance(document_store, dict):
        return False
    metadata = document_store.get("metadata") if isinstance(document_store.get("metadata"), dict) else {}
    return bool(
        str(document_store.get("doc_id") or "").strip()
        and str(metadata.get("vector_store_id") or "").strip()
        and str(metadata.get("file_id") or "").strip()
    )


def _is_document_followup(*, user_input: str, state: AgentState) -> bool:
    if not _has_active_document_context(state):
        return False

    text = str(user_input or "").strip().lower()
    if not text:
        return False

    explicit_patterns = (
        r"\bin the document\b",
        r"\bfrom the document\b",
        r"\bthis document\b",
        r"\bthat document\b",
        r"\bthe file\b",
        r"\bthis file\b",
        r"\bthat file\b",
        r"\bthe pdf\b",
        r"\bthis pdf\b",
        r"\bthat pdf\b",
    )
    if any(re.search(pattern, text, re.IGNORECASE) for pattern in explicit_patterns):
        return True

    document_terms = (
        "paragraph",
        "section",
        "clause",
        "page",
        "line",
        "table",
        "figure",
        "heading",
        "title",
        "document",
        "file",
        "pdf",
    )
    question_starts = (
        "what",
        "which",
        "who",
        "when",
        "where",
        "why",
        "how",
        "summarize",
        "list",
        "show",
        "give",
        "find",
        "tell me",
    )
    return text.startswith(question_starts) and any(term in text for term in document_terms)


async def _discover_endpoint(*, route: str, phase: str) -> str:
    policy = get_policy_for_route(route, phase)
    trace_event(
        "discover",
        "Discovery started",
        route=route,
        phase=phase,
        intent=policy.intent,
        primary_skill=policy.primary_skill,
        secondary_skills=skip_if_empty(list(policy.secondary_skills)),
    )
    endpoint = _directory_resolver.discover_best_a2a_agent(
        policy.primary_skill,
        secondary_skills=policy.secondary_skills,
    )
    trace_event(
        "discover",
        "Discovery result",
        subject=endpoint.subject_id,
        endpoint=endpoint.endpoint_url,
        identity_annotation=bool(endpoint.identity_agent_id),
        resolver_metadata_id=skip_if_empty(endpoint.identity_agent_id),
    )
    verification = await _identity_verifier.verify_subject(
        endpoint.subject_id,
        resolver_metadata_id=endpoint.identity_agent_id,
    )
    if not verification.verified:
        trace_event(
            "discover",
            "Discovery rejected",
            subject=endpoint.subject_id,
            reason=verification.error or "identity verification failed",
        )
        raise RuntimeError(verification.error or "identity verification failed")
    trace_event(
        "discover",
        "Discovery accepted",
        subject=endpoint.subject_id,
        verified=verification.verified,
        skipped_reason=skip_if_empty(verification.skipped_reason),
        controller=skip_if_empty(verification.controller),
        warning_count=verification.warning_count,
    )
    return endpoint.endpoint_url

async def _ingest_document_for_compare(*, path: str = "", file_ref: dict[str, Any] | None = None) -> dict:
    endpoint_url = await _discover_endpoint(
        route="compare_documents",
        phase="ingest",
    )
    trace_event(
        "compare",
        "Document ingest started",
        path=skip_if_empty(path),
        uses_file_ref=isinstance(file_ref, dict) and bool(file_ref),
        endpoint=endpoint_url,
    )
    payload: dict[str, Any] = {"dpi": 300}
    if str(path or "").strip():
        payload["path"] = path
    if isinstance(file_ref, dict) and file_ref:
        payload["file_ref"] = file_ref
    return await invoke_a2a_json(
        endpoint_url=endpoint_url,
        payload=payload,
        timeout_s=DOC_INGEST_AGENT_TIMEOUT_S,
    )


def _document_source_label(*, path: str = "", file_ref: dict[str, Any] | None = None, fallback: str = "document") -> str:
    return file_input_display_label(
        path=path,
        file_ref=file_ref,
        fallback=fallback,
    )


def _format_comparison_result(result: dict) -> str:
    lines: list[str] = []

    summary = str(result.get("summary") or "").strip()
    if summary:
        lines.append(f"Summary: {summary}")

    similarities = result.get("similarities") or []
    if similarities:
        lines.append("")
        lines.append("Similarities:")
        for item in similarities:
            lines.append(f"- {item}")

    differences = result.get("differences") or []
    if differences:
        lines.append("")
        lines.append("Differences:")
        for diff in differences:
            if not isinstance(diff, dict):
                lines.append(f"- {diff}")
                continue
            topic = str(diff.get("topic") or "Change").strip()
            left = str(diff.get("left") or "").strip()
            right = str(diff.get("right") or "").strip()
            impact = str(diff.get("impact") or "").strip()
            lines.append(f"- {topic}")
            if left:
                lines.append(f"  Left: {left}")
            if right:
                lines.append(f"  Right: {right}")
            if impact:
                lines.append(f"  Impact: {impact}")

    added = result.get("added_in_right") or []
    if added:
        lines.append("")
        lines.append("Added In Right:")
        for item in added:
            lines.append(f"- {item}")

    missing = result.get("missing_in_right") or []
    if missing:
        lines.append("")
        lines.append("Missing In Right:")
        for item in missing:
            lines.append(f"- {item}")

    risks = result.get("risk_changes") or []
    if risks:
        lines.append("")
        lines.append("Risk Changes:")
        for item in risks:
            lines.append(f"- {item}")

    confidence = str(result.get("confidence") or "").strip()
    if confidence:
        lines.append("")
        lines.append(f"Confidence: {confidence}")

    return "\n".join(lines).strip() or "Comparison completed, but no formatted result was returned."


def route_node(state: AgentState) -> AgentState:
    _ensure_errors(state)
    user_input = re.sub(r"[\r\n]+", " ", (state.get("user_input") or "")).strip()
    conversation_id = str(state.get("conversation_id") or "").strip() or None
    route_hint = str(state.get("route_hint") or "").strip() or None
    active_document_context = _has_active_document_context(state)
    doc_file_ref = state.get("doc_file_ref")
    has_doc_file_ref = isinstance(doc_file_ref, dict) and bool(doc_file_ref)
    left_file_ref = state.get("left_file_ref")
    has_left_file_ref = isinstance(left_file_ref, dict) and bool(left_file_ref)
    right_file_ref = state.get("right_file_ref")
    has_right_file_ref = isinstance(right_file_ref, dict) and bool(right_file_ref)
    trace_event(
        "route",
        "Route evaluation started",
        user_input=user_input,
        conversation_id=skip_if_empty(conversation_id),
        route_hint=skip_if_empty(route_hint),
        active_document_context=active_document_context,
    )

    if route_hint == "compare_documents" and has_left_file_ref and has_right_file_ref:
        trace_event(
            "route",
            "Route selected",
            route="compare_documents",
            reason="route hint with uploaded documents",
        )
        return {
            "user_input": user_input,
            "route": "compare_documents",
            "left_doc_path": str(state.get("left_doc_path") or "").strip() or None,
            "right_doc_path": str(state.get("right_doc_path") or "").strip() or None,
            "left_file_ref": left_file_ref,
            "right_file_ref": right_file_ref,
            "user_query": "",
        }

    if route_hint == "document" and has_doc_file_ref:
        trace_event(
            "route",
            "Route selected",
            route="document",
            reason="route hint with uploaded document",
        )
        return {
            "user_input": user_input,
            "route": "document",
            "doc_path": None,
            "doc_file_ref": doc_file_ref,
            "user_query": "",
        }

    if route_hint == "document_question" and has_doc_file_ref:
        trace_event(
            "route",
            "Route selected",
            route="document_question",
            reason="route hint with uploaded document",
            user_query=user_input,
        )
        return {
            "user_input": user_input,
            "route": "document_question",
            "doc_path": None,
            "doc_file_ref": doc_file_ref,
            "user_query": user_input,
        }

    if _is_conversation_followup(user_input=user_input, conversation_id=conversation_id):
        trace_event(
            "route",
            "Route selected",
            route="conversation_followup",
            user_query=user_input,
            reason="conversation reference detected",
        )
        return {
            "user_input": user_input,
            "route": "conversation_followup",
            "doc_path": None,
            "doc_file_ref": None,
            "user_query": user_input,
        }

    if _is_document_followup(user_input=user_input, state=state):
        trace_event(
            "route",
            "Route selected",
            route="document_question",
            user_query=user_input,
            reason="reusing active document context",
        )
        return {
            "user_input": user_input,
            "route": "document_question",
            "doc_path": None,
            "doc_file_ref": None,
            "user_query": user_input,
        }

    match = COMPARE_DOCS_CMD.match(user_input)
    if match:
        left_doc_path = match.group("left").strip()
        right_doc_path = match.group("right").strip()
        trace_event(
            "route",
            "Route selected",
            route="compare_documents",
            left_doc_path=left_doc_path,
            right_doc_path=right_doc_path,
        )
        return {
            "user_input": user_input,
            "route": "compare_documents",
            "left_doc_path": left_doc_path,
            "right_doc_path": right_doc_path,
            "left_file_ref": None,
            "right_file_ref": None,
            "user_query": "",
        }

    match = DOCQA_CMD.match(user_input) or RAW_DOC_AND_QUERY.match(user_input)
    if match:
        doc_path = match.group(1).strip() if match.re is DOCQA_CMD else match.group("path").strip()
        user_query = match.group(2).strip() if match.re is DOCQA_CMD else match.group("query").strip()
        trace_event(
            "route",
            "Route selected",
            route="document_question",
            doc_path=doc_path,
            user_query=user_query,
        )
        return {
            "user_input": user_input,
            "route": "document_question",
            "doc_path": doc_path,
            "doc_file_ref": None,
            "user_query": user_query,
        }

    match = OCR_CMD.match(user_input) or RAW_DOC_ONLY.match(user_input)
    if match:
        path = match.group(1).strip() if match.re is OCR_CMD else match.group("path").strip()
        trace_event(
            "route",
            "Route selected",
            route="document",
            doc_path=path,
        )
        return {
            "user_input": user_input,
            "route": "document",
            "doc_path": path,
            "doc_file_ref": None,
            "user_query": "",
        }

    trace_event(
        "route",
        "Route selected",
        route="question",
        user_query=user_input,
    )
    return {
        "user_input": user_input,
        "route": "question",
        "doc_path": None,
        "doc_file_ref": None,
        "user_query": user_input,
    }


async def document_store_node(state: AgentState) -> AgentState:
    _ensure_errors(state)
    path = str(state.get("doc_path") or "").strip()
    file_ref = state.get("doc_file_ref")
    file_ref = file_ref if isinstance(file_ref, dict) and file_ref else None
    if not path and not file_ref:
        if _has_active_document_context(state):
            document_store = state.get("document_store") or {}
            metadata = document_store.get("metadata") if isinstance(document_store, dict) else {}
            trace_event(
                "document_store",
                "Reusing active document context",
                doc_id=skip_if_empty(document_store.get("doc_id") if isinstance(document_store, dict) else None),
                file_id=skip_if_empty(metadata.get("file_id") if isinstance(metadata, dict) else None),
                vector_store_id=skip_if_empty(metadata.get("vector_store_id") if isinstance(metadata, dict) else None),
            )
        else:
            trace_event("document_store", "Skipped", reason="no document source")
        return {}

    try:
        route = str(state.get("route") or "document")
        endpoint_url = await _discover_endpoint(
            route=route,
            phase="ingest",
        )
        trace_event(
            "document_store",
            "Document ingest request",
            route=route,
            path=skip_if_empty(path),
            uses_file_ref=bool(file_ref),
            endpoint=endpoint_url,
        )
        payload: dict[str, Any] = {"dpi": 300}
        if path:
            payload["path"] = path
        if file_ref:
            payload["file_ref"] = file_ref
        result = await invoke_a2a_json(
            endpoint_url=endpoint_url,
            payload=payload,
            timeout_s=DOC_INGEST_AGENT_TIMEOUT_S,
        )
        ocr_text = result.get("ocr_text")
        document_store = result.get("document_store")
        if isinstance(document_store, dict):
            metadata = document_store.get("metadata") if isinstance(document_store.get("metadata"), dict) else {}
            trace_event(
                "document_store",
                "Document ingest completed",
                status=document_store.get("status"),
                doc_id=skip_if_empty(document_store.get("doc_id")),
                file_id=skip_if_empty(metadata.get("file_id")),
                vector_store_id=skip_if_empty(metadata.get("vector_store_id")),
            )
        return {"ocr_text": ocr_text, "document_store": document_store}
    except Exception as exc:
        source_label = _document_source_label(
            path=path,
            file_ref=file_ref,
            fallback="document",
        )
        trace_event(
            "document_store",
            "Document ingest failed",
            path=skip_if_empty(path),
            source_label=source_label,
            error=str(exc),
        )
        state["errors"].append(f"Document ingest failed: {exc}")
        doc_id = make_doc_id_for_input(path=path, file_ref=file_ref)
        return {
            "ocr_text": None,
            "document_store": {"status": "error", "doc_id": doc_id, "message": str(exc)},
            "errors": state["errors"],
        }


async def web_search_node(state: AgentState) -> AgentState:
    _ensure_errors(state)
    question = (state.get("user_query") or "").strip()
    if not question:
        trace_event("web", "Skipped", reason="no user query")
        return {}

    try:
        route = str(state.get("route") or "question")
        endpoint_url = await _discover_endpoint(
            route=route,
            phase="web_search",
        )
        trace_event(
            "web",
            "Web search request",
            route=route,
            question=question,
            endpoint=endpoint_url,
        )
        result = await invoke_a2a_json(
            endpoint_url=endpoint_url,
            payload={"question": question, "num_results": 5},
            timeout_s=WEB_SEARCH_AGENT_TIMEOUT_S,
        )
        web_error = (result.get("web_error") or "").strip()
        if web_error:
            trace_event(
                "web",
                "Web search failed",
                error=web_error,
            )
            state["errors"].append(f"Web search failed: {web_error}")
            return {"web_results": None, "web_error": web_error, "errors": state["errors"]}
        results = result.get("web_results") or []
        trace_event(
            "web",
            "Web search completed",
            result_count=len(results) if isinstance(results, list) else 0,
        )
        return {"web_results": result.get("web_results"), "web_error": None}
    except Exception as exc:
        trace_event(
            "web",
            "Web search failed",
            error=str(exc),
        )
        state["errors"].append(f"Web search failed: {exc}")
        return {"web_results": None, "web_error": str(exc), "errors": state["errors"]}


async def document_query_node(state: AgentState) -> AgentState:
    _ensure_errors(state)
    question = (state.get("user_query") or "").strip()
    if not question:
        trace_event("document_query", "Skipped", reason="no user query")
        return {"rag_confident": False, "rag_answer": "", "rag_citations": [], "errors": state["errors"]}

    document_store = state.get("document_store") or {}
    doc_id = document_store.get("doc_id") if isinstance(document_store, dict) else None
    metadata = document_store.get("metadata") if isinstance(document_store, dict) else None
    vector_store_id = metadata.get("vector_store_id") if isinstance(metadata, dict) else None
    file_id = metadata.get("file_id") if isinstance(metadata, dict) else None

    try:
        route = str(state.get("route") or "question")
        endpoint_url = await _discover_endpoint(
            route=route,
            phase="query",
        )
        trace_event(
            "document_query",
            "Document query request",
            question=question,
            route=skip_if_empty(route),
            doc_id=skip_if_empty(doc_id),
            file_id=skip_if_empty(file_id),
            vector_store_id=skip_if_empty(vector_store_id),
            endpoint=endpoint_url,
        )
        result = await invoke_a2a_json(
            endpoint_url=endpoint_url,
            payload={
                "question": question,
                "doc_id": doc_id,
                "vector_store_id": vector_store_id,
                "file_id": file_id,
                "ocr_text": state.get("ocr_text"),
                "top_k": 5,
            },
            timeout_s=DOCUMENT_QUERY_AGENT_TIMEOUT_S,
        )
        trace_event(
            "document_query",
            "Document query completed",
            search_scope=skip_if_empty(result.get("search_scope")),
            confident=bool(result.get("rag_confident")),
            citation_count=len(result.get("rag_citations") or []),
        )
        result["errors"] = state["errors"]
        return result
    except Exception as exc:
        trace_event(
            "document_query",
            "Document query failed",
            error=str(exc),
        )
        state["errors"].append(f"RAG query failed: {exc}")
        return {"rag_confident": False, "rag_answer": "", "rag_citations": [], "errors": state["errors"]}

async def compare_node(state: AgentState) -> AgentState:
    _ensure_errors(state)

    left_path = str(state.get("left_doc_path") or "").strip()
    right_path = str(state.get("right_doc_path") or "").strip()
    left_file_ref = state.get("left_file_ref")
    left_file_ref = left_file_ref if isinstance(left_file_ref, dict) and left_file_ref else None
    right_file_ref = state.get("right_file_ref")
    right_file_ref = right_file_ref if isinstance(right_file_ref, dict) and right_file_ref else None

    if not (left_path or left_file_ref) or not (right_path or right_file_ref):
        trace_event("compare", "Skipped", reason="one or both document sources are missing")
        return {}

    try:
        trace_event(
            "compare",
            "Comparison started",
            left_doc_path=skip_if_empty(left_path),
            right_doc_path=skip_if_empty(right_path),
            left_uses_file_ref=bool(left_file_ref),
            right_uses_file_ref=bool(right_file_ref),
        )

        left_result = await _ingest_document_for_compare(path=left_path, file_ref=left_file_ref)
        right_result = await _ingest_document_for_compare(path=right_path, file_ref=right_file_ref)

        left_text = str(left_result.get("ocr_text") or "").strip()
        right_text = str(right_result.get("ocr_text") or "").strip()
        left_label = _document_source_label(
            path=left_path,
            file_ref=left_file_ref,
            fallback="Left Document",
        )
        right_label = _document_source_label(
            path=right_path,
            file_ref=right_file_ref,
            fallback="Right Document",
        )

        if not left_text:
            raise RuntimeError(f"No OCR text returned for left document: {left_label}")
        if not right_text:
            raise RuntimeError(f"No OCR text returned for right document: {right_label}")

        endpoint_url = await _discover_endpoint(
            route="compare_documents",
            phase="comparison",
        )
        trace_event(
            "compare",
            "Comparison request",
            endpoint=endpoint_url,
            left_label=left_label,
            right_label=right_label,
        )

        comparison_result = await invoke_a2a_json(
            endpoint_url=endpoint_url,
            payload={
                "left_text": left_text,
                "right_text": right_text,
                "left_label": left_label,
                "right_label": right_label,
                "question": "What changed between these two documents?",
                "focus": "differences, similarities, added content, removed content, risk changes",
            },
            timeout_s=COMPARISON_AGENT_TIMEOUT_S,
        )

        trace_event(
            "compare",
            "Comparison completed",
            has_result=isinstance(comparison_result, dict) and bool(comparison_result),
            difference_count=len(comparison_result.get("differences") or []) if isinstance(comparison_result, dict) else 0,
        )
        return {
            "left_ocr_text": left_text,
            "right_ocr_text": right_text,
            "comparison_result": comparison_result,
            "errors": state["errors"],
        }
    except Exception as exc:
        trace_event(
            "compare",
            "Comparison failed",
            error=str(exc),
        )
        state["errors"].append(f"Comparison failed: {exc}")
        return {
            "comparison_result": None,
            "errors": state["errors"],
        }


def _build_web_block(web_results: List[Dict[str, Any]]) -> str:
    out: List[str] = []
    for i, result in enumerate(web_results, start=1):
        out.append(
            f"[{i}] {result.get('title', '')}\n{result.get('snippet', '')}\n{result.get('url', '')}".strip()
        )
    return "\n\n".join(out)


def _latest_error_answer(state: AgentState, default_message: str) -> str:
    errors = state.get("errors") or []
    if errors:
        return f"{default_message} Latest error: {errors[-1]}"
    return default_message


def _normalize_sentence(text: str) -> str:
    cleaned = re.sub(r"\s+", " ", str(text or "").strip())
    if not cleaned:
        return ""
    if cleaned[-1] not in ".!?":
        cleaned = f"{cleaned}."
    return cleaned


def _fallback_web_answer(web_results: List[Dict[str, Any]]) -> str:
    lines: list[str] = []
    for index, result in enumerate(web_results[:2], start=1):
        snippet = _normalize_sentence(result.get("snippet") or "")
        title = _normalize_sentence(result.get("title") or "")
        if snippet:
            lines.append(f"{snippet} [{index}]")
        elif title:
            lines.append(f"{title} [{index}]")
    return "\n".join(lines).strip()


def _fallback_finalize_answer(
    *,
    route: str | None,
    rag_context: str,
    web_results: List[Dict[str, Any]],
    web_error: str,
    last_final_answer: str,
) -> str:
    if route == "conversation_followup" and last_final_answer:
        return last_final_answer
    if rag_context:
        return f"{_normalize_sentence(rag_context)} [RAG]"
    if web_results:
        fallback = _fallback_web_answer(web_results)
        if fallback:
            return fallback
    if web_error:
        return (
            "I couldn't find a confident answer in the vector-store retrieval flow, and the live web search also failed. "
            f"Web search error: {web_error}"
        )
    if last_final_answer:
        return last_final_answer
    return "I couldn't generate a final grounded answer, but the upstream retrieval steps completed."


def finalize_node(state: AgentState) -> AgentState:
    _ensure_errors(state)

    route = state.get("route")
    trace_event(
        "finalize",
        "Finalize started",
        route=skip_if_empty(route),
    )
    ocr_text = (state.get("ocr_text") or "").strip()
    document_store = state.get("document_store") or {}
    ingest_status = document_store.get("status") if isinstance(document_store, dict) else None

    if route == "compare_documents":
        comparison_result = state.get("comparison_result")
        if isinstance(comparison_result, dict) and comparison_result:
            trace_event("finalize", "Finalize completed", strategy="comparison_result")
            return {"final_answer": _format_comparison_result(comparison_result)}
        trace_event("finalize", "Finalize completed", strategy="comparison_error")
        return {"final_answer": _latest_error_answer(state, "Comparison could not be completed.")}

    if route == "document":
        if ocr_text:
            stored_suffix = " Stored in the vector store." if ingest_status == "ok" else ""
            trace_event(
                "finalize",
                "Finalize completed",
                strategy="ocr_text",
                stored_in_vector_store=ingest_status == "ok",
            )
            return {"final_answer": f"{ocr_text}{stored_suffix}"}
        trace_event("finalize", "Finalize completed", strategy="ocr_error")
        return {
            "final_answer": _latest_error_answer(
                state,
                "OCR could not extract usable text from the document.",
            )
        }

    rag_conf = bool(state.get("rag_confident"))
    rag_context = (state.get("rag_answer") or "").strip()
    web_results = state.get("web_results") or []
    web_error = (state.get("web_error") or "").strip()
    errors = state.get("errors") or []
    conversation_id = str(state.get("conversation_id") or "").strip() or None
    last_final_answer = str(state.get("last_final_answer") or "").strip()
    last_route = str(state.get("last_route") or "").strip()

    if route == "conversation_followup":
        followup_input = str(state.get("user_query") or "").strip()
        try:
            trace_event(
                "finalize",
                "Calling response model",
                route=route,
                strategy="conversation_followup",
                conversation_id=skip_if_empty(conversation_id),
                last_route=skip_if_empty(last_route),
                has_last_final_answer=bool(last_final_answer),
            )
            resp = create_text_response(
                instructions=(
                    "You are the Orchestration Agent.\n"
                    "This request is a follow-up inside an active conversation.\n"
                    "Use the earlier conversation context and the application's last final answer to respond.\n"
                    "If the reference is unclear, say that briefly instead of inventing details.\n"
                    "Directly satisfy the user's formatting or rewrite request.\n"
                ),
                user_input=(
                    f"FOLLOW_UP_REQUEST:\n{followup_input}\n\n"
                    f"LAST_ROUTE:\n{last_route or '(unknown)'}\n\n"
                    f"LAST_FINAL_ANSWER:\n{last_final_answer or '(none)'}"
                ),
                conversation_id=conversation_id,
                max_output_tokens=1200,
                reasoning_effort="low",
            )
            trace_event(
                "finalize",
                "Response model completed",
                route=route,
                strategy="conversation_followup",
                conversation_id=skip_if_empty(resp.conversation_id or conversation_id),
            )
            return {
                "final_answer": _normalize_final_answer_text(resp.text),
                "conversation_id": resp.conversation_id or conversation_id,
                "errors": errors,
            }
        except Exception as exc:
            trace_event(
                "finalize",
                "Response model failed",
                route=route,
                strategy="conversation_followup",
                error=str(exc),
            )
            state["errors"].append(f"Finalize failed: {exc}")
            return {
                "final_answer": _fallback_finalize_answer(
                    route=route,
                    rag_context=rag_context,
                    web_results=web_results,
                    web_error=web_error,
                    last_final_answer=last_final_answer,
                ),
                "errors": state["errors"],
            }

    if route == "document_question" and not rag_context:
        trace_event("finalize", "Finalize completed", strategy="document_question_error")
        return {
            "final_answer": _latest_error_answer(
                state,
                "No grounded answer was found in the vector store for this document.",
            ),
            "errors": errors,
        }

    if not rag_conf and not web_results and web_error:
        trace_event("finalize", "Finalize completed", strategy="direct_error")
        return {
            "final_answer": (
                "I couldn't find a confident answer in the vector-store retrieval flow, and the live web search also failed. "
                f"Web search error: {web_error}"
            )
        }

    system = (
        "You are the Orchestration Agent.\n"
        "Use the grounded context and the active conversation history to answer the user's question.\n"
        "If the user is asking a follow-up like a summary, rewrite, clarification, or continuation, use the conversation history when it helps.\n"
        "For doc+query flows, prioritize the retrieved document context.\n"
        "For general question flows, prioritize document retrieval context and use live web results when retrieval is weak.\n"
        "Cite retrieved document context as [RAG] and web results as [1], [2], etc.\n"
        "Do not use citation styles like 【...】 or any unsupported special markers.\n"
        "Do not invent facts or sources.\n"
    )

    user = f"""QUESTION:
{state.get("user_query") or "(none)"}

RAG_CONFIDENT: {rag_conf}
RAG_CONTEXT:
{rag_context[:12000] if rag_context else "(none)"}

WEB_RESULTS:
{_build_web_block(web_results)[:12000] if web_results else "(none)"}

Write a concise grounded answer with citations.
"""

    try:
        trace_event(
            "finalize",
            "Calling response model",
            route=skip_if_empty(route),
            rag_confident=rag_conf,
            has_rag_context=bool(rag_context),
            web_result_count=len(web_results),
            conversation_id=skip_if_empty(conversation_id),
        )
        resp = create_text_response(
            instructions=system,
            user_input=user,
            conversation_id=conversation_id,
            max_output_tokens=1200,
            reasoning_effort="low",
        )
        trace_event(
            "finalize",
            "Response model completed",
            conversation_id=skip_if_empty(resp.conversation_id or conversation_id),
        )
        return {
            "final_answer": _normalize_final_answer_text(resp.text),
            "conversation_id": resp.conversation_id or conversation_id,
        }
    except Exception as exc:
        trace_event(
            "finalize",
            "Response model failed",
            error=str(exc),
        )
        state["errors"].append(f"Finalize failed: {exc}")
        return {
            "final_answer": _fallback_finalize_answer(
                route=route,
                rag_context=rag_context,
                web_results=web_results,
                web_error=web_error,
                last_final_answer=last_final_answer,
            ),
            "errors": state["errors"],
        }
