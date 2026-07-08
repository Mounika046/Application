from __future__ import annotations

import os
from typing import Any

from agents.a2a import JsonAgentExecutor, build_agent_app, build_agent_card, run_agent_app
from agents.agent_runtime import DocumentIngestRuntime
from agents.agent_utils import tool_result_to_obj
from config import DOC_INGEST_AGENT_HOST, DOC_INGEST_AGENT_PORT, DOC_INGEST_AGENT_PUBLIC_URL, OCI_EAI_ENABLED
from file_input import file_input_name, make_doc_id_for_input, summarize_file_input
from flow_trace import trace_event
from oracle_enterprise_ai.retrieval_client import ingest_text_document


async def ingest_document(
    *,
    path: str = "",
    file_ref: dict[str, Any] | None = None,
    dpi: int = 300,
    runtime: Any | None = None,
) -> dict:
    active_runtime = runtime or DocumentIngestRuntime()
    if not str(path or "").strip() and not isinstance(file_ref, dict):
        raise ValueError("Either 'path' or 'file_ref' is required.")

    ocr_res = await active_runtime.ocr_extract(path=path, file_ref=file_ref, dpi=dpi)
    ocr_obj = tool_result_to_obj(ocr_res)
    ocr_text = ocr_obj.get("text", "") if isinstance(ocr_obj, dict) else str(ocr_obj)
    pages = ocr_obj.get("pages") if isinstance(ocr_obj, dict) else None

    doc_id = make_doc_id_for_input(path=path, file_ref=file_ref)
    display_name = file_input_name(path=path, file_ref=file_ref, default_name=f"{doc_id}.txt")
    if not OCI_EAI_ENABLED:
        raise ValueError("OCI_EAI_ENABLED must be true for document ingest.")

    try:
        managed = ingest_text_document(
            doc_id=doc_id,
            text=ocr_text,
            file_name=display_name,
        )
        metadata = {
            "file_id": managed.file_id,
            "vector_store_id": managed.vector_store_id,
            "backend": "oracle_enterprise_ai",
            "source_name": display_name,
        }
        status = "ok"
        message = "Ingested into Oracle Enterprise AI vector store."
    except Exception as exc:
        trace_event(
            "document-ingest-agent",
            "Vector-store ingest fallback",
            doc_id=doc_id,
            source_name=display_name,
            error=str(exc),
        )
        metadata = {
            "file_id": "",
            "vector_store_id": "",
            "backend": "local_memory_fallback",
            "source_name": display_name,
            "ingest_error": str(exc),
        }
        status = "local_only"
        message = "OCR completed. Stored in local runtime memory only."
    if str(path or "").strip():
        metadata["source_path"] = os.path.abspath(path)
    else:
        metadata["source_ref"] = summarize_file_input(file_ref=file_ref)

    document_store = {
        "status": status,
        "doc_id": doc_id,
        "message": message,
        "metadata": metadata,
    }

    return {
        "doc_id": doc_id,
        "ocr_text": ocr_text,
        "ocr_pages": pages,
        "document_store": document_store,
    }


async def _handle(payload: dict) -> dict:
    path = str(payload.get("path") or "").strip()
    file_ref = payload.get("file_ref")
    file_ref = file_ref if isinstance(file_ref, dict) and file_ref else None
    dpi = int(payload.get("dpi", 300))
    return await ingest_document(path=path, file_ref=file_ref, dpi=dpi)


app = build_agent_app(
    agent_card=build_agent_card(
        name="document-ingest-agent",
        description="Extracts OCR text and ingests documents into the vector store.",
        base_url=DOC_INGEST_AGENT_PUBLIC_URL,
        skill_id="document-ingest",
        skill_name="Document Ingest",
        skill_description="OCR a document and ingest it into the vector store as a document-scoped asset.",
        examples=[
            '{"path":"C:\\\\docs\\\\file.pdf","dpi":300}',
            '{"file_ref":{"type":"inline","filename":"file.pdf","content_base64":"<base64>"},"dpi":300}',
        ],
        tags=["document", "ocr", "ingest", "vector-store"],
    ),
    executor=JsonAgentExecutor(agent_name="document-ingest-agent", handler=_handle),
)


def main() -> None:
    trace_event(
        "document-ingest-agent",
        "A2A server listening",
        host=DOC_INGEST_AGENT_HOST,
        port=DOC_INGEST_AGENT_PORT,
    )
    run_agent_app(app, host=DOC_INGEST_AGENT_HOST, port=DOC_INGEST_AGENT_PORT)


if __name__ == "__main__":
    main()
