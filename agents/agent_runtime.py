from __future__ import annotations

from typing import Any, Dict, Optional

from fastmcp.client import Client

from config import OCR_MCP_SERVER_URL
from mcp_clients.registry import resolve_server


class _BaseRuntime:
    def __init__(self, server_name: str, *, server_url: Optional[str] = None) -> None:
        self._server_url = server_url or resolve_server(server_name).url

    async def _call_tool(self, tool: str, tool_input: Dict[str, Any]) -> Any:
        async with Client(self._server_url) as client:
            return await client.call_tool(tool, tool_input)


class DocumentIngestRuntime(_BaseRuntime):
    def __init__(self, *, server_url: Optional[str] = None) -> None:
        super().__init__("ocr", server_url=server_url or OCR_MCP_SERVER_URL)

    async def ocr_extract(
        self,
        *,
        path: str = "",
        file_ref: Dict[str, Any] | None = None,
        dpi: int = 300,
    ) -> Any:
        tool_input: Dict[str, Any] = {"dpi": dpi}
        if str(path or "").strip():
            tool_input["path"] = path
        if isinstance(file_ref, dict) and file_ref:
            tool_input["file_ref"] = file_ref
        return await self._call_tool("ocr_extract", tool_input)
