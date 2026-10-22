"""
Document chunking.

Deliberately simple: a fixed-size, word-based sliding window with overlap.
This is the step between "Gemini extraction" and "embeddings" in the data
flow diagram. It has no external dependencies, so it's easy to swap for a
smarter, structure-aware chunker (e.g. one that respects the tables /
sections Gemini's structured extraction already identified) later without
touching anything else.
"""

from __future__ import annotations

from cloud_mcp_server import config
from cloud_mcp_server.models import Chunk


def chunk_text(
    doc_id: str,
    text: str,
    title: str = "",
    chunk_size: int = config.CHUNK_SIZE_WORDS,
    overlap: int = config.CHUNK_OVERLAP_WORDS,
) -> list[Chunk]:
    words = text.split()
    if not words:
        return []
    if overlap >= chunk_size:
        overlap = max(0, chunk_size // 4)

    chunks: list[Chunk] = []
    start = 0
    index = 0
    step = chunk_size - overlap
    while start < len(words):
        piece = words[start : start + chunk_size]
        chunks.append(
            Chunk(doc_id=doc_id, chunk_index=index, text=" ".join(piece), title=title)
        )
        index += 1
        if start + chunk_size >= len(words):
            break
        start += step
    return chunks
