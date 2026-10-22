"""
FastAPI REST layer — the bridge between the browser UI and the HybridAgent.

This module wraps the existing HybridAgent class in a proper HTTP API so
the Doctor's UI (or any other client) never needs to talk to the MCP
servers directly.

Run:
    python -m hybrid_agent.api
    → http://127.0.0.1:7860  (UI)
    → http://127.0.0.1:7860/docs  (auto-generated API docs)

The cloud MCP server must already be running on port 8000 (or whatever
CLOUD_SERVER_URL is set to in .env).
"""

from __future__ import annotations

import os
import re
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from hybrid_agent.agent import HybridAgent
from hybrid_agent.api_models import (
    AskRequest,
    AskResponse,
    AnalyzeRequest,
    DocumentInfo,
    HealthResponse,
    IngestResponse,
    SourceInfo,
    SummarizeRequest,
    TextResponse,
)
from hybrid_agent.pdf_utils import extract_text

# ---------------------------------------------------------------------------
# Application lifecycle — one HybridAgent for the entire server lifetime
# ---------------------------------------------------------------------------

_agent: HybridAgent | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _agent
    async with HybridAgent() as agent:
        _agent = agent
        yield
    _agent = None


def get_agent() -> HybridAgent:
    if _agent is None:
        raise HTTPException(status_code=503, detail="Agent not ready yet.")
    return _agent


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI(
    title="Hybrid Medical Agent API",
    description="REST interface for the local+cloud hybrid medical document agent.",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    # The UI is served by this same FastAPI origin. Keep the default CORS
    # allowlist local-only rather than exposing the API to arbitrary sites.
    allow_origins=[
        "http://127.0.0.1:7860",
        "http://localhost:7860",
    ],
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)

# ---------------------------------------------------------------------------
# API routes
# ---------------------------------------------------------------------------


@app.get("/api/health", response_model=HealthResponse, tags=["System"])
async def health():
    """Check whether the local and cloud MCP servers are reachable."""
    agent = get_agent()
    local_result, cloud_result = await agent.health_check()
    return HealthResponse(
        local_ok=local_result.ok,
        local_message=local_result.text if local_result.ok else local_result.error,
        cloud_ok=cloud_result.ok,
        cloud_message=cloud_result.text if cloud_result.ok else cloud_result.error,
    )


@app.post("/api/ask", response_model=AskResponse, tags=["Q&A"])
async def ask(req: AskRequest):
    """Ask a clinical question. Fires local + cloud in parallel and returns the merged answer."""
    agent = get_agent()
    response = await agent.ask(req.question, top_k=req.top_k)
    return AskResponse(
        answer=response.merged_text,
        used_cloud=response.used_cloud,
        local=SourceInfo(
            ok=response.local.ok,
            latency_seconds=response.local.latency_seconds,
            error=response.local.error,
        ),
        cloud=SourceInfo(
            ok=response.cloud.ok,
            latency_seconds=response.cloud.latency_seconds,
            error=response.cloud.error,
        ),
        query=response.query,
    )


@app.post("/api/documents", response_model=IngestResponse, tags=["Documents"])
async def upload_document(
    file: UploadFile = File(...),
    title: str = Form(""),
):
    """
    Upload a document (PDF or plain text).
    The text is extracted, cached locally (instant search), and ingested
    into the cloud vector store (semantic search).
    """
    agent = get_agent()
    filename = file.filename or "upload.txt"
    if Path(filename).suffix.lower() not in {".pdf", ".txt", ".md"}:
        raise HTTPException(status_code=415, detail="Supported file types are PDF, TXT, and MD.")

    max_upload_bytes = 20 * 1024 * 1024
    # Read in bounded chunks and stop as soon as the upload exceeds the limit.
    # This avoids loading an arbitrarily large upload into application memory.
    file_bytes = bytearray()
    read_chunk_size = 1024 * 1024
    while len(file_bytes) <= max_upload_bytes:
        remaining = max_upload_bytes + 1 - len(file_bytes)
        chunk = await file.read(min(read_chunk_size, remaining))
        if not chunk:
            break
        file_bytes.extend(chunk)

    if len(file_bytes) > max_upload_bytes:
        raise HTTPException(status_code=413, detail="File exceeds the 20 MB upload limit.")

    try:
        text = extract_text(filename, bytes(file_bytes))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    # Derive a stable, deterministic doc_id from the filename so that
    # re-uploading the same file updates the existing entry instead of
    # creating a new duplicate each time.
    safe_name = re.sub(r"[^a-zA-Z0-9_-]", "_", Path(file.filename or "doc").stem)
    doc_id = safe_name
    doc_title = title.strip() or Path(file.filename or "Untitled").stem

    # Cache locally (always)
    local_res = await agent.cache_document_locally(
        doc_id=doc_id, title=doc_title, content=text
    )

    # Ingest to cloud (best-effort)
    cloud_res = await agent.ingest_document_to_cloud(
        doc_id=doc_id, title=doc_title, text=text
    )

    return IngestResponse(
        doc_id=doc_id,
        title=doc_title,
        local_result=local_res.text if local_res.ok else f"Error: {local_res.error}",
        cloud_result=cloud_res.text if cloud_res.ok else f"Error: {cloud_res.error}",
    )


@app.get("/api/documents", response_model=list[DocumentInfo], tags=["Documents"])
async def list_documents(limit: int = 50):
    """List all documents currently cached in the local SQLite store."""
    rows = await agent.list_cached_documents(limit=limit)
    return [
        DocumentInfo(
            doc_id=r["doc_id"],
            title=r["title"],
            source=r["source"],
            created_at=r["created_at"],
        )
        for r in rows
    ]


@app.post("/api/summarize", response_model=TextResponse, tags=["Analysis"])
async def summarize(req: SummarizeRequest):
    """Summarize a document by ID or raw text using the cloud Gemini service."""
    agent = get_agent()

    text = req.text or ""
    if req.doc_id and not text:
        doc = await agent.get_cached_document(req.doc_id)
        if not doc.ok:
            raise HTTPException(status_code=404, detail=f"Document '{req.doc_id}' not found in local cache.")
        text = doc.text

    if not text.strip():
        raise HTTPException(status_code=422, detail="Provide either doc_id or non-empty text.")

    result = await agent._call_cloud(
        "summarize_document", {"text": text, "focus": req.focus}, timeout=60.0
    )
    if not result.ok:
        raise HTTPException(status_code=502, detail=f"Cloud error: {result.error}")
    return TextResponse(result=result.text)


@app.post("/api/analyze", response_model=TextResponse, tags=["Analysis"])
async def analyze(req: AnalyzeRequest):
    """Deep analysis of a document against a specific clinical question."""
    agent = get_agent()

    text = req.text or ""
    if req.doc_id and not text:
        doc = await agent.get_cached_document(req.doc_id)
        if not doc.ok:
            raise HTTPException(status_code=404, detail=f"Document '{req.doc_id}' not found in local cache.")
        text = doc.text

    if not text.strip():
        raise HTTPException(status_code=422, detail="Provide either doc_id or non-empty text.")

    result = await agent._call_cloud(
        "analyze_document", {"text": text, "question": req.question}, timeout=60.0
    )
    if not result.ok:
        raise HTTPException(status_code=502, detail=f"Cloud error: {result.error}")
    return TextResponse(result=result.text)


# ---------------------------------------------------------------------------
# Static file serving — UI lives in the ui/ folder next to hybrid_agent/
# ---------------------------------------------------------------------------

_UI_DIR = Path(__file__).parent.parent / "ui"

if _UI_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(_UI_DIR)), name="static")

    @app.get("/", include_in_schema=False)
    async def serve_ui():
        return FileResponse(str(_UI_DIR / "index.html"))


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    import uvicorn

    host = os.environ.get("UI_HOST", "127.0.0.1")
    port = int(os.environ.get("UI_PORT", "7860"))
    print(f"\nDoctor's UI -> http://{host}:{port}")
    print(f"API docs    -> http://{host}:{port}/docs\n")
    uvicorn.run("hybrid_agent.api:app", host=host, port=port, reload=False)



if __name__ == "__main__":
    main()
