"""
Gemini service: the "deep reasoning" model in the architecture.

Deliberately NOT used as a general chatbot. Every method here maps to one
of the jobs the project document assigns the cloud side:
  - extract_document(): heavy PDF / long-document extraction into
    structured JSON (text, tables, metadata) -- what a local model
    can't do well or fast.
  - summarize(): condense a long document or a set of retrieved chunks.
  - reason_over_context(): answer a question using retrieved chunks as
    grounding (the last step of the RAG flow: Qdrant hits -> Gemini).
  - analyze(): open-ended deep analysis over a whole document (e.g.
    "identify the progression of the disease, compare previous
    treatments, extract lab values").
"""

from __future__ import annotations

import json

from cloud_mcp_server import config
from cloud_mcp_server.models import ExtractionResult

_client = None


class GeminiServiceError(RuntimeError):
    pass


def _get_client():
    global _client
    if _client is not None:
        return _client
    if not config.gemini_configured():
        raise GeminiServiceError(
            "GEMINI_API_KEY is not set. Add it to your .env before calling "
            "any Gemini-backed tool."
        )
    from google import genai

    _client = genai.Client(api_key=config.GEMINI_API_KEY)
    return _client


def _generate(prompt: str, system_instruction: str | None = None) -> str:
    from google.genai import types

    client = _get_client()
    config_kwargs = {}
    if system_instruction:
        config_kwargs["system_instruction"] = system_instruction
    response = client.models.generate_content(
        model=config.GEMINI_MODEL,
        contents=prompt,
        config=types.GenerateContentConfig(**config_kwargs) if config_kwargs else None,
    )
    return (response.text or "").strip()


def extract_document(text: str, title: str = "") -> ExtractionResult:
    """Turn raw document text (already OCR'd/extracted from a PDF upstream,
    or pasted directly) into structured JSON: clean text, any tables found,
    and document-level metadata. This is the 'heavy PDF processing' step --
    designed for long, messy medical documents a local model would choke on.
    """
    system_instruction = (
        "You are a medical-document extraction engine. Given raw document "
        "text, return ONLY a JSON object (no markdown fences, no preamble) "
        "with keys: 'clean_text' (string), 'tables' (list of objects, each "
        "with 'caption' and 'rows'), and 'metadata' (object with any of: "
        "patient_context, document_type, dates, key_terms). If a field "
        "isn't present in the source, omit it or use an empty value -- "
        "never invent data."
    )
    prompt = f"Document title: {title or '(untitled)'}\n\nDocument text:\n{text}"
    raw = _generate(prompt, system_instruction=system_instruction)
    try:
        parsed = json.loads(raw)
        return ExtractionResult(
            text=parsed.get("clean_text", text),
            structured={"tables": parsed.get("tables", []), "metadata": parsed.get("metadata", {})},
        )
    except (json.JSONDecodeError, AttributeError):
        return ExtractionResult(
            text=text,
            structured={},
            warnings=["Gemini did not return valid JSON; falling back to raw input text."],
        )


def summarize(text: str, focus: str = "") -> str:
    instruction = "Summarize the following document clearly and concisely for a busy clinician."
    if focus:
        instruction += f" Focus specifically on: {focus}."
    return _generate(f"{instruction}\n\nDocument:\n{text}")


def analyze(text: str, question: str) -> str:
    """Open-ended deep analysis of a full document against a specific
    clinical question (e.g. disease progression, treatment comparison,
    lab-value extraction)."""
    system_instruction = (
        "You are assisting a clinician with deep analysis of a medical "
        "document. Be precise, cite specific values/dates from the text "
        "where relevant, and clearly flag anything ambiguous or missing "
        "rather than guessing."
    )
    prompt = f"Document:\n{text}\n\nAnalysis request: {question}"
    return _generate(prompt, system_instruction=system_instruction)


def reason_over_context(question: str, context_chunks: list[str]) -> str:
    """Answer `question` grounded in retrieved chunks when available, or using
    Gemini clinical knowledge directly if no vector matches were found."""
    if not context_chunks:
        system_instruction = (
            "You are an expert clinical decision support assistant aiding a physician. "
            "Provide helpful, direct clinical guidance, assessment steps, and treatment reasoning "
            "for the medical scenario or question asked. Do not refuse to answer with boilerplate disclaimers."
        )
        return _generate(question, system_instruction=system_instruction)

    context = "\n\n---\n\n".join(context_chunks)
    system_instruction = (
        "Answer the clinician's question using the provided context chunks. "
        "If the context doesn't contain enough information, answer as best as possible "
        "based on the available data."
    )
    prompt = f"Context:\n{context}\n\nQuestion: {question}"
    return _generate(prompt, system_instruction=system_instruction)

