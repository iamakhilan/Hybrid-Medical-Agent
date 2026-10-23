"""
SQLite-backed local cache.

This is the "Local Cache / SQLite" box in the architecture diagram. It
stores document text (usually the output of a previous cloud extraction,
or anything the doctor's UI has already seen) and makes it searchable
instantly, offline, with zero network round-trips.

Search uses SQLite's built-in FTS5 full-text index with BM25 ranking.
That's a deliberate choice over a vector-similarity cache: FTS5 ships with
every Python's sqlite3 build (no extra native extension to compile on a
Windows dev machine), and for the "fast, local, good-enough" tier of a
hybrid system, keyword/BM25 recall is a perfectly reasonable trade-off
against the deep, embedding-based recall the cloud side provides.

Upgrade path: if you want real local vector search later, swap this
module's search() implementation for the `sqlite-vec` extension
(https://github.com/asg017/sqlite-vec) without changing the MCP tool
surface in server.py.
"""

import sqlite3
import time
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Iterator

from local_mcp_server import config

_SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
    doc_id      TEXT PRIMARY KEY,
    title       TEXT NOT NULL,
    content     TEXT NOT NULL,
    page_content TEXT NOT NULL DEFAULT '',
    source      TEXT NOT NULL DEFAULT 'local',
    created_at  REAL NOT NULL
);

CREATE VIRTUAL TABLE IF NOT EXISTS documents_fts USING fts5(
    doc_id UNINDEXED,
    title,
    content,
    content='documents',
    content_rowid='rowid'
);

CREATE TRIGGER IF NOT EXISTS documents_ai AFTER INSERT ON documents BEGIN
    INSERT INTO documents_fts(rowid, doc_id, title, content)
    VALUES (new.rowid, new.doc_id, new.title, new.content);
END;

CREATE TRIGGER IF NOT EXISTS documents_ad AFTER DELETE ON documents BEGIN
    INSERT INTO documents_fts(documents_fts, rowid, doc_id, title, content)
    VALUES ('delete', old.rowid, old.doc_id, old.title, old.content);
END;

CREATE TRIGGER IF NOT EXISTS documents_au AFTER UPDATE ON documents BEGIN
    INSERT INTO documents_fts(documents_fts, rowid, doc_id, title, content)
    VALUES ('delete', old.rowid, old.doc_id, old.title, old.content);
    INSERT INTO documents_fts(rowid, doc_id, title, content)
    VALUES (new.rowid, new.doc_id, new.title, new.content);
END;
"""


@dataclass
class SearchResult:
    doc_id: str
    title: str
    snippet: str
    score: float


@contextmanager
def get_connection() -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(config.LOCAL_DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    with get_connection() as conn:
        conn.executescript(_SCHEMA)
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(documents)")}
        if "page_content" not in columns:
            conn.execute("ALTER TABLE documents ADD COLUMN page_content TEXT NOT NULL DEFAULT ''")


def upsert_document(doc_id: str, title: str, content: str, source: str = "local", page_content: str = "") -> None:
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO documents (doc_id, title, content, page_content, source, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(doc_id) DO UPDATE SET
                title = excluded.title,
                content = excluded.content,
                page_content = excluded.page_content,
                source = excluded.source
            """,
            (doc_id, title, content, page_content, source, time.time()),
        )


def get_document(doc_id: str) -> dict | None:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT doc_id, title, content, page_content, source, created_at FROM documents WHERE doc_id = ?",
            (doc_id,),
        ).fetchone()
        return dict(row) if row else None


def list_documents(limit: int = 50) -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT doc_id, title, source, created_at FROM documents ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]


_STOPWORDS = {
    "a", "an", "the", "is", "are", "was", "were", "be", "been", "being",
    "what", "when", "where", "who", "whom", "which", "why", "how",
    "to", "of", "in", "on", "at", "for", "with", "about", "into", "over",
    "and", "or", "but", "if", "then", "than", "so", "this", "that", "these",
    "those", "it", "its", "do", "does", "did", "has", "have", "had",
    "happened", "happen", "please", "can", "could", "would", "should",
}


def _fts_escape(query: str) -> str:
    # Treat the query as a bag of terms rather than raw FTS5 syntax, so
    # punctuation typed by a user (or a doctor's free-text question) never
    # raises an FTS5 syntax error. Terms are OR'd together (with common
    # stopwords dropped) rather than AND'd, since a free-text question
    # like "what happened to the HbA1c" should match on its meaningful
    # keywords (HbA1c) rather than requiring every word -- including
    # "what"/"the"/"to" -- to appear verbatim in the cached document.
    # BM25 ranking still surfaces the best-matching document first.
    raw_terms = [t for t in query.replace('"', " ").split() if t]
    terms = [t for t in raw_terms if t.lower() not in _STOPWORDS] or raw_terms
    if not terms:
        return '""'
    return " OR ".join(f'"{t}"' for t in terms)


_GENERIC_CLINICAL_WORDS = {
    "patient", "patients", "record", "records", "document", "documents",
    "clinical", "medical", "data", "test", "tests", "value", "values",
    "level", "levels", "result", "results", "info", "information", "detail", "details",
}


def _extract_best_passages(
    doc_id: str,
    title: str,
    content: str,
    page_content: str,
    query: str,
    top_k: int = 3,
) -> list[tuple[str, float]]:
    """Extract the most relevant clinical sections/paragraphs from a document for a query."""
    import re

    # Meaningful query terms
    raw_terms = re.findall(r"[A-Za-z0-9]+", query.lower())
    q_words = [w for w in raw_terms if len(w) > 1 and w not in _STOPWORDS] or raw_terms
    if not q_words:
        return []

    specific_words = [w for w in q_words if w not in _GENERIC_CLINICAL_WORDS] or q_words

    # Use page_content if available, otherwise content
    target_text = page_content if page_content else content
    if not target_text.strip():
        return []

    # Split into sections by headers, dividers, or double newlines
    raw_sections = re.split(r"\n\s*(?:[-=_]{3,}|(?=[A-Z0-9\s]{3,}:))\s*\n|\n\s*\n", target_text)
    sections = [s.strip() for s in raw_sections if len(s.strip()) > 15]
    if not sections:
        sections = [target_text.strip()]

    scored_sections = []
    q_lower = query.strip().lower()

    # Intent detection for better clinical ranking
    is_med_q = any(w in q_lower for w in ["medication", "medicine", "drug", "taking", "prescribed", "dose", "dosage", "pill", "tablet", "treatment", "rx"])
    is_lab_q = any(w in q_lower for w in ["lab", "test", "investigation", "glucose", "hba1c", "fbg", "ppg", "lipid", "cholesterol", "creatinine", "blood test"])
    is_vital_q = any(w in q_lower for w in ["vital", "vitals", "bp", "blood pressure", "heart rate", "pulse", "temp", "temperature", "weight", "height", "bmi", "spo2"])
    is_allergy_q = any(w in q_lower for w in ["allergy", "allergies", "allergic", "reaction", "rash", "nkda", "adverse"])
    is_demo_q = any(w in q_lower for w in ["name", "patient name", "age", "dob", "birth", "gender", "doctor", "physician", "hospital", "who is", "address"])

    # Check if document header/title matches specific query entities (e.g. patient name)
    doc_header_text = (title + " " + content[:800]).lower()
    doc_header_words = set(re.findall(r"[A-Za-z0-9]+", doc_header_text))
    doc_entity_bonus = float(len(set(specific_words) & doc_header_words)) * 2.5

    for sec in sections:
        sec_lower = sec.lower()
        sec_words = set(re.findall(r"[A-Za-z0-9]+", sec_lower))
        specific_matches = len(set(specific_words) & sec_words)
        generic_matches = len(set(q_words) & sec_words) - specific_matches

        # If specific terms exist in query, require at least one specific match or clinical intent match
        if specific_words != q_words and specific_matches == 0:
            base_score = float(generic_matches) * 0.5 + doc_entity_bonus * 0.5
        else:
            base_score = float(specific_matches) * 3.0 + float(generic_matches) * 0.5 + doc_entity_bonus

        if base_score <= 0 and not (is_med_q or is_lab_q or is_vital_q or is_allergy_q):
            continue

        score = base_score
        # Bonus for exact query phrase in section
        if len(q_lower) > 3 and q_lower in sec_lower:
            score += 6.0

        # Clinical intent alignment bonus
        if is_med_q:
            has_med_dose = bool(re.search(r"\b\d+\s*(?:mg|mcg|g)\s*(?:once|daily|od|bd|tid|qid|po|morning|night)?\b", sec_lower)) and ("mg/dl" not in sec_lower or "daily" in sec_lower)
            has_med_terms = any(m in sec_lower for m in ["tablet", "capsule", "daily", "once daily", "twice daily", "medication", "prescribed", "amlodipine", "atorvastatin", "metformin", "insulin", "lisinopril", "aspirin"])
            if has_med_dose or has_med_terms:
                score += 8.0
        if is_lab_q and any(m in sec_lower for m in ["hba1c", "glucose", "mg/dl", "mmol/l", "ref:", "high", "low", "lipid", "cholesterol", "fasting", "investigation"]):
            score += 7.0
        if is_vital_q and any(m in sec_lower for m in ["mmhg", "bpm", "bp", "blood pressure", "vitals", "pulse", "weight", "bmi", "spo2", "heart rate"]):
            score += 7.0
        if is_allergy_q and any(m in sec_lower for m in ["allergy", "allergies", "allergic", "rash", "reaction", "nkda", "no known"]):
            score += 7.0
        if is_demo_q and any(m in sec_lower for m in ["patient id", "name:", "name ", "date of birth", "age:", "gender", "physician", "doctor", "hospital"]):
            score += 5.0

        # Strip page markers from display snippet for cleanliness
        clean_sec = re.sub(r"\[\[PAGE:\d+\]\]\s*", "", sec).strip()
        scored_sections.append((clean_sec, score))

    scored_sections.sort(key=lambda x: x[1], reverse=True)
    return scored_sections[:top_k]


def search(query: str, top_k: int = config.DEFAULT_TOP_K) -> list[SearchResult]:
    """Search locally cached documents and return rich, relevant clinical passages."""
    import re

    if not query.strip():
        return []

    fts_query = _fts_escape(query)
    results: list[SearchResult] = []

    with get_connection() as conn:
        try:
            rows = conn.execute(
                """
                SELECT d.doc_id AS doc_id,
                       d.title AS title,
                       d.content AS content,
                       d.page_content AS page_content,
                       snippet(documents_fts, 2, '', '', ' … ', 64) AS fts_snippet,
                       bm25(documents_fts) AS rank
                FROM documents_fts
                JOIN documents d ON d.doc_id = documents_fts.doc_id
                WHERE documents_fts MATCH ?
                ORDER BY rank
                LIMIT ?
                """,
                (fts_query, max(top_k * 2, 10)),
            ).fetchall()
        except sqlite3.OperationalError:
            rows = []

        # If FTS returns matches, extract the best contextual passages from each
        for r in rows:
            doc_id = r["doc_id"]
            title = r["title"]
            content = r["content"] or ""
            page_content = r["page_content"] or ""
            fts_snip = (r["fts_snippet"] or "").strip()
            score = -r["rank"]

            passages = _extract_best_passages(doc_id, title, content, page_content, query, top_k=2)
            if passages:
                for passage_text, p_score in passages:
                    results.append(
                        SearchResult(
                            doc_id=doc_id,
                            title=title,
                            snippet=passage_text,
                            score=score + p_score,
                        )
                    )
            elif fts_snip:
                clean_snip = re.sub(r"\[/?\w+\]", "", fts_snip).strip()
                results.append(
                    SearchResult(
                        doc_id=doc_id,
                        title=title,
                        snippet=clean_snip,
                        score=score,
                    )
                )

        # Fallback if FTS5 returned nothing: direct term search over cached documents
        if not results:
            all_docs = conn.execute(
                "SELECT doc_id, title, content, page_content FROM documents ORDER BY created_at DESC LIMIT 50"
            ).fetchall()
            for r in all_docs:
                passages = _extract_best_passages(
                    r["doc_id"], r["title"], r["content"] or "", r["page_content"] or "", query, top_k=2
                )
                for passage_text, p_score in passages:
                    results.append(
                        SearchResult(
                            doc_id=r["doc_id"],
                            title=r["title"],
                            snippet=passage_text,
                            score=p_score,
                        )
                    )

    # Sort results by relevance score descending and take top_k
    results.sort(key=lambda x: x.score, reverse=True)
    return results[:top_k]

