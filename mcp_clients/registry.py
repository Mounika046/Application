from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict

from config import MCP_REGISTRY_FILE


@dataclass(frozen=True)
class RegistryServer:
    name: str
    url: str
    timeout_s: float = 10.0
    notes: str = ""


def _registry_path() -> Path:
    return Path(MCP_REGISTRY_FILE).expanduser()


def load_registry() -> Dict[str, Any]:
    path = _registry_path()
    if not path.is_absolute():
        path = Path.cwd() / path
    if not path.exists():
        raise FileNotFoundError(f"MCP registry file not found: {path}")

    with path.open("r", encoding="utf-8") as fp:
        data = json.load(fp)

    if not isinstance(data, dict):
        raise ValueError(f"MCP registry file must contain a JSON object: {path}")
    return data


def resolve_server(name: str) -> RegistryServer:
    registry = load_registry()
    servers = registry.get("servers")
    if not isinstance(servers, dict):
        raise ValueError("MCP registry JSON must contain a 'servers' object.")

    server = servers.get(name)
    if not isinstance(server, dict):
        raise KeyError(f"MCP registry does not define server {name!r}.")

    url = str(server.get("url") or "").strip()
    if not url:
        raise ValueError(f"MCP registry entry {name!r} is missing a non-empty 'url'.")

    timeout_raw = server.get("timeout_s", 10.0)
    timeout_s = float(timeout_raw)
    notes = str(server.get("notes") or "").strip()
    return RegistryServer(name=name, url=url, timeout_s=timeout_s, notes=notes)
