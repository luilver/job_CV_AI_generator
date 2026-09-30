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
