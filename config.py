import os
import sys
from pathlib import Path

REGION = os.environ.get("OCI_REGION", "us-chicago-1")
COMPARTMENT_ID = os.environ.get("OCI_COMPARTMENT_ID", "")
CHAT_MODEL = os.environ.get("OCI_CHAT_MODEL", "openai.gpt-oss-120b")
EMBED_MODEL = os.environ.get("OCI_EMBED_MODEL", "openai.text-embedding-3-large")
OCI_AUTH_MODE = os.environ.get("OCI_AUTH_MODE", "session").strip().lower()
OCI_CONFIG_PROFILE = os.environ.get("OCI_CONFIG_PROFILE", "Profile1").strip() or "Profile1"
OCI_CONFIG_FILE = os.environ.get("OCI_CONFIG_FILE", "").strip()

OCI_EAI_ENABLED = os.environ.get("OCI_EAI_ENABLED", "true").strip().lower() == "true"
OCI_EAI_PROJECT_OCID = os.environ.get("OCI_EAI_PROJECT_OCID", "").strip()
OCI_EAI_COMPARTMENT_ID = os.environ.get("OCI_EAI_COMPARTMENT_ID", COMPARTMENT_ID).strip() or COMPARTMENT_ID
OCI_EAI_AUTH_MODE = os.environ.get("OCI_EAI_AUTH_MODE", "resource_principal").strip().lower() or "resource_principal"
OCI_EAI_CONFIG_PROFILE = os.environ.get("OCI_EAI_CONFIG_PROFILE", OCI_CONFIG_PROFILE).strip() or OCI_CONFIG_PROFILE
OCI_EAI_CONFIG_FILE = os.environ.get("OCI_EAI_CONFIG_FILE", OCI_CONFIG_FILE).strip()
OCI_EAI_BASE_URL = os.environ.get("OCI_EAI_BASE_URL", "").strip().rstrip("/")
OCI_EAI_MODEL = os.environ.get("OCI_EAI_MODEL", CHAT_MODEL).strip() or CHAT_MODEL
OCI_EAI_TIMEOUT_S = float(os.environ.get("OCI_EAI_TIMEOUT_S", "60").strip() or "60")
OCI_EAI_VECTOR_STORE_ID = os.environ.get("OCI_EAI_VECTOR_STORE_ID", "").strip()
OCI_EAI_CONVERSATIONS_ENABLED = os.environ.get("OCI_EAI_CONVERSATIONS_ENABLED", "true").strip().lower() == "true"
OCI_EAI_MCP_REQUIRE_APPROVAL = os.environ.get("OCI_EAI_MCP_REQUIRE_APPROVAL", "never").strip() or "never"
OCI_EAI_COMPARE_CODE_INTERPRETER_ENABLED = (
    os.environ.get("OCI_EAI_COMPARE_CODE_INTERPRETER_ENABLED", "true").strip().lower() == "true"
)
OCI_EAI_COMPARE_CODE_INTERPRETER_MEMORY = (
    os.environ.get("OCI_EAI_COMPARE_CODE_INTERPRETER_MEMORY", "1g").strip() or "1g"
)

OCI_LOGGING_ENABLED = os.environ.get("OCI_LOGGING_ENABLED", "false").strip().lower() == "true"
OCI_LOGGING_AUTH_MODE = os.environ.get("OCI_LOGGING_AUTH_MODE", OCI_AUTH_MODE).strip().lower() or OCI_AUTH_MODE
OCI_LOGGING_CONFIG_PROFILE = os.environ.get("OCI_LOGGING_CONFIG_PROFILE", OCI_CONFIG_PROFILE).strip() or OCI_CONFIG_PROFILE
OCI_LOGGING_CONFIG_FILE = os.environ.get("OCI_LOGGING_CONFIG_FILE", OCI_CONFIG_FILE).strip()
OCI_LOGGING_REQUEST_LOG_OCID = os.environ.get("OCI_LOGGING_REQUEST_LOG_OCID", "").strip()
OCI_LOGGING_AGENT_LOG_OCID = os.environ.get("OCI_LOGGING_AGENT_LOG_OCID", "").strip()
OCI_LOGGING_SOURCE = os.environ.get("OCI_LOGGING_SOURCE", "multi-agent-application").strip() or "multi-agent-application"
OCI_LOGGING_REQUEST_SUBJECT = (
    os.environ.get("OCI_LOGGING_REQUEST_SUBJECT", "application/request").strip() or "application/request"
)
OCI_LOGGING_AGENT_SUBJECT = (
    os.environ.get("OCI_LOGGING_AGENT_SUBJECT", "application/agent").strip() or "application/agent"
)
OCI_LOGGING_TIMEOUT_S = float(os.environ.get("OCI_LOGGING_TIMEOUT_S", "10").strip() or "10")


SERPAPI_API_KEY = os.environ.get("SERPAPI_API_KEY", "")
POPPLER_PATH = os.environ.get("POPPLER_PATH", "")
MCP_REGISTRY_FILE = os.environ.get("MCP_REGISTRY_FILE", "mcp_registry.json").strip() or "mcp_registry.json"
OCR_MCP_SERVER_URL = os.environ.get("OCR_MCP_SERVER_URL", "").strip()
DOC_INGEST_AGENT_URL = os.environ.get("DOC_INGEST_AGENT_URL", "http://127.0.0.1:8031").strip() or "http://127.0.0.1:8031"
DOC_INGEST_AGENT_PUBLIC_URL = os.environ.get("DOC_INGEST_AGENT_PUBLIC_URL", DOC_INGEST_AGENT_URL).strip() or DOC_INGEST_AGENT_URL
DOC_INGEST_AGENT_TIMEOUT_S = float(os.environ.get("DOC_INGEST_AGENT_TIMEOUT_S", "120").strip() or "120")
DOC_INGEST_AGENT_HOST = os.environ.get("DOC_INGEST_AGENT_HOST", "127.0.0.1").strip() or "127.0.0.1"
DOC_INGEST_AGENT_PORT = int(os.environ.get("DOC_INGEST_AGENT_PORT", "8031").strip() or "8031")
DOCUMENT_QUERY_AGENT_URL = (
    os.environ.get("DOCUMENT_QUERY_AGENT_URL", os.environ.get("KB_QUERY_AGENT_URL", "http://127.0.0.1:8032")).strip()
    or "http://127.0.0.1:8032"
)
DOCUMENT_QUERY_AGENT_PUBLIC_URL = (
    os.environ.get(
        "DOCUMENT_QUERY_AGENT_PUBLIC_URL",
        os.environ.get("KB_QUERY_AGENT_PUBLIC_URL", DOCUMENT_QUERY_AGENT_URL),
    ).strip()
    or DOCUMENT_QUERY_AGENT_URL
)
DOCUMENT_QUERY_AGENT_TIMEOUT_S = float(
    os.environ.get("DOCUMENT_QUERY_AGENT_TIMEOUT_S", os.environ.get("KB_QUERY_AGENT_TIMEOUT_S", "30")).strip()
    or "30"
)
DOCUMENT_QUERY_AGENT_HOST = (
    os.environ.get("DOCUMENT_QUERY_AGENT_HOST", os.environ.get("KB_QUERY_AGENT_HOST", "127.0.0.1")).strip()
    or "127.0.0.1"
)
DOCUMENT_QUERY_AGENT_PORT = int(
    os.environ.get("DOCUMENT_QUERY_AGENT_PORT", os.environ.get("KB_QUERY_AGENT_PORT", "8032")).strip() or "8032"
)
WEB_SEARCH_AGENT_URL = os.environ.get("WEB_SEARCH_AGENT_URL", "http://127.0.0.1:8033").strip() or "http://127.0.0.1:8033"
WEB_SEARCH_AGENT_PUBLIC_URL = os.environ.get("WEB_SEARCH_AGENT_PUBLIC_URL", WEB_SEARCH_AGENT_URL).strip() or WEB_SEARCH_AGENT_URL
WEB_SEARCH_AGENT_TIMEOUT_S = float(os.environ.get("WEB_SEARCH_AGENT_TIMEOUT_S", "30").strip() or "30")
WEB_SEARCH_AGENT_HOST = os.environ.get("WEB_SEARCH_AGENT_HOST", "127.0.0.1").strip() or "127.0.0.1"
WEB_SEARCH_AGENT_PORT = int(os.environ.get("WEB_SEARCH_AGENT_PORT", "8033").strip() or "8033")
COMPARISON_AGENT_URL = os.environ.get("COMPARISON_AGENT_URL", "http://127.0.0.1:8034").strip() or "http://127.0.0.1:8034"
COMPARISON_AGENT_PUBLIC_URL = os.environ.get("COMPARISON_AGENT_PUBLIC_URL", COMPARISON_AGENT_URL).strip() or COMPARISON_AGENT_URL
COMPARISON_AGENT_TIMEOUT_S = float(os.environ.get("COMPARISON_AGENT_TIMEOUT_S", "60").strip() or "60")
COMPARISON_AGENT_HOST = os.environ.get("COMPARISON_AGENT_HOST", "127.0.0.1").strip() or "127.0.0.1"
COMPARISON_AGENT_PORT = int(os.environ.get("COMPARISON_AGENT_PORT", "8034").strip() or "8034")
HOSTED_APP_HOST = os.environ.get("HOSTED_APP_HOST", "0.0.0.0").strip() or "0.0.0.0"
HOSTED_APP_PORT = int(
    (
        os.environ.get("HOSTED_APP_PORT", "").strip()
        or os.environ.get("PORT", "").strip()
        or "8080"
    )
)
SHARED_FILE_REF_STRATEGY = (
    os.environ.get("SHARED_FILE_REF_STRATEGY", "inline").strip().lower() or "inline"
)
SHARED_FILE_INLINE_MAX_BYTES = int(
    os.environ.get("SHARED_FILE_INLINE_MAX_BYTES", str(5 * 1024 * 1024)).strip()
    or str(5 * 1024 * 1024)
)


# Agntcy identity/directory bootstrap settings.
AGNTCY_DIRECTORY_DISCOVERY_ENABLED = os.environ.get("AGNTCY_DIRECTORY_DISCOVERY_ENABLED", "false").strip().lower() == "true"
AGNTCY_DIRECTORY_SERVER_ADDR = os.environ.get("AGNTCY_DIRECTORY_SERVER_ADDR", "127.0.0.1:8888").strip() or "127.0.0.1:8888"
AGNTCY_DIRECTORY_DISCOVERY_TIMEOUT_S = float(
    os.environ.get("AGNTCY_DIRECTORY_DISCOVERY_TIMEOUT_S", "5").strip() or "5"
)
AGNTCY_DIRECTORY_NAME_PREFIX = os.environ.get("AGNTCY_DIRECTORY_NAME_PREFIX", "application").strip().strip("/") or "application"
_default_dirctl_path = ""
if sys.platform.startswith("win"):
    _candidate_dirctl = Path("agntcy/bin/dirctl.exe")
    if _candidate_dirctl.exists():
        _default_dirctl_path = str(_candidate_dirctl)
else:
    _candidate_dirctl = Path("agntcy/bin/dirctl-linux")
    if _candidate_dirctl.exists():
        _default_dirctl_path = str(_candidate_dirctl)
AGNTCY_DIRCTL_PATH = os.environ.get("AGNTCY_DIRCTL_PATH", _default_dirctl_path).strip()

AGNTCY_IDENTITY_REST_BASE_URL = os.environ.get(
    "AGNTCY_IDENTITY_REST_BASE_URL",
    "https://api.agent-identity.outshift.com",
).strip().rstrip("/")
AGNTCY_IDENTITY_ORG_API_KEY = os.environ.get("AGNTCY_IDENTITY_ORG_API_KEY", "").strip()
AGNTCY_IDENTITY_VERIFY_ENABLED = (
    os.environ.get("AGNTCY_IDENTITY_VERIFY_ENABLED", "false").strip().lower() == "true"
)
AGNTCY_IDENTITY_VERIFY_TIMEOUT_S = float(
    os.environ.get("AGNTCY_IDENTITY_VERIFY_TIMEOUT_S", "10").strip() or "10"
)
