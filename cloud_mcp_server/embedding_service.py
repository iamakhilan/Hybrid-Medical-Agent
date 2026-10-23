"""
Embedding generation, via Gemini's embedding model.

Kept as its own module (rather than folded into gemini_service.py) because
in the target architecture it's a distinct pipeline stage -- chunking ->
embeddings -> Qdrant -- and you may eventually want to swap the embedding
provider independently of the reasoning model.
"""

from __future__ import annotations

from cloud_mcp_server import config
from cloud_mcp_server.models import Chunk

_client = None


class EmbeddingServiceError(RuntimeError):
    pass


def _get_client():
    global _client
    if _client is not None:
        return _client
    if not config.gemini_configured():
        raise EmbeddingServiceError(
            "GEMINI_API_KEY is not set. Add it to your .env before calling "
            "any embedding/search/reasoning tool."
        )
    from google import genai

    # Use the supported Gemini embedding API. The model and output dimension
    # are configurable so vectors stay compatible with the Qdrant collection.
    _client = genai.Client(api_key=config.GEMINI_API_KEY)
    return _client


def embed_texts(texts: list[str]) -> list[list[float]]:
    """Embed a batch of strings, preserving order."""
    if not texts:
        return []
    client = _get_client()

    # The google-genai SDK requires the model name prefixed with "models/"
    # when calling embed_content. The API also expects each item in contents
    # to be a plain string.
    model_name = config.GEMINI_EMBEDDING_MODEL
    if not model_name.startswith("models/"):
        model_name = f"models/{model_name}"

    from google.genai import types

    response = client.models.embed_content(
        model=model_name,
        contents=texts,
        config=types.EmbedContentConfig(
            output_dimensionality=config.GEMINI_EMBEDDING_DIM
        ),
    )
    return [e.values for e in response.embeddings]


def embed_query(query: str) -> list[float]:
    return embed_texts([query])[0]


def embed_chunks(chunks: list[Chunk]) -> list[tuple[Chunk, list[float]]]:
    vectors = embed_texts([c.text for c in chunks])
    return list(zip(chunks, vectors))
