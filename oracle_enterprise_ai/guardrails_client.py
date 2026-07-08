from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

from config import (
    OCI_EAI_AUTH_MODE,
    OCI_EAI_COMPARTMENT_ID,
    OCI_EAI_CONFIG_FILE,
    OCI_EAI_CONFIG_PROFILE,
    OCI_EAI_TIMEOUT_S,
    REGION,
)
from oracle_enterprise_ai.oci_client import build_oci_auth

# Hardcoded guardrail defaults so the hosted app does not need extra env vars.
# OCI docs currently show content moderation categories OVERALL and BLOCKLIST,
# and common PII labels such as EMAIL and TELEPHONE_NUMBER.
GUARDRAILS_ENABLED = True
GUARDRAILS_INPUT_ENABLED = True
GUARDRAILS_OUTPUT_ENABLED = True
GUARDRAILS_LANGUAGE_CODE = "en"
GUARDRAILS_CONTENT_CATEGORIES = ("OVERALL", "BLOCKLIST")
GUARDRAILS_PII_TYPES = ("EMAIL", "TELEPHONE_NUMBER")
GUARDRAILS_PROMPT_INJECTION_ENABLED = True
GUARDRAILS_PROMPT_INJECTION_THRESHOLD = 0.8
GUARDRAILS_CONTENT_THRESHOLD = 0.8
GUARDRAILS_BLOCK_INPUT_PII = True


@dataclass(frozen=True)
class GuardrailAssessment:
    allowed: bool
    reasons: list[str]
    prompt_injection_score: float | None
    pii_findings: list[dict[str, Any]]
    content_categories: list[dict[str, Any]]
    raw: dict[str, Any]


def assert_guardrails_pass(*, text: str, stage: str) -> None:
    assessment = evaluate_text_guardrails(text=text, stage=stage)
    if assessment.allowed:
        return
    raise ValueError("; ".join(assessment.reasons) or f"Guardrails blocked {stage} text.")


def evaluate_text_guardrails(*, text: str, stage: str) -> GuardrailAssessment:
    clean_text = str(text or "").strip()
    if not clean_text:
        return GuardrailAssessment(True, [], None, [], [], {})
    if not GUARDRAILS_ENABLED:
        return GuardrailAssessment(True, [], None, [], [], {})
    if stage == "input" and not GUARDRAILS_INPUT_ENABLED:
        return GuardrailAssessment(True, [], None, [], [], {})
    if stage == "output" and not GUARDRAILS_OUTPUT_ENABLED:
        return GuardrailAssessment(True, [], None, [], [], {})

    guardrail_configs = _build_guardrail_configs()
    if not guardrail_configs:
        return GuardrailAssessment(True, [], None, [], [], {})

    payload = {
        "input": {
            "type": "TEXT",
            "content": clean_text,
            "languageCode": GUARDRAILS_LANGUAGE_CODE,
        },
        "guardrailConfigs": guardrail_configs,
        "compartmentId": OCI_EAI_COMPARTMENT_ID,
    }

    with httpx.Client(
        auth=build_oci_auth(
            mode=OCI_EAI_AUTH_MODE,
            profile_name=OCI_EAI_CONFIG_PROFILE,
            config_file=OCI_EAI_CONFIG_FILE,
        ),
        timeout=OCI_EAI_TIMEOUT_S,
    ) as client:
        response = client.post(
            f"https://inference.generativeai.{REGION}.oci.oraclecloud.com/20231130/actions/applyGuardrails",
            json=payload,
            headers={"opc-compartment-id": OCI_EAI_COMPARTMENT_ID},
        )
        response.raise_for_status()
        raw = response.json()

    results = raw.get("results") if isinstance(raw, dict) else None
    if not isinstance(results, dict):
        return GuardrailAssessment(True, [], None, [], [], raw if isinstance(raw, dict) else {})

    reasons: list[str] = []
    prompt_score = _to_float(((results.get("promptInjection") or {}).get("score")))
    if prompt_score is not None and prompt_score >= GUARDRAILS_PROMPT_INJECTION_THRESHOLD:
        reasons.append(f"prompt injection score {prompt_score:.2f}")

    pii_findings = results.get("personallyIdentifiableInformation")
    pii_findings = pii_findings if isinstance(pii_findings, list) else []
    if pii_findings and (stage == "output" or GUARDRAILS_BLOCK_INPUT_PII):
        reasons.append(f"PII findings {len(pii_findings)}")

    categories = ((results.get("contentModeration") or {}).get("categories"))
    categories = categories if isinstance(categories, list) else []
    blocked_categories = [
        item
        for item in categories
        if isinstance(item, dict) and _to_float(item.get("score"), default=0.0) >= GUARDRAILS_CONTENT_THRESHOLD
    ]
    if blocked_categories:
        reasons.append(
            "content moderation "
            + ", ".join(str(item.get("name") or "unknown") for item in blocked_categories)
        )

    return GuardrailAssessment(
        allowed=not reasons,
        reasons=reasons,
        prompt_injection_score=prompt_score,
        pii_findings=[item for item in pii_findings if isinstance(item, dict)],
        content_categories=[item for item in categories if isinstance(item, dict)],
        raw=raw if isinstance(raw, dict) else {},
    )


def _build_guardrail_configs() -> dict[str, Any]:
    configs: dict[str, Any] = {}
    if GUARDRAILS_CONTENT_CATEGORIES:
        configs["contentModerationConfig"] = {
            "categories": list(GUARDRAILS_CONTENT_CATEGORIES),
        }
    if GUARDRAILS_PII_TYPES:
        configs["personallyIdentifiableInformationConfig"] = {
            "types": list(GUARDRAILS_PII_TYPES),
        }
    if GUARDRAILS_PROMPT_INJECTION_ENABLED:
        configs["promptInjectionConfig"] = {}
    return configs


def _to_float(value: Any, default: float | None = None) -> float | None:
    try:
        return float(value)
    except Exception:
        return default
