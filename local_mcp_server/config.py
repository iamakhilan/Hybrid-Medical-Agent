"""
Configuration for the Local MCP Server.

Everything here has a safe default so the server runs out of the box with
zero setup. The only thing worth changing early on is LOCAL_LLM_MODEL_PATH,
if/when you plug in a real local model (Phi-3, Llama-3-8B-Instruct-4bit, etc.
via llama-cpp-python).
"""

import os
from pathlib import Path

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    # python-dotenv is optional; env vars can also be set directly.
    pass

# --- Storage -----------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent
# Use 'or' so that an empty string in .env (LOCAL_DB_PATH=) also falls
# back to the default, instead of passing "" to sqlite3.connect() which
# creates a temporary database that disappears after each connection.
LOCAL_DB_PATH = os.environ.get("LOCAL_DB_PATH") or str(BASE_DIR / "local_cache.db")

# --- Local LLM -----------------------------------------------------------
# If LOCAL_LLM_MODEL_PATH is unset (or llama-cpp-python isn't installed),
# the server falls back to a lightweight extractive "answer" built from the
# top matching cached chunks. That keeps `ask_local` usable on day one,
# with a real local model as a drop-in upgrade later.
LOCAL_LLM_MODEL_PATH = os.environ.get("LOCAL_LLM_MODEL_PATH", "")
LOCAL_LLM_CONTEXT_WINDOW = int(os.environ.get("LOCAL_LLM_CONTEXT_WINDOW", "4096"))
LOCAL_LLM_MAX_TOKENS = int(os.environ.get("LOCAL_LLM_MAX_TOKENS", "512"))

# --- Ollama Local LLM ---------------------------------------------------
OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "llama3.2:1b")



# --- Search ---------------------------------------------------------------
DEFAULT_TOP_K = int(os.environ.get("LOCAL_DEFAULT_TOP_K", "5"))
