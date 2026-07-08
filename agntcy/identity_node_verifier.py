from __future__ import annotations
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

import httpx

from agntcy.subject_catalog import SubjectIdentity, get_subject_identity
from config import (
    AGNTCY_IDENTITY_ORG_API_KEY,
    AGNTCY_IDENTITY_REST_BASE_URL,
    AGNTCY_IDENTITY_VERIFY_ENABLED,
    AGNTCY_IDENTITY_VERIFY_TIMEOUT_S,
)
from flow_trace import skip_if_empty, trace_event


JsonDict = dict[str, Any]


@dataclass(frozen=True)
class IdentityVerificationResult:
    subject_id: str
    verified: bool
    skipped_reason: str = ""
    error: str = ""
    controller: str = ""
    warning_count: int = 0


def _trace(message: str) -> None:
    trace_event("identity", message)


class IdentityNodeVerifier:
    def __init__(
        self,
        *,
        verify_enabled: bool = AGNTCY_IDENTITY_VERIFY_ENABLED,
        rest_base_url: str = AGNTCY_IDENTITY_REST_BASE_URL,
        org_api_key: str = AGNTCY_IDENTITY_ORG_API_KEY,
        timeout_s: float = AGNTCY_IDENTITY_VERIFY_TIMEOUT_S,
    ) -> None:
        self._verify_enabled = verify_enabled
        self._rest_base_url = rest_base_url.rstrip("/")
        self._org_api_key = org_api_key.strip()
        self._timeout_s = timeout_s

    async def verify_subject(
        self,
        subject_id: str,
        *,
        resolver_metadata_id: str = "",
    ) -> IdentityVerificationResult:
        if not self._verify_enabled:
            trace_event(
                "identity",
                "Verification skipped",
                subject=skip_if_empty(subject_id),
                reason="AGNTCY_IDENTITY_VERIFY_ENABLED is false",
            )
            return IdentityVerificationResult(
                subject_id=subject_id,
                verified=True,
                skipped_reason="identity verification disabled by configuration",
            )

        subject = _resolve_subject_identity(
            subject_id,
            resolver_metadata_id=resolver_metadata_id,
        )
        if not self._org_api_key or not self._rest_base_url:
            return IdentityVerificationResult(
                subject_id=subject_id,
                verified=False,
                error="hosted identity verification backend is not configured",
            )
        if subject is None:
            return IdentityVerificationResult(
                subject_id=subject_id,
                verified=False,
                error="subject identity is not registered locally and no resolver metadata ID was provided",
            )
        if not subject.resolver_metadata_id:
            return IdentityVerificationResult(
                subject_id=subject_id,
                verified=False,
                error="resolver metadata ID is missing for subject",
            )

        trace_event(
            "identity",
            "Verification started",
            subject=subject.subject_id,
            resolver_metadata_id=subject.resolver_metadata_id,
            backend=self._rest_base_url,
        )
        try:
            verification = await self._verify_via_hosted_service(subject)
        except Exception as exc:
            trace_event(
                "identity",
                "Verification failed",
                subject=subject.subject_id,
                error=str(exc),
            )
            return IdentityVerificationResult(
                subject_id=subject.subject_id,
                verified=False,
                error=str(exc),
            )

        warnings = verification.get("warnings")
        errors = verification.get("errors")
        controller = str(verification.get("controller") or "").strip()
        verified = bool(verification.get("status"))
        trace_event(
            "identity",
            "Verification completed",
            subject=subject.subject_id,
            verified=verified,
            controller=skip_if_empty(controller),
            warning_count=len(warnings) if isinstance(warnings, list) else 0,
            error=skip_if_empty(_collect_error_messages(errors)),
        )
        return IdentityVerificationResult(
            subject_id=subject.subject_id,
            verified=verified,
            error=_collect_error_messages(errors),
            controller=controller,
            warning_count=len(warnings) if isinstance(warnings, list) else 0,
        )

    async def _verify_via_hosted_service(self, subject: SubjectIdentity) -> JsonDict:
        headers = {
            "X-Id-Api-Key": self._org_api_key,
            "Content-Type": "application/json",
        }
        badge_id = quote(subject.resolver_metadata_id, safe="")
        async with httpx.AsyncClient(timeout=self._timeout_s, headers=headers) as client:
            badge_response = await client.get(
                f"{self._rest_base_url}/v1alpha1/apps/{badge_id}/badge"
            )
            badge_response.raise_for_status()
            badge_payload = badge_response.json()
            if not isinstance(badge_payload, dict) or not badge_payload:
                raise ValueError("hosted identity service returned an empty badge payload")
            verifiable_credential = badge_payload.get("verifiableCredential")
            if not isinstance(verifiable_credential, dict):
                raise ValueError("hosted identity service badge did not include verifiableCredential")
            proof = verifiable_credential.get("proof")
            if not isinstance(proof, dict):
                raise ValueError("hosted identity service badge did not include proof data")
            proof_value = str(proof.get("proofValue") or "").strip()
            if not proof_value:
                raise ValueError("hosted identity service badge proof value is missing")

            verify_response = await client.post(
                f"{self._rest_base_url}/v1alpha1/badges/verify",
                json={"badge": proof_value},
            )
            verify_response.raise_for_status()
            payload = verify_response.json()
            if not isinstance(payload, dict):
                raise ValueError("hosted identity verify response was not a JSON object")
            return payload


def _collect_error_messages(errors: Any) -> str:
    if not isinstance(errors, list):
        return ""
    messages: list[str] = []
    for error in errors:
        if not isinstance(error, dict):
            continue
        message = str(error.get("message") or error.get("reason") or "").strip()
        if message:
            messages.append(message)
    return "; ".join(messages)


def _resolve_subject_identity(
    subject_id: str,
    *,
    resolver_metadata_id: str = "",
) -> SubjectIdentity | None:
    clean_subject_id = subject_id.strip()
    clean_resolver_metadata_id = resolver_metadata_id.strip()
    if not clean_resolver_metadata_id:
        return get_subject_identity(clean_subject_id) if clean_subject_id else None

    subject = get_subject_identity(clean_subject_id) if clean_subject_id else None
    if subject is not None:
        return SubjectIdentity(
            subject_id=subject.subject_id,
            subject_type=subject.subject_type,
            display_name=subject.display_name,
            agentic_service_id=subject.agentic_service_id,
            resolver_metadata_id=clean_resolver_metadata_id,
            agent_card_url=subject.agent_card_url,
        )

    return SubjectIdentity(
        subject_id=clean_subject_id or clean_resolver_metadata_id,
        subject_type="agent",
        display_name=clean_subject_id or clean_resolver_metadata_id,
        agentic_service_id="",
        resolver_metadata_id=clean_resolver_metadata_id,
        agent_card_url="",
    )
