"""
Hybrid Agent — the MCP client that sits above both the local and cloud
MCP servers and never lets the doctor's UI talk to Gemini/Qdrant (or
even the local cache) directly.

Strategy: speculative parallel routing. Every query is fired at the
local server (always fast, always available) and the cloud server
(deep, but can be slow/unreachable) at the same time. Whichever comes
back first can be shown immediately; the response manager then merges
or upgrades the answer once both are in, and falls back to local-only
if the cloud call fails or times out.

Run directly for a quick interactive test:
    python -m hybrid_agent.agent
(starts the local server as a subprocess automatically; the cloud
server must already be running separately -- see cloud_mcp_server/server.py)
"""

from __future__ import annotations

import asyncio
import json
import time
from contextlib import AsyncExitStack
from dataclasses import dataclass, field

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.client.streamable_http import streamable_http_client

from hybrid_agent import config


@dataclass
class SourceResult:
    ok: bool
    text: str = ""
    latency_seconds: float = 0.0
    error: str = ""


@dataclass
class MergedResponse:
    query: str
    local: SourceResult
    cloud: SourceResult
    used_cloud: bool
    merged_text: str = field(default="")


class HybridAgent:
    """Connects to the local (stdio) and cloud (Streamable HTTP) MCP
    servers and routes/merges queries across both."""

    def __init__(self) -> None:
        self._stack: AsyncExitStack | None = None
        self.local_session: ClientSession | None = None
        self.cloud_session: ClientSession | None = None
        self.cloud_available = False

    async def __aenter__(self) -> "HybridAgent":
        self._stack = AsyncExitStack()
        await self._stack.__aenter__()

        # --- Local MCP server: launch as a subprocess over stdio -----
        local_params = StdioServerParameters(
            command=config.LOCAL_SERVER_COMMAND,
            args=config.LOCAL_SERVER_ARGS,
        )
        local_read, local_write = await self._stack.enter_async_context(stdio_client(local_params))
        self.local_session = await self._stack.enter_async_context(ClientSession(local_read, local_write))
        await self.local_session.initialize()

        # --- Cloud MCP server: connect to an already-running HTTP server ---
        # Connection attempts happen against a *separate* exit stack. If
        # anything fails partway through (unreachable host, refused
        # connection, timeout), that stack unwinds and closes whatever it
        # partially opened on its own, cleanly, right here -- instead of
        # leaving half-entered context managers registered on the agent's
        # long-lived stack, which caused cross-task cancel-scope errors
        # during shutdown. On success, ownership of the opened resources
        # transfers into the long-lived stack via pop_all().
        #
        # Note: a failed network connection surfaces as
        # asyncio.CancelledError here (raised by the underlying anyio task
        # group), which is a BaseException, not an Exception -- a plain
        # `except Exception` silently misses it. Both are caught below.
        cloud_stack = AsyncExitStack()
        try:
            async with cloud_stack:
                cloud_read, cloud_write, _ = await cloud_stack.enter_async_context(
                    streamable_http_client(config.CLOUD_SERVER_URL)  # mcp 1.29.0 does not accept timeout=
                )
                self.cloud_session = await cloud_stack.enter_async_context(ClientSession(cloud_read, cloud_write))
                await self.cloud_session.initialize()
                self.cloud_available = True
                self._stack.push_async_callback(cloud_stack.pop_all().aclose)
        except (Exception, asyncio.CancelledError):
            # Cloud server not running / unreachable at startup. The agent
            # still works local-only; every cloud call below will simply
            # report an error instead of crashing the agent.
            self.cloud_session = None
            self.cloud_available = False

        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        if self._stack is not None:
            await self._stack.__aexit__(exc_type, exc, tb)

    # -- low-level tool calls -------------------------------------------------

    async def _call_local(self, tool: str, arguments: dict) -> SourceResult:
        start = time.monotonic()
        if self.local_session is None:
            return SourceResult(ok=False, error="Local MCP session not connected.")
        try:
            result = await self.local_session.call_tool(tool, arguments)
            text = "\n".join(getattr(block, "text", str(block)) for block in result.content)
            # A tool can fail *inside* its own logic (e.g. a caught
            # exception) and still return a normal CallToolResult -- the
            # protocol call itself didn't raise. `isError` is how MCP
            # distinguishes "the call succeeded but the tool reports a
            # failure" from a real answer; treat that the same as an
            # exception so the merge step doesn't mistake an error string
            # for a valid response.
            if getattr(result, "isError", False):
                return SourceResult(ok=False, error=text, latency_seconds=time.monotonic() - start)
            return SourceResult(ok=True, text=text, latency_seconds=time.monotonic() - start)
        except Exception as exc:
            return SourceResult(ok=False, error=str(exc), latency_seconds=time.monotonic() - start)

    async def _call_cloud(self, tool: str, arguments: dict, timeout: float = config.CLOUD_TIMEOUT_SECONDS) -> SourceResult:
        start = time.monotonic()
        if self.cloud_session is None:
            return SourceResult(ok=False, error="Cloud MCP session not connected.")
        try:
            result = await asyncio.wait_for(
                self.cloud_session.call_tool(tool, arguments),
                timeout=timeout,
            )
            text = "\n".join(getattr(block, "text", str(block)) for block in result.content)
            if getattr(result, "isError", False):
                return SourceResult(ok=False, error=text, latency_seconds=time.monotonic() - start)
            return SourceResult(ok=True, text=text, latency_seconds=time.monotonic() - start)
        except asyncio.TimeoutError:
            return SourceResult(
                ok=False,
                error=f"Cloud call timed out after {timeout}s.",
                latency_seconds=time.monotonic() - start,
            )
        except Exception as exc:
            return SourceResult(ok=False, error=str(exc), latency_seconds=time.monotonic() - start)

    # -- high-level routing -----------------------------------------------------

    async def ask(self, question: str, top_k: int = config.DEFAULT_TOP_K) -> MergedResponse:
        """Speculative parallel routing: fire the local and cloud queries
        at the same time, then merge. Falls back to local-only if the
        cloud call fails or isn't connected."""
        local_task = asyncio.create_task(self._call_local("ask_local", {"question": question, "top_k": top_k}))
        cloud_task = asyncio.create_task(
            self._call_cloud("answer_with_cloud_context", {"question": question, "top_k": top_k}, timeout=config.CLOUD_TIMEOUT_SECONDS)
        )
        local_result, cloud_result = await asyncio.gather(local_task, cloud_task)

        merged = self._merge(question, local_result, cloud_result)
        return merged

    def _merge(self, question: str, local: SourceResult, cloud: SourceResult) -> MergedResponse:
        cloud_text = cloud.text.strip()
        local_text = local.text.strip()

        cloud_has_disclaimer = any(
            phrase in cloud_text.lower()
            for phrase in [
                "cannot access specific patient data",
                "don't have access to any patient data",
                "do not have access to any patient data",
                "please provide the patient's data",
                "please provide the patient",
                "i don't have access",
                "i do not have access",
                "don't have access",
                "do not have access",
                "no relevant cloud documents",
                "no relevant documents found in the cloud vector store",
                "ingest the document into the cloud store",
                "as an ai, i am not equipped",
                "no patient data provided",
                "need the following details",
                "provide the following information",
            ]
        )



        # If cloud returned a valid response that isn't a disclaimer, use cloud.
        # If cloud returned a disclaimer/refusal but local has a valid cached answer, use local.
        if cloud.ok and cloud_text and not cloud_has_disclaimer:
            used_cloud = True
            # Keep the cloud answer primary and expose the fast local result
            # as a preview, as specified by the project report.
            if local.ok and local_text and local_text.strip() != cloud_text:
                merged_text = f"{cloud_text}\n\n---\nLocal fast preview ({local.latency_seconds * 1000:.2f} ms):\n{local_text}"
            else:
                merged_text = cloud_text
        elif local.ok and local_text:
            used_cloud = False
            merged_text = local_text
        elif cloud.ok and cloud_text:
            used_cloud = True
            merged_text = cloud_text
        else:
            used_cloud = False
            merged_text = (
                "Neither the local cache nor the cloud backend could answer this. "
                f"Local status: {local.error or 'n/a'}. Cloud status: {cloud.error or 'n/a'}."
            )

        return MergedResponse(query=question, local=local, cloud=cloud, used_cloud=used_cloud, merged_text=merged_text)

    async def cache_document_locally(self, doc_id: str, title: str, content: str, source: str = "local", page_content: str = "") -> SourceResult:
        return await self._call_local(
            "cache_document", {"doc_id": doc_id, "title": title, "content": content, "source": source, "page_content": page_content}
        )

    async def ingest_document_to_cloud(self, doc_id: str, title: str, text: str) -> SourceResult:
        return await self._call_cloud(
            "ingest_document", {"doc_id": doc_id, "title": title, "text": text}, timeout=45.0
        )

    async def list_cached_documents(self, limit: int = 50) -> list[dict]:
        """List local document metadata through the local MCP server."""
        result = await self._call_local("list_cached_documents", {"limit": limit})
        if not result.ok:
            return []
        try:
            payload = json.loads(result.text)
            return payload if isinstance(payload, list) else []
        except (json.JSONDecodeError, TypeError):
            return []

    async def get_cached_document(self, doc_id: str) -> SourceResult:
        """Fetch document text through MCP rather than accessing SQLite directly."""
        return await self._call_local("get_cached_document", {"doc_id": doc_id})

    async def get_cached_document_page_text(self, doc_id: str) -> SourceResult:
        return await self._call_local("get_cached_document_page_text", {"doc_id": doc_id})

    async def search_cloud_sources(self, query: str, top_k: int = 5) -> list[dict]:
        result = await self._call_cloud("search_cloud_documents", {"query": query, "top_k": top_k})
        if not result.ok or not result.text.strip():
            return []
        try:
            payload = json.loads(result.text)
            return payload if isinstance(payload, list) else []
        except (json.JSONDecodeError, TypeError):
            return []

    async def health_check(self) -> tuple[SourceResult, SourceResult]:
        local = await self._call_local("local_health_check", {})
        cloud = await self._call_cloud("cloud_health_check", {}, timeout=5.0)
        return local, cloud


async def _interactive_main() -> None:
    async with HybridAgent() as agent:
        local_health, cloud_health = await agent.health_check()
        print(f"[local]  {local_health.text if local_health.ok else local_health.error}")
        print(f"[cloud]  {cloud_health.text if cloud_health.ok else cloud_health.error}")
        print("\nType a question (or 'quit'):")
        while True:
            question = input("> ").strip()
            if question.lower() in {"quit", "exit"}:
                break
            if not question:
                continue
            response = await agent.ask(question)
            print(f"\n--- answer (used_cloud={response.used_cloud}) ---")
            print(response.merged_text)
            print(
                f"[local: ok={response.local.ok} {response.local.latency_seconds:.2f}s | "
                f"cloud: ok={response.cloud.ok} {response.cloud.latency_seconds:.2f}s]\n"
            )


def main() -> None:
    asyncio.run(_interactive_main())


if __name__ == "__main__":
    main()
