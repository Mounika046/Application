from __future__ import annotations

import math
from typing import Any

from config import EMBED_MODEL
from oracle_enterprise_ai.responses_client import build_enterprise_ai_responses_client


def create_embeddings(*, texts: list[str], model: str = EMBED_MODEL) -> list[list[float]]:
    clean_texts = [str(item or "").strip() for item in texts if str(item or "").strip()]
    if not clean_texts:
        return []
    client = build_enterprise_ai_responses_client()
    response = client.embeddings.create(model=model, input=clean_texts)
    vectors: list[list[float]] = []
    for item in getattr(response, "data", []) or []:
        embedding = getattr(item, "embedding", None)
        if isinstance(embedding, list):
            vectors.append([float(value) for value in embedding])
    return vectors


def cosine_similarity(left: list[float], right: list[float]) -> float | None:
    if not left or not right or len(left) != len(right):
        return None
    dot = sum(a * b for a, b in zip(left, right))
    left_norm = math.sqrt(sum(a * a for a in left))
    right_norm = math.sqrt(sum(b * b for b in right))
    if left_norm == 0 or right_norm == 0:
        return None
    return dot / (left_norm * right_norm)


def compare_text_similarity(*, left_text: str, right_text: str, model: str = EMBED_MODEL) -> float | None:
    vectors = create_embeddings(texts=[left_text, right_text], model=model)
    if len(vectors) != 2:
        return None
    return cosine_similarity(vectors[0], vectors[1])
