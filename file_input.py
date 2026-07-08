from __future__ import annotations

import base64
import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any
from urllib.request import urlopen


FileRef = dict[str, Any]


def make_doc_id_for_input(
    *,
    path: str = "",
    file_ref: FileRef | None = None,
    default_name: str = "document",
) -> str:
    clean_path = str(path or "").strip()
    if clean_path:
        return _make_doc_id_from_path(clean_path)

    clean_ref = _safe_file_ref(file_ref)
    if not clean_ref:
        raise ValueError("Either 'path' or 'file_ref' is required.")

    display_name = file_input_name(path="", file_ref=clean_ref, default_name=default_name)
    base = Path(display_name).stem.strip() or default_name
    digest_source = json.dumps(clean_ref, sort_keys=True, ensure_ascii=True)
    digest = hashlib.sha256(digest_source.encode("utf-8")).hexdigest()[:16]
    return f"{base}_{digest}"


def file_input_name(*, path: str = "", file_ref: FileRef | None = None, default_name: str = "document") -> str:
    clean_path = str(path or "").strip()
    if clean_path:
        return Path(clean_path).name or default_name

    clean_ref = _safe_file_ref(file_ref)
    if not clean_ref:
        return default_name

    name = str(
        clean_ref.get("filename")
        or clean_ref.get("name")
        or clean_ref.get("path")
        or clean_ref.get("url")
        or default_name
    ).strip()
    return Path(name).name or default_name


def file_input_display_label(
    *,
    path: str = "",
    file_ref: FileRef | None = None,
    fallback: str = "document",
) -> str:
    name = file_input_name(path=path, file_ref=file_ref, default_name=fallback).strip()
    stem = Path(name).stem.strip()
    return stem or name or fallback


def summarize_file_input(*, path: str = "", file_ref: FileRef | None = None) -> dict[str, Any]:
    clean_path = str(path or "").strip()
    if clean_path:
        return {
            "type": "path",
            "path": os.path.abspath(clean_path),
            "name": Path(clean_path).name,
        }

    clean_ref = _safe_file_ref(file_ref)
    if not clean_ref:
        return {}

    summary: dict[str, Any] = {
        "type": str(clean_ref.get("type") or "").strip() or _infer_file_ref_type(clean_ref),
        "name": file_input_name(file_ref=clean_ref),
    }
    if clean_ref.get("url"):
        summary["url"] = str(clean_ref.get("url")).strip()
    if clean_ref.get("path"):
        summary["path"] = str(clean_ref.get("path")).strip()
    if clean_ref.get("content_type"):
        summary["content_type"] = str(clean_ref.get("content_type")).strip()
    if clean_ref.get("content_base64"):
        try:
            summary["inline_bytes"] = len(base64.b64decode(str(clean_ref.get("content_base64")), validate=False))
        except Exception:
            summary["inline_bytes"] = None
    return summary


def materialize_file_input(*, path: str = "", file_ref: FileRef | None = None) -> tuple[str, list[str]]:
    clean_path = str(path or "").strip()
    if clean_path:
        if not os.path.exists(clean_path):
            raise FileNotFoundError(f"File not found: {clean_path}")
        return clean_path, []

    clean_ref = _safe_file_ref(file_ref)
    if not clean_ref:
        raise ValueError("Either 'path' or 'file_ref' is required.")

    ref_type = _infer_file_ref_type(clean_ref)
    if ref_type == "path":
        nested_path = str(clean_ref.get("path") or "").strip()
        if not nested_path:
            raise ValueError("file_ref.type=path requires a non-empty 'path'.")
        return materialize_file_input(path=nested_path)

    filename = file_input_name(file_ref=clean_ref, default_name="upload.bin")
    suffix = Path(filename).suffix or ".bin"

    if ref_type == "inline":
        content_base64 = str(clean_ref.get("content_base64") or "").strip()
        if not content_base64:
            raise ValueError("Inline file_ref requires non-empty 'content_base64'.")
        try:
            content = base64.b64decode(content_base64, validate=False)
        except Exception as exc:
            raise ValueError(f"Inline file_ref content could not be decoded: {exc}") from exc
        return _write_temp_bytes(content=content, suffix=suffix)

    if ref_type == "url":
        url = str(clean_ref.get("url") or "").strip()
        if not url:
            raise ValueError("file_ref.type=url requires a non-empty 'url'.")
        try:
            with urlopen(url, timeout=30) as response:
                content = response.read()
        except Exception as exc:
            raise ValueError(f"file_ref URL could not be downloaded: {exc}") from exc
        return _write_temp_bytes(content=content, suffix=suffix)

    raise ValueError(f"Unsupported file_ref type: {ref_type!r}")


def _write_temp_bytes(*, content: bytes, suffix: str) -> tuple[str, list[str]]:
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(content)
        temp_path = tmp.name
    return temp_path, [temp_path]


def _make_doc_id_from_path(path: str) -> str:
    abspath = os.path.abspath(path)
    try:
        mtime = str(os.path.getmtime(abspath))
    except Exception:
        mtime = "0"
    digest = hashlib.sha256((abspath + "|" + mtime).encode("utf-8")).hexdigest()[:16]
    base = os.path.splitext(os.path.basename(path))[0] or "document"
    return f"{base}_{digest}"


def _safe_file_ref(file_ref: FileRef | None) -> FileRef:
    return file_ref if isinstance(file_ref, dict) else {}


def _infer_file_ref_type(file_ref: FileRef) -> str:
    explicit = str(file_ref.get("type") or "").strip().lower()
    if explicit:
        return explicit
    if str(file_ref.get("path") or "").strip():
        return "path"
    if str(file_ref.get("url") or "").strip():
        return "url"
    if str(file_ref.get("content_base64") or "").strip():
        return "inline"
    return ""
