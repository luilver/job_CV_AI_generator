"""Keep a searchable history of the match analyses a user has run."""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from src.auth.db import get_connection, init_db

MAX_HISTORY = 100
JOB_TEXT_LIMIT = 20_000


def match_score(analysis: dict[str, Any] | None) -> int:
    """Percentage of requirements the CV matched (0 when unknown)."""
    if not analysis:
        return 0
    requirements = [r for r in analysis.get("requirements", []) if isinstance(r, dict)]
    if not requirements:
        return 0
    matched = sum(1 for r in requirements if str(r.get("status", "")).lower() == "match")
    return round(100 * matched / len(requirements))


def record_match(
    user_id: int,
    analysis: dict[str, Any],
    *,
    job_url: str = "",
    job_text: str = "",
    compensation: dict[str, Any] | None = None,
) -> int:
    """Store one analysis and return its id. Keeps only the newest MAX_HISTORY rows."""
    init_db()
    if not isinstance(analysis, dict) or not analysis:
        raise ValueError("There is no analysis to save.")
    requirements = [r for r in analysis.get("requirements", []) if isinstance(r, dict)]

    conn = get_connection()
    try:
        with conn:
            cursor = conn.execute(
                """
                INSERT INTO match_history (
                    user_id, position_name, company, job_url, job_text, analysis,
                    compensation, requirements_matched, requirements_total
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    user_id,
                    str(analysis.get("position_name") or "")[:200],
                    str(analysis.get("institution_name") or "")[:200],
                    str(job_url or "")[:500],
                    str(job_text or "")[:JOB_TEXT_LIMIT],
                    json.dumps(analysis, ensure_ascii=False),
                    json.dumps(compensation, ensure_ascii=False) if compensation else "",
                    sum(1 for r in requirements if str(r.get("status", "")).lower() == "match"),
                    len(requirements),
                ),
            )
            match_id = int(cursor.lastrowid or 0)
            conn.execute(
                """
                DELETE FROM match_history
                 WHERE user_id = ?
                   AND id NOT IN (SELECT id FROM match_history WHERE user_id = ? ORDER BY id DESC LIMIT ?)
                """,
                (user_id, user_id, MAX_HISTORY),
            )
        return match_id
    finally:
        conn.close()


def list_matches(user_id: int, limit: int = 50) -> list[dict[str, Any]]:
    """Newest first, without the heavy job text."""
    init_db()
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT id, position_name, company, job_url, compensation,
                   requirements_matched, requirements_total, created_at
              FROM match_history
             WHERE user_id = ?
             ORDER BY id DESC
             LIMIT ?
            """,
            (user_id, int(limit)),
        ).fetchall()
    finally:
        conn.close()
    return [_summary(row) for row in rows]


def get_match(user_id: int, match_id: int) -> dict[str, Any] | None:
    """One saved match with the analysis decoded, or None if it is not theirs."""
    init_db()
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM match_history WHERE id = ? AND user_id = ?", (match_id, user_id)
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        return None

    record = _summary(row)
    record["job_text"] = row["job_text"]
    record["analysis"] = _loads(row["analysis"]) or {}
    record["compensation"] = _loads(row["compensation"])
    return record


def delete_match(user_id: int, match_id: int) -> bool:
    init_db()
    conn = get_connection()
    try:
        with conn:
            cursor = conn.execute(
                "DELETE FROM match_history WHERE id = ? AND user_id = ?", (match_id, user_id)
            )
        return cursor.rowcount > 0
    finally:
        conn.close()


def clear_history(user_id: int) -> int:
    """Delete every saved match for the user. Returns how many were removed."""
    init_db()
    conn = get_connection()
    try:
        with conn:
            cursor = conn.execute("DELETE FROM match_history WHERE user_id = ?", (user_id,))
        return int(cursor.rowcount or 0)
    finally:
        conn.close()


def _loads(value: str) -> dict[str, Any] | None:
    """Decode a JSON column, tolerating anything unreadable."""
    if not value:
        return None
    try:
        loaded = json.loads(value)
    except (TypeError, ValueError):
        return None
    return loaded if isinstance(loaded, dict) else None


def _summary(row: sqlite3.Row) -> dict[str, Any]:
    matched = row["requirements_matched"] or 0
    total = row["requirements_total"] or 0
    return {
        "id": row["id"],
        "position_name": row["position_name"],
        "company": row["company"],
        "job_url": row["job_url"],
        "compensation": _loads(row["compensation"]),
        "requirements_matched": matched,
        "requirements_total": total,
        "score": round(100 * matched / total) if total else 0,
        "created_at": row["created_at"],
    }
