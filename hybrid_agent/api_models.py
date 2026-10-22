"""
Pydantic models for the FastAPI REST layer.

Keeps api.py clean and gives the frontend a well-typed contract.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


# ---------- Request models -----------------------------------------------

class AskRequest(BaseModel):
    question: str = Field(..., min_length=1, description="The clinical question to ask")
    top_k: int = Field(5, ge=1, le=20, description="Number of results to retrieve per source")


class SummarizeRequest(BaseModel):
    doc_id: str | None = Field(None, description="ID of a cached document to summarize")
    text: str | None = Field(None, description="Raw text to summarize (used if doc_id not given)")
    focus: str = Field("", description="Optional focus area for the summary")


class AnalyzeRequest(BaseModel):
    doc_id: str | None = Field(None, description="ID of a cached document to analyze")
    text: str | None = Field(None, description="Raw text to analyze (used if doc_id not given)")
    question: str = Field(..., min_length=1, description="The analytical question to answer")


# ---------- Response models ----------------------------------------------

class SourceInfo(BaseModel):
    ok: bool
    latency_seconds: float
    error: str = ""


class EvidenceCitation(BaseModel):
    doc_id: str
    title: str
    page_number: int | None = None
    chunk_index: int
    passage: str
    score: float


class AskResponse(BaseModel):
    answer: str
    used_cloud: bool
    local: SourceInfo
    cloud: SourceInfo
    query: str
    citations: list[EvidenceCitation] = Field(default_factory=list)


class DocumentInfo(BaseModel):
    doc_id: str
    title: str
    source: str
    created_at: float | str
    char_count: int | None = None


class IngestResponse(BaseModel):
    doc_id: str
    title: str
    local_result: str
    cloud_result: str


class HealthResponse(BaseModel):
    local_ok: bool
    local_message: str
    cloud_ok: bool
    cloud_message: str


class TextResponse(BaseModel):
    result: str
