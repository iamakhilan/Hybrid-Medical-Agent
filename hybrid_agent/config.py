"""
Configuration for the Hybrid Agent (the MCP client / router).

LOCAL_SERVER_COMMAND / LOCAL_SERVER_ARGS describe how to *launch* the
local MCP server as a subprocess (stdio transport) -- the agent starts
and owns that process's lifetime.

CLOUD_SERVER_URL is where an *already-running* cloud MCP server is
listening (Streamable HTTP) -- the agent connects to it as a client, it
does not launch it.
"""

import os
import sys

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

# --- Local MCP server (stdio, spawned by this agent) -----------------
# Defaults to running the local server as `python -m local_mcp_server.server`
# using the SAME interpreter the agent itself is running under, which
# avoids the classic "works in my venv, breaks as a subprocess" trap.
LOCAL_SERVER_COMMAND = os.environ.get("LOCAL_SERVER_COMMAND", sys.executable)
LOCAL_SERVER_ARGS = os.environ.get("LOCAL_SERVER_ARGS", "-m local_mcp_server.server").split()

# --- Cloud MCP server (Streamable HTTP, already running) -----------------
CLOUD_SERVER_URL = os.environ.get("CLOUD_SERVER_URL", "http://127.0.0.1:8000/mcp")

# How long to wait on the cloud call before falling back to local-only.
# Keep this well under what a doctor would tolerate staring at a spinner.
CLOUD_TIMEOUT_SECONDS = float(os.environ.get("CLOUD_TIMEOUT_SECONDS", "20"))

DEFAULT_TOP_K = int(os.environ.get("HYBRID_DEFAULT_TOP_K", "5"))
