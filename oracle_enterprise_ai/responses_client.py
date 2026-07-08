from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any

import httpx
from openai import OpenAI
from oci_openai import OciOpenAI

from config import (
    REGION,
    OCI_EAI_BASE_URL,
    OCI_EAI_COMPARTMENT_ID,
    OCI_EAI_CONVERSATIONS_ENABLED,
    OCI_EAI_ENABLED,
    OCI_EAI_AUTH_MODE,
    OCI_EAI_CONFIG_FILE,
    OCI_EAI_CONFIG_PROFILE,
    OCI_EAI_MCP_REQUIRE_APPROVAL,
    OCI_EAI_MODEL,
    OCI_EAI_PROJECT_OCID,
    OCI_EAI_TIMEOUT_S,
)
from oracle_enterprise_ai.oci_client import build_oci_auth
from oracle_enterprise_ai.guardrails_client import assert_guardrails_pass


@dataclass(frozen=True)
class EnterpriseAIResponseResult:
    text: str
    response_id: str
    conversation_id: str
    raw: Any


def _build_http_client() -> httpx.Client:
    return httpx.Client(
        auth=build_oci_auth(
            mode=OCI_EAI_AUTH_MODE,
            profile_name=OCI_EAI_CONFIG_PROFILE,
            config_file=OCI_EAI_CONFIG_FILE,
        ),
        timeout=OCI_EAI_TIMEOUT_S,
    )


def build_enterprise_ai_responses_client() -> OpenAI:
    if not OCI_EAI_ENABLED:
        raise ValueError("OCI_EAI_ENABLED is false.")
    if not OCI_EAI_BASE_URL:
        raise ValueError("OCI_EAI_BASE_URL is not configured.")
    if not OCI_EAI_PROJECT_OCID:
        raise ValueError("OCI_EAI_PROJECT_OCID is not configured.")

    return OpenAI(
        api_key="oci_iam",
        base_url=OCI_EAI_BASE_URL,
        project=OCI_EAI_PROJECT_OCID,
        timeout=OCI_EAI_TIMEOUT_S,
        default_headers={"opc-compartment-id": OCI_EAI_COMPARTMENT_ID},
        http_client=_build_http_client(),
    )


def build_enterprise_ai_vector_dp_client() -> OciOpenAI:
    if not OCI_EAI_ENABLED:
        raise ValueError("OCI_EAI_ENABLED is false.")
    return OciOpenAI(
        auth=build_oci_auth(
            mode=OCI_EAI_AUTH_MODE,
            profile_name=OCI_EAI_CONFIG_PROFILE,
            config_file=OCI_EAI_CONFIG_FILE,
        ),
        service_endpoint=f"https://inference.generativeai.{REGION}.oci.oraclecloud.com/20231130",
        compartment_id=OCI_EAI_COMPARTMENT_ID,
        timeout=OCI_EAI_TIMEOUT_S,
    )


def build_enterprise_ai_vector_cp_client() -> OciOpenAI:
    if not OCI_EAI_ENABLED:
        raise ValueError("OCI_EAI_ENABLED is false.")
    return OciOpenAI(
        auth=build_oci_auth(
            mode=OCI_EAI_AUTH_MODE,
            profile_name=OCI_EAI_CONFIG_PROFILE,
            config_file=OCI_EAI_CONFIG_FILE,
        ),
        service_endpoint=f"https://generativeai.{REGION}.oci.oraclecloud.com/20231130",
        compartment_id=OCI_EAI_COMPARTMENT_ID,
        timeout=OCI_EAI_TIMEOUT_S,
    )


def create_text_response(
    *,
    instructions: str,
    user_input: str,
    conversation_id: str | None = None,
    model: str = OCI_EAI_MODEL,
    temperature: float | None = None,
    max_output_tokens: int = 1200,
    reasoning_effort: str = "low",
) -> EnterpriseAIResponseResult:
    request: dict[str, Any] = {
        "model": model,
        "instructions": instructions,
        "input": user_input,
        "max_output_tokens": max_output_tokens,
        "reasoning": {"effort": reasoning_effort},
    }
    if temperature is not None:
        request["temperature"] = temperature
    if conversation_id:
        request["conversation"] = conversation_id
    return _create_response_result(request=request, conversation_id=conversation_id)


def create_conversation(*, metadata: dict[str, str] | None = None) -> str:
    if not OCI_EAI_ENABLED:
        raise ValueError("OCI_EAI_ENABLED is false.")
    if not OCI_EAI_CONVERSATIONS_ENABLED:
        raise ValueError("OCI_EAI_CONVERSATIONS_ENABLED is false.")
    client = build_enterprise_ai_responses_client()
    conversation = client.conversations.create(metadata=metadata or {})
    return str(getattr(conversation, "id", "") or "")


def create_mcp_response(
    *,
    instructions: str,
    user_input: str,
    server_url: str,
    server_label: str,
    server_description: str,
    allowed_tools: list[str] | None = None,
    authorization: str | None = None,
    conversation_id: str | None = None,
    model: str = OCI_EAI_MODEL,
    max_output_tokens: int = 1200,
    reasoning_effort: str = "low",
) -> EnterpriseAIResponseResult:
    tool: dict[str, Any] = {
        "type": "mcp",
        "server_url": server_url,
        "server_label": server_label,
        "server_description": server_description,
        "require_approval": OCI_EAI_MCP_REQUIRE_APPROVAL,
    }
    if allowed_tools:
        tool["allowed_tools"] = allowed_tools
    auth_value = (authorization or "").strip()
    if auth_value:
        tool["authorization"] = auth_value
    request: dict[str, Any] = {
        "model": model,
        "instructions": instructions,
        "input": user_input,
        "max_output_tokens": max_output_tokens,
        "reasoning": {"effort": reasoning_effort},
        "tools": [tool],
    }
    if conversation_id:
        request["conversation"] = conversation_id
    return _create_response_result(request=request, conversation_id=conversation_id)


def create_code_interpreter_response(
    *,
    instructions: str,
    user_input: str,
    conversation_id: str | None = None,
    model: str = OCI_EAI_MODEL,
    max_output_tokens: int = 1200,
    reasoning_effort: str = "low",
    memory_limit: str = "1g",
) -> EnterpriseAIResponseResult:
    request: dict[str, Any] = {
        "model": model,
        "instructions": instructions,
        "input": user_input,
        "max_output_tokens": max_output_tokens,
        "reasoning": {"effort": reasoning_effort},
        "tools": [
            {
                "type": "code_interpreter",
                "container": {"type": "auto", "memory_limit": memory_limit},
            }
        ],
    }
    if conversation_id:
        request["conversation"] = conversation_id
    return _create_response_result(request=request, conversation_id=conversation_id)


def extract_function_calls(response: Any) -> list[dict[str, Any]]:
    try:
        dumped = response.model_dump()
    except Exception:
        dumped = None
    if not isinstance(dumped, dict):
        return []
    calls: list[dict[str, Any]] = []
    for item in dumped.get("output", []):
        if isinstance(item, dict) and item.get("type") == "function_call":
            calls.append(item)
    return calls


def parse_function_call_arguments(response: Any) -> dict[str, Any]:
    calls = extract_function_calls(response)
    if not calls:
        raise ValueError("Enterprise AI response did not return a function call.")
    arguments = calls[0].get("arguments")
    if not isinstance(arguments, str) or not arguments.strip():
        raise ValueError("Function call arguments were empty.")
    parsed = json.loads(arguments)
    if not isinstance(parsed, dict):
        raise ValueError("Function call arguments were not a JSON object.")
    return parsed


def _create_response_result(
    *,
    request: dict[str, Any],
    conversation_id: str | None,
) -> EnterpriseAIResponseResult:
    input_text = _request_input_to_text(request.get("input"))
    if input_text:
        assert_guardrails_pass(text=input_text, stage="input")

    client = build_enterprise_ai_responses_client()
    response = client.responses.create(**request)
    text = _extract_text(response)
    if text:
        assert_guardrails_pass(text=text, stage="output")

    response_conversation_id = (
        str(getattr(response, "conversation", "") or "")
        or str(getattr(response, "conversation_id", "") or "")
        or conversation_id
        or ""
    )
    return EnterpriseAIResponseResult(
        text=text,
        response_id=str(getattr(response, "id", "") or ""),
        conversation_id=response_conversation_id,
        raw=response,
    )


def _request_input_to_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            if isinstance(item, dict):
                output = item.get("output")
                if isinstance(output, str) and output.strip():
                    parts.append(output.strip())
                    continue
                text = item.get("text")
                if isinstance(text, str) and text.strip():
                    parts.append(text.strip())
        return "\n".join(parts).strip()
    return ""


def _extract_text(response: Any) -> str:
    output_text = getattr(response, "output_text", None)
    if isinstance(output_text, str) and output_text.strip():
        return output_text.strip()

    try:
        dumped = response.model_dump()
    except Exception:
        dumped = None
    text = _extract_text_from_dump(dumped)
    if text:
        return text

    output = getattr(response, "output", None)
    if isinstance(output, list):
        texts: list[str] = []
        for item in output:
            content = getattr(item, "content", None)
            if not isinstance(content, list):
                continue
            for part in content:
                text = getattr(part, "text", None)
                if isinstance(text, str) and text.strip():
                    texts.append(text.strip())
                    continue
                text_obj = getattr(part, "text", None)
                value = getattr(text_obj, "value", None)
                if isinstance(value, str) and value.strip():
                    texts.append(value.strip())
        if texts:
            return "\n".join(texts)

    raise ValueError("Enterprise AI response did not include text output.")


def _extract_text_from_dump(payload: Any) -> str:
    if not isinstance(payload, dict):
        return ""
    output = payload.get("output")
    if not isinstance(output, list):
        return ""

    texts: list[str] = []
    for item in output:
        if not isinstance(item, dict):
            continue
        content = item.get("content")
        if not isinstance(content, list):
            continue
        for part in content:
            if not isinstance(part, dict):
                continue
            text = part.get("text")
            if isinstance(text, str) and text.strip():
                texts.append(text.strip())
                continue
            if isinstance(text, dict):
                value = text.get("value")
                if isinstance(value, str) and value.strip():
                    texts.append(value.strip())

    return "\n".join(texts).strip()
