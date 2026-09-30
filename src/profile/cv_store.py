"""Store the one CV each user keeps on their profile (.tex or .pdf)."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from src.auth.db import get_connection, init_db
from src.parsers.latex_parser import clean_latex
from src.parsers.pdf_parser import extract_pdf_text

MAX_CV_BYTES = 4 * 1024 * 1024
MIN_TEXT_LENGTH = 120
ALLOWED_TYPES = ("tex", "pdf")
MIME_TYPES = {"tex": "application/x-tex", "pdf": "application/pdf"}


def detect_filetype(filename: str) -> str:
    """Return 'tex' or 'pdf' from a filename, raising ValueError if unsupported."""
    suffix = Path(str(filename)).suffix.lower().lstrip(".")
    if suffix not in ALLOWED_TYPES:
        raise ValueError("Only .tex and .pdf CVs are supported.")
    return suffix


def extract_cv_text(filetype: str, data: bytes) -> str:
    """Plain text for the AI: cleaned LaTeX, or the text layer of a PDF."""
    if filetype == "tex":
        text = clean_latex(data.decode("utf-8", errors="replace"))
    else:
        text = extract_pdf_text(data)
    if len(text.strip()) < MIN_TEXT_LENGTH:
        raise ValueError("That file does not contain enough readable CV text.")
    return text


def save_cv(user_id: int, filename: str, data: bytes) -> dict[str, Any]:
    """Store (or replace) the user's CV. Returns a summary of the saved file."""
    init_db()
    filename = str(filename).strip() or "cv"
    filetype = detect_filetype(filename)
    if not data:
        raise ValueError("The uploaded file is empty.")
    if len(data) > MAX_CV_BYTES:
        raise ValueError("That file is larger than 4 MB — please upload a smaller CV.")
    text = extract_cv_text(filetype, data)

    conn = get_connection()
    try:
        with conn:
            conn.execute("DELETE FROM saved_cvs WHERE user_id = ?", (user_id,))
            conn.execute(
                """
                INSERT INTO saved_cvs (user_id, filename, filetype, content, text, size_bytes)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (user_id, filename, filetype, sqlite3.Binary(data), text, len(data)),
            )
        row = conn.execute("SELECT * FROM saved_cvs WHERE user_id = ?", (user_id,)).fetchone()
    finally:
        conn.close()
    return _summary(row)


def get_cv(user_id: int) -> dict[str, Any] | None:
    """
    The user's stored CV, or None.

    'data' is the exact uploaded bytes (for downloads), 'raw' is the source the
    AI should see — the original .tex, or the extracted text for a PDF, since a
    binary PDF is meaningless to a language model.
    """
    init_db()
    conn = get_connection()
    try:
        row = conn.execute("SELECT * FROM saved_cvs WHERE user_id = ?", (user_id,)).fetchone()
    finally:
        conn.close()
    if row is None:
        return None
    content = row["content"]
    data = bytes(content) if isinstance(content, (bytes, memoryview)) else str(content).encode("utf-8")
    record = _summary(row)
    record["raw"] = data.decode("utf-8", errors="replace") if record["filetype"] == "tex" else record["text"]
    record["data"] = data
    return record


def delete_cv(user_id: int) -> bool:
    """Remove the stored CV. Returns True if there was one."""
    init_db()
    conn = get_connection()
    try:
        with conn:
            cursor = conn.execute("DELETE FROM saved_cvs WHERE user_id = ?", (user_id,))
        return cursor.rowcount > 0
    finally:
        conn.close()


def describe_cv(record: dict[str, Any] | None) -> str:
    """Short human-readable summary for the sidebar."""
    if not record:
        return "No CV saved yet"
    size_kb = record["size_bytes"] / 1024
    return f"{record['filename']} ({record['filetype'].upper()}, {size_kb:.0f} KB)"


def _summary(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "filename": row["filename"],
        "filetype": row["filetype"],
        "text": row["text"],
        "size_bytes": row["size_bytes"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }
