"""
PDF and plain-text extraction utility.

Keeps the API layer thin: call extract_text() on any uploaded file and
get back a plain string regardless of whether it was a PDF or .txt file.

Uses pypdf (pure Python, no system dependencies, no Poppler/Ghostscript
to install on Windows) for PDF parsing. Falls back gracefully when a
page has no extractable text (e.g. scanned images -- would need OCR,
which is out of scope here).
"""

from __future__ import annotations

import io


def extract_pages(filename: str, file_bytes: bytes) -> list[str]:
    """Extract one text string per PDF page; plain text files are page 1."""
    if filename.lower().endswith(".pdf"):
        try:
            from pypdf import PdfReader
        except ImportError as exc:
            raise ValueError("pypdf is not installed. Run: pip install pypdf") from exc
        reader = PdfReader(io.BytesIO(file_bytes))
        pages = [(page.extract_text() or "").strip() or f"[Page {i + 1}: no extractable text — may be a scanned image]" for i, page in enumerate(reader.pages)]
        if not pages:
            raise ValueError("PDF has no pages or no extractable text.")
        return pages
    try:
        decoded = file_bytes.decode("utf-8")
    except UnicodeDecodeError:
        decoded = file_bytes.decode("latin-1")
    return [decoded]


def extract_text(filename: str, file_bytes: bytes) -> str:
    """
    Extract plain text from an uploaded file.

    Supports:
      - PDF (.pdf)  -- via pypdf
      - Plain text  (.txt, .md, anything else) -- decoded as UTF-8

    Returns the extracted text string. Raises ValueError with a
    human-readable message if the file cannot be parsed.
    """
    return "\n\n".join(extract_pages(filename, file_bytes))


def _extract_pdf(file_bytes: bytes) -> str:
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise ValueError(
            "pypdf is not installed. Run: pip install pypdf"
        ) from exc

    reader = PdfReader(io.BytesIO(file_bytes))
    pages: list[str] = []
    for i, page in enumerate(reader.pages):
        text = page.extract_text() or ""
        text = text.strip()
        if text:
            pages.append(text)
        else:
            # Page had no extractable text (likely a scanned image)
            pages.append(f"[Page {i + 1}: no extractable text — may be a scanned image]")

    if not pages:
        raise ValueError("PDF has no pages or no extractable text.")

    return "\n\n".join(pages)
