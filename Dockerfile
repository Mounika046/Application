FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

EXPOSE 8080

RUN apt-get update && apt-get install -y --no-install-recommends \
    poppler-utils \
    tesseract-ocr \
    libgl1 \
    libglib2.0-0 \
    libgomp1 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /app/requirements.txt

RUN pip install --upgrade pip && pip install -r /app/requirements.txt

COPY agents /app/agents
COPY agntcy /app/agntcy
COPY docker /app/docker
COPY mcp_clients /app/mcp_clients
COPY mcp_servers /app/mcp_servers
COPY oracle_enterprise_ai /app/oracle_enterprise_ai
COPY demo_files /app/demo_files
COPY web_ui /app/web_ui
COPY config.py /app/config.py
COPY file_input.py /app/file_input.py
COPY flow_trace.py /app/flow_trace.py
COPY graph_app.py /app/graph_app.py
COPY hosted_app.py /app/hosted_app.py
COPY mcp_registry.json /app/mcp_registry.json
COPY mcp_registry.json /app/mcp_registry.local.json
COPY mcp_registry.json /app/mcp_registry.docker.json
COPY mcp_registry.json /app/mcp_registry.single.json
COPY agent_runners /app/agent_runners
COPY session_memory.py /app/session_memory.py
COPY state.py /app/state.py

ENTRYPOINT ["sh", "/app/docker/entrypoint.sh"]
CMD ["python", "hosted_app.py"]
