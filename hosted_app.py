from __future__ import annotations

import base64
import re
import uuid
import time
from typing import Any
from threading import Lock
import traceback

from dotenv import load_dotenv
load_dotenv()

import httpx
from fastapi import FastAPI, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
import uvicorn
from pathlib import Path

from config import (
    HOSTED_APP_HOST,
    HOSTED_APP_PORT,
    OCI_EAI_CONVERSATIONS_ENABLED,
    OCI_EAI_AUTH_MODE,
    OCI_EAI_BASE_URL,
    OCI_EAI_COMPARTMENT_ID,
    OCI_EAI_ENABLED,
    OCI_EAI_PROJECT_OCID,
    OCI_EAI_MODEL,
    OCI_EAI_VECTOR_STORE_ID,
    DOC_INGEST_AGENT_URL,
    DOCUMENT_QUERY_AGENT_URL,
    WEB_SEARCH_AGENT_URL,
    COMPARISON_AGENT_URL,
    SHARED_FILE_INLINE_MAX_BYTES,
    SHARED_FILE_REF_STRATEGY,
)
from flow_trace import (
    clear_request_context,
    get_request_context,
    get_trace_snapshot,
    reset_trace,
    skip_if_empty,
    start_request_context,
    trace_event,
    update_request_context,
)
from agents.agent_runtime import DocumentIngestRuntime
from agents.agent_utils import tool_result_to_obj
from oracle_enterprise_ai.domain_guardrail import classify_domain, is_allowed_domain
from oracle_enterprise_ai.observability_logging import emit_request_log
from oracle_enterprise_ai.responses_client import (
    build_enterprise_ai_responses_client,
    build_enterprise_ai_vector_cp_client,
    create_conversation,
)
from session_memory import load_session_context, save_session_context


class InvokeRequest(BaseModel):
    user_input: str = Field(..., min_length=1)
    conversation_id: str | None = None
    return_state: bool = False


class InvokeResponse(BaseModel):
    final_answer: str
    conversation_id: str | None = None
    route: str | None = None
    state: dict[str, Any] | None = None
    trace: list[dict[str, Any]] | None = None


class SessionResponse(BaseModel):
    conversation_id: str | None = None
    conversation_available: bool = True
    detail: str | None = None


def _make_local_conversation_id() -> str:
    return f"local-{uuid.uuid4().hex[:16]}"


class DebugPingRequest(BaseModel):
    include_downstream_checks: bool = False


class EnterpriseAIDebugRequest(BaseModel):
    include_response_probe: bool = True
    include_conversation_probe: bool = True
    include_vector_store_probe: bool = True


DOCQA_CMD = re.compile(r'^\s*docqa\s+"?(.+?)"?\s*\|\s*(.+)\s*$', re.IGNORECASE)
OCR_CMD = re.compile(r'^\s*ocr\s+"?(.+?)"?\s*$', re.IGNORECASE)
COMPARE_DOCS_CMD = re.compile(
    r'^\s*compare\s+"(?P<left>.+?)"\s+with\s+"(?P<right>.+?)"\s*$',
    re.IGNORECASE,
)
RAW_DOC_AND_QUERY = re.compile(
    r'^\s*"?(?P<path>[A-Za-z]:\\[^"|]+?\.(?:pdf|png|jpg|jpeg|tif|tiff|bmp))"?\s*\|\s*(?P<query>.+)\s*$',
    re.IGNORECASE,
)
RAW_DOC_ONLY = re.compile(
    r'^\s*"?(?P<path>[A-Za-z]:\\[^"]+?\.(?:pdf|png|jpg|jpeg|tif|tiff|bmp))"?\s*$',
    re.IGNORECASE,
)
DOMAIN_BLOCK_MESSAGE = "Input belongs to a restricted domain and cannot be processed."
DOMAIN_CLASSIFICATION_FAILURE_MESSAGE = "Request could not be classified safely and has been blocked."


_GRAPH_LOCK = Lock()


def _get_graph() -> Any:
    graph = getattr(app.state, "graph", None)
    if graph is not None:
        return graph

    with _GRAPH_LOCK:
        graph = getattr(app.state, "graph", None)
        if graph is None:
            from graph_app import compile_app

            graph = compile_app()
            app.state.graph = graph
        return graph


app = FastAPI(title="application-hosted-runtime")
WEB_UI_DIR = Path(__file__).resolve().parent / "web_ui"
if WEB_UI_DIR.exists():
    app.mount("/static", StaticFiles(directory=WEB_UI_DIR), name="static")


@app.middleware("http")
async def debug_exception_middleware(request, call_next):
    try:
        return await call_next(request)
    except Exception as exc:
        return JSONResponse(
            status_code=500,
            content={
                "status": "error",
                "error_type": type(exc).__name__,
                "error": str(exc),
                "path": str(request.url.path),
                "traceback": traceback.format_exc().splitlines()[-20:],
            },
        )


@app.middleware("http")
async def request_logging_middleware(request: Request, call_next):
    request_id = uuid.uuid4().hex
    request.state.request_id = request_id
    start_request_context(
        request_id=request_id,
        method=request.method,
        path=str(request.url.path),
    )
    emit_request_log(
        {
            "event": "request_started",
            "request_id": request_id,
            "conversation_id": None,
            "path": str(request.url.path),
            "method": request.method,
            "route": None,
            "status_code": None,
            "duration_ms": None,
            "success": None,
            "error": None,
            "timestamp": _now_iso(),
            "service": "multi-agent-application",
            "subject": "application/request",
        }
    )

    started_at = time.perf_counter()
    status_code = 500
    error_message = None

    try:
        response = await call_next(request)
        status_code = response.status_code
        return response
    except Exception as exc:
        error_message = str(exc)
        raise
    finally:
        context = get_request_context()
        emit_request_log(
            {
                "event": "request_completed",
                "request_id": request_id,
                "conversation_id": context.get("conversation_id"),
                "path": context.get("path") or str(request.url.path),
                "method": context.get("method") or request.method,
                "route": context.get("route"),
                "status_code": status_code,
                "duration_ms": round((time.perf_counter() - started_at) * 1000, 2),
                "success": status_code < 500 and error_message is None,
                "error": error_message,
                "timestamp": _now_iso(),
                "service": "multi-agent-application",
                "subject": "application/request",
            }
        )
        clear_request_context()


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/ready")
async def ready() -> dict[str, str]:
    return {"status": "ready"}


@app.get("/debug/connectivity")
async def debug_connectivity() -> dict[str, Any]:
    targets = {
        "doc_ingest": DOC_INGEST_AGENT_URL,
        "document_query": DOCUMENT_QUERY_AGENT_URL,
        "web_search": WEB_SEARCH_AGENT_URL,
        "comparison": COMPARISON_AGENT_URL,
    }
    results: dict[str, Any] = {}
    async with httpx.AsyncClient(timeout=5.0, follow_redirects=True) as client:
        for label, base_url in targets.items():
            url = str(base_url or "").strip().rstrip("/")
            if not url:
                results[label] = {"configured": False, "error": "missing URL"}
                continue
            try:
                response = await client.get(f"{url}/health")
                results[label] = {
                    "configured": True,
                    "url": url,
                    "status_code": response.status_code,
                    "body": response.text[:200],
                }
            except Exception as exc:
                results[label] = {
                    "configured": True,
                    "url": url,
                    "error": str(exc),
                }

    return {
        "status": "ok",
        "hosted_app": {
            "host": HOSTED_APP_HOST,
            "port": HOSTED_APP_PORT,
            "shared_file_ref_strategy": SHARED_FILE_REF_STRATEGY,
        },
        "oci_eai": {
            "enabled": OCI_EAI_ENABLED,
            "base_url_set": bool(OCI_EAI_BASE_URL),
            "project_ocid_set": bool(OCI_EAI_PROJECT_OCID),
            "compartment_id_set": bool(OCI_EAI_COMPARTMENT_ID),
            "vector_store_id_set": bool(OCI_EAI_VECTOR_STORE_ID),
            "auth_mode": OCI_EAI_AUTH_MODE,
            "conversations_enabled": OCI_EAI_CONVERSATIONS_ENABLED,
        },
        "downstream": results,
    }


@app.post("/debug/ping")
async def debug_ping(request: DebugPingRequest) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "status": "ok",
        "path": "/debug/ping",
        "hosted_app": {
            "host": HOSTED_APP_HOST,
            "port": HOSTED_APP_PORT,
        },
        "oci_eai": {
            "enabled": OCI_EAI_ENABLED,
            "base_url_set": bool(OCI_EAI_BASE_URL),
            "project_ocid_set": bool(OCI_EAI_PROJECT_OCID),
            "compartment_id_set": bool(OCI_EAI_COMPARTMENT_ID),
            "vector_store_id_set": bool(OCI_EAI_VECTOR_STORE_ID),
            "auth_mode": OCI_EAI_AUTH_MODE,
            "conversations_enabled": OCI_EAI_CONVERSATIONS_ENABLED,
        },
        "downstream_urls": {
            "doc_ingest_set": bool(str(DOC_INGEST_AGENT_URL or "").strip()),
            "document_query_set": bool(str(DOCUMENT_QUERY_AGENT_URL or "").strip()),
            "web_search_set": bool(str(WEB_SEARCH_AGENT_URL or "").strip()),
            "comparison_set": bool(str(COMPARISON_AGENT_URL or "").strip()),
        },
    }
    if not request.include_downstream_checks:
        return payload

    payload["downstream"] = (await debug_connectivity()).get("downstream", {})
    return payload


@app.post("/debug/enterprise-ai")
async def debug_enterprise_ai(request: EnterpriseAIDebugRequest) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "status": "ok",
        "config": {
            "enabled": OCI_EAI_ENABLED,
            "base_url": OCI_EAI_BASE_URL,
            "project_ocid": OCI_EAI_PROJECT_OCID,
            "compartment_id": OCI_EAI_COMPARTMENT_ID,
            "vector_store_id": OCI_EAI_VECTOR_STORE_ID,
            "auth_mode": OCI_EAI_AUTH_MODE,
            "conversations_enabled": OCI_EAI_CONVERSATIONS_ENABLED,
        },
        "checks": {},
    }

    if not OCI_EAI_ENABLED:
        payload["status"] = "disabled"
        return payload

    try:
        responses_client = build_enterprise_ai_responses_client()
        payload["checks"]["client_build"] = {"status": "ok"}
    except Exception as exc:
        payload["status"] = "error"
        payload["checks"]["client_build"] = {"status": "error", "error": str(exc)}
        return payload

    if request.include_response_probe:
        try:
            response = responses_client.responses.create(
                model=OCI_EAI_MODEL,
                input="Reply with READY.",
                max_output_tokens=20,
            )
            payload["checks"]["responses_create"] = {
                "status": "ok",
                "response_id": str(getattr(response, "id", "") or ""),
                "conversation": str(getattr(response, "conversation", "") or ""),
            }
        except Exception as exc:
            payload["status"] = "error"
            payload["checks"]["responses_create"] = {"status": "error", "error": str(exc)}

    if request.include_conversation_probe:
        try:
            conversation = responses_client.conversations.create(
                metadata={"app": "application", "channel": "debug-enterprise-ai"},
            )
            payload["checks"]["conversations_create"] = {
                "status": "ok",
                "conversation_id": str(getattr(conversation, "id", "") or ""),
            }
        except Exception as exc:
            payload["status"] = "error"
            payload["checks"]["conversations_create"] = {"status": "error", "error": str(exc)}

    if request.include_vector_store_probe and str(OCI_EAI_VECTOR_STORE_ID or "").strip():
        try:
            cp_client = build_enterprise_ai_vector_cp_client()
            vector_store = cp_client.vector_stores.retrieve(
                vector_store_id=OCI_EAI_VECTOR_STORE_ID,
            )
            payload["checks"]["vector_store_retrieve"] = {
                "status": "ok",
                "vector_store_id": str(getattr(vector_store, "id", "") or ""),
                "vector_store_status": str(getattr(vector_store, "status", "") or ""),
            }
        except Exception as exc:
            payload["status"] = "error"
            payload["checks"]["vector_store_retrieve"] = {"status": "error", "error": str(exc)}

    return payload


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(WEB_UI_DIR / "index.html")


@app.post("/sessions", response_model=SessionResponse)
async def create_session() -> SessionResponse:
    if not OCI_EAI_CONVERSATIONS_ENABLED:
        return SessionResponse(
            conversation_id=_make_local_conversation_id(),
            conversation_available=False,
            detail="OCI conversations are disabled. Using local session memory.",
        )
    try:
        conversation_id = create_conversation(
            metadata={"app": "application", "channel": "hosted-http"},
        )
    except Exception as exc:
        return SessionResponse(
            conversation_id=_make_local_conversation_id(),
            conversation_available=False,
            detail=f"Could not create conversation: {exc}. Using local session memory instead.",
        )
    return SessionResponse(conversation_id=conversation_id, conversation_available=True)


async def _ensure_conversation_id(conversation_id: str | None) -> str | None:
    current = (conversation_id or "").strip() or None
    if current or not OCI_EAI_CONVERSATIONS_ENABLED:
        return current
    try:
        return create_conversation(
            metadata={"app": "application", "channel": "hosted-http"},
        )
    except Exception:
        return _make_local_conversation_id()


async def _upload_to_file_ref(upload: UploadFile) -> dict[str, Any]:
    if SHARED_FILE_REF_STRATEGY != "inline":
        raise HTTPException(
            status_code=500,
            detail=f"Unsupported shared file strategy: {SHARED_FILE_REF_STRATEGY}",
        )
    content = await upload.read()
    if not content:
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")
    if len(content) > SHARED_FILE_INLINE_MAX_BYTES:
        raise HTTPException(
            status_code=413,
            detail=(
                f"Uploaded file is too large for inline transport. "
                f"Max allowed size is {SHARED_FILE_INLINE_MAX_BYTES} bytes."
            ),
        )
    return {
        "type": "inline",
        "filename": upload.filename or "upload.bin",
        "name": upload.filename or "upload.bin",
        "content_type": upload.content_type or "application/octet-stream",
        "content_base64": base64.b64encode(content).decode("ascii"),
    }


def _coerce_form_bool(value: str | None, *, default: bool = False) -> bool:
    raw = str(value or "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


def _coerce_bool(value: Any, *, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    return _coerce_form_bool(str(value or ""), default=default)


def _inline_file_ref_from_json(value: Any, *, field_name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise HTTPException(status_code=400, detail=f"'{field_name}' is required.")

    filename = str(value.get("filename") or "upload.bin").strip() or "upload.bin"
    content_type = str(value.get("content_type") or "application/octet-stream").strip() or "application/octet-stream"
    content_base64 = str(value.get("content_base64") or "").strip()
    if not content_base64:
        raise HTTPException(status_code=400, detail=f"'{field_name}.content_base64' is required.")

    try:
        content = base64.b64decode(content_base64, validate=True)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"'{field_name}' is not valid base64 content.") from exc

    if not content:
        raise HTTPException(status_code=400, detail=f"'{field_name}' is empty.")
    if len(content) > SHARED_FILE_INLINE_MAX_BYTES:
        raise HTTPException(
            status_code=413,
            detail=(
                f"Uploaded file is too large for inline transport. "
                f"Max allowed size is {SHARED_FILE_INLINE_MAX_BYTES} bytes."
            ),
        )

    return {
        "type": "inline",
        "filename": filename,
        "name": filename,
        "content_type": content_type,
        "content_base64": base64.b64encode(content).decode("ascii"),
    }


async def _extract_upload_request(
    request: Request,
    *,
    expected_files: list[str],
    required_text_fields: list[str] | None = None,
) -> tuple[dict[str, Any], str | None, bool]:
    required_text_fields = required_text_fields or []
    content_type = str(request.headers.get("content-type") or "").lower()

    if "application/json" in content_type:
        payload = await request.json()
        if not isinstance(payload, dict):
            raise HTTPException(status_code=400, detail="JSON body must be an object.")

        files = {
            file_field: _inline_file_ref_from_json(payload.get(file_field), field_name=file_field)
            for file_field in expected_files
        }
        for field_name in required_text_fields:
            value = str(payload.get(field_name) or "").strip()
            if not value:
                raise HTTPException(status_code=400, detail=f"'{field_name}' is required.")
            files[field_name] = value

        return (
            files,
            str(payload.get("conversation_id") or "").strip() or None,
            _coerce_bool(payload.get("return_state"), default=False),
        )

    form = await request.form()
    files: dict[str, Any] = {}
    for file_field in expected_files:
        upload = form.get(file_field)
        if not isinstance(upload, UploadFile):
            raise HTTPException(status_code=400, detail=f"'{file_field}' is required.")
        files[file_field] = await _upload_to_file_ref(upload)

    for field_name in required_text_fields:
        value = str(form.get(field_name) or "").strip()
        if not value:
            raise HTTPException(status_code=400, detail=f"'{field_name}' is required.")
        files[field_name] = value

    return (
        files,
        str(form.get("conversation_id") or "").strip() or None,
        _coerce_form_bool(str(form.get("return_state") or "false"), default=False),
    )


def _file_ref_display_name(file_ref: dict[str, Any] | None) -> str:
    if not isinstance(file_ref, dict):
        return "upload"
    return (
        str(file_ref.get("filename") or file_ref.get("name") or "upload").strip()
        or "upload"
    )


def _extract_inline_text_if_available(file_ref: dict[str, Any] | None) -> str:
    if not isinstance(file_ref, dict):
        return ""

    content_base64 = str(file_ref.get("content_base64") or "").strip()
    if not content_base64:
        return ""

    filename = _file_ref_display_name(file_ref).lower()
    content_type = str(file_ref.get("content_type") or "").strip().lower()
    is_text_like = filename.endswith(".txt") or content_type.startswith("text/")
    if not is_text_like:
        return ""

    try:
        content = base64.b64decode(content_base64, validate=False)
    except Exception:
        return ""

    for encoding in ("utf-8", "utf-16", "latin-1"):
        try:
            return content.decode(encoding).strip()
        except Exception:
            continue
    return ""


async def _extract_source_text_for_guardrail(
    *,
    path: str = "",
    file_ref: dict[str, Any] | None = None,
    input_type: str,
    source_name: str,
) -> str:
    inline_text = _extract_inline_text_if_available(file_ref)
    if inline_text:
        trace_event(
            "domain_guardrail",
            "Input text prepared",
            input_type=input_type,
            source_name=source_name,
            extraction="inline_text",
            char_count=len(inline_text),
        )
        return inline_text

    trace_event(
        "domain_guardrail",
        "OCR extraction started",
        input_type=input_type,
        source_name=source_name,
        path=skip_if_empty(path),
        uses_file_ref=isinstance(file_ref, dict) and bool(file_ref),
    )
    runtime = DocumentIngestRuntime()
    ocr_res = await runtime.ocr_extract(path=path, file_ref=file_ref, dpi=300)
    ocr_obj = tool_result_to_obj(ocr_res)
    text = (ocr_obj.get("text", "") if isinstance(ocr_obj, dict) else str(ocr_obj)).strip()
    if not text:
        raise ValueError(f"No text was available from OCR for {source_name}.")

    trace_event(
        "domain_guardrail",
        "OCR extraction completed",
        input_type=input_type,
        source_name=source_name,
        extraction="ocr",
        char_count=len(text),
    )
    return text


def _enforce_allowed_domain(*, text: str, input_type: str, source_name: str) -> None:
    try:
        domain = classify_domain(text)
    except Exception as exc:
        trace_event(
            "domain_guardrail",
            "Classification failed",
            input_type=input_type,
            source_name=source_name,
            decision="blocked",
            error=str(exc),
        )
        raise HTTPException(status_code=400, detail=DOMAIN_CLASSIFICATION_FAILURE_MESSAGE) from exc

    allowed = is_allowed_domain(domain)
    trace_event(
        "domain_guardrail",
        "Classification completed",
        input_type=input_type,
        source_name=source_name,
        detected_domain=domain,
        decision="allowed" if allowed else "blocked",
    )
    if not allowed:
        raise HTTPException(status_code=400, detail=DOMAIN_BLOCK_MESSAGE)


async def _guardrail_check_document_source(
    *,
    path: str = "",
    file_ref: dict[str, Any] | None = None,
    input_type: str,
    source_name: str,
) -> None:
    text = await _extract_source_text_for_guardrail(
        path=path,
        file_ref=file_ref,
        input_type=input_type,
        source_name=source_name,
    )
    _enforce_allowed_domain(
        text=text,
        input_type=input_type,
        source_name=source_name,
    )


async def _enforce_domain_guardrail(
    *,
    user_input: str,
    extra_state: dict[str, Any] | None = None,
) -> None:
    state = extra_state or {}
    route_hint = str(state.get("route_hint") or "").strip()

    if route_hint == "compare_documents":
        await _guardrail_check_document_source(
            path=str(state.get("left_doc_path") or "").strip(),
            file_ref=state.get("left_file_ref") if isinstance(state.get("left_file_ref"), dict) else None,
            input_type="comparison",
            source_name="left_document",
        )
        await _guardrail_check_document_source(
            path=str(state.get("right_doc_path") or "").strip(),
            file_ref=state.get("right_file_ref") if isinstance(state.get("right_file_ref"), dict) else None,
            input_type="comparison",
            source_name="right_document",
        )
        return

    if route_hint == "document":
        await _guardrail_check_document_source(
            path=str(state.get("doc_path") or "").strip(),
            file_ref=state.get("doc_file_ref") if isinstance(state.get("doc_file_ref"), dict) else None,
            input_type="file",
            source_name="document",
        )
        return

    if route_hint == "document_question":
        clean_question = str(user_input or "").strip()
        if clean_question:
            _enforce_allowed_domain(
                text=clean_question,
                input_type="query",
                source_name="document_question",
            )
        await _guardrail_check_document_source(
            path=str(state.get("doc_path") or "").strip(),
            file_ref=state.get("doc_file_ref") if isinstance(state.get("doc_file_ref"), dict) else None,
            input_type="file",
            source_name="document",
        )
        return

    match = COMPARE_DOCS_CMD.match(user_input)
    if match:
        await _guardrail_check_document_source(
            path=match.group("left").strip(),
            input_type="comparison",
            source_name="left_document",
        )
        await _guardrail_check_document_source(
            path=match.group("right").strip(),
            input_type="comparison",
            source_name="right_document",
        )
        return

    match = DOCQA_CMD.match(user_input) or RAW_DOC_AND_QUERY.match(user_input)
    if match:
        doc_path = match.group(1).strip() if match.re is DOCQA_CMD else match.group("path").strip()
        query_text = match.group(2).strip() if match.re is DOCQA_CMD else match.group("query").strip()
        if query_text:
            _enforce_allowed_domain(
                text=query_text,
                input_type="query",
                source_name="document_question",
            )
        await _guardrail_check_document_source(
            path=doc_path,
            input_type="file",
            source_name="document",
        )
        return

    match = OCR_CMD.match(user_input) or RAW_DOC_ONLY.match(user_input)
    if match:
        doc_path = match.group(1).strip() if match.re is OCR_CMD else match.group("path").strip()
        await _guardrail_check_document_source(
            path=doc_path,
            input_type="file",
            source_name="document",
        )
        return

    clean_input = str(user_input or "").strip()
    if clean_input:
        _enforce_allowed_domain(
            text=clean_input,
            input_type="query",
            source_name="command_query",
        )


async def _invoke_graph(
    *,
    user_input: str,
    conversation_id: str | None = None,
    return_state: bool = False,
    extra_state: dict[str, Any] | None = None,
) -> InvokeResponse:
    reset_trace()
    update_request_context(
        conversation_id=skip_if_empty(conversation_id),
    )
    await _enforce_domain_guardrail(
        user_input=user_input,
        extra_state=extra_state,
    )
    conversation_id = await _ensure_conversation_id(conversation_id)
    update_request_context(
        conversation_id=skip_if_empty(conversation_id),
    )

    try:
        initial_state = {
            "user_input": user_input,
            "conversation_id": conversation_id,
            **load_session_context(conversation_id),
            "errors": [],
        }
        if extra_state:
            initial_state.update(extra_state)
        out = await _get_graph().ainvoke(
            initial_state
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    save_session_context(conversation_id, out)
    update_request_context(
        conversation_id=skip_if_empty(str(out.get("conversation_id") or conversation_id or "") or None),
        route=skip_if_empty(str(out.get("route") or "") or None),
    )

    return InvokeResponse(
        final_answer=str(out.get("final_answer") or ""),
        conversation_id=str(out.get("conversation_id") or conversation_id or "") or None,
        route=str(out.get("route") or "") or None,
        state=out if return_state else None,
        trace=get_trace_snapshot(),
    )


@app.post("/invoke", response_model=InvokeResponse)
async def invoke(request: InvokeRequest) -> InvokeResponse:
    return await _invoke_graph(
        user_input=request.user_input,
        conversation_id=request.conversation_id,
        return_state=request.return_state,
    )


@app.post("/invoke/ocr", response_model=InvokeResponse)
async def invoke_ocr(request: Request) -> InvokeResponse:
    payload, conversation_id, return_state = await _extract_upload_request(
        request,
        expected_files=["file"],
    )
    file_ref = payload["file"]
    return await _invoke_graph(
        user_input=f"OCR uploaded document: {file_ref.get('filename') or 'upload'}",
        conversation_id=conversation_id,
        return_state=return_state,
        extra_state={
            "route_hint": "document",
            "doc_file_ref": file_ref,
            "doc_path": None,
        },
    )


@app.post("/invoke/docqa", response_model=InvokeResponse)
async def invoke_docqa(request: Request) -> InvokeResponse:
    payload, conversation_id, return_state = await _extract_upload_request(
        request,
        expected_files=["file"],
        required_text_fields=["question"],
    )
    clean_question = str(payload["question"]).strip()
    file_ref = payload["file"]
    return await _invoke_graph(
        user_input=clean_question,
        conversation_id=conversation_id,
        return_state=return_state,
        extra_state={
            "route_hint": "document_question",
            "doc_file_ref": file_ref,
            "doc_path": None,
        },
    )


@app.post("/invoke/compare", response_model=InvokeResponse)
async def invoke_compare(request: Request) -> InvokeResponse:
    payload, conversation_id, return_state = await _extract_upload_request(
        request,
        expected_files=["left_file", "right_file"],
    )
    left_file_ref = payload["left_file"]
    right_file_ref = payload["right_file"]
    return await _invoke_graph(
        user_input="Compare the uploaded documents.",
        conversation_id=conversation_id,
        return_state=return_state,
        extra_state={
            "route_hint": "compare_documents",
            "left_file_ref": left_file_ref,
            "right_file_ref": right_file_ref,
            "left_doc_path": None,
            "right_doc_path": None,
        },
    )


def main() -> None:
    uvicorn.run(app, host=HOSTED_APP_HOST, port=HOSTED_APP_PORT)


def _now_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


if __name__ == "__main__":
    main()
