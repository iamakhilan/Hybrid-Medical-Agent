"""
Cloud MCP Server — the "DEEP RESPONSE" side of the hybrid agent.

Transport: Streamable HTTP (the current recommended transport for new MCP
servers; SSE is being superseded). This process runs as a standalone HTTP
service. In Phase 1 that's "on your PC, talking out to Qdrant Cloud +
Gemini." Later, this same file is what you containerize and deploy --
nothing about the MCP surface changes, only where the process runs.

Run directly:
    python -m cloud_mcp_server.server
    -> serves http://127.0.0.1:8000/mcp  (configurable via .env)

Exposed MCP tools, in the order you'll actually build/test them:
    cloud_health_check()                        -> liveness check, no API keys needed
    ingest_document(doc_id, title, text)         -> extract -> chunk -> embed -> store in Qdrant
    search_cloud_documents(query, top_k)         -> semantic search only, returns raw hits
    fetch_deep_cloud_context(query, top_k)       -> semantic search, formatted as context for an agent/LLM
    analyze_document(text, question)             -> deep analysis of a whole document
    summarize_document(text, focus="")           -> condense a document
    answer_with_cloud_context(question, top_k)   -> full RAG: search -> Gemini reasoning
"""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from cloud_mcp_server import config, document_processor, embedding_service, gemini_service, qdrant_service

mcp = FastMCP(
    "cloud-deep-context",
    host=config.CLOUD_MCP_HOST,
    port=config.CLOUD_MCP_PORT,
)


@mcp.tool()
def cloud_health_check() -> str:
    """Report that the cloud MCP server is running, and which backing
    services (Gemini, Qdrant) currently have credentials configured. Safe
    to call with no API keys set -- useful as the very first milestone
    when standing this server up."""
    gemini_status = "configured" if config.gemini_configured() else "NOT configured (set GEMINI_API_KEY)"
    qdrant_status = "configured" if config.qdrant_configured() else "NOT configured (set QDRANT_URL)"
    return (
        "Cloud MCP Server is running. "
        f"Gemini: {gemini_status}. Qdrant: {qdrant_status} "
        f"(collection='{config.QDRANT_COLLECTION}')."
    )


@mcp.tool()
def ingest_document(doc_id: str, title: str, text: str, deep_extraction: bool = False) -> str:
    """Run the cloud ingestion pipeline on a document's raw text:
    chunking, Gemini embeddings, then upsert into Qdrant. Call this once per
    document before searching/reasoning over it. Requires GEMINI_API_KEY
    and QDRANT_URL to be set."""
    working_text = text
    warnings: list[str] = []
    if deep_extraction:
        extraction = gemini_service.extract_document(text, title=title)
        working_text = extraction.text
        warnings = extraction.warnings

    chunks = document_processor.chunk_text(doc_id=doc_id, text=working_text, title=title)
    if not chunks:
        # An empty replacement must not leave chunks from an older version
        # searchable in Qdrant.
        qdrant_service.delete_document_chunks(doc_id)
        return f"Cleared cloud chunks for '{doc_id}': document text was empty."

    chunks_with_vectors = embedding_service.embed_chunks(chunks)
    count = qdrant_service.upsert_chunks(chunks_with_vectors)

    msg = f"Ingested '{doc_id}' ({title}): {count} chunk(s) embedded and stored in Qdrant."
    if warnings:
        msg += f" Warnings: {'; '.join(warnings)}"
    return msg


@mcp.tool()
def search_cloud_documents(query: str, top_k: int = config.DEFAULT_TOP_K) -> list[dict]:
    """Semantic (embedding) search over cloud-ingested documents. Returns
    raw hits as a list of {doc_id, title, chunk_index, text, score},
    best match first. Use this when you want the matches themselves;
    use fetch_deep_cloud_context or answer_with_cloud_context when you
    want them turned into an answer."""
    query_vector = embedding_service.embed_query(query)
    hits = qdrant_service.search(query_vector, top_k=top_k)
    return [h.__dict__ for h in hits]


@mcp.tool()
def fetch_deep_cloud_context(query: str, top_k: int = config.DEFAULT_TOP_K) -> str:
    """Semantic search formatted as a single context block, ready to hand
    to an LLM (local or cloud). This is the tool the hybrid agent calls
    when it decides a query needs 'deep' context beyond what's cached
    locally."""
    query_vector = embedding_service.embed_query(query)
    hits = qdrant_service.search(query_vector, top_k=top_k)
    if not hits:
        return "No relevant documents found in the cloud vector store."
    parts = [f"[{h.title or h.doc_id} · chunk {h.chunk_index} · score {h.score:.3f}]\n{h.text}" for h in hits]
    return "\n\n---\n\n".join(parts)


@mcp.tool()
def analyze_document(text: str, question: str) -> str:
    """Deep, open-ended analysis of a full document against a specific
    question (e.g. 'identify the progression of the disease, compare
    previous treatments, extract lab values'). Pass the document text
    directly -- this does not require the document to have been ingested
    into Qdrant first."""
    return gemini_service.analyze(text, question)


@mcp.tool()
def summarize_document(text: str, focus: str = "") -> str:
    """Condense a document (or a block of retrieved context) into a
    clinician-facing summary. Optionally pass `focus` to steer it toward
    a specific aspect (e.g. 'medication history')."""
    return gemini_service.summarize(text, focus=focus)


@mcp.tool()
def answer_with_cloud_context(question: str, top_k: int = config.DEFAULT_TOP_K) -> str:
    """Full deep-RAG pipeline: semantic search in Qdrant, then Gemini
    reasoning grounded in retrieved cloud chunks. If no relevant cloud chunks are
    available, returns a clear no-context result; the hybrid agent handles local fallback."""
    context_chunks = []
    try:
        query_vector = embedding_service.embed_query(question)
        hits = qdrant_service.search(query_vector, top_k=top_k)
        context_chunks = [f"[{h.title or h.doc_id}]\n{h.text}" for h in hits]
    except Exception:
        context_chunks = []

    if not context_chunks:
        # Keep the cloud server independent of the edge cache. The hybrid
        # agent is the only MCP client and chooses the response tier.
        return "No relevant documents found in the cloud vector store. Ingest the document into the cloud store or use the local cache result."

    return gemini_service.reason_over_context(question, context_chunks)




def main() -> None:
    mcp.run(transport="streamable-http")


if __name__ == "__main__":
    main()
