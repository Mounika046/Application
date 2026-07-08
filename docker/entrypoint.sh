#!/bin/sh
set -eu

python /app/docker/prepare_oci_config.py

if [ -f /tmp/oci/config.generated ]; then
  export OCI_CONFIG_FILE="${OCI_CONFIG_FILE:-/tmp/oci/config.generated}"
  export OCI_EAI_CONFIG_FILE="${OCI_EAI_CONFIG_FILE:-/tmp/oci/config.generated}"
  export OCI_CONFIG_PROFILE="${OCI_CONFIG_PROFILE:-DEFAULT}"
  export OCI_EAI_CONFIG_PROFILE="${OCI_EAI_CONFIG_PROFILE:-$OCI_CONFIG_PROFILE}"
fi

if [ "${1:-}" = "python" ] && [ "${2:-}" = "hosted_app.py" ]; then
  python /app/mcp_servers/ocr_server.py &
  python -m agent_runners.document_ingest &
  python -m agent_runners.document_query &
  python -m agent_runners.web_search &
  python -m agent_runners.comparison &
fi

exec "$@"
