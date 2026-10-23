#!/bin/sh
# Run the cloud MCP server and the web API in one container for ECS Express Mode.
set -eu

export CLOUD_MCP_HOST="${CLOUD_MCP_HOST:-127.0.0.1}"
export CLOUD_MCP_PORT="${CLOUD_MCP_PORT:-8000}"
export CLOUD_SERVER_URL="${CLOUD_SERVER_URL:-http://127.0.0.1:8000/mcp}"
export UI_HOST="${UI_HOST:-0.0.0.0}"
export UI_PORT="${UI_PORT:-7860}"
export LOCAL_DB_PATH="${LOCAL_DB_PATH:-/data/local_cache.db}"

mkdir -p "$(dirname "$LOCAL_DB_PATH")"

python -m cloud_mcp_server.server &
CLOUD_PID=$!

cleanup() {
    kill "$CLOUD_PID" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

# Wait for the MCP server to accept connections before starting FastAPI.
attempt=0
until python -c "import socket; s=socket.create_connection(('127.0.0.1', int('$CLOUD_MCP_PORT')), 2); s.close()" >/dev/null 2>&1; do
    attempt=$((attempt + 1))
    if [ "$attempt" -ge 30 ]; then
        echo "Cloud MCP server did not start within 30 seconds." >&2
        exit 1
    fi
    sleep 1
done

python -m hybrid_agent.api &
API_PID=$!

# Keep the container alive while the API runs. Stop the MCP child on exit.
wait "$API_PID"
