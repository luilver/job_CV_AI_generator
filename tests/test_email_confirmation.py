"""Email confirmation: new accounts must confirm before they can log in."""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import src.auth.db as db
from src.auth import auth
from src.auth.auth import (
    UNVERIFIED_MESSAGE,
    confirm_email,
    is_verified,
    issue_verification,
    login,
    send_confirmation_for,
    signup,
)
from src.mail import mailer

DB_PATH = Path(__file__).resolve().parents[1] / "data" / "app.db"


@pytest.fixture(autouse=True)
def temp_db(monkeypatch):
    original = db.DB_PATH
    db.DB_PATH = Path(pytest.importorskip("tempfile").mkdtemp()) / "app.db"
    db.init_db()
    yield
    db.DB_PATH = original


@pytest.fixture
def sent(monkeypatch):
    """Capture outgoing verification emails instead of contacting Google."""
    calls: list[tuple] = []

    def fake_send(to_email, name="", token=""):
        calls.append((to_email, name, token))
        return f"http://localhost:8501/?verify={token}"

    monkeypatch.setattr(mailer, "send_verification_email", fake_send)
    return calls


def _token_of(calls) -> str:
    assert calls, "no verification email was sent"
    return calls[-1][2]


def test_signup_requires_confirmation(sent):
    ok, msg = signup("new@example.com", "password123")
    assert ok
    assert "confirmation link" in msg
    assert is_verified(1) is False
    assert login("new@example.com", "password123") == (False, UNVERIFIED_MESSAGE)


def test_confirm_then_login(sent):
    signup("new@example.com", "password123")
    ok, msg = confirm_email(_token_of(sent))
    assert ok and "confirmed" in msg
    assert is_verified(1) is True
    assert login("new@example.com", "password123") == (True, "Logged in.")


def test_confirm_is_idempotent(sent):
    signup("new@example.com", "password123")
    token = _token_of(sent)
    confirm_email(token)
    ok, msg = confirm_email(token)
    assert ok and "already confirmed" in msg


def test_invalid_token_rejected(sent):
    signup("new@example.com", "password123")
    ok, msg = confirm_email("not-a-token")
    assert not ok and "not valid" in msg
    assert is_verified(1) is False


def test_missing_token_rejected(sent):
    assert confirm_email("")[0] is False
    assert confirm_email("   ")[0] is False


def test_expired_token_rejected(sent, monkeypatch):
    signup("new@example.com", "password123")
    token = _token_of(sent)
    stale = (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat()
    conn = db.get_connection()
    with conn:
        conn.execute("UPDATE users SET verification_sent_at = ?", (stale,))
    conn.close()
    ok, msg = confirm_email(token)
    assert not ok and "expired" in msg


def test_resend_replaces_token_and_verifies(sent):
    signup("new@example.com", "password123")
    first = _token_of(sent)
    ok, msg = issue_verification("new@example.com")
    assert ok and "confirmation link" in msg
    second = _token_of(sent)
    assert second != first
    assert confirm_email(first)[0] is False, "the superseded token must stop working"
    assert confirm_email(second)[0] is True


def test_resend_unknown_email_does_not_leak(sent):
    ok, msg = issue_verification("nobody@example.com")
    assert not ok
    assert "unconfirmed account" in msg
    assert not sent


def test_resend_rejects_verified_account(sent):
    signup("new@example.com", "password123")
    confirm_email(_token_of(sent))
    ok, msg = issue_verification("new@example.com")
    assert not ok and "already confirmed" in msg


def test_resend_validates_email(sent):
    assert issue_verification("nope")[0] is False


def test_send_failure_reports_reason(monkeypatch):
    def boom(to_email, name="", token=""):
        raise mailer.EmailError("SMTP_APP_PASSWORD is missing")

    monkeypatch.setattr(mailer, "send_verification_email", boom)
    ok, msg = signup("new@example.com", "password123")
    assert ok, "the account still exists even when the mail cannot go out"
    assert "could not be sent" in msg and "SMTP_APP_PASSWORD" in msg
    assert login("new@example.com", "password123")[1] == UNVERIFIED_MESSAGE


def test_send_confirmation_for_uses_stored_name(sent):
    signup("new@example.com", "password123")
    user = auth.get_user_row(1)
    assert user["email_verified"] == 0
    assert user["verification_sent_at"] is not None
    assert len(_token_of(sent)) >= 32, "tokens must be unguessable"


def test_duplicate_signup_rejected(sent):
    signup("dup@example.com", "password123")
    ok, msg = signup("dup@example.com", "password123")
    assert not ok and "already exists" in msg


def test_legacy_accounts_are_verified_by_migration(monkeypatch):
    """Accounts created before this feature keep working."""
    original = db.DB_PATH
    legacy = Path(pytest.importorskip("tempfile").mkdtemp()) / "legacy.db"
    monkeypatch.setattr(db, "DB_PATH", legacy)
    conn = db.get_connection()
    conn.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, email TEXT, password_hash TEXT, created_at TEXT, own_api_key INTEGER DEFAULT 0)")
    conn.execute("INSERT INTO users (id, email, password_hash) VALUES (1, 'old@example.com', 'x')")
    conn.commit()
    conn.close()
    db.init_db()
    row = auth.get_user_row(1)
    assert row["email_verified"] == 1
    db.DB_PATH = original
