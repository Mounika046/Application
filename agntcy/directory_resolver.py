from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, Optional, Sequence

from config import (
    AGNTCY_DIRECTORY_DISCOVERY_ENABLED,
    AGNTCY_DIRECTORY_DISCOVERY_TIMEOUT_S,
    AGNTCY_DIRCTL_PATH,
    AGNTCY_DIRECTORY_NAME_PREFIX,
    AGNTCY_DIRECTORY_SERVER_ADDR,
    COMPARISON_AGENT_PUBLIC_URL,
    DOC_INGEST_AGENT_PUBLIC_URL,
    DOCUMENT_QUERY_AGENT_PUBLIC_URL,
    WEB_SEARCH_AGENT_PUBLIC_URL,
)
from flow_trace import trace_event


@dataclass(frozen=True)
class DirectoryDiscoveryConfig:
    enabled: bool
    server_addr: str
    timeout_s: float
    name_prefix: str
    dirctl_path: str


@dataclass(frozen=True)
class DiscoveredEndpoint:
    subject_id: str
    record_name: str
    integration: str
    endpoint_url: str
    identity_agent_id: str = ""
    source: str = "directory"


@dataclass(frozen=True)
class DirectoryRecordCandidate:
    subject_id: str
    record_name: str
    matched_skills: tuple[str, ...]
    record: Dict[str, Any]


def _trace(message: str) -> None:
    trace_event("ads", message)


def build_directory_config() -> DirectoryDiscoveryConfig:
    return DirectoryDiscoveryConfig(
        enabled=AGNTCY_DIRECTORY_DISCOVERY_ENABLED and bool(AGNTCY_DIRECTORY_SERVER_ADDR),
        server_addr=AGNTCY_DIRECTORY_SERVER_ADDR,
        timeout_s=AGNTCY_DIRECTORY_DISCOVERY_TIMEOUT_S,
        name_prefix=AGNTCY_DIRECTORY_NAME_PREFIX,
        dirctl_path=AGNTCY_DIRCTL_PATH,
    )


def build_record_name(subject_id: str, *, name_prefix: str = "") -> str:
    prefix = (name_prefix or AGNTCY_DIRECTORY_NAME_PREFIX).strip().strip("/")
    clean_subject = subject_id.strip().strip("/")
    if not clean_subject:
        raise ValueError("subject_id must be non-empty.")
    return f"{prefix}/{clean_subject}" if prefix else clean_subject


def extract_a2a_endpoint(record: Dict[str, Any]) -> str:
    module = _find_module(record, "integration/a2a")
    card_data = _as_dict(_as_dict(module.get("data")).get("card_data"))

    for interface in _as_list(card_data.get("supportedInterfaces")):
        interface_obj = _as_dict(interface)
        url = str(interface_obj.get("url") or "").strip()
        if url:
            return url.rstrip("/")

    provider_url = str(_as_dict(card_data.get("provider")).get("url") or "").strip()
    if provider_url:
        return provider_url.rstrip("/")

    raise ValueError("A2A record is missing a supported interface URL.")


class DirectoryResolver:
    """Resolve A2A agents from AGNTCY Directory skill searches."""

    def __init__(self, config: Optional[DirectoryDiscoveryConfig] = None) -> None:
        self._config = config or build_directory_config()

    @property
    def enabled(self) -> bool:
        return self._config.enabled

    @property
    def server_addr(self) -> str:
        return self._config.server_addr

    @property
    def timeout_s(self) -> float:
        return self._config.timeout_s

    @property
    def dirctl_path(self) -> str:
        return self._config.dirctl_path

    def record_name(self, subject_id: str) -> str:
        return build_record_name(subject_id, name_prefix=self._config.name_prefix)

    def resolve_a2a_from_record(self, subject_id: str, record: Dict[str, Any]) -> DiscoveredEndpoint:
        data = _record_data(record)
        annotations = _as_dict(data.get("annotations"))
        return DiscoveredEndpoint(
            subject_id=subject_id,
            record_name=self.record_name(subject_id),
            integration="a2a",
            endpoint_url=_override_endpoint_url(subject_id, extract_a2a_endpoint(record)),
            identity_agent_id=str(annotations.get("identity.agent_id") or "").strip(),
        )

    def search_records_by_skills(self, skill_names: Sequence[str], *, limit: int = 10) -> list[DirectoryRecordCandidate]:
        normalized_skills = [skill.strip() for skill in skill_names if skill and skill.strip()]
        if not normalized_skills:
            raise ValueError("At least one non-empty skill name is required for ADS discovery.")
        _trace(f"search skills={normalized_skills!r}")
        records = self._search_records_via_cli(normalized_skills, limit=limit)
        _trace(f"search results={len(records)}")
        candidates: list[DirectoryRecordCandidate] = []
        for record in records:
            if not _is_directory_a2a_agent(record):
                _trace(f"skip non-agent {str(_record_data(record).get('name') or '').strip()!r}")
                continue
            matched_skills = _matched_skill_names(record, normalized_skills)
            if not matched_skills:
                _trace(f"skip no-skill-match {str(_record_data(record).get('name') or '').strip()!r}")
                continue
            record_name = str(_record_data(record).get("name") or "").strip()
            subject_id = _subject_id_from_record_name(record_name)
            _trace(f"candidate {subject_id!r} identity={bool(_record_identity_agent_id(record))}")
            candidates.append(
                DirectoryRecordCandidate(
                    subject_id=subject_id,
                    record_name=record_name,
                    matched_skills=tuple(matched_skills),
                    record=record,
                )
            )
        deduped = _dedupe_candidates(candidates)
        if len(deduped) != len(candidates):
            _trace(f"deduped {len(candidates)} to {len(deduped)}")
        return deduped

    def discover_a2a_agents_by_skills(
        self,
        skill_names: Sequence[str],
        *,
        limit: int = 10,
    ) -> list[DiscoveredEndpoint]:
        candidates = self.search_records_by_skills(skill_names, limit=limit)
        endpoints: list[DiscoveredEndpoint] = []
        for candidate in candidates:
            try:
                endpoint = self.resolve_a2a_from_record(candidate.subject_id, candidate.record)
                _trace(f"resolved {candidate.subject_id!r}")
                endpoints.append(endpoint)
            except Exception as exc:
                _trace(f"resolve failed {candidate.subject_id!r}: {exc}")
                continue
        return endpoints

    def discover_best_a2a_agent(
        self,
        primary_skill: str,
        *,
        secondary_skills: Sequence[str] = (),
        limit: int = 10,
    ) -> DiscoveredEndpoint:
        skill_names = [primary_skill, *secondary_skills]
        _trace(f"select primary_skill={primary_skill!r}")
        candidates = self.search_records_by_skills(skill_names, limit=limit)
        ranked = sorted(
            candidates,
            key=lambda candidate: (
                primary_skill not in candidate.matched_skills,
                -_identity_agent_id_priority(_record_identity_agent_id(candidate.record)),
                -len(candidate.matched_skills),
                candidate.record_name,
            ),
        )
        if ranked:
            _trace(f"ranked {len(ranked)} candidates")
        else:
            _trace("ranked 0 candidates")
        for candidate in ranked:
            try:
                endpoint = self.resolve_a2a_from_record(candidate.subject_id, candidate.record)
                _trace(f"selected {candidate.subject_id!r}")
                return endpoint
            except Exception as exc:
                _trace(f"selection failed {candidate.subject_id!r}: {exc}")
                continue
        _trace("no agent selected")
        raise LookupError(f"No A2A agent record found for skills: {', '.join(skill_names)}")

    def _search_records_via_cli(self, skill_names: Sequence[str], *, limit: int) -> list[Dict[str, Any]]:
        if not self.enabled:
            _trace("directory disabled; using local OASF fallback")
            return _search_local_oasf_records(skill_names, limit=limit)
        dirctl_path = self.dirctl_path.strip()
        if not dirctl_path:
            _trace("dirctl path missing; using local OASF fallback")
            return _search_local_oasf_records(skill_names, limit=limit)

        command = [
            dirctl_path,
            "--server-addr",
            self.server_addr,
            "--auth-mode",
            "none",
            "search",
        ]
        for skill_name in skill_names:
            command.extend(["--skill", skill_name])
        command.extend(["--limit", str(limit), "--format", "record", "--output", "json"])
        _trace("running dirctl search")

        try:
            completed = subprocess.run(
                command,
                check=True,
                capture_output=True,
                text=True,
                timeout=self.timeout_s,
            )
        except subprocess.CalledProcessError as exc:
            stderr = (exc.stderr or "").strip()
            stdout = (exc.stdout or "").strip()
            _trace(f"dirctl failed code={exc.returncode}")
            _trace("using local OASF fallback after dirctl failure")
            return _search_local_oasf_records(skill_names, limit=limit)
        except subprocess.TimeoutExpired as exc:
            _trace("dirctl timed out")
            _trace("using local OASF fallback after dirctl timeout")
            return _search_local_oasf_records(skill_names, limit=limit)
        stdout = completed.stdout.strip()
        stderr = completed.stderr.strip()
        if stderr:
            _trace("dirctl stderr")
        if not stdout:
            _trace("dirctl returned no records")
            return []
        data = json.loads(stdout)
        if not isinstance(data, list):
            raise ValueError("ADS search output must be a JSON array of records.")
        _trace(f"dirctl parsed={len(data)}")
        return [item for item in data if isinstance(item, dict)]


def _find_module(record: Dict[str, Any], module_name: str) -> Dict[str, Any]:
    data = _record_data(record)
    modules = _as_list(data.get("modules"))
    for module in modules:
        module_obj = _as_dict(module)
        if str(module_obj.get("name") or "").strip() == module_name:
            return module_obj
    raise ValueError(f"Record does not contain module {module_name!r}.")


def _record_data(record: Dict[str, Any]) -> Dict[str, Any]:
    data = _as_dict(record.get("data"))
    return data if data else record


def _matched_skill_names(record: Dict[str, Any], skill_names: Sequence[str]) -> list[str]:
    wanted = {skill.strip() for skill in skill_names if skill and skill.strip()}
    if not wanted:
        return []
    actual = {
        str(_as_dict(skill).get("name") or "").strip()
        for skill in _as_list(_record_data(record).get("skills"))
    }
    return [skill_name for skill_name in skill_names if skill_name in actual]


def _is_directory_a2a_agent(record: Dict[str, Any]) -> bool:
    data = _record_data(record)
    annotations = _as_dict(data.get("annotations"))
    role = str(annotations.get("role") or "").strip()
    if role != "specialist-agent":
        return False
    modules = _as_list(data.get("modules"))
    for module in modules:
        module_obj = _as_dict(module)
        if str(module_obj.get("name") or "").strip() == "integration/a2a":
            return True
    return False


def _record_identity_agent_id(record: Dict[str, Any]) -> str:
    data = _record_data(record)
    annotations = _as_dict(data.get("annotations"))
    return str(annotations.get("identity.agent_id") or "").strip()


def _identity_agent_id_priority(identity_agent_id: str) -> int:
    value = identity_agent_id.strip()
    if not value:
        return 0
    if value.startswith("ORY-"):
        return 3
    if value.startswith("did:"):
        return 2
    return 1


def _candidate_sort_key(candidate: DirectoryRecordCandidate) -> tuple[Any, ...]:
    return (
        -_identity_agent_id_priority(_record_identity_agent_id(candidate.record)),
        -len(candidate.matched_skills),
        candidate.record_name,
    )


def _dedupe_candidates(candidates: Sequence[DirectoryRecordCandidate]) -> list[DirectoryRecordCandidate]:
    best_by_record_name: dict[str, DirectoryRecordCandidate] = {}
    for candidate in candidates:
        current = best_by_record_name.get(candidate.record_name)
        if current is None or _candidate_sort_key(candidate) < _candidate_sort_key(current):
            best_by_record_name[candidate.record_name] = candidate
    return sorted(best_by_record_name.values(), key=_candidate_sort_key)


def _subject_id_from_record_name(record_name: str) -> str:
    name = record_name.strip().strip("/")
    if not name:
        return ""
    return name.split("/")[-1]


def _as_dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> Iterable[Any]:
    return value if isinstance(value, list) else []


def _search_local_oasf_records(skill_names: Sequence[str], *, limit: int) -> list[Dict[str, Any]]:
    oasf_dir = Path(__file__).with_name("oasf")
    if not oasf_dir.exists():
        return []

    wanted = {skill.strip() for skill in skill_names if skill and skill.strip()}
    records: list[Dict[str, Any]] = []
    for path in sorted(oasf_dir.glob("*.json")):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(record, dict):
            continue
        actual = {
            str(_as_dict(skill).get("name") or "").strip()
            for skill in _as_list(_record_data(record).get("skills"))
        }
        if wanted.intersection(actual):
            records.append(record)
        if len(records) >= limit:
            break
    _trace(f"local oasf parsed={len(records)}")
    return records


def _override_endpoint_url(subject_id: str, default_url: str) -> str:
    overrides = {
        "document-ingest-agent": DOC_INGEST_AGENT_PUBLIC_URL,
        "kb-query-agent": DOCUMENT_QUERY_AGENT_PUBLIC_URL,
        "web-search-agent": WEB_SEARCH_AGENT_PUBLIC_URL,
        "comparison-agent": COMPARISON_AGENT_PUBLIC_URL,
    }
    return str(overrides.get(subject_id) or default_url).strip().rstrip("/")
