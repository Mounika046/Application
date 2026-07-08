from __future__ import annotations

import re

from oracle_enterprise_ai.responses_client import create_text_response


DOMAIN_LABELS: tuple[str, ...] = (
    "Finance",
    "Legal",
    "Healthcare",
    "Technology",
    "Sports",
    "Entertainment",
    "Other",
)
DISALLOWED_DOMAINS: frozenset[str] = frozenset({"Sports", "Entertainment"})
_DOMAIN_LOOKUP = {label.lower(): label for label in DOMAIN_LABELS}


def classify_domain(text: str) -> str:
    prepared_text = _prepare_text_for_classification(text)
    if not prepared_text:
        raise ValueError("Cannot classify an empty input.")

    response = create_text_response(
        instructions=(
            "You are a strict domain classification service.\n"
            "Classify the domain of the provided input into exactly one label from this set:\n"
            "Finance, Legal, Healthcare, Technology, Sports, Entertainment, Other.\n"
            "Return only the label.\n"
            "If the content mixes domains, choose the dominant one.\n"
            "If the content is unclear, return Other."
        ),
        user_input=f"Input:\n{prepared_text}",
        max_output_tokens=12,
        reasoning_effort="low",
        temperature=0,
    )
    return _normalize_domain_label(response.text)


def is_allowed_domain(domain: str) -> bool:
    normalized = _normalize_domain_label(domain)
    return normalized not in DISALLOWED_DOMAINS


def _prepare_text_for_classification(text: str, *, max_chars: int = 3600) -> str:
    cleaned = re.sub(r"\s+", " ", str(text or "").strip())
    if not cleaned:
        return ""
    if len(cleaned) <= max_chars:
        return cleaned

    head = cleaned[:2400].strip()
    tail = cleaned[-1000:].strip()
    return f"{head}\n...\n{tail}".strip()


def _normalize_domain_label(raw_value: str) -> str:
    cleaned = str(raw_value or "").strip()
    if not cleaned:
        raise ValueError("Domain classifier returned an empty label.")

    exact_match = _DOMAIN_LOOKUP.get(cleaned.lower())
    if exact_match:
        return exact_match

    for label in DOMAIN_LABELS:
        if re.search(rf"\b{re.escape(label)}\b", cleaned, flags=re.IGNORECASE):
            return label

    raise ValueError(f"Unrecognized domain label returned by classifier: {cleaned}")
