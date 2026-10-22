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

import re

from cloud_mcp_server import config
from cloud_mcp_server.models import Chunk


def chunk_text(
    doc_id: str,
    text: str,
    title: str = "",
    chunk_size: int = config.CHUNK_SIZE_WORDS,
    overlap: int = config.CHUNK_OVERLAP_WORDS,
) -> list[Chunk]:
    """Create overlapping chunks and preserve page numbers from [[PAGE:n]] markers."""
    marker = re.compile(r"\[\[PAGE:(\d+)\]\]")
    matches = list(marker.finditer(text))
    sections: list[tuple[int | None, str]] = []
    if matches:
        for index, match in enumerate(matches):
            end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
            sections.append((int(match.group(1)), text[match.end():end]))
    else:
        sections.append((None, text))

    if overlap >= chunk_size:
        overlap = max(0, chunk_size // 4)
    step = max(1, chunk_size - overlap)
    chunks: list[Chunk] = []
    chunk_index = 0
    for page_number, section in sections:
        words = section.split()
        start = 0
        while start < len(words):
            piece = words[start:start + chunk_size]
            chunks.append(Chunk(doc_id=doc_id, chunk_index=chunk_index, text=" ".join(piece),
                                title=title, page_number=page_number))
            chunk_index += 1
            if start + chunk_size >= len(words):
                break
            start += step
    return chunks
