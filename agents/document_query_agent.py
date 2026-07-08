from __future__ import annotations

import re
from collections import Counter
from typing import Optional

from agents.a2a import JsonAgentExecutor, build_agent_app, build_agent_card, run_agent_app
from config import (
    DOCUMENT_QUERY_AGENT_HOST,
    DOCUMENT_QUERY_AGENT_PORT,
    DOCUMENT_QUERY_AGENT_PUBLIC_URL,
    OCI_EAI_VECTOR_STORE_ID,
)
from flow_trace import trace_event
from oracle_enterprise_ai.responses_client import create_text_response
from oracle_enterprise_ai.retrieval_client import search_vector_store, search_vector_store_for_file


async def query_documents(
    question: str,
    doc_id: Optional[str] = None,
    vector_store_id: Optional[str] = None,
    file_id: Optional[str] = None,
    ocr_text: Optional[str] = None,
    top_k: int = 5,
) -> dict:
    if not question.strip():
        return {"rag_confident": False, "rag_answer": "", "rag_citations": []}

    direct_answer = _answer_structured_document_question(
        question=question,
        ocr_text=ocr_text or "",
    )
    if direct_answer:
        return {
            "rag_confident": True,
            "rag_answer": direct_answer,
            "rag_citations": [],
            "search_scope": "document_text",
        }

    local_answer = _answer_unstructured_document_question(
        question=question,
        ocr_text=ocr_text or "",
    )
    if local_answer:
        return {
            "rag_confident": True,
            "rag_answer": local_answer,
            "rag_citations": [],
            "search_scope": "document_text",
        }

    active_vector_store_id = str(vector_store_id or "").strip() or OCI_EAI_VECTOR_STORE_ID.strip()
    if not active_vector_store_id:
        return {"rag_confident": False, "rag_answer": "", "rag_citations": []}

    scoped_file_id = str(file_id or "").strip()
    search_scope = "document" if scoped_file_id else "knowledge_base"
    if scoped_file_id:
        hits = search_vector_store_for_file(
            vector_store_id=active_vector_store_id,
            file_id=scoped_file_id,
            query=question,
            top_k=top_k,
        )
    else:
        hits = search_vector_store(
            vector_store_id=active_vector_store_id,
            query=question,
            top_k=top_k,
        )

    context_blocks: list[str] = []
    citations = []
    for idx, hit in enumerate(hits, start=1):
        chunk_text = str(hit.get("text") or "").strip()
        if not chunk_text:
            continue
        hit_file_id = str(hit.get("file_id") or "").strip()
        filename = str(hit.get("filename") or hit_file_id or active_vector_store_id).strip()
        context_blocks.append(f"[RAG:{idx}] {chunk_text}")
        citations.append(
            {
                "id": hit_file_id or scoped_file_id or active_vector_store_id,
                "score": hit.get("score", 0.0),
                "doc_id": doc_id,
                "source": filename,
                "chunk": idx,
                "scope": search_scope,
            }
        )
    context = "\n\n".join(context_blocks).strip()
    if not context:
        return {"rag_confident": False, "rag_answer": "", "rag_citations": []}

    trace_event(
        "document-query-agent",
        "RAG context prepared",
        search_scope=search_scope,
        vector_store_id=active_vector_store_id,
        file_id=scoped_file_id or None,
        hit_count=len(citations),
    )

    response = create_text_response(
        instructions=(
            "Answer only from the provided retrieved document chunks. "
            "If the answer is not supported by the chunks, say that clearly. "
            "Be concise and grounded. Cite the chunk ids like [RAG:1], [RAG:2]."
        ),
        user_input=(
            f"QUESTION:\n{question}\n\n"
            f"RETRIEVED_DOCUMENT_CHUNKS:\n{context}"
        ),
        max_output_tokens=1200,
        reasoning_effort="low",
    )
    return {
        "rag_confident": bool((response.text or "").strip()),
        "rag_answer": response.text,
        "rag_citations": citations,
        "search_scope": search_scope,
    }


async def _handle(payload: dict) -> dict:
    question = str(payload.get("question") or "").strip()
    doc_id = payload.get("doc_id")
    doc_id = None if doc_id in ("", None) else str(doc_id)
    vector_store_id = payload.get("vector_store_id")
    vector_store_id = None if vector_store_id in ("", None) else str(vector_store_id)
    file_id = payload.get("file_id")
    file_id = None if file_id in ("", None) else str(file_id)
    ocr_text = payload.get("ocr_text")
    ocr_text = None if ocr_text in ("", None) else str(ocr_text)
    top_k = int(payload.get("top_k", 5))
    return await query_documents(
        question=question,
        doc_id=doc_id,
        vector_store_id=vector_store_id,
        file_id=file_id,
        ocr_text=ocr_text,
        top_k=top_k,
    )


def _answer_structured_document_question(*, question: str, ocr_text: str) -> str:
    text = str(ocr_text or "").strip()
    if not text:
        return ""

    normalized_question = str(question or "").strip().lower()
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    paragraphs = [part.strip() for part in re.split(r"\n\s*\n+", text) if part.strip()]

    if "first and last line" in normalized_question or "first and last lines" in normalized_question:
        if lines:
            return f'First line: "{lines[0]}"\nLast line: "{lines[-1]}"'

    if "first line" in normalized_question and lines:
        return f'First line: "{lines[0]}"'

    if "last line" in normalized_question and lines:
        return f'Last line: "{lines[-1]}"'

    ordinal_match = re.search(
        r"\b(?P<ordinal>first|second|third|fourth|fifth|\d+(?:st|nd|rd|th)?)\s+(?P<unit>paragraph|line)\b",
        normalized_question,
        re.IGNORECASE,
    )
    if not ordinal_match:
        return ""

    index = _ordinal_to_index(ordinal_match.group("ordinal"))
    if index is None:
        return ""

    unit = ordinal_match.group("unit").lower()
    if unit == "paragraph":
        if 0 <= index < len(paragraphs):
            return f'{ordinal_match.group("ordinal").capitalize()} paragraph: "{paragraphs[index]}"'
        return "That paragraph was not found in the stored document text."

    if unit == "line":
        if 0 <= index < len(lines):
            return f'{ordinal_match.group("ordinal").capitalize()} line: "{lines[index]}"'
        return "That line was not found in the stored document text."

    return ""


def _ordinal_to_index(value: str) -> int | None:
    cleaned = str(value or "").strip().lower()
    word_map = {
        "first": 0,
        "second": 1,
        "third": 2,
        "fourth": 3,
        "fifth": 4,
    }
    if cleaned in word_map:
        return word_map[cleaned]
    number_match = re.match(r"(\d+)(?:st|nd|rd|th)?$", cleaned)
    if not number_match:
        return None
    number = int(number_match.group(1))
    return number - 1 if number > 0 else None


def _answer_unstructured_document_question(*, question: str, ocr_text: str) -> str:
    text = str(ocr_text or "").strip()
    if not text:
        return ""

    query = str(question or "").strip().lower()
    if not query:
        return ""

    stop_words = {
        "a", "an", "and", "are", "as", "at", "be", "by", "do", "does", "for", "from",
        "in", "is", "it", "of", "on", "or", "the", "to", "was", "what", "when", "where",
        "which", "who", "why", "how", "with", "this", "that", "these", "those", "document",
        "file", "pdf", "first", "last", "line", "lines", "paragraph", "paragraphs",
    }
    query_terms = [
        token for token in re.findall(r"[a-z0-9]+", query)
        if len(token) > 2 and token not in stop_words
    ]
    if not query_terms:
        return ""

    paragraphs = [part.strip() for part in re.split(r"\n\s*\n+", text) if part.strip()]
    candidates = paragraphs if paragraphs else [line.strip() for line in text.splitlines() if line.strip()]
    if not candidates:
        return ""

    scored: list[tuple[int, int, str]] = []
    for index, candidate in enumerate(candidates):
        candidate_terms = re.findall(r"[a-z0-9]+", candidate.lower())
        if not candidate_terms:
            continue
        counts = Counter(candidate_terms)
        score = sum(counts.get(term, 0) for term in query_terms)
        if score <= 0:
            continue
        scored.append((score, -len(candidate), candidate))

    if not scored:
        return ""

    scored.sort(reverse=True)
    top_chunks = [item[2] for item in scored[:2]]
    lines = []
    for index, chunk in enumerate(top_chunks, start=1):
        snippet = re.sub(r"\s+", " ", chunk).strip()
        if len(snippet) > 320:
            snippet = f"{snippet[:317].rstrip()}..."
        lines.append(f"{snippet} [RAG:{index}]")
    return "\n".join(lines).strip()


app = build_agent_app(
    agent_card=build_agent_card(
        name="kb-query-agent",
        description="Queries vector-store content and returns grounded RAG context.",
        base_url=DOCUMENT_QUERY_AGENT_PUBLIC_URL,
        skill_id="document-query",
        skill_name="Document Query",
        skill_description="Retrieve document context and citations from the vector store for a user question.",
        examples=['{"question":"What is the capital of France?","doc_id":null,"top_k":5}'],
        tags=["rag", "retrieval", "vector-store", "query"],
    ),
    executor=JsonAgentExecutor(agent_name="kb-query-agent", handler=_handle),
)


def main() -> None:
    trace_event(
        "document-query-agent",
        "A2A server listening",
        host=DOCUMENT_QUERY_AGENT_HOST,
        port=DOCUMENT_QUERY_AGENT_PORT,
    )
    run_agent_app(app, host=DOCUMENT_QUERY_AGENT_HOST, port=DOCUMENT_QUERY_AGENT_PORT)


if __name__ == "__main__":
    main()
