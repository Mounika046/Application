# Separate Services Deployment Guide

This guide is for the proper-architecture deployment path where the support services are deployed separately and wired through URLs.

Quick start templates:

- support services env: `project_docs/deployment/proper-architecture-services.env.example`
- hosted app env: `project_docs/deployment/proper-architecture-hosted-app.env.example`
- rollout order and validation: `project_docs/deployment/proper-architecture-rollout-guide.md`

## Services In The Split Runtime

Deploy these services as separate containers:

- `ocr-server`
- `document-ingest-agent`
- `kb-query-agent`
- `web-search-agent`
- `comparison-agent`
- `hosted-app`

The public entrypoint should be:

- `hosted-app`

The support services should stay private/internal where possible.

## Current Wiring Model

The application now supports this split-service model directly:

- A2A support agents are reached by explicit URL environment variables
- OCR is reached by `OCR_MCP_SERVER_URL`
- uploaded browser files are passed through the hosted app as inline `file_ref` payloads

That means the support services do not need shared local disk with the hosted app.

## Container Commands

Use the same image, but change the command per deployed service.

### OCR server

```text
python mcp_servers/ocr_server.py
```

### Document ingest agent

```text
python -m agents.document_ingest_agent
```

### Document query agent

```text
python -m agents.document_query_agent
```

### Web search agent

```text
python -m agents.web_search_agent
```

### Comparison agent

```text
python -m agents.comparison_agent
```

### Hosted app

```text
python hosted_app.py
```

## Minimum Environment By Service

### OCR server

Required:

- `OCR_MCP_HOST=0.0.0.0`
- `OCR_MCP_PORT=8011`
- `POPPLER_PATH` if needed for the target runtime

### Document ingest agent

Required:

- `DOC_INGEST_AGENT_HOST=0.0.0.0`
- `DOC_INGEST_AGENT_PORT=8031`
- `DOC_INGEST_AGENT_PUBLIC_URL=<public-or-internal-agent-url>`
- `OCR_MCP_SERVER_URL=<ocr-server-mcp-url>`
- `OCI_EAI_ENABLED=true`
- `OCI_EAI_BASE_URL`
- `OCI_EAI_PROJECT_OCID`
- `OCI_EAI_COMPARTMENT_ID`
- `OCI_EAI_VECTOR_STORE_ID`
- `OCI_EAI_AUTH_MODE=resource_principal`

Alternative when hosted-app resource principal cannot be granted OCI access:

- `OCI_EAI_AUTH_MODE=user`
- `OCI_EAI_USER_TENANCY_OCID`
- `OCI_EAI_USER_OCID`
- `OCI_EAI_USER_FINGERPRINT`
- `OCI_EAI_USER_PRIVATE_KEY_PEM`
- `OCI_EAI_CONFIG_PROFILE=DEFAULT`

### Document query agent

Required:

- `DOCUMENT_QUERY_AGENT_HOST=0.0.0.0`
- `DOCUMENT_QUERY_AGENT_PORT=8032`
- `DOCUMENT_QUERY_AGENT_PUBLIC_URL=<public-or-internal-agent-url>`
- `OCI_EAI_ENABLED=true`
- `OCI_EAI_BASE_URL`
- `OCI_EAI_PROJECT_OCID`
- `OCI_EAI_COMPARTMENT_ID`
- `OCI_EAI_VECTOR_STORE_ID`
- `OCI_EAI_AUTH_MODE=resource_principal`

### Web search agent

Required:

- `WEB_SEARCH_AGENT_HOST=0.0.0.0`
- `WEB_SEARCH_AGENT_PORT=8033`
- `WEB_SEARCH_AGENT_PUBLIC_URL=<public-or-internal-agent-url>`
- `SERPAPI_API_KEY`
- `OCI_EAI_ENABLED=true`
- `OCI_EAI_BASE_URL`
- `OCI_EAI_PROJECT_OCID`
- `OCI_EAI_COMPARTMENT_ID`
- `OCI_EAI_AUTH_MODE=resource_principal`

### Comparison agent

Required:

- `COMPARISON_AGENT_HOST=0.0.0.0`
- `COMPARISON_AGENT_PORT=8034`
- `COMPARISON_AGENT_PUBLIC_URL=<public-or-internal-agent-url>`
- `OCI_EAI_ENABLED=true`
- `OCI_EAI_BASE_URL`
- `OCI_EAI_PROJECT_OCID`
- `OCI_EAI_COMPARTMENT_ID`
- `OCI_EAI_AUTH_MODE=resource_principal`

Optional but useful:

- `OCI_EAI_COMPARE_CODE_INTERPRETER_ENABLED=true`
- `OCI_EAI_COMPARE_CODE_INTERPRETER_MEMORY=1g`
- guardrails env values

### Hosted app

Required:

- `HOSTED_APP_HOST=0.0.0.0`
- `HOSTED_APP_PORT=8080`
- `DOC_INGEST_AGENT_URL=<document-ingest-agent-url>`
- `DOCUMENT_QUERY_AGENT_URL=<kb-query-agent-url>`
- `WEB_SEARCH_AGENT_URL=<web-search-agent-url>`
- `COMPARISON_AGENT_URL=<comparison-agent-url>`
- `OCI_EAI_ENABLED=true`
- `OCI_EAI_BASE_URL`
- `OCI_EAI_PROJECT_OCID`
- `OCI_EAI_COMPARTMENT_ID`
- `OCI_EAI_AUTH_MODE=resource_principal`

Recommended while validating split deployment:

- `AGNTCY_DIRECTORY_DISCOVERY_ENABLED=false`
- `AGNTCY_IDENTITY_VERIFY_ENABLED=false`
- `SHARED_FILE_REF_STRATEGY=inline`
- `SHARED_FILE_INLINE_MAX_BYTES=5242880`

## URL Wiring Summary

Use the support service URLs like this:

- `OCR_MCP_SERVER_URL=https://<ocr-service>/mcp`
- `DOC_INGEST_AGENT_URL=https://<document-ingest-agent>`
- `DOCUMENT_QUERY_AGENT_URL=https://<kb-query-agent>`
- `WEB_SEARCH_AGENT_URL=https://<web-search-agent>`
- `COMPARISON_AGENT_URL=https://<comparison-agent>`

For A2A agents, the configured URL should point at the agent base URL, not a separate custom path.

## Recommended Deployment Order

1. Deploy `ocr-server`
2. Deploy `document-ingest-agent`
3. Deploy `kb-query-agent`
4. Deploy `web-search-agent`
5. Deploy `comparison-agent`
6. Deploy `hosted-app`

## Validation Order

Check each support service first:

- `ocr-server` is reachable on its MCP URL
- each A2A agent responds on `/health`

Then check the hosted app:

- `/health`
- `/sessions`
- `/invoke`
- `/invoke/ocr`
- `/invoke/docqa`
- `/invoke/compare`

## Current Practical Mode

The split architecture is now prepared for:

- separate support-service deployments
- explicit URL wiring
- uploaded browser files flowing through `file_ref` instead of local shared paths

The next rollout step after this guide is to deploy the support services one by one and wire their URLs into the hosted app deployment.
