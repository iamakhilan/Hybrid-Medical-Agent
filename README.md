# Hybrid Doctor-Document Agent — Local Cache + Cloud Deep Context

Three components, three processes, one job split cleanly between them:

```
Doctor UI
   │
   ▼
Hybrid Agent (MCP client)  ──stdio──►  Local MCP Server  ──►  SQLite cache (+ optional local LLM)
   │
   └────────Streamable HTTP──►  Cloud MCP Server  ──►  Qdrant Cloud + Gemini
```

The agent never talks to Gemini or Qdrant directly. It only ever talks to
two MCP servers. Every query is fired at **both** at the same time
(speculative parallel routing); the local answer comes back in
milliseconds, the cloud answer arrives later (or fails/times out), and
the agent merges the two — falling back to local-only if the cloud side
is unreachable.

This matches the phased plan: **build and test the cloud system locally
first, deploy later.** Nothing in `cloud_mcp_server/` needs to run on
AWS/Azure/GCP to work — only Gemini and Qdrant (its two backing services)
are actually remote. The server process itself runs on your PC exactly
as it will run in a container later.

---

## The three components

### 1. `local_mcp_server/` — the fast, always-available side

- **Transport:** stdio. It's spawned as a subprocess by the hybrid agent
  — it never needs to be reachable over a network, so there's nothing to
  deploy, configure, or secure for this half of the system.
- **Storage:** SQLite, with an FTS5 full-text index for BM25 keyword
  search. FTS5 ships with every Python's built-in `sqlite3` module, so
  there's no native extension to compile on a Windows dev machine.
- **"LLM":** pluggable. Supports **Ollama** natively (e.g. `phi3` model running via `ollama serve`), or a local GGUF file via `llama-cpp-python`. If neither is active, it falls back to a deterministic **extractive** answer built from the best-matching cached chunks — so the tool is always usable, even before you've set up a real local model.

- **Tools:** `local_health_check`, `cache_document`, `search_local_cache`,
  `ask_local`.
- **Resource:** `cache://docs/{doc_id}` — full text of a cached document.

### 2. `cloud_mcp_server/` — the deep, "expensive" side

- **Transport:** Streamable HTTP (the current recommended MCP transport
  for new servers; SSE is being superseded). This process is a standalone
  HTTP service — run it on your PC today, containerize and deploy it
  later, with no code changes.
- **Gemini** is used for the things a local model is bad at: extracting
  structured JSON (clean text, tables, metadata) out of long/messy
  documents, deep multi-page analysis, and grounded question answering
  — never as a general-purpose chatbot.
- **Qdrant** (Qdrant Cloud, or a local Qdrant container during dev —
  same code path either way) stores chunk embeddings and does the
  semantic search.
- **Tools**, in build/test order:
  1. `cloud_health_check` — no API keys required. Confirms the server is
     reachable and reports whether Gemini/Qdrant are configured.
  2. `ingest_document(doc_id, title, text)` — Gemini extraction (optional)
     → chunk → Gemini embeddings → upsert into Qdrant.
  3. `search_cloud_documents(query, top_k)` — raw semantic search hits.
  4. `fetch_deep_cloud_context(query, top_k)` — same search, formatted as
     a context block ready to feed an LLM.
  5. `analyze_document(text, question)` — deep analysis of a whole
     document against a specific question (disease progression, treatment
     comparison, lab-value extraction, etc.).
  6. `summarize_document(text, focus="")` — condense a document.
  7. `answer_with_cloud_context(question, top_k)` — the full RAG pipeline:
     search → Gemini reasoning, grounded in what was found.

### 3. `hybrid_agent/` — the MCP client / router

- Launches the local server as a subprocess (stdio) and connects to the
  cloud server over HTTP.
- `ask(question)` fires both in parallel, waits (with a timeout on the
  cloud side), and merges: cloud answer as the primary response when it
  succeeds, local-only with a short note when the cloud side fails,
  times out, or was never reachable at startup.
- Distinguishes a genuine protocol failure from a **tool-level** error —
  MCP's `CallToolResult.isError` flag catches the case where a tool call
  "succeeds" but the tool itself reports failure internally (e.g. a
  missing `GEMINI_API_KEY`), so that error text never gets mistaken for a
  real answer.
- `cache_document_locally(...)` / `ingest_document_to_cloud(...)` expose
  the ingestion tools on both sides for whatever's feeding documents in
  (the doctor's UI, a batch script, etc.).

---

## Setup

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate

pip install -r requirements.txt
cp .env.example .env
```

`mcp` is pinned to `1.29.0` in every `requirements.txt` here on purpose —
the MCP Python SDK's newest release (`2.0.0`) reworked the server API
(`MCPServer` instead of `FastMCP`, plus new resource/request-state
machinery). `1.29.0` is the last release on the well-established
`FastMCP` API this project is built against; a plain `pip install mcp`
will grab `2.0.0` instead and this code will not run against it unless
you rewrite the server modules for the new API.

You don't need `GEMINI_API_KEY` or `QDRANT_URL` to get started — every
cloud tool checks for them and returns a clear error instead of crashing,
and the hybrid agent treats that exactly like an unreachable cloud
server: clean fallback to the local answer.

## Running it

**Milestone 1 — cloud health check, no keys needed:**
```bash
python -m cloud_mcp_server.server
# in another terminal:
curl -N -X POST http://127.0.0.1:8000/mcp \
  -H "Content-Type: application/json" -H "Accept: application/json, text/event-stream" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"cloud_health_check","arguments":{}}}'
```

**Milestone 2 — local server on its own:**
```bash
python -m local_mcp_server.server
```
(stdio-only — it expects to be driven by an MCP client, not curl. Use the
hybrid agent, or any MCP Inspector-style tool, to exercise it directly.)

**Milestone 3 — the full hybrid agent:**
```bash
# terminal 1
python -m cloud_mcp_server.server

# terminal 2 (spawns the local server itself)
python -m hybrid_agent.agent
```
This drops you into a small REPL: it prints both servers' health, then
lets you type questions and see the merged local+cloud answer, with
per-source latency and success/failure shown underneath.

## Then, only once this all works locally

1. Create the Qdrant Cloud cluster, set `QDRANT_URL` / `QDRANT_API_KEY`.
2. Get a Gemini API key, set `GEMINI_API_KEY`.
3. Call `ingest_document` on a few real documents, then try
   `answer_with_cloud_context` for real.
4. Only then: containerize `cloud_mcp_server/` and deploy it. Nothing in
   `hybrid_agent/` changes except `CLOUD_SERVER_URL` in `.env`.
