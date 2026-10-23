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
_last_ollama_check_time: float = 0.0


def _get_active_ollama_model() -> str | None:
    global _active_ollama_model, _last_ollama_check_time
    import time
    now = time.monotonic()
    if now - _last_ollama_check_time < 30.0 and _active_ollama_model is not None:
        return _active_ollama_model
    if now - _last_ollama_check_time < 10.0 and _active_ollama_model is None:
        return None
    _last_ollama_check_time = now

    url = f"{config.OLLAMA_HOST.rstrip('/')}/api/tags"
    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=1.0) as resp:
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


def synthesize_rag_response(question: str, chunks: list[SearchResult]) -> str:
    """Generate a structured, evidence-grounded clinical RAG answer from retrieved chunks."""
    import re

    if not chunks:
        q_lower = question.strip().lower()
        if q_lower in {"hello", "hi", "hey", "help", "hello!", "hi!"}:
            return (
                "Hello! I am your clinical RAG assistant. "
                "I search and retrieve grounded clinical information from your cached medical records.\n\n"
                "You can ask clinical questions such as:\n"
                "- *What is the patient's name and age?*\n"
                "- *What medications is the patient taking?*\n"
                "- *What are the patient's latest HbA1c and glucose levels?*\n"
                "- *What are the documented vital signs?*\n"
                "- *Does the patient have any known drug allergies?*"
            )
        return (
            f"No relevant clinical records matching '{question}' were found in the local cache. "
            "Please upload or ingest a clinical document to search it."
        )

    q_lower = question.strip().lower()

    # Collect and clean lines from retrieved passages
    seen_lines = set()
    cleaned_passages: list[tuple[str, list[str]]] = []
    sources: list[str] = []

    for c in chunks:
        title = getattr(c, "title", "") or getattr(c, "doc_id", "Medical Record")
        if title not in sources:
            sources.append(title)
        snippet = getattr(c, "snippet", "") or ""
        clean_snip = re.sub(r"\[\[PAGE:\d+\]\]", "", snippet)
        clean_snip = re.sub(r"\[/?(?:b|em|strong|mark|\w+)\]", "", clean_snip)
        lines = [line.strip() for line in clean_snip.splitlines() if len(line.strip()) > 2]
        cleaned_passages.append((title, lines))

    sources_str = ", ".join(f"*{s}*" for s in sources)
    all_lines: list[tuple[str, str]] = []
    for title, lines in cleaned_passages:
        for l in lines:
            norm = l.lower()
            if not re.match(r"^[=-]{3,}$", l) and norm not in seen_lines:
                seen_lines.add(norm)
                all_lines.append((title, l))

    # 1. Medications & Prescriptions
    is_med_q = any(w in q_lower for w in ["medication", "medicine", "drug", "dose", "dosage", "taking", "prescribed", "pill", "tablet", "rx"])
    if is_med_q:
        med_lines = []
        for title, line in all_lines:
            ll = line.lower()
            if any(term in ll for term in ["mg", "tablet", "daily", "capsule", "od", "bd", "tid", "morning", "night", "started", "titrate", "increase to", "amlodipine", "metformin", "atorvastatin", "insulin", "statin", "ramipril"]):
                if not any(noise in ll for noise in ["mg/dl", "mg/g", "expected test signals", "synthetic test"]):
                    med_lines.append(line)
        if med_lines:
            bullets = "\n".join(f"- {m}" for m in med_lines[:8])
            return (
                f"### Documented Medications\n\n"
                f"Based on retrieved clinical records ({sources_str}):\n\n"
                f"{bullets}\n\n"
                f"*Review the original document for full reconciliation and clinical context.*"
            )

    # 2. Laboratory Investigations / Blood Tests
    is_lab_q = any(w in q_lower for w in ["lab", "test", "hba1c", "glucose", "cholesterol", "creatinine", "lipid", "investigation", "blood test", "fbg", "ppg"])
    if is_lab_q:
        lab_lines = []
        for title, line in all_lines:
            ll = line.lower()
            if any(term in ll for term in ["hba1c", "glucose", "mg/dl", "mmol/l", "cholesterol", "ref:", "high", "low", "creatinine", "egfr", "microalbuminuria", "eag"]):
                lab_lines.append(line)
        if lab_lines:
            bullets = "\n".join(f"- {l}" for l in lab_lines[:10])
            return (
                f"### Laboratory Findings\n\n"
                f"Based on retrieved clinical records ({sources_str}):\n\n"
                f"{bullets}"
            )

    # 3. Vital Signs & Physical Examination
    is_vital_q = any(w in q_lower for w in ["vital", "vitals", "blood pressure", "bp", "heart rate", "pulse", "temperature", "weight", "height", "bmi", "spo2"])
    if is_vital_q:
        vital_lines = []
        for title, line in all_lines:
            ll = line.lower()
            if any(term in ll for term in ["blood pressure", "heart rate", "bpm", "temperature", "respiratory rate", "spo2", "weight", "height", "bmi", "vitals:"]):
                vital_lines.append(line)
        if vital_lines:
            bullets = "\n".join(f"- {l}" for l in vital_lines[:8])
            return (
                f"### Documented Vital Signs\n\n"
                f"Based on retrieved clinical records ({sources_str}):\n\n"
                f"{bullets}"
            )

    # 4. Allergies & Adverse Reactions
    is_allergy_q = any(w in q_lower for w in ["allergy", "allergies", "allergic", "reaction", "rash", "nkda", "adverse"])
    if is_allergy_q:
        allergy_lines = [l for _, l in all_lines if any(w in l.lower() for w in ["allergy", "allergies", "allergic", "rash", "reaction", "nkda", "no known"])]
        if allergy_lines:
            bullets = "\n".join(f"- {l}" for l in allergy_lines[:6])
            return (
                f"### Allergy & Adverse Reaction Records\n\n"
                f"Based on retrieved clinical records ({sources_str}):\n\n"
                f"{bullets}"
            )

    # 5. Patient Profile & Demographics
    is_demo_q = any(w in q_lower for w in ["name", "patient name", "patient id", "who is", "age", "birth", "dob", "gender", "doctor", "physician", "hospital", "demographics", "profile", "contact", "address", "phone"])
    if is_demo_q:
        demo_lines = []
        for title, line in all_lines:
            ll = line.lower()
            if any(term in ll for term in ["patient id", "name:", "name ", "date of birth", "age:", "gender", "physician", "doctor", "hospital", "blood group", "contact", "address"]):
                demo_lines.append(line)
        if demo_lines:
            bullets = "\n".join(f"- {l}" for l in demo_lines[:8])
            return (
                f"### Patient Demographics & Profile\n\n"
                f"Based on retrieved clinical records ({sources_str}):\n\n"
                f"{bullets}"
            )

    # 6. Diagnoses & Medical History
    is_dx_q = any(w in q_lower for w in ["diagnosis", "diagnoses", "condition", "history", "assessment", "complaint", "illness", "problem"])
    if is_dx_q:
        dx_lines = [l for _, l in all_lines if any(w in l.lower() for w in ["diagnosis", "diagnoses", "history", "assessment", "complaint", "hypertension", "diabetes", "dyslipidemia"])]
        if dx_lines:
            bullets = "\n".join(f"- {l}" for l in dx_lines[:8])
            return (
                f"### Clinical Diagnosis & History\n\n"
                f"Based on retrieved clinical records ({sources_str}):\n\n"
                f"{bullets}"
            )

    # 7. General Clinical Term Overlap (General RAG Extraction)
    q_words = set(re.findall(r"[a-zA-Z0-9]+", q_lower)) - {"what", "when", "where", "which", "who", "the", "is", "are", "in", "of", "for", "to", "and", "a", "an", "patient", "document", "record"}
    scored_lines: list[tuple[int, str]] = []
    for title, line in all_lines:
        score = len(q_words & set(re.findall(r"[a-zA-Z0-9]+", line.lower())))
        if score > 0:
            scored_lines.append((score, line))
    scored_lines.sort(key=lambda x: x[0], reverse=True)

    if scored_lines:
        bullets = "\n".join(f"- {l}" for _, l in scored_lines[:8])
        return (
            f"### Clinical Findings\n\n"
            f"Based on retrieved clinical records ({sources_str}):\n\n"
            f"{bullets}"
        )

    # Fallback: summarize top lines cleanly
    fallback_bullets = "\n".join(f"- {l}" for _, l in all_lines[:6])
    return (
        f"### Clinical Record Excerpt\n\n"
        f"Based on retrieved clinical records ({sources_str}):\n\n"
        f"{fallback_bullets}"
    )


_extractive_answer = synthesize_rag_response


def _generate_ollama(question: str, chunks: list[SearchResult], model_name: str) -> str:
    context = "\n\n".join(f"[{c.title}]\n{c.snippet}" for c in chunks) or "(no local context found)"
    prompt = (
        "You are an expert on-device clinical RAG assistant. Answer the question using "
        "only the provided medical context below. State exact dosages, dates, values, and flags. "
        "Be concise, direct, and factual. Cite the document title when possible.\n\n"
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
    falls back to a structured deterministic clinical RAG summary if neither is active.
    """
    ollama_model = _get_active_ollama_model()
    if ollama_model:
        try:
            return _generate_ollama(question, chunks, ollama_model)
        except Exception:
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
        except Exception:
            pass

    return synthesize_rag_response(question, chunks)


