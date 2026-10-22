"""
Qdrant vector store service.

Wraps the parts of the Qdrant Python client this project needs: creating
the collection on first use, upserting chunk embeddings with their
payload (doc_id, title, chunk text), and querying by vector similarity.

Talks to Qdrant Cloud by default (QDRANT_URL + QDRANT_API_KEY from .env).
For local development without a cloud account yet, point QDRANT_URL at a
local Qdrant instance (e.g. `docker run -p 6333:6333 qdrant/qdrant`) --
same code path, nothing else changes.
"""

from __future__ import annotations

import hashlib
import uuid

from cloud_mcp_server import config
from cloud_mcp_server.models import Chunk, SearchHit

_client = None


class QdrantServiceError(RuntimeError):
    pass


def _get_client():
    global _client
    if _client is not None:
        return _client
    if not config.qdrant_configured():
        raise QdrantServiceError(
            "QDRANT_URL is not set. Add QDRANT_URL (and QDRANT_API_KEY for "
            "Qdrant Cloud) to your .env before calling any cloud search "
            "or ingestion tool."
        )
    from qdrant_client import QdrantClient

    _client = QdrantClient(url=config.QDRANT_URL, api_key=config.QDRANT_API_KEY or None)
    return _client


def _point_id(doc_id: str, chunk_index: int) -> str:
    # Deterministic UUID from (doc_id, chunk_index) so re-ingesting the
    # same document updates existing points instead of duplicating them.
    raw = f"{doc_id}:{chunk_index}".encode("utf-8")
    return str(uuid.UUID(bytes=hashlib.md5(raw).digest()))


def ensure_collection(vector_size: int = config.GEMINI_EMBEDDING_DIM) -> None:
    from qdrant_client.models import Distance, VectorParams

    client = _get_client()
    if not client.collection_exists(config.QDRANT_COLLECTION):
        client.create_collection(
            collection_name=config.QDRANT_COLLECTION,
            vectors_config=VectorParams(size=vector_size, distance=Distance.COSINE),
        )


def upsert_chunks(chunks_with_vectors: list[tuple[Chunk, list[float]]]) -> int:
    from qdrant_client.models import PointStruct

    if not chunks_with_vectors:
        return 0

    vector_size = len(chunks_with_vectors[0][1])
    ensure_collection(vector_size=vector_size)

    client = _get_client()
    points = [
        PointStruct(
            id=_point_id(chunk.doc_id, chunk.chunk_index),
            vector=vector,
            payload={
                "doc_id": chunk.doc_id,
                "chunk_index": chunk.chunk_index,
                "title": chunk.title,
                "text": chunk.text,
            },
        )
        for chunk, vector in chunks_with_vectors
    ]
    client.upsert(collection_name=config.QDRANT_COLLECTION, points=points)
    return len(points)


def search(query_vector: list[float], top_k: int = config.DEFAULT_TOP_K) -> list[SearchHit]:
    try:
        client = _get_client()
        if not client.collection_exists(config.QDRANT_COLLECTION):
            return []
        result = client.query_points(
            collection_name=config.QDRANT_COLLECTION,
            query=query_vector,
            limit=top_k,
            with_payload=True,
        )
        hits: list[SearchHit] = []
        for point in result.points:
            payload = point.payload or {}
            hits.append(
                SearchHit(
                    doc_id=payload.get("doc_id", ""),
                    chunk_index=payload.get("chunk_index", -1),
                    text=payload.get("text", ""),
                    title=payload.get("title", ""),
                    score=point.score,
                )
            )
        return hits
    except Exception as exc:
        # If Qdrant Cloud is unreachable or errors out, return empty hits safely
        return []

