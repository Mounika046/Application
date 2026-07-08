from __future__ import annotations

import httpx

from agents.a2a import JsonAgentExecutor, build_agent_app, build_agent_card, run_agent_app
from config import (
    SERPAPI_API_KEY,
    WEB_SEARCH_AGENT_HOST,
    WEB_SEARCH_AGENT_PORT,
    WEB_SEARCH_AGENT_PUBLIC_URL,
)
from flow_trace import trace_event


async def web_search(question: str, num_results: int = 5) -> dict:
    if not question.strip():
        return {"web_results": None, "web_error": "Question is required."}

    api_key = SERPAPI_API_KEY.strip()
    if not api_key:
        return {"web_results": None, "web_error": "SERPAPI_API_KEY is required for web search."}

    params = {
        "engine": "google",
        "q": question.strip(),
        "api_key": api_key,
        "num": max(1, min(int(num_results), 10)),
    }

    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
        response = await client.get("https://serpapi.com/search.json", params=params)
        response.raise_for_status()
        payload = response.json()

    organic_results = payload.get("organic_results") or []
    web_results = []
    for item in organic_results[: max(1, min(int(num_results), 10))]:
        if not isinstance(item, dict):
            continue
        web_results.append(
            {
                "title": str(item.get("title") or "").strip(),
                "snippet": str(item.get("snippet") or "").strip(),
                "url": str(item.get("link") or "").strip(),
            }
        )

    if not web_results:
        answer_box = payload.get("answer_box") if isinstance(payload.get("answer_box"), dict) else {}
        fallback_text = (
            str(answer_box.get("snippet") or answer_box.get("answer") or "").strip()
            or str(payload.get("search_information", {}).get("organic_results_state") or "").strip()
        )
        if fallback_text:
            web_results = [{"title": "SerpApi Result", "snippet": fallback_text, "url": ""}]

    if not web_results:
        return {"web_results": None, "web_error": "SerpApi returned no usable search results."}

    trace_event(
        "web-search-agent",
        "SerpApi search completed",
        query=question,
        result_count=len(web_results),
    )
    return {
        "web_results": web_results,
        "web_error": None,
    }


async def _handle(payload: dict) -> dict:
    question = str(payload.get("question") or "").strip()
    num_results = int(payload.get("num_results", 5))
    return await web_search(question=question, num_results=num_results)


app = build_agent_app(
    agent_card=build_agent_card(
        name="web-search-agent",
        description="Runs live web research when document retrieval is not enough.",
        base_url=WEB_SEARCH_AGENT_PUBLIC_URL,
        skill_id="web-search",
        skill_name="Web Search",
        skill_description="Search the web and return grounded live web results.",
        examples=['{"question":"Latest France capital facts","num_results":5}'],
        tags=["web", "search", "research", "live"],
    ),
    executor=JsonAgentExecutor(agent_name="web-search-agent", handler=_handle),
)


def main() -> None:
    trace_event(
        "web-search-agent",
        "A2A server listening",
        host=WEB_SEARCH_AGENT_HOST,
        port=WEB_SEARCH_AGENT_PORT,
    )
    run_agent_app(app, host=WEB_SEARCH_AGENT_HOST, port=WEB_SEARCH_AGENT_PORT)


if __name__ == "__main__":
    main()
