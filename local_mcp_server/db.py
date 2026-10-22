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


def search(query: str, top_k: int = config.DEFAULT_TOP_K) -> list[SearchResult]:
    if not query.strip():
        return []
    fts_query = _fts_escape(query)
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT d.doc_id AS doc_id,
                   d.title AS title,
                   snippet(documents_fts, 2, '[', ']', ' … ', 12) AS snippet,
                   bm25(documents_fts) AS rank
            FROM documents_fts
            JOIN documents d ON d.doc_id = documents_fts.doc_id
            WHERE documents_fts MATCH ?
            ORDER BY rank
            LIMIT ?

            """,
            (fts_query, top_k),
        ).fetchall()
    # bm25() in SQLite returns *lower is better*; flip the sign so callers
    # (and the hybrid agent merging local + cloud results) can treat a
    # higher score as "more relevant" consistently across both sources.
    return [
        SearchResult(doc_id=r["doc_id"], title=r["title"], snippet=r["snippet"], score=-r["rank"])
        for r in rows
    ]
