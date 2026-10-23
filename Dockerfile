FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Install the shared dependencies for the API, hybrid agent, and both MCP servers.
COPY requirements.txt ./requirements.txt
RUN python -m pip install --upgrade pip && pip install -r requirements.txt

COPY hybrid_agent ./hybrid_agent
COPY local_mcp_server ./local_mcp_server
COPY cloud_mcp_server ./cloud_mcp_server
COPY ui ./ui
COPY sample_patient_record.txt ./sample_patient_record.txt
COPY deploy/aws/entrypoint.sh ./deploy/aws/entrypoint.sh

# Persistent state is mounted at /data by Compose/Kubernetes.
RUN useradd --create-home --uid 10001 appuser \
    && mkdir -p /data \
    && chmod +x /app/deploy/aws/entrypoint.sh \
    && chown -R appuser:appuser /app /data

USER appuser

EXPOSE 7860 8000
CMD ["/app/deploy/aws/entrypoint.sh"]
