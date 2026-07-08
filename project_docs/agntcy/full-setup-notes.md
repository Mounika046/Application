# AGNTCY Setup Notes

This is the single setup notes file for the AGNTCY work in this application.

## Current Runtime Shape

The running application now uses these pieces:

- `main.py`
- `hosted_app.py`
- `graph_app.py`
- `agents/orchestrator_agent.py`
- `agents/document_ingest_agent.py`
- `agents/document_query_agent.py`
- `agents/web_search_agent.py`
- `agents/comparison_agent.py`
- `mcp_servers/ocr_server.py`
- `oracle_enterprise_ai/retrieval_client.py`
- `oracle_enterprise_ai/responses_client.py`
- `agntcy/subjects.json`

Important note:

- some published AGNTCY record ids still use stable external names like `kb-query-agent`
- that is intentional so the already-created AGNTCY identity and directory records do not break
- inside the app code, the runtime naming is now document-based and easier to follow

## Current Flow

At runtime:

1. the orchestrator reads the user input
2. it decides whether the request is:
   - document OCR
   - document question
   - web question
   - document comparison
3. it asks AGNTCY Directory for the right A2A agent by capability
4. it verifies the discovered agent identity using the hosted AGNTCY identity record
5. it calls the selected agent over A2A
6. document text is stored in the Oracle Enterprise AI vector store
7. document questions are answered from vector-store retrieval
8. general questions use the web-search agent
9. comparison requests use the comparison agent

## Current Services

Docker services used by the application:

- `ocr-server`
- `document-ingest-agent`
- `kb-query-agent`
- `web-search-agent`
- `comparison-agent`
- `app`
- `hosted-app`

Notes:

- `ocr-server` is the MCP server used for OCR extraction
- `kb-query-agent` is still the external service name, but its runtime module is now `agents/document_query_agent.py`
- the old local web MCP server was removed
- web search now uses hosted SerpApi MCP through Oracle Enterprise AI remote MCP

## Document Flow

For `ocr "path"`:

1. orchestrator routes to `document`
2. document ingest agent is discovered through AGNTCY
3. identity is verified
4. OCR is done through `ocr-server`
5. extracted text is uploaded into Oracle Enterprise AI
6. the text file is attached to the configured shared vector store
7. OCR text is returned to the user

For `docqa "path" | question`:

1. orchestrator routes to `document_question`
2. document ingest agent runs first
3. OCR text is stored in the Oracle Enterprise AI vector store
4. document query agent is discovered through AGNTCY
5. identity is verified
6. vector-store search is used to retrieve matching chunks
7. grounded answer is returned directly from retrieved content

## Web Flow

For a normal web question:

1. orchestrator routes to `question`
2. if there is no document retrieval context, it discovers the web-search agent
3. identity is verified
4. the web-search agent uses Oracle Enterprise AI remote MCP
5. hosted SerpApi MCP returns grounded live web results
6. the grounded web result is returned to the user

## Comparison Flow

For `compare "left" with "right"`:

1. orchestrator routes to `compare_documents`
2. both documents are ingested through the document ingest agent
3. OCR text is extracted for both
4. comparison agent is discovered through AGNTCY
5. identity is verified
6. the comparison agent runs structured comparison using Oracle Enterprise AI
7. the result is returned as summary, similarities, differences, additions, removals, and risks

## AGNTCY Identity Notes

The application verifies discovered agents through the hosted AGNTCY identity resolver ids stored in the OASF records.

Examples used in this app setup:

- `document-ingest-agent`
- `kb-query-agent`
- `web-search-agent`
- `comparison-agent`

For hosted agentic services:

- creating the service alone is not enough
- the hosted badge must also be created and published
- until that is done, hosted verification can fail with `404`

When creating the hosted badge through `identity-cli`:

- paste the service API key without quotes
- terminal input may remain hidden while pasting

## Oracle Enterprise AI Notes

The application currently uses Oracle Enterprise AI for:

- Responses API
- Conversations
- remote MCP
- vector-store file attach and search
- code interpreter in comparison flow
- embeddings-based similarity in comparison flow
- guardrails checks around comparison input/output

The document storage path now uses:

- uploaded OCR text as a temporary `.txt` file
- Files API upload
- vector store file attach
- wait for file readiness
- vector store search for retrieval

The application no longer uses the old database-backed retrieval path.

## Current Important Config

Main values that must be set in `.env`:

- `OCI_EAI_ENABLED=true`
- `OCI_EAI_BASE_URL`
- `OCI_EAI_PROJECT_OCID`
- `OCI_EAI_COMPARTMENT_ID`
- `OCI_EAI_MODEL`
- `OCI_EAI_VECTOR_STORE_ID`
- `AGNTCY_DIRECTORY_DISCOVERY_ENABLED=true`
- `AGNTCY_DIRECTORY_SERVER_ADDR`
- `AGNTCY_IDENTITY_ORG_API_KEY`
- `SERPAPI_API_KEY`

For local Docker on this machine:

- `HOST_FILES_DIR`
- `OCI_HOST_DIR`

## Validation Commands

Useful local checks:

```powershell
docker-compose up -d --build --remove-orphans
docker ps
Invoke-RestMethod -Method Get -Uri http://127.0.0.1:8080/health
```

Example hosted app checks:

```powershell
$body = @{ user_input = 'ocr "/app/demo_files/basic-text.pdf"'; return_state = $true } | ConvertTo-Json -Depth 5
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8080/invoke -ContentType 'application/json' -Body $body
```

```powershell
$body = @{ user_input = 'docqa "/app/demo_files/basic-text.pdf" | what are the first and last lines in the document?'; return_state = $true } | ConvertTo-Json -Depth 5
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8080/invoke -ContentType 'application/json' -Body $body
```

```powershell
$body = @{ user_input = 'compare "/app/demo_files/basic-text.pdf" with "/app/demo_files/testing.pdf"'; return_state = $true } | ConvertTo-Json -Depth 5
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8080/invoke -ContentType 'application/json' -Body $body
```

```powershell
$body = @{ user_input = 'latest weather in Hyderabad'; return_state = $true } | ConvertTo-Json -Depth 5
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8080/invoke -ContentType 'application/json' -Body $body
```
