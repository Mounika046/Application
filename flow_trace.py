from __future__ import annotations

import json
from contextvars import ContextVar
from typing import Any

_TRACE_BUFFER: ContextVar[list[dict[str, Any]] | None] = ContextVar("_TRACE_BUFFER", default=None)
_REQUEST_CONTEXT: ContextVar[dict[str, Any] | None] = ContextVar("_REQUEST_CONTEXT", default=None)


def trace_event(stage: str, title: str, **fields: Any) -> None:
    prefix = f"[flow:{stage}]"
    lines = ["", f"{prefix} {title}"]
    structured_fields: dict[str, Any] = {}
    for key, value in fields.items():
        if value is _SKIP:
            continue
        lines.append(f"{prefix}   {key:<16}: {_format_value(value)}")
        structured_fields[key] = _normalize_structured_value(value)
    _append_trace_entry({"kind": "event", "stage": stage, "title": title, "fields": structured_fields})
    print("\n".join(lines))
    _emit_agent_log(stage=stage, title=title, fields=structured_fields)


def trace_banner(stage: str, title: str, **fields: Any) -> None:
    prefix = f"[flow:{stage}]"
    divider = "=" * 72
    lines = ["", f"{prefix} {divider}", f"{prefix} {title}", f"{prefix} {divider}"]
    structured_fields: dict[str, Any] = {}
    for key, value in fields.items():
        if value is _SKIP:
            continue
        lines.append(f"{prefix}   {key:<16}: {_format_value(value)}")
        structured_fields[key] = _normalize_structured_value(value)
    _append_trace_entry({"kind": "banner", "stage": stage, "title": title, "fields": structured_fields})
    print("\n".join(lines))
    _emit_agent_log(stage=stage, title=title, fields=structured_fields, kind="banner")


def reset_trace() -> None:
    _TRACE_BUFFER.set([])


def get_trace_snapshot() -> list[dict[str, Any]]:
    current = _TRACE_BUFFER.get()
    if not current:
        return []
    return [dict(item) for item in current]


def start_request_context(**fields: Any) -> None:
    _REQUEST_CONTEXT.set(_normalize_context(fields))


def update_request_context(**fields: Any) -> None:
    current = _REQUEST_CONTEXT.get() or {}
    updated = dict(current)
    for key, value in fields.items():
        if value is _SKIP:
            continue
        if value is None:
            updated.pop(key, None)
            continue
        if isinstance(value, str) and not value.strip():
            updated.pop(key, None)
            continue
        updated[str(key)] = _normalize_structured_value(value)
    _REQUEST_CONTEXT.set(updated)


def get_request_context() -> dict[str, Any]:
    return dict(_REQUEST_CONTEXT.get() or {})


def clear_request_context() -> None:
    _REQUEST_CONTEXT.set(None)


def skip_if_empty(value: Any) -> Any:
    if value is None:
        return _SKIP
    if isinstance(value, str) and not value.strip():
        return _SKIP
    if isinstance(value, (list, tuple, set, dict)) and not value:
        return _SKIP
    return value


class _SkipValue:
    pass


_SKIP = _SkipValue()


def _append_trace_entry(entry: dict[str, Any]) -> None:
    current = _TRACE_BUFFER.get()
    if current is None:
        return
    current.append(entry)


def _emit_agent_log(
    *,
    stage: str,
    title: str,
    fields: dict[str, Any],
    kind: str = "event",
) -> None:
    try:
        from oracle_enterprise_ai.observability_logging import emit_agent_log

        context = get_request_context()
        payload = {
            "kind": kind,
            "stage": stage,
            "title": title,
            "fields": fields,
            "request_id": context.get("request_id"),
            "conversation_id": context.get("conversation_id"),
            "route": context.get("route"),
            "path": context.get("path"),
            "method": context.get("method"),
            "agent_name": context.get("agent_name"),
            "service": "multi-agent-application",
            "subject": "application/agent",
            "timestamp": datetime_now_iso(),
        }
        emit_agent_log(payload)
    except Exception:
        return


def _normalize_context(fields: dict[str, Any]) -> dict[str, Any]:
    normalized: dict[str, Any] = {}
    for key, value in fields.items():
        if value is _SKIP or value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        normalized[str(key)] = _normalize_structured_value(value)
    return normalized


def datetime_now_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


def _format_value(value: Any) -> str:
    if value is None:
        return "(none)"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        text = value.strip().replace("\r", " ").replace("\n", " ")
        return _truncate(text or "(empty)")
    if isinstance(value, dict):
        return _truncate(json.dumps(value, ensure_ascii=True, sort_keys=True))
    if isinstance(value, (list, tuple, set)):
        return _truncate(", ".join(_format_simple(item) for item in value) or "(empty)")
    return _truncate(str(value))


def _format_simple(value: Any) -> str:
    if isinstance(value, str):
        return value.strip().replace("\r", " ").replace("\n", " ")
    if isinstance(value, (dict, list, tuple, set)):
        return json.dumps(value, ensure_ascii=True, sort_keys=True)
    return str(value)


def _truncate(text: str, limit: int = 180) -> str:
    if len(text) <= limit:
        return text
    return f"{text[: limit - 3]}..."


def _normalize_structured_value(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, dict):
        return {str(key): _normalize_structured_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_normalize_structured_value(item) for item in value]
    return str(value)
