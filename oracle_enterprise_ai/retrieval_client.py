from __future__ import annotations

from dataclasses import dataclass
import os
import tempfile
import time
from typing import Any
from config import OCI_EAI_ENABLED, OCI_EAI_TIMEOUT_S, OCI_EAI_VECTOR_STORE_ID
from oracle_enterprise_ai.responses_client import (
    build_enterprise_ai_responses_client,
    build_enterprise_ai_vector_dp_client,
)


@dataclass(frozen=True)
class ManagedIngestResult:
    doc_id: str
    file_id: str
    vector_store_id: str
    file_name: str


def ingest_text_document(*, doc_id: str, text: str, file_name: str) -> ManagedIngestResult:
    if not OCI_EAI_ENABLED:
        raise ValueError("OCI_EAI_ENABLED is false.")
    clean_text = str(text or "").strip()
    if not clean_text:
        raise ValueError("Document text is empty.")
    shared_vector_store_id = OCI_EAI_VECTOR_STORE_ID.strip()
    if not shared_vector_store_id:
        raise ValueError("OCI_EAI_VECTOR_STORE_ID must be set for document storage.")

    files_client = build_enterprise_ai_responses_client()
    dp_client = build_enterprise_ai_vector_dp_client()
    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False, encoding="utf-8") as tmp:
        tmp.write(clean_text)
        temp_path = tmp.name

    try:
        with open(temp_path, "rb") as handle:
            uploaded = files_client.files.create(
                file=handle,
                purpose="assistants",
                timeout=OCI_EAI_TIMEOUT_S,
            )
        attached = dp_client.vector_stores.files.create(
            vector_store_id=shared_vector_store_id,
            file_id=str(uploaded.id),
            timeout=OCI_EAI_TIMEOUT_S,
        )
        _wait_for_vector_store_file_ready(
            dp_client=dp_client,
            vector_store_id=shared_vector_store_id,
            vector_store_file_id=str(getattr(attached, "id", "") or ""),
            timeout_s=max(OCI_EAI_TIMEOUT_S, 120.0),
        )
        return ManagedIngestResult(
            doc_id=doc_id,
            file_id=str(uploaded.id),
            vector_store_id=shared_vector_store_id,
            file_name=file_name,
        )
    finally:
        try:
            os.remove(temp_path)
        except OSError:
            pass


def search_vector_store(*, vector_store_id: str, query: str, top_k: int = 5) -> list[dict[str, Any]]:
    if not OCI_EAI_ENABLED:
        raise ValueError("OCI_EAI_ENABLED is false.")
    if not vector_store_id.strip():
        raise ValueError("vector_store_id is required.")
    if not query.strip():
        return []

    # Keep search on the Oracle vector data-plane client. Using the earlier
    # direct project-backed vector-store path was one of the approaches that
    # failed before we aligned the implementation to the Oracle docs.
    client = build_enterprise_ai_vector_dp_client()
    page = client.vector_stores.search(
        vector_store_id=vector_store_id,
        query=query,
        max_num_results=max(1, min(int(top_k), 20)),
        rewrite_query=False,
        timeout=OCI_EAI_TIMEOUT_S,
    )
    return _normalize_search_page(page)


def search_vector_store_for_file(
    *,
    vector_store_id: str,
    file_id: str,
    query: str,
    top_k: int = 5,
) -> list[dict[str, Any]]:
    if not file_id.strip():
        return search_vector_store(
            vector_store_id=vector_store_id,
            query=query,
            top_k=top_k,
        )

    client = build_enterprise_ai_vector_dp_client()
    page = client.vector_stores.search(
        vector_store_id=vector_store_id,
        query=query,
        max_num_results=max(5, min(max(int(top_k) * 5, 20), 50)),
        rewrite_query=False,
        timeout=OCI_EAI_TIMEOUT_S,
    )
    results = _normalize_search_page(page)
    filtered = [item for item in results if str(item.get("file_id") or "").strip() == file_id.strip()]
    return filtered[: max(1, int(top_k))]


def _normalize_search_page(page: Any) -> list[dict[str, Any]]:
    try:
        payload = page.model_dump()
    except Exception:
        payload = None
    if not isinstance(payload, dict):
        return []

    raw_data = payload.get("data")
    if not isinstance(raw_data, list):
        return []

    results: list[dict[str, Any]] = []
    for item in raw_data:
        if not isinstance(item, dict):
            continue
        content = item.get("content")
        text_parts: list[str] = []
        if isinstance(content, list):
            for part in content:
                if not isinstance(part, dict):
                    continue
                text = part.get("text")
                if isinstance(text, str) and text.strip():
                    text_parts.append(text.strip())
                elif isinstance(text, dict):
                    value = text.get("value")
                    if isinstance(value, str) and value.strip():
                        text_parts.append(value.strip())

        filename = str(item.get("filename") or "").strip()
        file_id = str(item.get("file_id") or "").strip()
        score = item.get("score")
        attributes = item.get("attributes") if isinstance(item.get("attributes"), dict) else {}
        chunk_text = "\n".join(text_parts).strip()
        if not chunk_text:
            continue
        results.append(
            {
                "file_id": file_id,
                "filename": filename,
                "score": float(score) if isinstance(score, (int, float)) else 0.0,
                "text": chunk_text,
                "attributes": attributes,
            }
        )

    return results

def _wait_for_vector_store_file_ready(
    *,
    dp_client: Any,
    vector_store_id: str,
    vector_store_file_id: str,
    timeout_s: float,
    poll_interval_s: float = 3.0,
) -> Any:
    if not vector_store_file_id.strip():
        return None

    deadline = time.time() + max(timeout_s, poll_interval_s)
    last_details: Any = None
    while time.time() < deadline:
        last_details = dp_client.vector_stores.files.retrieve(
            vector_store_file_id.strip(),
            vector_store_id=vector_store_id,
            timeout=OCI_EAI_TIMEOUT_S,
        )
        try:
            dumped = last_details.model_dump()
        except Exception:
            dumped = {}
        status = str((dumped or {}).get("status") or "").strip().lower()
        if status in {"completed", "ready"}:
            return last_details
        if status in {"failed", "cancelled", "expired"}:
            raise RuntimeError(
                f"Vector store file processing failed for file {vector_store_file_id}: {status}"
            )
        time.sleep(poll_interval_s)
    return last_details
