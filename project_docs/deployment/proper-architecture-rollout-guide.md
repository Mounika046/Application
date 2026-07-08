# Proper Architecture Rollout Guide

This guide is the operational runbook for the split deployment path.

Use the same image for every workload, but deploy each workload with its own command, port, and environment.

## Deployment order

1. Deploy `ocr-server`
2. Deploy `document-ingest-agent`
3. Deploy `kb-query-agent`
4. Deploy `web-search-agent`
5. Deploy `comparison-agent`
6. Deploy `hosted-app`

## Commands by workload

- `ocr-server`: `python mcp_servers/ocr_server.py`
- `document-ingest-agent`: `python -m agents.document_ingest_agent`
- `kb-query-agent`: `python -m agents.document_query_agent`
- `web-search-agent`: `python -m agents.web_search_agent`
- `comparison-agent`: `python -m agents.comparison_agent`
- `hosted-app`: `python hosted_app.py`

## Environment templates

Use these files as the starting point:

- support services: `project_docs/deployment/proper-architecture-services.env.example`
- hosted app: `project_docs/deployment/proper-architecture-hosted-app.env.example`

## URL wiring

Once the support services are deployed, put their reachable base URLs into the hosted app environment:

- `DOC_INGEST_AGENT_URL`
- `DOCUMENT_QUERY_AGENT_URL`
- `WEB_SEARCH_AGENT_URL`
- `COMPARISON_AGENT_URL`

Put the OCR MCP URL into the document-ingest agent environment:

- `OCR_MCP_SERVER_URL=https://<ocr-service>/mcp`

## Validation gates

Do not move to the next workload until the current one passes its gate.

The repo-level validation scripts were removed during runtime cleanup. Use the manual endpoint checks below for rollout validation.

### OCR server

- confirm the service is up
- confirm the MCP URL ends with `/mcp`

### A2A agents

- confirm `/health` returns success
- confirm each agent advertises the expected public URL

### Hosted app

- confirm `/health` returns success
- confirm `/` serves the web UI
- confirm `/sessions` returns a conversation id
- confirm `/invoke/ocr` accepts a small test file
- confirm `/invoke/docqa` accepts a small test file and question
- confirm `/invoke/compare` accepts two small test files

## Current shared-file contract

The proper-architecture path currently uses inline `file_ref` payloads between the hosted app and downstream services.

That means:

- no shared local disk is required between the hosted app and the agents
- uploads should stay within the configured inline size limit
- this is suitable for small and medium demo documents, not large production files

## Current status in this repo

The codebase is already prepared for this rollout:

- hosted upload endpoints exist
- route hints exist for upload-first flows
- OCR supports explicit `OCR_MCP_SERVER_URL`
- the web UI already uses the new upload endpoints
