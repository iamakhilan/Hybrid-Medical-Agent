"""Shared plain-data types passed between the cloud server's internal
services. Kept dependency-free (dataclasses only) so gemini_service,
qdrant_service, and document_processor don't need to import each other.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Chunk:
    doc_id: str
    chunk_index: int
    text: str
    title: str = ""


@dataclass
class SearchHit:
    doc_id: str
    chunk_index: int
    text: str
    title: str
    score: float


@dataclass
class ExtractionResult:
    """Output of Gemini's document extraction step."""

    text: str
    structured: dict = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
