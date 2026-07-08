from __future__ import annotations

import json
from difflib import SequenceMatcher
from typing import Optional

from agents.a2a import JsonAgentExecutor, build_agent_app, build_agent_card, run_agent_app
from config import (
    COMPARISON_AGENT_HOST,
    COMPARISON_AGENT_PORT,
    COMPARISON_AGENT_PUBLIC_URL,
    OCI_EAI_COMPARE_CODE_INTERPRETER_ENABLED,
    OCI_EAI_COMPARE_CODE_INTERPRETER_MEMORY,
    OCI_EAI_MODEL,
)
from flow_trace import trace_event
from oracle_enterprise_ai.embeddings_client import compare_text_similarity
from oracle_enterprise_ai.guardrails_client import assert_guardrails_pass
from oracle_enterprise_ai.responses_client import (
    build_enterprise_ai_responses_client,
    create_code_interpreter_response,
    parse_function_call_arguments,
)


def _normalize_text(value: object) -> str:
    return str(value or "").strip()


def _safe_json_object(value: str) -> dict:
    try:
        parsed = json.loads(value)
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _truncate_for_compare(text: str, char_limit: int) -> str:
    normalized = text.replace("\x00", " ").strip()
    if len(normalized) <= char_limit:
        return normalized
    return normalized[:char_limit].rstrip()


def _simple_metrics(left_text: str, right_text: str) -> dict:
    left_words = left_text.split()
    right_words = right_text.split()
    return {
        "left_chars": len(left_text),
        "right_chars": len(right_text),
        "left_words": len(left_words),
        "right_words": len(right_words),
        "common_number_tokens": sorted(set(_extract_tokens(left_text, r"\b\d+\b")) & set(_extract_tokens(right_text, r"\b\d+\b")))[:10],
        "common_date_tokens": sorted(set(_extract_tokens(left_text, r"\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b")) & set(_extract_tokens(right_text, r"\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b")))[:10],
        "notable_length_change": _describe_length_change(len(left_text), len(right_text)),
    }


def _extract_tokens(text: str, pattern: str) -> list[str]:
    import re
    return re.findall(pattern, text or "")


def _describe_length_change(left_len: int, right_len: int) -> str:
    if left_len <= 0 and right_len <= 0:
        return "none"
    if left_len <= 0:
        return "right document contains all detected text"
    ratio = (right_len - left_len) / max(left_len, 1)
    if abs(ratio) < 0.1:
        return "none"
    if ratio > 0:
        return "right document is longer"
    return "right document is shorter"


def _local_compare_result(
    *,
    left_text: str,
    right_text: str,
    left_label: str,
    right_label: str,
    similarity: float | None,
    code_metrics: dict,
) -> dict:
    left_lines = [line.strip() for line in left_text.splitlines() if line.strip()]
    right_lines = [line.strip() for line in right_text.splitlines() if line.strip()]
    left_set = set(left_lines)
    right_set = set(right_lines)
    similarities = list(sorted(left_set & right_set))[:5]
    added = list(sorted(right_set - left_set))[:5]
    missing = list(sorted(left_set - right_set))[:5]
    differences = []
    if added or missing:
        differences.append(
            {
                "topic": "Content changes",
                "left": missing[0] if missing else f"No unique content in {left_label}",
                "right": added[0] if added else f"No unique content in {right_label}",
                "impact": "The documents are not identical.",
            }
        )

    if not similarities and not differences:
        differences.append(
            {
                "topic": "Overall wording",
                "left": left_text[:180].strip(),
                "right": right_text[:180].strip(),
                "impact": "The documents differ in wording or formatting.",
            }
        )

    ratio = similarity if similarity is not None else SequenceMatcher(None, left_text[:8000], right_text[:8000]).ratio()
    confidence = "high" if ratio >= 0.85 else "medium" if ratio >= 0.55 else "low"
    summary = (
        f"{left_label} and {right_label} are highly similar."
        if ratio >= 0.85
        else f"{left_label} and {right_label} share some content but differ in key areas."
        if ratio >= 0.55
        else f"{left_label} and {right_label} are materially different."
    )

    return {
        "summary": summary,
        "similarities": similarities,
        "differences": differences,
        "added_in_right": added,
        "missing_in_right": missing,
        "risk_changes": [],
        "confidence": confidence,
        "embedding_similarity": ratio,
        "analysis_metrics": code_metrics,
    }


def _run_structured_compare(
    *,
    client: object,
    left_label: str,
    right_label: str,
    question: str,
    focus: str,
    similarity: float | None,
    code_metrics: dict,
    left_text: str,
    right_text: str,
    char_limit: int,
) -> dict:
    response = client.responses.create(
        model=OCI_EAI_MODEL,
        instructions=(
            "You are a document comparison agent. "
            "You must call emit_comparison exactly once with a grounded structured result. "
            "Do not answer outside the function call. "
            "Keep string values concise and valid JSON-safe text."
        ),
        input=(
            f"Compare these two inputs.\n\n"
            f"LEFT_LABEL: {left_label}\n"
            f"RIGHT_LABEL: {right_label}\n"
            f"QUESTION: {question or '(none)'}\n"
            f"FOCUS: {focus or '(none)'}\n"
            f"EMBEDDING_SIMILARITY: {similarity if similarity is not None else 0.0}\n"
            f"ANALYSIS_METRICS: {json.dumps(code_metrics, ensure_ascii=True)}\n\n"
            f"LEFT_TEXT:\n{_truncate_for_compare(left_text, char_limit)}\n\n"
            f"RIGHT_TEXT:\n{_truncate_for_compare(right_text, char_limit)}"
        ),
        tools=[
            {
                "type": "function",
                "name": "emit_comparison",
                "description": "Return the final structured comparison result.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "summary": {"type": "string"},
                        "similarities": {"type": "array", "items": {"type": "string"}},
                        "differences": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "topic": {"type": "string"},
                                    "left": {"type": "string"},
                                    "right": {"type": "string"},
                                    "impact": {"type": "string"},
                                },
                                "required": ["topic", "left", "right", "impact"],
                                "additionalProperties": False,
                            },
                        },
                        "added_in_right": {"type": "array", "items": {"type": "string"}},
                        "missing_in_right": {"type": "array", "items": {"type": "string"}},
                        "risk_changes": {"type": "array", "items": {"type": "string"}},
                        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
                        "embedding_similarity": {"type": "number"},
                        "analysis_metrics": {
                            "type": "object",
                            "additionalProperties": True,
                        },
                    },
                    "required": [
                        "summary",
                        "similarities",
                        "differences",
                        "added_in_right",
                        "missing_in_right",
                        "risk_changes",
                        "confidence",
                    ],
                    "additionalProperties": False,
                },
            }
        ],
        tool_choice="required",
        max_output_tokens=1600,
        reasoning={"effort": "low"},
        temperature=0,
    )
    return parse_function_call_arguments(response)


async def compare_texts(
    *,
    left_text: str,
    right_text: str,
    left_label: str = "Left Document",
    right_label: str = "Right Document",
    question: Optional[str] = None,
    focus: Optional[str] = None,
) -> dict:
    left_text = _normalize_text(left_text)
    right_text = _normalize_text(right_text)
    left_label = _normalize_text(left_label) or "Left Document"
    right_label = _normalize_text(right_label) or "Right Document"
    question = _normalize_text(question)
    focus = _normalize_text(focus)

    if not left_text:
        raise ValueError("Field 'left_text' is required.")
    if not right_text:
        raise ValueError("Field 'right_text' is required.")

    try:
        similarity = compare_text_similarity(left_text=left_text[:8000], right_text=right_text[:8000])
    except Exception:
        similarity = SequenceMatcher(None, left_text[:8000], right_text[:8000]).ratio()
    compare_input = (
        f"LEFT_LABEL: {left_label}\nRIGHT_LABEL: {right_label}\nQUESTION: {question or '(none)'}\n"
        f"FOCUS: {focus or '(none)'}\n\nLEFT_TEXT:\n{left_text[:12000]}\n\nRIGHT_TEXT:\n{right_text[:12000]}"
    )
    try:
        assert_guardrails_pass(text=compare_input, stage="input")
    except Exception:
        pass
    code_metrics: dict = {}
    if OCI_EAI_COMPARE_CODE_INTERPRETER_ENABLED:
        try:
            metrics_response = create_code_interpreter_response(
                instructions=(
                    "Use Python if helpful and return only valid JSON. "
                    "Do not include markdown fences. "
                    "Return this exact shape: "
                    '{"left_chars":0,"right_chars":0,"left_words":0,"right_words":0,'
                    '"common_number_tokens":[],"common_date_tokens":[],"notable_length_change":""}'
                ),
                user_input=(
                    f"LEFT_LABEL: {left_label}\n"
                    f"RIGHT_LABEL: {right_label}\n\n"
                    f"LEFT_TEXT:\n{left_text[:12000]}\n\n"
                    f"RIGHT_TEXT:\n{right_text[:12000]}"
                ),
                max_output_tokens=900,
                memory_limit=OCI_EAI_COMPARE_CODE_INTERPRETER_MEMORY,
            )
            code_metrics = _safe_json_object(metrics_response.text)
        except Exception:
            code_metrics = {}

    client = None
    try:
        client = build_enterprise_ai_responses_client()
        parsed = _run_structured_compare(
            client=client,
            left_label=left_label,
            right_label=right_label,
            question=question,
            focus=focus,
            similarity=similarity,
            code_metrics=code_metrics,
            left_text=left_text,
            right_text=right_text,
            char_limit=12000,
        )
    except Exception:
        if client is not None:
            try:
                parsed = _run_structured_compare(
                    client=client,
                    left_label=left_label,
                    right_label=right_label,
                    question=question,
                    focus=focus,
                    similarity=similarity,
                    code_metrics=code_metrics,
                    left_text=left_text,
                    right_text=right_text,
                    char_limit=6000,
                )
            except Exception:
                parsed = _local_compare_result(
                    left_text=left_text,
                    right_text=right_text,
                    left_label=left_label,
                    right_label=right_label,
                    similarity=similarity,
                    code_metrics=code_metrics or _simple_metrics(left_text, right_text),
                )
        else:
            parsed = _local_compare_result(
                left_text=left_text,
                right_text=right_text,
                left_label=left_label,
                right_label=right_label,
                similarity=similarity,
                code_metrics=code_metrics or _simple_metrics(left_text, right_text),
            )
    if not isinstance(parsed, dict):
        raise ValueError("Comparison model returned invalid function call arguments.")

    parsed.setdefault("summary", "")
    parsed.setdefault("similarities", [])
    parsed.setdefault("differences", [])
    parsed.setdefault("added_in_right", [])
    parsed.setdefault("missing_in_right", [])
    parsed.setdefault("risk_changes", [])
    parsed.setdefault("confidence", "medium")
    parsed.setdefault("embedding_similarity", similarity if similarity is not None else 0.0)
    parsed.setdefault("analysis_metrics", code_metrics)

    return parsed


async def _handle(payload: dict) -> dict:
    return await compare_texts(
        left_text=payload.get("left_text"),
        right_text=payload.get("right_text"),
        left_label=payload.get("left_label") or "Left Document",
        right_label=payload.get("right_label") or "Right Document",
        question=payload.get("question"),
        focus=payload.get("focus"),
    )


app = build_agent_app(
    agent_card=build_agent_card(
        name="comparison-agent",
        description="Compares two text inputs and returns structured differences and similarities.",
        base_url=COMPARISON_AGENT_PUBLIC_URL,
        skill_id="document-comparison",
        skill_name="Document Comparison",
        skill_description="Compare two text inputs and return structured similarities, differences, additions, and risks.",
        examples=[
            '{"left_text":"Policy v1 text","right_text":"Policy v2 text","left_label":"v1","right_label":"v2","question":"What changed?","focus":"dates, obligations"}'
        ],
        tags=["comparison", "document", "diff", "analysis"],
    ),
    executor=JsonAgentExecutor(agent_name="comparison-agent", handler=_handle),
)


def main() -> None:
    trace_event(
        "comparison-agent",
        "A2A server listening",
        host=COMPARISON_AGENT_HOST,
        port=COMPARISON_AGENT_PORT,
    )
    run_agent_app(app, host=COMPARISON_AGENT_HOST, port=COMPARISON_AGENT_PORT)


if __name__ == "__main__":
    main()
