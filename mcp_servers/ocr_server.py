from __future__ import annotations
from dotenv import load_dotenv
load_dotenv()

import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, List

from fastmcp import FastMCP
from pdf2image import convert_from_path, pdfinfo_from_path
from PIL import Image

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config import POPPLER_PATH
from file_input import materialize_file_input

mcp = FastMCP("ocr-server")


def _is_pdf(path: str) -> bool:
    return path.lower().endswith(".pdf")


def _run_text_command(command: List[str]) -> str:
    completed = subprocess.run(
        command,
        check=True,
        capture_output=True,
        text=True,
    )
    return (completed.stdout or "").strip()


def _ocr_image_with_tesseract(image_path: str) -> str:
    return _run_text_command(["tesseract", image_path, "stdout"]).strip()


def _pdf_text_via_pdftotext(path: str) -> str:
    try:
        return _run_text_command(["pdftotext", "-layout", "-enc", "UTF-8", path, "-"])
    except Exception:
        return ""


@mcp.tool()
def ocr_extract(path: str = "", file_ref: dict[str, Any] | None = None, dpi: int = 300) -> dict:
    source_path, cleanup_paths = materialize_file_input(path=path, file_ref=file_ref)

    try:
        texts: List[str] = []
        if _is_pdf(source_path):
            info = pdfinfo_from_path(source_path, poppler_path=(POPPLER_PATH or None))
            page_count = int(info.get("Pages") or 0)
            direct_text = _pdf_text_via_pdftotext(source_path)
            if direct_text:
                return {"text": direct_text, "pages": max(page_count, 1)}
            for page_no in range(1, page_count + 1):
                images = convert_from_path(
                    source_path,
                    dpi=dpi,
                    first_page=page_no,
                    last_page=page_no,
                    poppler_path=(POPPLER_PATH or None),
                )
                if not images:
                    texts.append("")
                    continue
                img = images[0]
                with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
                    temp_path = tmp.name
                try:
                    img.convert("RGB").save(temp_path, format="PNG")
                    texts.append(_ocr_image_with_tesseract(temp_path))
                finally:
                    img.close()
                    del images
                    try:
                        os.remove(temp_path)
                    except OSError:
                        pass
        else:
            with Image.open(source_path) as img:
                with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
                    temp_path = tmp.name
                try:
                    img.convert("RGB").save(temp_path, format="PNG")
                    texts.append(_ocr_image_with_tesseract(temp_path))
                finally:
                    try:
                        os.remove(temp_path)
                    except OSError:
                        pass

        full_text = "\n\n".join([t for t in texts if t]).strip()
        return {"text": full_text, "pages": len(texts)}
    finally:
        for cleanup_path in cleanup_paths:
            try:
                os.remove(cleanup_path)
            except OSError:
                pass


if __name__ == "__main__":
    mcp.run(
        transport="http",
        host=os.environ.get("OCR_MCP_HOST", "127.0.0.1"),
        port=int(os.environ.get("OCR_MCP_PORT", "8011")),
    )
