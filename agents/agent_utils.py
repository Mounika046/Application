from __future__ import annotations

import hashlib
import json
import os
from typing import Any, Dict, List


def tool_result_to_obj(res: Any) -> Any:
    if isinstance(res, (dict, list, str)):
        return res

    content = getattr(res, "content", None)
    if content and isinstance(content, list):
        text_parts: List[str] = []
        for part in content:
            text = getattr(part, "text", None)
            if isinstance(text, str):
                text_parts.append(text)
        joined = "\n".join(text_parts).strip()
        if not joined:
            return joined
        try:
            return json.loads(joined)
        except Exception:
            return joined

    return str(res)


def format_context(hits: List[Dict[str, Any]], max_chars: int = 6000) -> str:
    out: List[str] = []
    total = 0
    for hit in hits:
        md = hit.get("metadata") or {}
        source = md.get("source") or hit.get("doc_id") or "unknown"
        chunk = md.get("chunk_no")
        cite = f"[RAG:{source}#{chunk if chunk is not None else '?'}]"
        block = f"{cite} {hit.get('text', '')}".strip()
        if total + len(block) > max_chars:
            break
        out.append(block)
        total += len(block)
    return "\n\n".join(out)


def make_doc_id(path: str) -> str:
    abspath = os.path.abspath(path)
    try:
        mtime = str(os.path.getmtime(abspath))
    except Exception:
        mtime = "0"
    digest = hashlib.sha256((abspath + "|" + mtime).encode("utf-8")).hexdigest()[:16]
    base = os.path.splitext(os.path.basename(path))[0]
    return f"{base}_{digest}"
