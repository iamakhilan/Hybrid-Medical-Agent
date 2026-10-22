"""
Configuration for the Cloud MCP Server.

Per the phased plan: only the *data/model services* (Gemini, Qdrant) are
actually remote. This server process itself runs wherever you start it --
your PC today, a container later -- unchanged either way, because it only
talks to Gemini/Qdrant over their HTTPS APIs.

All secrets come from environment variables (see ../.env.example). Never
hardcode keys here.
"""

import os

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

# --- Gemini -----------------------------------------------------------
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
GEMINI_EMBEDDING_MODEL = os.environ.get("GEMINI_EMBEDDING_MODEL", "text-embedding-004")
GEMINI_EMBEDDING_DIM = int(os.environ.get("GEMINI_EMBEDDING_DIM", "768"))

# --- Qdrant -------------------------------------------------------------
QDRANT_URL = os.environ.get("QDRANT_URL", "")
QDRANT_API_KEY = os.environ.get("QDRANT_API_KEY", "")
QDRANT_COLLECTION = os.environ.get("QDRANT_COLLECTION", "medical_documents")

# --- Chunking -------------------------------------------------------------
CHUNK_SIZE_WORDS = int(os.environ.get("CHUNK_SIZE_WORDS", "220"))
CHUNK_OVERLAP_WORDS = int(os.environ.get("CHUNK_OVERLAP_WORDS", "40"))

# --- Server (Streamable HTTP) ---------------------------------------------
CLOUD_MCP_HOST = os.environ.get("CLOUD_MCP_HOST", "127.0.0.1")
CLOUD_MCP_PORT = int(os.environ.get("CLOUD_MCP_PORT", "8000"))

# --- Search defaults ---------------------------------------------------
DEFAULT_TOP_K = int(os.environ.get("CLOUD_DEFAULT_TOP_K", "5"))


def gemini_configured() -> bool:
    return bool(GEMINI_API_KEY)


def qdrant_configured() -> bool:
    return bool(QDRANT_URL)
