## Agent Runners

This folder contains the tiny startup wrappers for the internal agent services used by the hosted application.

- `document_ingest.py`: starts the document ingest agent
- `document_query.py`: starts the document query agent
- `web_search.py`: starts the web search agent
- `comparison.py`: starts the comparison agent

These files do not contain the business logic themselves. They only call the corresponding `main()` function from the real agent modules under `agents/`.
