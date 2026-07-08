from __future__ import annotations
from typing import Any, Dict, List, Optional, TypedDict, Literal

Route = Literal["question", "document", "document_question", "compare_documents", "conversation_followup"]


class DocumentStoreResult(TypedDict, total=False):
    status: str
    doc_id: str
    chunks_indexed: int
    message: str
    metadata: Dict[str, Any]


class AgentState(TypedDict, total=False):
    user_input: str
    conversation_id: Optional[str]
    last_final_answer: Optional[str]
    last_route: Optional[Route]
    route_hint: Optional[Route]

    route: Route
    user_query: str
    doc_path: Optional[str]
    doc_file_ref: Optional[Dict[str, Any]]

    left_doc_path: Optional[str]
    right_doc_path: Optional[str]
    left_file_ref: Optional[Dict[str, Any]]
    right_file_ref: Optional[Dict[str, Any]]

    ocr_text: Optional[str]
    left_ocr_text: Optional[str]
    right_ocr_text: Optional[str]

    document_store: Optional[DocumentStoreResult]

    rag_answer: Optional[str]
    rag_confident: Optional[bool]
    rag_citations: Optional[List[Dict[str, Any]]]

    web_results: Optional[List[Dict[str, Any]]]
    web_error: Optional[str]

    comparison_result: Optional[Dict[str, Any]]

    final_answer: Optional[str]
    errors: List[str]
