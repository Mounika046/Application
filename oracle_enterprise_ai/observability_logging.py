from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from threading import Lock
from typing import Any

from oci.loggingingestion import LoggingClient
from oci.loggingingestion.models import LogEntry, LogEntryBatch, PutLogsDetails

from config import (
    OCI_LOGGING_AGENT_LOG_OCID,
    OCI_LOGGING_AGENT_SUBJECT,
    OCI_LOGGING_AUTH_MODE,
    OCI_LOGGING_CONFIG_FILE,
    OCI_LOGGING_CONFIG_PROFILE,
    OCI_LOGGING_ENABLED,
    OCI_LOGGING_REQUEST_LOG_OCID,
    OCI_LOGGING_REQUEST_SUBJECT,
    OCI_LOGGING_SOURCE,
    OCI_LOGGING_TIMEOUT_S,
)
from oracle_enterprise_ai.oci_client import build_oci_sdk_client_kwargs

_CLIENT: LoggingClient | None = None
_CLIENT_LOCK = Lock()


def emit_request_log(payload: dict[str, Any]) -> None:
    _emit(
        log_id=OCI_LOGGING_REQUEST_LOG_OCID,
        payload=payload,
        entry_type="request",
        default_subject=OCI_LOGGING_REQUEST_SUBJECT,
    )


def emit_agent_log(payload: dict[str, Any]) -> None:
    _emit(
        log_id=OCI_LOGGING_AGENT_LOG_OCID,
        payload=payload,
        entry_type="agent",
        default_subject=OCI_LOGGING_AGENT_SUBJECT,
    )


def _emit(
    *,
    log_id: str,
    payload: dict[str, Any],
    entry_type: str,
    default_subject: str,
) -> None:
    if not OCI_LOGGING_ENABLED or not str(log_id or "").strip():
        return

    try:
        client = _get_client()
        timestamp = datetime.now(timezone.utc)
        entry = LogEntry(
            id=str(payload.get("event_id") or uuid.uuid4().hex),
            time=timestamp,
            data=json.dumps(payload, ensure_ascii=True, default=str),
        )
        batch = LogEntryBatch(
            entries=[entry],
            source=str(payload.get("service") or OCI_LOGGING_SOURCE),
            type=entry_type,
            subject=str(payload.get("subject") or default_subject),
            defaultlogentrytime=timestamp,
        )
        details = PutLogsDetails(
            specversion="1.0",
            log_entry_batches=[batch],
        )
        client.put_logs(log_id=log_id, put_logs_details=details)
    except Exception as exc:
        print(f"[observability] Failed to emit {entry_type} log: {exc}")


def _get_client() -> LoggingClient:
    global _CLIENT
    if _CLIENT is not None:
        return _CLIENT

    with _CLIENT_LOCK:
        if _CLIENT is None:
            kwargs = build_oci_sdk_client_kwargs(
                mode=OCI_LOGGING_AUTH_MODE,
                profile_name=OCI_LOGGING_CONFIG_PROFILE,
                config_file=OCI_LOGGING_CONFIG_FILE,
            )
            client_kwargs = {}
            signer = kwargs.get("signer")
            if signer is not None:
                client_kwargs["signer"] = signer
            client_kwargs["timeout"] = (OCI_LOGGING_TIMEOUT_S, OCI_LOGGING_TIMEOUT_S)
            _CLIENT = LoggingClient(kwargs["config"], **client_kwargs)
        return _CLIENT
