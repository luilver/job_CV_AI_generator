"""Persistence for LinkedIn connections, job postings and discovery settings.

Jobs are global (a posting is the same for everyone) while evaluations, settings
and generated materials are per user. That split means the same posting is only
ever fetched and scored once per user.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any

from src.auth.db import get_connection, init_db

STATE_TTL_MINUTES = 15
MAX_KEYWORDS = 8
MAX_KEYWORD_LENGTH = 60
JOB_TEXT_LIMIT = 20_000


# ---------------------------------------------------------------------------
# time helpers
# ---------------------------------------------------------------------------


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def now_iso() -> str:
    return utc_now().isoformat()


def _parse_timestamp(value: object) -> datetime | None:
    if not value:
        return None
    try:
        stamp = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    return stamp if stamp.tzinfo else stamp.replace(tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# LinkedIn accounts
# ---------------------------------------------------------------------------


def save_linkedin_account(user_id: int, member_id: str, *, name: str = "", email: str = "", picture: str = "", scopes: str = "") -> dict[str, Any]:
    """Upsert the connected LinkedIn identity for a user."""
    init_db()
    member_id = str(member_id or "").strip()
    if not member_id:
        raise ValueError("LinkedIn did not return a member id.")
    conn = get_connection()
    try:
        with conn:
            conn.execute(
                """
                INSERT INTO linkedin_accounts (
                    user_id, linkedin_member_id, member_name, member_email,
                    picture_url, scopes, connected_at, last_verified_at
                ) VALUES (?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'))
                ON CONFLICT(user_id) DO UPDATE SET
                    linkedin_member_id = excluded.linkedin_member_id,
                    member_name       = excluded.member_name,
                    member_email      = excluded.member_email,
                    picture_url       = excluded.picture_url,
                    scopes            = excluded.scopes,
                    last_verified_at  = excluded.last_verified_at
                """,
                (int(user_id), member_id, str(name or "")[:200], str(email or "")[:320], str(picture or "")[:500], str(scopes or "")[:200]),
            )
    finally:
        conn.close()
    return get_linkedin_account(user_id) or {}


def get_linkedin_account(user_id: int) -> dict[str, Any] | None:
    """The user's LinkedIn connection, or None."""
    init_db()
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM linkedin_accounts WHERE user_id = ?", (int(user_id),)
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        return None
    return {
        "linkedin_member_id": row["linkedin_member_id"],
        "member_name": row["member_name"],
        "member_email": row["member_email"],
        "picture_url": row["picture_url"],
        "scopes": row["scopes"],
        "connected_at": row["connected_at"],
        "last_verified_at": row["last_verified_at"],
    }


def disconnect_linkedin(user_id: int) -> bool:
    """Remove the connection. Returns True if there was one."""
    init_db()
    conn = get_connection()
    try:
        with conn:
            cursor = conn.execute(
                "DELETE FROM linkedin_accounts WHERE user_id = ?", (int(user_id),)
            )
        return cursor.rowcount > 0
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# OAuth state (CSRF protection for the connect flow)
# ---------------------------------------------------------------------------


def store_oauth_state(user_id: int, state: str) -> None:
    init_db()
    conn = get_connection()
    try:
        with conn:
            conn.execute(
                "INSERT INTO oauth_states (state, user_id) VALUES (?, ?)",
                (state, int(user_id)),
            )
            # The connect flow is one round trip; anything older than the TTL is debris.
            conn.execute(
                "DELETE FROM oauth_states WHERE created_at < datetime('now', ?)",
                (f"-{STATE_TTL_MINUTES} minutes",),
            )
    finally:
        conn.close()


def consume_oauth_state(state: str) -> int | None:
    """Delete the state and return its user id, or None when it is unknown/expired."""
    init_db()
    state = str(state or "").strip()
    if not state:
        return None
    conn = get_connection()
    try:
        with conn:
            row = conn.execute("SELECT user_id, created_at FROM oauth_states WHERE state = ?", (state,)).fetchone()
            if row is None:
                return None
            created = _parse_timestamp(row["created_at"])
            conn.execute("DELETE FROM oauth_states WHERE state = ?", (state,))
    finally:
        conn.close()
    if created is None:
        return None
    if utc_now() - created > _minutes(STATE_TTL_MINUTES):
        return None
    return int(row["user_id"])


def peek_oauth_state(state: str) -> int | None:
    """Return the user a live state was minted for, without consuming it.

    The OAuth redirect comes back in a brand-new Streamlit session, so the
    callback cannot read who is connecting from session_state. The state is the
    proof: single-use, short-lived, and stored against exactly one user id.
    """
    init_db()
    state = str(state or "").strip()
    if not state:
        return None
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT user_id, created_at FROM oauth_states WHERE state = ?", (state,)
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        return None
    created = _parse_timestamp(row["created_at"])
    if created is None:
        return None
    if utc_now() - created > _minutes(STATE_TTL_MINUTES):
        return None
    return int(row["user_id"])


def _minutes(count: int):
    from datetime import timedelta

    return timedelta(minutes=count)


# ---------------------------------------------------------------------------
# Discovery settings
# ---------------------------------------------------------------------------

DEFAULT_SETTINGS: dict[str, Any] = {
    "enabled": 0,
    "keywords": "",
    "location": "United States",
    "remote_only": 1,
    "max_age_days": 3,
    "min_prefilter": 25,
    "min_match_score": 80,
    "daily_llm_budget": 10,
    "daily_gen_budget": 3,
    "generate_materials": 1,
    "search_pages": 2,
    "digest_enabled": 1,
    "digest_hour": 7,
    "digest_tz": "UTC",
}


def _clamp(value: int, low: int, high: int) -> int:
    return max(low, min(high, int(value)))


def clean_keywords(raw: str) -> list[str]:
    """Split a comma/newline separated list into at most MAX_KEYWORDS queries."""
    seen: list[str] = []
    for chunk in str(raw or "").replace("\n", ",").replace(";", ",").split(","):
        keyword = " ".join(chunk.split())[:MAX_KEYWORD_LENGTH].strip()
        if keyword and keyword.lower() not in {k.lower() for k in seen}:
            seen.append(keyword)
    return seen[:MAX_KEYWORDS]


def get_settings(user_id: int) -> dict[str, Any]:
    """Settings with defaults applied, plus the parsed keyword list."""
    init_db()
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM discovery_settings WHERE user_id = ?", (int(user_id),)
        ).fetchone()
    finally:
        conn.close()
    settings = dict(DEFAULT_SETTINGS)
    if row is not None:
        for key in DEFAULT_SETTINGS:
            if key in row.keys():
                settings[key] = row[key]
    settings["keywords"] = str(settings.get("keywords") or "")
    settings["keyword_list"] = clean_keywords(settings["keywords"])
    settings["enabled"] = int(settings["enabled"] or 0)
    settings["generate_materials"] = int(settings["generate_materials"] or 0)
    settings["digest_enabled"] = int(settings["digest_enabled"] or 0)
    settings["remote_only"] = int(settings["remote_only"] or 0)
    return settings


def save_settings(user_id: int, **changes: Any) -> dict[str, Any]:
    """Update discovery settings. Unknown keys and text fields are ignored."""
    init_db()
    current = get_settings(user_id)
    allowed = set(DEFAULT_SETTINGS) | {"last_run_report"}
    updates = {key: value for key, value in changes.items() if key in allowed}

    for flag in ("enabled", "generate_materials", "digest_enabled", "remote_only"):
        if flag in updates:
            updates[flag] = 1 if updates[flag] in (True, 1, "1", "true", "yes", "on") else 0

    if "keywords" in updates:
        updates["keywords"] = ", ".join(clean_keywords(updates["keywords"]))
    if "location" in updates:
        updates["location"] = " ".join(str(updates["location"] or "").split())[:120] or DEFAULT_SETTINGS["location"]

    bounds = {
        "max_age_days": (1, 30),
        "min_prefilter": (0, 100),
        "min_match_score": (1, 100),
        "daily_llm_budget": (1, 100),
        "daily_gen_budget": (0, 50),
        "search_pages": (1, 10),
        "digest_hour": (0, 23),
    }
    for key, (low, high) in bounds.items():
        if key in updates:
            try:
                updates[key] = _clamp(int(updates[key]), low, high)
            except (TypeError, ValueError):
                raise ValueError(f"{key.replace('_', ' ').capitalize()} must be a number.") from None
    if "digest_tz" in updates:
        updates["digest_tz"] = str(updates["digest_tz"] or "UTC")[:60]

    merged = {**current, **updates}
    # A generation budget of zero means "do not pre-generate", so keep the flag honest.
    if int(merged["daily_gen_budget"]) == 0:
        merged["generate_materials"] = 0

    conn = get_connection()
    try:
        with conn:
            conn.execute(
                """
                INSERT INTO discovery_settings (
                    user_id, enabled, keywords, location, remote_only, max_age_days,
                    min_prefilter, min_match_score, daily_llm_budget, daily_gen_budget,
                    generate_materials, search_pages, digest_enabled, digest_hour, digest_tz
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    enabled = excluded.enabled,
                    keywords = excluded.keywords,
                    location = excluded.location,
                    remote_only = excluded.remote_only,
                    max_age_days = excluded.max_age_days,
                    min_prefilter = excluded.min_prefilter,
                    min_match_score = excluded.min_match_score,
                    daily_llm_budget = excluded.daily_llm_budget,
                    daily_gen_budget = excluded.daily_gen_budget,
                    generate_materials = excluded.generate_materials,
                    search_pages = excluded.search_pages,
                    digest_enabled = excluded.digest_enabled,
                    digest_hour = excluded.digest_hour,
                    digest_tz = excluded.digest_tz
                """,
                (
                    int(user_id),
                    int(merged["enabled"]),
                    merged["keywords"],
                    merged["location"],
                    int(merged["remote_only"]),
                    int(merged["max_age_days"]),
                    int(merged["min_prefilter"]),
                    int(merged["min_match_score"]),
                    int(merged["daily_llm_budget"]),
                    int(merged["daily_gen_budget"]),
                    int(merged["generate_materials"]),
                    int(merged["search_pages"]),
                    int(merged["digest_enabled"]),
                    int(merged["digest_hour"]),
                    merged["digest_tz"],
                ),
            )
    finally:
        conn.close()
    return get_settings(user_id)


def mark_run(user_id: int, report: dict[str, Any]) -> None:
    """Record that a discovery run finished, and keep its summary for the UI."""
    init_db()
    conn = get_connection()
    try:
        with conn:
            conn.execute(
                """
                INSERT INTO discovery_settings (user_id, last_run_at, last_run_report)
                VALUES (?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    last_run_at = excluded.last_run_at,
                    last_run_report = excluded.last_run_report
                """,
                (int(user_id), now_iso(), json.dumps(report, ensure_ascii=False)[:8000]),
            )
    finally:
        conn.close()


def last_run_report(user_id: int) -> dict[str, Any] | None:
    init_db()
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT last_run_at, last_run_report FROM discovery_settings WHERE user_id = ?",
            (int(user_id),),
        ).fetchone()
    finally:
        conn.close()
    if row is None or not row["last_run_report"]:
        return None
    try:
        loaded = json.loads(row["last_run_report"])
    except (TypeError, ValueError):
        return None
    if not isinstance(loaded, dict):
        return None
    # The timestamp lives in its own column; the UI reads it from the report.
    loaded.setdefault("last_run_at", str(row["last_run_at"] or ""))
    return loaded


def mark_digest_sent(user_id: int) -> None:
    """Stamp the moment a digest went out, so the day's second run does not repeat it."""
    init_db()
    conn = get_connection()
    try:
        with conn:
            conn.execute(
                """
                INSERT INTO discovery_settings (user_id, last_digest_at)
                VALUES (?, ?)
                ON CONFLICT(user_id) DO UPDATE SET last_digest_at = excluded.last_digest_at
                """,
                (int(user_id), now_iso()),
            )
    finally:
        conn.close()


def last_digest_at(user_id: int) -> datetime | None:
    init_db()
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT last_digest_at FROM discovery_settings WHERE user_id = ?", (int(user_id),)
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        return None
    return _parse_timestamp(row["last_digest_at"])


def _zoneinfo(name: str):
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

    try:
        return ZoneInfo(str(name or "UTC"))
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo("UTC")


def digest_is_due(user_id: int, *, now: datetime | None = None) -> tuple[bool, str]:
    """Whether the scheduled digest should run for this user right now.

    Returns (due, reason-if-not). The scheduled worker uses this so a restart or a
    second container cannot mail the same digest twice in a day; the manual "Run
    now" button deliberately bypasses it.
    """
    settings = get_settings(user_id)
    if not int(settings.get("enabled") or 0):
        return False, "Discovery is switched off."
    if not int(settings.get("digest_enabled") or 0):
        return False, "The digest is switched off."

    tz = _zoneinfo(settings.get("digest_tz") or "UTC")
    moment = (now or datetime.now(timezone.utc)).astimezone(tz)
    hour = int(settings.get("digest_hour") or 0)
    if moment.hour < hour:
        return False, f"Before the {hour:02d}:00 {settings.get('digest_tz') or 'UTC'} digest hour."

    sent_at = last_digest_at(user_id)
    if sent_at is not None and sent_at.astimezone(tz).date() == moment.date():
        return False, "Today's digest was already sent."
    return True, ""



# ---------------------------------------------------------------------------
# Skill profile
# ---------------------------------------------------------------------------


def get_skills(user_id: int) -> list[str]:
    init_db()
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT skills FROM skill_profiles WHERE user_id = ?", (int(user_id),)
        ).fetchone()
    finally:
        conn.close()
    if row is None or not row["skills"]:
        return []
    try:
        loaded = json.loads(row["skills"])
    except (TypeError, ValueError):
        return []
    return [str(item).strip() for item in loaded if str(item).strip()] if isinstance(loaded, list) else []


def save_skills(user_id: int, skills: list[str], cv_updated_at: str = "") -> list[str]:
    """Cache the extracted skills against the CV version they came from."""
    init_db()
    cleaned: list[str] = []
    for item in skills:
        value = " ".join(str(item or "").split())[:80]
        if value and value.lower() not in {c.lower() for c in cleaned}:
            cleaned.append(value)
    conn = get_connection()
    try:
        with conn:
            conn.execute(
                """
                INSERT INTO skill_profiles (user_id, skills, cv_updated_at, updated_at)
                VALUES (?, ?, ?, datetime('now'))
                ON CONFLICT(user_id) DO UPDATE SET
                    skills = excluded.skills,
                    cv_updated_at = excluded.cv_updated_at,
                    updated_at = excluded.updated_at
                """,
                (int(user_id), json.dumps(cleaned, ensure_ascii=False), str(cv_updated_at or "")),
            )
    finally:
        conn.close()
    return cleaned


def skills_are_current(user_id: int, cv_updated_at: str) -> bool:
    """True when a cached skill profile was built from this exact CV."""
    init_db()
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT cv_updated_at FROM skill_profiles WHERE user_id = ?", (int(user_id),)
        ).fetchone()
    finally:
        conn.close()
    return bool(row and row["cv_updated_at"] and row["cv_updated_at"] == str(cv_updated_at or ""))


def delete_skill_profile(user_id: int) -> None:
    init_db()
    conn = get_connection()
    try:
        with conn:
            conn.execute("DELETE FROM skill_profiles WHERE user_id = ?", (int(user_id),))
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Jobs
# ---------------------------------------------------------------------------


def upsert_job(job: dict[str, Any]) -> int:
    """Insert a posting, or refresh the metadata of one already seen. Returns its row id."""
    init_db()
    key = str(job.get("job_key") or "").strip()
    if not key:
        raise ValueError("A job posting needs a job_key.")
    conn = get_connection()
    try:
        with conn:
            conn.execute(
                """
                INSERT INTO linkedin_jobs (
                    job_key, title, company, location, posted_on, url, description, applicants
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(job_key) DO UPDATE SET
                    title = excluded.title,
                    company = excluded.company,
                    location = excluded.location,
                    posted_on = excluded.posted_on,
                    url = excluded.url,
                    description = excluded.description,
                    applicants = excluded.applicants
                """,
                (
                    key,
                    str(job.get("title") or "")[:300],
                    str(job.get("company") or "")[:300],
                    str(job.get("location") or "")[:200],
                    str(job.get("posted_on") or ""),
                    str(job.get("url") or "")[:500],
                    str(job.get("description") or "")[:JOB_TEXT_LIMIT],
                    job.get("applicants") if isinstance(job.get("applicants"), int) else None,
                ),
            )
            row = conn.execute("SELECT id FROM linkedin_jobs WHERE job_key = ?", (key,)).fetchone()
    finally:
        conn.close()
    return int(row["id"])


def get_job(job_id: int) -> dict[str, Any] | None:
    init_db()
    conn = get_connection()
    try:
        row = conn.execute("SELECT * FROM linkedin_jobs WHERE id = ?", (int(job_id),)).fetchone()
    finally:
        conn.close()
    return dict(row) if row is not None else None


def unscored_job_ids(user_id: int) -> set[int]:
    """Row ids this user has never scored.

    Call this *after* upserting newly found postings, or a posting that is not in
    the table yet will look like it has already been seen.
    """
    init_db()
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT j.id
              FROM linkedin_jobs j
              LEFT JOIN job_evaluations e ON e.job_id = j.id AND e.user_id = ?
             WHERE e.id IS NULL
            """,
            (int(user_id),),
        ).fetchall()
    finally:
        conn.close()
    return {int(row["id"]) for row in rows}


def purge_old_jobs(keep: int = 5_000) -> int:
    """Drop the oldest postings so the table cannot grow without bound."""
    init_db()
    conn = get_connection()
    try:
        with conn:
            cursor = conn.execute(
                """
                DELETE FROM linkedin_jobs
                 WHERE id NOT IN (SELECT id FROM linkedin_jobs ORDER BY id DESC LIMIT ?)
                   AND id NOT IN (SELECT job_id FROM job_evaluations)
                   AND id NOT IN (SELECT job_id FROM application_kits)
                """,
                (int(keep),),
            )
        return int(cursor.rowcount or 0)
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Evaluations
# ---------------------------------------------------------------------------


def record_evaluation(
    user_id: int,
    job_id: int,
    *,
    prefilter_score: int = 0,
    match_score: int = 0,
    matched_count: int = 0,
    total_count: int = 0,
    analysis: dict[str, Any] | None = None,
) -> int:
    init_db()
    conn = get_connection()
    try:
        with conn:
            conn.execute(
                """
                INSERT INTO job_evaluations (
                    user_id, job_id, prefilter_score, match_score, matched_count,
                    total_count, analysis, evaluated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))
                ON CONFLICT(user_id, job_id) DO UPDATE SET
                    prefilter_score = excluded.prefilter_score,
                    match_score = excluded.match_score,
                    matched_count = excluded.matched_count,
                    total_count = excluded.total_count,
                    analysis = excluded.analysis,
                    evaluated_at = excluded.evaluated_at
                """,
                (
                    int(user_id),
                    int(job_id),
                    int(prefilter_score),
                    int(match_score),
                    int(matched_count),
                    int(total_count),
                    json.dumps(analysis, ensure_ascii=False) if analysis else "",
                ),
            )
    finally:
        conn.close()
    return get_evaluation(user_id, job_id) or {}


def mark_rejected(user_id: int, job_ids: list[int], *, prefilter_score: int = 0) -> int:
    """
    Record postings that were considered and are not matches.

    Storing a zero-score evaluation is what makes a daily run idempotent: without
    it every posting the gate turned down would be re-fetched on every run, for as
    long as the account exists.
    """
    ids = [int(job_id) for job_id in job_ids or []]
    if not ids:
        return 0
    init_db()
    conn = get_connection()
    try:
        with conn:
            conn.executemany(
                """
                INSERT INTO job_evaluations (user_id, job_id, prefilter_score, match_score, evaluated_at)
                VALUES (?, ?, ?, 0, datetime('now'))
                ON CONFLICT(user_id, job_id) DO UPDATE SET
                    prefilter_score = excluded.prefilter_score,
                    evaluated_at = excluded.evaluated_at
                """,
                [(int(user_id), job_id, int(prefilter_score)) for job_id in ids],
            )
    finally:
        conn.close()
    return len(ids)


def get_evaluation(user_id: int, job_id: int) -> dict[str, Any] | None:
    init_db()
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM job_evaluations WHERE user_id = ? AND job_id = ?",
            (int(user_id), int(job_id)),
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        return None
    record = dict(row)
    try:
        loaded = json.loads(record.get("analysis") or "")
        record["analysis"] = loaded if isinstance(loaded, dict) else {}
    except (TypeError, ValueError):
        record["analysis"] = {}
    return record


def list_matching_jobs(user_id: int, limit: int = 50, min_score: int = 0) -> list[dict[str, Any]]:
    """Scored postings joined with their job row and any generated materials."""
    init_db()
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT j.*, e.match_score, e.prefilter_score, e.matched_count, e.total_count,
                   e.analysis, e.evaluated_at,
                   k.tailored_cv, k.cover_letter
              FROM job_evaluations e
              JOIN linkedin_jobs j ON j.id = e.job_id
              LEFT JOIN application_kits k ON k.job_id = e.job_id AND k.user_id = e.user_id
             WHERE e.user_id = ? AND e.match_score >= ?
             ORDER BY e.match_score DESC, e.id DESC
             LIMIT ?
            """,
            (int(user_id), int(min_score), int(limit)),
        ).fetchall()
    finally:
        conn.close()
    items: list[dict[str, Any]] = []
    for row in rows:
        record = dict(row)
        try:
            loaded = json.loads(record.get("analysis") or "")
            record["analysis"] = loaded if isinstance(loaded, dict) else {}
        except (TypeError, ValueError):
            record["analysis"] = {}
        items.append(record)
    return items


def reset_digest_flags(user_id: int) -> None:
    """Clear the in_digest markers so the next run starts a fresh digest."""
    init_db()
    conn = get_connection()
    try:
        with conn:
            conn.execute("UPDATE job_evaluations SET in_digest = 0 WHERE user_id = ?", (int(user_id),))
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Application kits
# ---------------------------------------------------------------------------


def save_kit(user_id: int, job_id: int, tailored_cv: str, cover_letter: str) -> None:
    init_db()
    conn = get_connection()
    try:
        with conn:
            conn.execute(
                """
                INSERT INTO application_kits (user_id, job_id, tailored_cv, cover_letter, created_at)
                VALUES (?, ?, ?, ?, datetime('now'))
                ON CONFLICT(user_id, job_id) DO UPDATE SET
                    tailored_cv = excluded.tailored_cv,
                    cover_letter = excluded.cover_letter,
                    created_at = excluded.created_at
                """,
                (int(user_id), int(job_id), str(tailored_cv or ""), str(cover_letter or "")),
            )
    finally:
        conn.close()


def get_kit(user_id: int, job_id: int) -> dict[str, Any] | None:
    init_db()
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM application_kits WHERE user_id = ? AND job_id = ?",
            (int(user_id), int(job_id)),
        ).fetchone()
    finally:
        conn.close()
    return dict(row) if row is not None else None


def kit_count_today(user_id: int) -> int:
    """How many CV/letter pairs this user has already generated today (UTC)."""
    init_db()
    conn = get_connection()
    try:
        row = conn.execute(
            """
            SELECT COUNT(*) AS total FROM application_kits
             WHERE user_id = ? AND created_at >= datetime('now', '-1 day')
               AND date(created_at) = date('now')
            """,
            (int(user_id),),
        ).fetchone()
    finally:
        conn.close()
    return int(row["total"] or 0)



def evaluations_today(user_id: int) -> int:
    """How many postings this user has had scored by the LLM today (UTC).

    Free prefilter and triage rejections are stored as evaluations too, so that a
    run stays idempotent, but they cost nothing. Only a row carrying an analysis
    came from a paid model call, and only those may consume the daily LLM budget.
    """
    init_db()
    conn = get_connection()
    try:
        row = conn.execute(
            """
            SELECT COUNT(*) AS total FROM job_evaluations
             WHERE user_id = ? AND analysis <> ''
               AND evaluated_at >= datetime('now', '-1 day')
               AND date(evaluated_at) = date('now')
            """,
            (int(user_id),),
        ).fetchone()
    finally:
        conn.close()
    return int(row["total"] or 0)


def candidates_for_digest(user_id: int, min_score: int) -> list[dict[str, Any]]:
    """Matches not yet included in a digest."""
    return [item for item in list_matching_jobs(user_id, limit=100, min_score=min_score) if not int(item.get("in_digest") or 0)]


def mark_in_digest(user_id: int, job_ids: list[int]) -> None:
    if not job_ids:
        return
    init_db()
    placeholders = ",".join("?" for _ in job_ids)
    conn = get_connection()
    try:
        with conn:
            conn.execute(
                f"UPDATE job_evaluations SET in_digest = 1 WHERE user_id = ? AND job_id IN ({placeholders})",
                [int(user_id), *[int(j) for j in job_ids]],
            )
    finally:
        conn.close()
