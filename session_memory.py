from __future__ import annotations

from threading import Lock
from typing import Any


_LOCK = Lock()
_SESSIONS: dict[str, dict[str, Any]] = {}


def load_session_context(conversation_id: str | None) -> dict[str, Any]:
    key = str(conversation_id or "").strip()
    if not key:
        return {}
    with _LOCK:
        saved = _SESSIONS.get(key) or {}
        return dict(saved)


def save_session_context(conversation_id: str | None, state: dict[str, Any]) -> None:
    key = str(conversation_id or "").strip()
    if not key:
        return

    update: dict[str, Any] = {}
    if state.get("doc_path"):
        update["doc_path"] = state.get("doc_path")
    if state.get("document_store"):
        update["document_store"] = state.get("document_store")
    if state.get("ocr_text"):
        update["ocr_text"] = state.get("ocr_text")
    if state.get("final_answer"):
        update["last_final_answer"] = state.get("final_answer")
    if state.get("route"):
        update["last_route"] = state.get("route")

    if not update:
        return

    with _LOCK:
        current = dict(_SESSIONS.get(key) or {})
        current.update(update)
        _SESSIONS[key] = current


def clear_session_context(conversation_id: str | None) -> None:
    key = str(conversation_id or "").strip()
    if not key:
        return
    with _LOCK:
        _SESSIONS.pop(key, None)
