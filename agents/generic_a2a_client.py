from __future__ import annotations

from typing import Any

from agents.a2a import send_json_message


JsonDict = dict[str, Any]


async def invoke_a2a_json(
    *,
    endpoint_url: str,
    payload: JsonDict,
    timeout_s: float,
) -> JsonDict:
    if not endpoint_url.strip():
        raise ValueError("A2A endpoint URL is required.")
    obj = await send_json_message(
        base_url=endpoint_url.strip(),
        payload=payload,
        timeout_s=timeout_s,
    )
    if not isinstance(obj, dict):
        raise ValueError(f"A2A agent returned unexpected payload: {obj!r}")
    return obj
