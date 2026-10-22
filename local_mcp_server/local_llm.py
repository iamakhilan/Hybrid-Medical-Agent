"""
Pluggable local LLM.

Real on-device inference (Phi-3, Llama-3-8B-Instruct-4bit, etc. via
llama-cpp-python / llama.cpp) is intentionally optional here. Downloading
and running a quantized model is a separate, larger setup step, and this
project should run end-to-end without it.

If `llama-cpp-python` is installed and LOCAL_LLM_MODEL_PATH points at a
.gguf file, that model is used. Otherwise generate() falls back to a
simple extractive strategy: stitch together the most relevant cached
chunks into a direct answer. That fallback is not "AI-generated" text,
but it is fast, fully offline, and always available -- which matches the
FAST RESPONSE role the local side plays in the architecture.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from pathlib import Path

from local_mcp_server import config
from local_mcp_server.db import SearchResult

_llama = None
_llama_load_attempted = False
_ollama_check_attempted = False
_active_ollama_model: str | None = None


def _get_active_ollama_model() -> str | None:
    global _active_ollama_model
    url = f"{config.OLLAMA_HOST.rstrip('/')}/api/tags"
    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=2.0) as resp:
            if resp.status == 200:
                data = json.loads(resp.read().decode("utf-8"))
                models = [m.get("name", "") for m in data.get("models", [])]
                if not models:
                    _active_ollama_model = None
                    return None
                # Check for configured model or match
                target = config.OLLAMA_MODEL.lower()
                for m in models:
                    if m.lower() == target or m.lower().startswith(f"{target}:"):
                        _active_ollama_model = m
                        return m
                # Fallback to first available model if any
                _active_ollama_model = models[0]
                return models[0]
    except Exception:
        pass
    _active_ollama_model = None
    return None


def _try_load_llama():
    global _llama, _llama_load_attempted
    if _llama_load_attempted:
        return _llama
    _llama_load_attempted = True
    if not config.LOCAL_LLM_MODEL_PATH:
        return None
    try:
        from llama_cpp import Llama
    except ImportError:
        return None
    try:
        _llama = Llama(
            model_path=config.LOCAL_LLM_MODEL_PATH,
            n_ctx=config.LOCAL_LLM_CONTEXT_WINDOW,
            verbose=False,
        )
    except Exception:
        _llama = None
    return _llama


def is_real_model_loaded() -> bool:
    return (_get_active_ollama_model() is not None) or (_try_load_llama() is not None)



def get_model_mode_description() -> str:
    model = _get_active_ollama_model()
    if model:
        return f"Ollama model '{model}' ({config.OLLAMA_HOST})"
    if _try_load_llama() is not None:
        return f"real local model ({Path(config.LOCAL_LLM_MODEL_PATH).name})"
    return "extractive fallback (Ollama active & downloading)"


def _extractive_answer(question: str, chunks: list[SearchResult]) -> str:
    if not chunks:
        q_lower = question.strip().lower()
        if q_lower in {"hello", "hi", "hey", "help", "hello!", "hi!"}:
            return (
                "Hello! I am your local patient document assistant. "
                "I can search and retrieve cached clinical records offline. "
                "Ask a question about a patient or upload a document to get started."
            )
        return (
            "No cached local documents matched this question yet. "
            "Ask a clinical question about cached documents or upload a patient record to get started."
        )
    lines = [f"Based on {len(chunks)} cached local document(s):"]
    for c in chunks:
        lines.append(f"- [{c.title}] {c.snippet}")
    return "\n".join(lines)



def _generate_ollama(question: str, chunks: list[SearchResult], model_name: str) -> str:
    context = "\n\n".join(f"[{c.title}]\n{c.snippet}" for c in chunks) or "(no local context found)"
    prompt = (
        "You are a fast, on-device clinical assistant. Answer the question using "
        "only the provided medical context below. Be concise, direct, and factual. "
        "If the context is insufficient, state that briefly.\n\n"
        f"Context:\n{context}\n\nQuestion: {question}\nAnswer:"
    )
    url = f"{config.OLLAMA_HOST.rstrip('/')}/api/generate"
    payload = json.dumps(
        {
            "model": model_name,
            "prompt": prompt,
            "stream": False,
            "options": {
                "num_ctx": config.LOCAL_LLM_CONTEXT_WINDOW,
                "num_predict": config.LOCAL_LLM_MAX_TOKENS,
            },
        }
    ).encode("utf-8")

    req = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30.0) as resp:
        data = json.loads(resp.read().decode("utf-8"))
        return data.get("response", "").strip()


def generate(question: str, chunks: list[SearchResult]) -> str:
    """Answer `question` using `chunks` as context.

    Uses Ollama if an active model is loaded, otherwise llama-cpp if configured;
    falls back to a deterministic extractive summary if neither is active.
    """
    ollama_model = _get_active_ollama_model()
    if ollama_model:
        try:
            return _generate_ollama(question, chunks, ollama_model)
        except Exception as exc:
            pass


    llama = _try_load_llama()
    if llama is not None:
        context = "\n\n".join(f"[{c.title}]\n{c.snippet}" for c in chunks) or "(no local context found)"
        prompt = (
            "You are a fast, on-device assistant. Answer the question using "
            "only the context below. If the context is insufficient, say so "
            "briefly rather than guessing.\n\n"
            f"Context:\n{context}\n\nQuestion: {question}\nAnswer:"
        )
        try:
            result = llama(
                prompt,
                max_tokens=config.LOCAL_LLM_MAX_TOKENS,
                stop=["Question:", "\n\n\n"],
            )
            return result["choices"][0]["text"].strip()
        except Exception as exc:
            return f"[local model error, falling back to extractive answer] {_extractive_answer(question, chunks)} ({exc})"

    return _extractive_answer(question, chunks)

