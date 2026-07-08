from __future__ import annotations

import json
from json import JSONDecodeError
from typing import Any, Awaitable, Callable

import httpx
import uvicorn
from a2a.client import ClientConfig, ClientFactory
from a2a.client.card_resolver import A2ACardResolver
from a2a.client.helpers import create_text_message_object
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.apps import A2AStarletteApplication
from a2a.server.events import EventQueue
from a2a.server.request_handlers.default_request_handler import DefaultRequestHandler
from a2a.server.tasks import InMemoryTaskStore, TaskUpdater
from a2a.types import (
    AgentCapabilities,
    AgentCard,
    AgentProvider,
    AgentSkill,
    Message,
    Part,
    Role,
    Task,
    TextPart,
    TransportProtocol,
)
from a2a.utils.message import get_message_text, new_agent_text_message
from fastapi import FastAPI


JsonDict = dict[str, Any]


def _json_text_to_obj(text: str) -> JsonDict:
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError(f"Expected JSON object payload but received: {data!r}")
    return data


def _parse_json_payload(text: str) -> JsonDict | None:
    try:
        return _json_text_to_obj(text)
    except (JSONDecodeError, ValueError):
        return None


def _extract_text_parts(parts: list[Part]) -> str:
    return get_message_text(Message(role=Role.agent, parts=parts, message_id="parts-only"))


def result_to_obj(result: Task | Message) -> JsonDict:
    if isinstance(result, Message):
        text = get_message_text(result)
        obj = _parse_json_payload(text)
        if obj is not None:
            return obj
        raise RuntimeError(text.strip() or "A2A agent returned a non-JSON message.")

    for artifact in reversed(result.artifacts or []):
        text = _extract_text_parts(artifact.parts or [])
        if text.strip():
            obj = _parse_json_payload(text)
            if obj is not None:
                return obj

    status_message = result.status.message if result.status else None
    if status_message:
        text = get_message_text(status_message)
        if text.strip():
            obj = _parse_json_payload(text)
            if obj is not None:
                return obj
            raise RuntimeError(text.strip())

    raise ValueError("A2A agent returned no JSON artifact or message payload.")


def payload_from_context(context: RequestContext) -> JsonDict:
    text = context.get_user_input().strip()
    if not text:
        raise ValueError("A2A request message is empty.")
    return _json_text_to_obj(text)


class JsonAgentExecutor(AgentExecutor):
    def __init__(
        self,
        *,
        agent_name: str,
        handler: Callable[[JsonDict], Awaitable[JsonDict]],
    ) -> None:
        self._agent_name = agent_name
        self._handler = handler

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        task_id = context.task_id or ""
        context_id = context.context_id or ""
        updater = TaskUpdater(event_queue, task_id=task_id, context_id=context_id)
        await updater.start_work(
            new_agent_text_message(
                f"{self._agent_name} started processing.",
                context_id=context_id,
                task_id=task_id,
            )
        )

        try:
            payload = payload_from_context(context)
            result = await self._handler(payload)
            result_text = json.dumps(result, ensure_ascii=True)
            await updater.add_artifact(
                parts=[Part(root=TextPart(text=result_text))],
                name="result",
                metadata={"content_type": "application/json"},
                last_chunk=True,
            )
            await updater.complete(
                new_agent_text_message(
                    f"{self._agent_name} completed successfully.",
                    context_id=context_id,
                    task_id=task_id,
                )
            )
        except Exception as exc:
            await updater.failed(
                new_agent_text_message(
                    str(exc),
                    context_id=context_id,
                    task_id=task_id,
                )
            )

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        task_id = context.task_id or ""
        context_id = context.context_id or ""
        updater = TaskUpdater(event_queue, task_id=task_id, context_id=context_id)
        await updater.cancel(
            new_agent_text_message(
                f"{self._agent_name} canceled.",
                context_id=context_id,
                task_id=task_id,
            )
        )


def build_agent_card(
    *,
    name: str,
    description: str,
    base_url: str,
    skill_id: str,
    skill_name: str,
    skill_description: str,
    examples: list[str],
    tags: list[str],
) -> AgentCard:
    return AgentCard(
        protocol_version="0.3.0",
        name=name,
        description=description,
        url=base_url.rstrip("/") + "/",
        preferred_transport=TransportProtocol.jsonrpc,
        version="1.0.0",
        capabilities=AgentCapabilities(streaming=False, push_notifications=False),
        default_input_modes=["text/plain", "application/json"],
        default_output_modes=["application/json", "text/plain"],
        provider=AgentProvider(organization="Application", url=base_url.rstrip("/") + "/"),
        skills=[
            AgentSkill(
                id=skill_id,
                name=skill_name,
                description=skill_description,
                tags=tags,
                examples=examples,
                input_modes=["text/plain", "application/json"],
                output_modes=["application/json"],
            )
        ],
    )


def build_agent_app(*, agent_card: AgentCard, executor: AgentExecutor) -> FastAPI:
    app = FastAPI(title=agent_card.name)

    @app.get("/health")
    async def health() -> JsonDict:
        return {"status": "ok", "agent": agent_card.name}

    @app.get("/")
    async def root() -> JsonDict:
        # Identity and discovery tooling may probe the agent base URL directly
        # before following the well-known A2A metadata path.
        return {
            "status": "ok",
            "agent": agent_card.name,
            "agent_card_url": agent_card.url.rstrip("/") + "/.well-known/agent-card.json",
        }

    handler = DefaultRequestHandler(executor, InMemoryTaskStore())
    a2a_app = A2AStarletteApplication(agent_card=agent_card, http_handler=handler)
    a2a_app.add_routes_to_app(app)
    return app


def run_agent_app(app: FastAPI, *, host: str, port: int) -> None:
    uvicorn.run(app, host=host, port=port)


async def send_json_message(
    *,
    base_url: str,
    payload: JsonDict,
    timeout_s: float,
) -> JsonDict:
    normalized_base_url = base_url.strip().rstrip("/")
    if not normalized_base_url:
        raise ValueError("A2A endpoint URL is required.")
    message = create_text_message_object(
        role=Role.user,
        content=json.dumps(payload, ensure_ascii=True),
    )
    async with httpx.AsyncClient(timeout=timeout_s) as client:
        resolver = A2ACardResolver(client, normalized_base_url)
        card = await resolver.get_agent_card()
        # Prefer the configured endpoint URL over the agent card's self-advertised
        # URL so split deployments still work when the runtime sits behind an
        # ephemeral public IP or proxy.
        card = card.model_copy(update={"url": normalized_base_url + "/"})
        a2a_client = ClientFactory(
            ClientConfig(httpx_client=client, streaming=False)
        ).create(card)
        final_result: Task | Message | None = None
        async for event in a2a_client.send_message(message):
            if isinstance(event, Message):
                final_result = event
                continue
            final_result = event[0]

    if final_result is None:
        raise ValueError("A2A agent returned no final result.")
    return result_to_obj(final_result)
