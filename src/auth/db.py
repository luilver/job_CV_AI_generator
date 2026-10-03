"""SQLite database setup for users and subscriptions."""

from __future__ import annotations

import sqlite3
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[2]
DB_PATH = APP_ROOT / "data" / "app.db"


def get_connection() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    # The web app and the digest worker are separate processes on one file.
    # WAL lets them read and write together; the timeout absorbs the brief
    # overlaps instead of raising "database is locked".
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def init_db() -> None:
    conn = get_connection()
    with conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                email       TEXT    NOT NULL UNIQUE,
                password_hash TEXT  NOT NULL,
                created_at  TEXT    NOT NULL DEFAULT (datetime('now')),
                own_api_key INTEGER NOT NULL DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS subscriptions (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id         INTEGER NOT NULL REFERENCES users(id),
                plan            TEXT    NOT NULL DEFAULT 'free',
                status          TEXT    NOT NULL DEFAULT 'active',
                paypal_sub_id   TEXT,
                trial_uses      INTEGER NOT NULL DEFAULT 0,
                trial_limit     INTEGER NOT NULL DEFAULT 3,
                current_period_end TEXT,
                created_at      TEXT    NOT NULL DEFAULT (datetime('now')),
                updated_at      TEXT    NOT NULL DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS paypal_orders (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id     INTEGER NOT NULL REFERENCES users(id),
                order_id    TEXT    NOT NULL UNIQUE,
                plan        TEXT    NOT NULL,
                status      TEXT    NOT NULL DEFAULT 'CREATED',
                created_at  TEXT    NOT NULL DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS saved_cvs (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id    INTEGER NOT NULL UNIQUE REFERENCES users(id),
                filename   TEXT    NOT NULL,
                filetype   TEXT    NOT NULL,
                content    BLOB    NOT NULL,
                text       TEXT    NOT NULL,
                size_bytes INTEGER NOT NULL,
                created_at TEXT    NOT NULL DEFAULT (datetime('now')),
                updated_at TEXT    NOT NULL DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS match_history (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id             INTEGER NOT NULL REFERENCES users(id),
                position_name       TEXT    NOT NULL DEFAULT '',
                company             TEXT    NOT NULL DEFAULT '',
                job_url             TEXT    NOT NULL DEFAULT '',
                job_text            TEXT    NOT NULL DEFAULT '',
                analysis            TEXT    NOT NULL,
                compensation        TEXT    NOT NULL DEFAULT '',
                requirements_matched INTEGER NOT NULL DEFAULT 0,
                requirements_total   INTEGER NOT NULL DEFAULT 0,
                created_at          TEXT    NOT NULL DEFAULT (datetime('now'))
            );

            CREATE INDEX IF NOT EXISTS idx_match_history_user
                ON match_history (user_id, id DESC);

            CREATE TABLE IF NOT EXISTS linkedin_accounts (
                user_id            INTEGER PRIMARY KEY REFERENCES users(id),
                linkedin_member_id TEXT    NOT NULL,
                member_name        TEXT    NOT NULL DEFAULT '',
                member_email       TEXT    NOT NULL DEFAULT '',
                picture_url        TEXT    NOT NULL DEFAULT '',
                scopes             TEXT    NOT NULL DEFAULT '',
                connected_at       TEXT    NOT NULL DEFAULT (datetime('now')),
                last_verified_at   TEXT
            );

            CREATE TABLE IF NOT EXISTS oauth_states (
                state       TEXT PRIMARY KEY,
                user_id     INTEGER NOT NULL REFERENCES users(id),
                created_at  TEXT    NOT NULL DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS discovery_settings (
                user_id            INTEGER PRIMARY KEY REFERENCES users(id),
                enabled            INTEGER NOT NULL DEFAULT 0,
                keywords           TEXT    NOT NULL DEFAULT '',
                location           TEXT    NOT NULL DEFAULT 'United States',
                remote_only        INTEGER NOT NULL DEFAULT 1,
                max_age_days       INTEGER NOT NULL DEFAULT 3,
                min_prefilter      INTEGER NOT NULL DEFAULT 25,
                min_match_score    INTEGER NOT NULL DEFAULT 80,
                daily_llm_budget   INTEGER NOT NULL DEFAULT 10,
                daily_gen_budget   INTEGER NOT NULL DEFAULT 3,
                generate_materials INTEGER NOT NULL DEFAULT 1,
                search_pages       INTEGER NOT NULL DEFAULT 2,
                digest_enabled     INTEGER NOT NULL DEFAULT 1,
                digest_hour        INTEGER NOT NULL DEFAULT 7,
                digest_tz          TEXT    NOT NULL DEFAULT 'UTC',
                last_run_at        TEXT,
                last_digest_at     TEXT,
                last_run_report    TEXT    NOT NULL DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS skill_profiles (
                user_id        INTEGER PRIMARY KEY REFERENCES users(id),
                skills         TEXT NOT NULL DEFAULT '',
                cv_updated_at  TEXT,
                updated_at     TEXT NOT NULL DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS linkedin_jobs (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                job_key       TEXT    NOT NULL UNIQUE,
                title         TEXT    NOT NULL DEFAULT '',
                company       TEXT    NOT NULL DEFAULT '',
                location      TEXT    NOT NULL DEFAULT '',
                posted_on     TEXT,
                url           TEXT    NOT NULL DEFAULT '',
                description   TEXT    NOT NULL DEFAULT '',
                applicants    INTEGER,
                first_seen_at TEXT    NOT NULL DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS job_evaluations (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id         INTEGER NOT NULL REFERENCES users(id),
                job_id          INTEGER NOT NULL REFERENCES linkedin_jobs(id),
                prefilter_score INTEGER NOT NULL DEFAULT 0,
                match_score     INTEGER NOT NULL DEFAULT 0,
                matched_count   INTEGER NOT NULL DEFAULT 0,
                total_count     INTEGER NOT NULL DEFAULT 0,
                analysis        TEXT    NOT NULL DEFAULT '',
                in_digest       INTEGER NOT NULL DEFAULT 0,
                evaluated_at    TEXT    NOT NULL DEFAULT (datetime('now')),
                UNIQUE (user_id, job_id)
            );

            CREATE TABLE IF NOT EXISTS application_kits (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id      INTEGER NOT NULL REFERENCES users(id),
                job_id       INTEGER NOT NULL REFERENCES linkedin_jobs(id),
                tailored_cv  TEXT NOT NULL DEFAULT '',
                cover_letter TEXT NOT NULL DEFAULT '',
                created_at   TEXT NOT NULL DEFAULT (datetime('now')),
                UNIQUE (user_id, job_id)
            );

            CREATE INDEX IF NOT EXISTS idx_job_evaluations_user
                ON job_evaluations (user_id, match_score DESC, id DESC);
            CREATE INDEX IF NOT EXISTS idx_linkedin_jobs_seen
                ON linkedin_jobs (first_seen_at DESC);
            """
        )
        _add_missing_columns(conn)
    conn.close()


# Columns added after the first release, applied to databases created earlier.
# Each entry: (table, column, definition, rows_to_backfill)
_MIGRATIONS = (
    ("users", "full_name", "TEXT NOT NULL DEFAULT ''", None),
    ("users", "email_verified", "INTEGER NOT NULL DEFAULT 0", "UPDATE users SET email_verified = 1"),
    ("users", "verification_token", "TEXT NOT NULL DEFAULT ''", None),
    ("users", "verification_sent_at", "TEXT", None),
    ("discovery_settings", "last_digest_at", "TEXT", None),
)


def _add_missing_columns(conn: sqlite3.Connection) -> None:
    for table, column, definition, backfill in _MIGRATIONS:
        existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
        if not existing or column in existing:
            continue
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
        if backfill:
            # Accounts that predate email confirmation are trusted as verified.
            conn.execute(backfill)
