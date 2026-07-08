from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class SubjectIdentity:
    subject_id: str
    subject_type: str
    display_name: str
    agentic_service_id: str
    resolver_metadata_id: str
    agent_card_url: str


SUBJECTS_PATH = Path(__file__).with_name("subjects.json")



def _load_subject_index() -> dict[str, SubjectIdentity]:
    payload = json.loads(SUBJECTS_PATH.read_text(encoding="utf-8"))
    subjects = payload.get("subjects")
    if not isinstance(subjects, list):
        return {}

    index: dict[str, SubjectIdentity] = {}
    for subject in subjects:
        if not isinstance(subject, dict):
            continue
        subject_id = str(subject.get("id") or "").strip()
        if not subject_id:
            continue
        index[subject_id] = SubjectIdentity(
            subject_id=subject_id,
            subject_type=str(subject.get("type") or "").strip(),
            display_name=str(subject.get("display_name") or "").strip(),
            agentic_service_id=str(subject.get("agentic_service_id") or "").strip(),
            resolver_metadata_id=str(subject.get("resolver_metadata_id") or "").strip(),
            agent_card_url=str(subject.get("agent_card_url") or "").strip(),
        )
    return index


def get_subject_identity(subject_id: str) -> SubjectIdentity | None:
    clean_subject_id = subject_id.strip()
    if not clean_subject_id:
        return None
    return _load_subject_index().get(clean_subject_id)
