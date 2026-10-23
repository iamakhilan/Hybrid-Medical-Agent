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
from hybrid_agent.pdf_utils import extract_pages
from hybrid_agent.clinical_records import build_timeline, detect_contradictions

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

_SOURCE_DIR = Path(os.environ.get("LOCAL_DB_PATH") or (Path(__file__).parent.parent / "local_mcp_server" / "local_cache.db")).expanduser().resolve().parent / "source_files"


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
    # Citation lookup is best-effort: a search adapter failure must not turn
    # an otherwise successful Q&A request into a generic HTTP 500.
    try:
        raw_citations = await agent.search_cloud_sources(req.question, top_k=req.top_k)
    except Exception:
        raw_citations = []
    try:
        local_citations = await agent.search_local_sources(req.question, top_k=req.top_k)
    except Exception:
        local_citations = []
    all_citations = raw_citations + local_citations
    citations = []
    seen_citations = set()
    # Build a focused term set once and reject title/heading-only snippets.
    question_terms = {w.lower() for w in re.findall(r"[A-Za-z0-9]+", req.question)
                      if len(w) > 2 and w.lower() not in {
                          "what", "when", "where", "which", "with", "from", "that", "this",
                          "each", "does", "have", "has", "are", "the", "and", "for", "into",
                          "about", "patient", "document", "documents", "record", "records",
                          "please", "find", "list", "show", "source", "page", "pages",
                          "answer", "evidence", "based", "only", "return", "exact", "matching",
                          "passages", "include", "their", "every", "entry", "entries", "search",
                          "cite", "citations", "supporting", "identify", "conflicting", "documented",
                      }}
    def passage_is_relevant(passage: str) -> bool:
        clean = re.sub(r"[^A-Za-z0-9 ]", " ", passage).lower()
        words = set(clean.split())
        if len(words) < 4:
            return False
        # Explicitly reject document titles and short section headers.
        normalized = " ".join(clean.split())
        if normalized in {"synthetic patient test record", "medication reconciliation",
                          "laboratory results", "encounter notes", "patient profile",
                          "patient profile field value", "date source medication entry"}:
            return False
        # Filenames and section headings must never qualify as clinical evidence.
        if ("synthetic patient test record" in normalized and len(words) < 9) or normalized.endswith(".pdf"):
            return False
        overlap = len(question_terms & words)
        medication_triggers = {
            "medication", "medications", "medicine", "drug", "drugs", "dose", "doses",
            "taking", "prescribed", "amlodipine", "metformin", "atorvastatin", "ramipril", "statin", "insulin",
        }
        asks_medication = bool(question_terms & medication_triggers)
        medication_content_terms = {
            "mg", "daily", "once", "twice", "tablet", "capsule", "od", "bd", "tid", "po",
            "dose", "medication", "prescribed", "amlodipine", "metformin", "atorvastatin",
            "ramipril", "insulin", "allergy", "rash",
        }
        clinical_match = bool(words & medication_content_terms)
        return overlap >= 1 and (clinical_match if asks_medication else True)
    for hit in all_citations:
        passage = re.sub(r"\[/?(?:b|em|strong|mark)\]", "", str(hit.get("text") or hit.get("snippet") or "")).strip()
        doc_id = str(hit.get("doc_id") or "")
        title = str(hit.get("title") or doc_id or "Source document")
        if not passage or not doc_id or not passage_is_relevant(passage):
            continue
        page_number = hit.get("page_number")
        if isinstance(page_number, str) and page_number.isdigit():
            page_number = int(page_number)
        if not isinstance(page_number, int) or page_number < 1:
            page_number = None
        key = (doc_id.casefold(), re.sub(r"\W+", " ", passage).casefold()[:240])
        if key in seen_citations:
            continue
        seen_citations.add(key)
        citations.append({
            "doc_id": doc_id,
            "title": title,
            "page_number": page_number,
            "chunk_index": int(hit.get("chunk_index", -1)),
            "passage": passage,
            "score": float(hit.get("score", 0.0) or 0.0),
        })

    # Local evidence fallback. Rank actual stored lines by meaningful query-term
    # overlap, rather than requiring two terms on the same line (which fails
    # for short medication/lab entries). If the question names a document,
    # prioritize that document. Never fabricate a citation or page number.
    if not citations:
        stop_words = {
            "what", "when", "where", "which", "with", "from", "that", "this", "each",
            "does", "have", "has", "are", "the", "and", "for", "into", "about", "patient",
            "document", "documents", "record", "records", "please", "find", "list", "show",
            "source", "page", "pages", "answer", "evidence", "based", "only", "return",
            "exact", "matching", "passages", "include", "their", "every", "entry", "entries",
            "search", "cite", "citations", "supporting", "identify", "conflicting",
        }
        terms = {w.lower() for w in re.findall(r"[A-Za-z0-9]+", req.question)
                 if len(w) > 2 and w.lower() not in stop_words}
        documents = await agent.list_cached_documents(limit=200)
        question_lower = req.question.lower()
        named_docs = [d for d in documents if str(d.get("title", "")).lower() in question_lower
                      or str(d.get("doc_id", "")).lower() in question_lower]
        if named_docs:
            documents = named_docs + [d for d in documents if d not in named_docs]
        candidates = []
        for doc in documents:
            stored = await agent.get_cached_document_page_text(doc["doc_id"])
            if not stored.ok:
                continue
            marked = stored.text
            page_matches = list(re.finditer(r"\[\[PAGE:(\d+)\]\]", marked))
            segments = []
            if page_matches:
                for i, marker in enumerate(page_matches):
                    finish = page_matches[i + 1].start() if i + 1 < len(page_matches) else len(marked)
                    segments.append((int(marker.group(1)), marked[marker.end():finish]))
            else:
                segments.append((None, marked))
            for page, body in segments:
                for line in body.splitlines():
                    line = line.strip()
                    line_terms = {w.lower() for w in re.findall(r"[A-Za-z0-9]+", line) if len(w) > 2}
                    overlap = len(terms & line_terms)
                    if overlap and len(line) >= 12 and passage_is_relevant(line):
                        candidates.append((overlap, str(doc["doc_id"]), str(doc.get("title") or doc["doc_id"]), page, line))
        candidates.sort(key=lambda item: item[0], reverse=True)
        for score, doc_id, title, page, passage in candidates:
            key = (doc_id.casefold(), re.sub(r"\W+", " ", passage).casefold()[:240])
            if key in seen_citations:
                continue
            seen_citations.add(key)
            citations.append({"doc_id": doc_id, "title": title, "page_number": page,
                              "chunk_index": -1, "passage": passage, "score": float(score)})
            if len(citations) >= req.top_k:
                break

    answer_text = response.merged_text.strip()
    if not answer_text:
        if citations:
            answer_text = (
                "I found relevant evidence in the cached clinical records. "
                "Review the supporting evidence below for details."
            )
        else:
            answer_text = (
                "No clinical answer could be retrieved for this question from the available records. "
                "Try checking the document library or rephrasing your question."
            )

    return AskResponse(
        answer=answer_text,
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
        citations=citations,
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
        pages = extract_pages(filename, bytes(file_bytes))
        text = "\n\n".join(pages)
        page_content = "\n\n".join(f"[[PAGE:{i + 1}]]\n{page}" for i, page in enumerate(pages))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    # Derive a stable, deterministic doc_id from the filename so that
    # re-uploading the same file updates the existing entry instead of
    # creating a new duplicate each time.
    safe_name = re.sub(r"[^a-zA-Z0-9_-]", "_", Path(file.filename or "doc").stem)
    doc_id = safe_name
    doc_title = title.strip() or Path(file.filename or "Untitled").name

    # Keep the original upload locally so evidence citations can open the source.
    # This folder stays alongside the local cache and is not served as a directory.
    _SOURCE_DIR.mkdir(parents=True, exist_ok=True)
    suffix = Path(filename).suffix.lower()
    for old_suffix in {".pdf", ".txt", ".md"} - {suffix}:
        old_source = _SOURCE_DIR / f"{doc_id}{old_suffix}"
        if old_source.exists():
            old_source.unlink()
    (_SOURCE_DIR / f"{doc_id}{suffix}").write_bytes(bytes(file_bytes))

    # Cache locally (always)
    local_res = await agent.cache_document_locally(
        doc_id=doc_id, title=doc_title, content=text, page_content=page_content, source=filename
    )

    # Ingest to cloud (best-effort)
    cloud_res = await agent.ingest_document_to_cloud(
        doc_id=doc_id, title=doc_title, text=page_content
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
    agent = get_agent()
    rows = await agent.list_cached_documents(limit=limit)
    docs = []
    for r in rows:
        source = r.get("source", "local")
        if source == "local":
            for ext in (".pdf", ".txt", ".md"):
                if (_SOURCE_DIR / f"{r['doc_id']}{ext}").is_file():
                    source = f"{r['doc_id']}{ext}"
                    break
        docs.append(
            DocumentInfo(
                doc_id=r["doc_id"],
                title=r["title"],
                source=source,
                created_at=r["created_at"],
            )
        )
    return docs


@app.get("/api/timeline", tags=["Clinical records"])
async def clinical_timeline():
    """Extract dated, source-linked events from cached records for human review."""
    agent = get_agent()
    documents = await agent.list_cached_documents(limit=200)
    sources = []
    for doc in documents:
        result = await agent.get_cached_document_page_text(doc["doc_id"])
        if result.ok:
            sources.append({"doc_id": doc["doc_id"], "title": doc["title"], "text": result.text})
    return {"events": build_timeline(sources), "document_count": len(sources)}


@app.get("/api/safety-check", tags=["Clinical records"])
async def clinical_safety_check():
    """Find potential record inconsistencies; findings are not diagnoses."""
    agent = get_agent()
    documents = await agent.list_cached_documents(limit=200)
    sources = []
    for doc in documents:
        result = await agent.get_cached_document_page_text(doc["doc_id"])
        if result.ok:
            sources.append({"doc_id": doc["doc_id"], "title": doc["title"], "text": result.text})
    return {"findings": detect_contradictions(sources), "document_count": len(sources)}


@app.get("/api/documents/{doc_id}/source", tags=["Documents"])
async def open_document_source(doc_id: str):
    """Open the original uploaded file, or extracted text for legacy cached records."""
    if not re.fullmatch(r"[A-Za-z0-9_-]+", doc_id):
        raise HTTPException(status_code=400, detail="Invalid document identifier.")
    for suffix, media_type in ((".pdf", "application/pdf"), (".txt", "text/plain; charset=utf-8"), (".md", "text/markdown; charset=utf-8")):
        source_path = _SOURCE_DIR / f"{doc_id}{suffix}"
        if source_path.is_file():
            return FileResponse(str(source_path), media_type=media_type, headers={"Cache-Control": "no-store"})
    result = await get_agent().get_cached_document(doc_id)
    if not result.ok:
        raise HTTPException(status_code=404, detail="Source document is not available in the local cache.")
    from fastapi.responses import PlainTextResponse
    return PlainTextResponse(result.text, headers={"Cache-Control": "no-store"})


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
