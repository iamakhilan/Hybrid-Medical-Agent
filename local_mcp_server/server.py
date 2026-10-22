"""
Local MCP Server — the "FAST RESPONSE" side of the hybrid agent.

Transport: stdio (this process is meant to be spawned as a subprocess by
the hybrid agent, not run as a standalone network service). That matches
the architecture: the local side never needs to be reachable over a
network, so stdio keeps it simple and dependency-free.

Run directly for manual testing:
    python -m local_mcp_server.server

Exposed MCP tools:
    local_health_check()               -> liveness check
    cache_document(doc_id, title, content, source="local")
                                        -> ingest text into the local cache
    search_local_cache(query, top_k=5) -> BM25 keyword search over the cache
    ask_local(question, top_k=5)       -> search + local LLM answer

Exposed MCP resource:
    cache://docs/{doc_id}              -> full text of a cached document
"""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from local_mcp_server import config, db, local_llm

mcp = FastMCP("local-doc-cache")


@mcp.tool()
def local_health_check() -> str:
    """Report that the local MCP server is up, and whether an Ollama/local
    LLM is loaded or the extractive fallback is active."""
    mode = local_llm.get_model_mode_description()
    return f"Local MCP Server is running (answer mode: {mode})"



@mcp.tool()
def cache_document(doc_id: str, title: str, content: str, source: str = "local") -> str:
    """Store or update a document's text in the local cache so it becomes
    instantly searchable offline. `source` is a free-text label, e.g.
    'local' for something typed/scanned locally, or 'cloud-sync' for text
    that was pulled down after a deep cloud extraction."""
    db.upsert_document(doc_id=doc_id, title=title, content=content, source=source)
    return f"Cached document '{doc_id}' ({len(content)} chars) under source='{source}'."


@mcp.tool()
def search_local_cache(query: str, top_k: int = config.DEFAULT_TOP_K) -> list[dict]:
    """Fast keyword (BM25) search over locally cached documents. Returns
    a list of {doc_id, title, snippet, score}, best match first."""
    results = db.search(query, top_k=top_k)
    return [r.__dict__ for r in results]


@mcp.tool()
def ask_local(question: str, top_k: int = config.DEFAULT_TOP_K) -> str:
    """Answer a question quickly using only what's already cached locally.
    This is the low-latency path: no network call, so it always returns
    fast -- but it only knows what has been cached via cache_document (or
    synced down from the cloud side)."""
    chunks = db.search(question, top_k=top_k)
    return local_llm.generate(question, chunks)


@mcp.tool()
def list_cached_documents(limit: int = 50) -> str:
    """List cached document metadata as JSON for the agent/API layer."""
    import json
    return json.dumps(db.list_documents(limit=max(1, min(limit, 200))))


@mcp.tool()
def get_cached_document(doc_id: str) -> str:
    """Fetch the full text of a cached document by id through the MCP boundary."""
    doc = db.get_document(doc_id)
    if doc is None:
        raise ValueError(f"Cached document '{doc_id}' was not found.")
    return doc["content"]


@mcp.resource("cache://docs/{doc_id}")
def get_cached_document_resource(doc_id: str) -> str:
    """Return the full cached text of a document by id."""
    doc = db.get_document(doc_id)
    if doc is None:
        return f"No cached document with id '{doc_id}'."
    return doc["content"]


def main() -> None:
    db.init_db()
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
