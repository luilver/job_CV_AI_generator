"""User authentication: signup, login, email confirmation, session helpers."""

from __future__ import annotations

import hashlib
import re
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone

import streamlit as st

from src.auth.db import get_connection, init_db
from src.mail import mailer


_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
UNVERIFIED_MESSAGE = "Please confirm your email address before logging in. Check your inbox for the link we sent you."
TOKEN_TTL = timedelta(hours=mailer.TOKEN_TTL_HOURS)


def _hash_password(password: str) -> str:
    return hashlib.sha256(password.encode("utf-8")).hexdigest()


def _valid_email(email: str) -> bool:
    return bool(_EMAIL_RE.match(email))


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_timestamp(value: object) -> datetime | None:
    if not value:
        return None
    try:
        stamp = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    return stamp if stamp.tzinfo else stamp.replace(tzinfo=timezone.utc)


def _new_token() -> str:
    return secrets.token_urlsafe(32)


def _store_token(user_id: int, token: str) -> None:
    conn = get_connection()
    try:
        with conn:
            conn.execute(
                "UPDATE users SET verification_token = ?, verification_sent_at = ? WHERE id = ?",
                (token, _now().isoformat(), user_id),
            )
    finally:
        conn.close()


def _lookup_by_email(email: str) -> sqlite3.Row | None:
    conn = get_connection()
    try:
        return conn.execute("SELECT * FROM users WHERE email = ?", (email.strip().lower(),)).fetchone()
    finally:
        conn.close()


def send_confirmation_for(user_id: int, email: str, name: str = "") -> tuple[bool, str]:
    """Issue a fresh token and email it. Returns (success, message)."""
    token = _new_token()
    _store_token(user_id, token)
    try:
        link = mailer.send_verification_email(email, name, token)
    except mailer.EmailError as exc:
        return False, f"Your account is created, but the confirmation email could not be sent: {exc}"
    return True, f"We sent a confirmation link to {email}. It expires in {mailer.TOKEN_TTL_HOURS} hours. ({link})"


def issue_verification(email: str) -> tuple[bool, str]:
    """Re-send a confirmation email for an unconfirmed account."""
    init_db()
    email = email.strip().lower()
    if not _valid_email(email):
        return False, "Enter a valid email address."
    row = _lookup_by_email(email)
    if row is None:
        # Do not reveal which addresses have accounts.
        return False, "If that address has an unconfirmed account, a new link is on its way."
    if int(row["email_verified"] or 0):
        return False, "That address is already confirmed. You can log in."
    return send_confirmation_for(int(row["id"]), row["email"], str(row["full_name"] or ""))


def confirm_email(token: str) -> tuple[bool, str]:
    """Validate a confirmation token and mark the account verified."""
    init_db()
    token = (token or "").strip()
    if not token:
        return False, "This confirmation link is missing its code."
    conn = get_connection()
    try:
        row = conn.execute("SELECT * FROM users WHERE verification_token = ?", (token,)).fetchone()
    finally:
        conn.close()
    if row is None:
        return False, "This confirmation link is not valid. Request a new one from the login screen."
    if int(row["email_verified"] or 0):
        return True, "Your email is already confirmed. You can log in."

    sent_at = _parse_timestamp(row["verification_sent_at"])
    if sent_at is None or _now() - sent_at > TOKEN_TTL:
        return False, "This confirmation link has expired. Request a new one from the login screen."

    conn = get_connection()
    try:
        with conn:
            # The token is kept so a second click on the same link explains that the
            # address is confirmed; it grants nothing once the account is verified.
            conn.execute("UPDATE users SET email_verified = 1 WHERE id = ?", (row["id"],))
    finally:
        conn.close()
    return True, f"Email confirmed — {row['email']} is now verified. You can log in."


def is_verified(user_id: int) -> bool:
    row = get_user_row(user_id)
    if row is None:
        return False
    try:
        return bool(int(row["email_verified"] or 0))
    except (IndexError, KeyError, TypeError, ValueError):
        return False


def signup(email: str, password: str, own_api_key: bool = False) -> tuple[bool, str]:
    """Create a new (unconfirmed) account and email a confirmation link."""
    init_db()
    email = email.strip().lower()
    if not _valid_email(email):
        return False, "Enter a valid email address."
    if len(password) < 8:
        return False, "Password must be at least 8 characters."
    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO users (email, password_hash, own_api_key) VALUES (?, ?, ?)",
            (email, _hash_password(password), int(own_api_key)),
        )
        user_id = conn.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone()["id"]
        conn.execute(
            "INSERT INTO subscriptions (user_id, plan, status, trial_uses, trial_limit) VALUES (?, 'free', 'active', 0, 3)",
            (user_id,),
        )
        conn.commit()
    except sqlite3.IntegrityError:
        return False, "An account with this email already exists."
    finally:
        conn.close()

    sent, message = send_confirmation_for(user_id, email)
    if not sent:
        return True, message
    return True, f"Account created. {message}"


def login(email: str, password: str) -> tuple[bool, str]:
    """Verify credentials and require a confirmed email. Returns (success, message)."""
    init_db()
    email = email.strip().lower()
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT id, password_hash, email_verified FROM users WHERE email = ?", (email,)
        ).fetchone()
        if row is None or row["password_hash"] != _hash_password(password):
            return False, "Invalid email or password."
        if not int(row["email_verified"] or 0):
            return False, UNVERIFIED_MESSAGE
        st.session_state["auth_user_id"] = row["id"]
        st.session_state["auth_email"] = email
        return True, "Logged in."
    finally:
        conn.close()


def logout() -> None:
    for key in ("auth_user_id", "auth_email"):
        st.session_state.pop(key, None)


def current_user_id() -> int | None:
    return st.session_state.get("auth_user_id")


def is_logged_in() -> bool:
    return current_user_id() is not None


def get_user_row(user_id: int) -> sqlite3.Row | None:
    conn = get_connection()
    try:
        return conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    finally:
        conn.close()


MAX_NAME_LENGTH = 80


def get_full_name(user_id: int) -> str:
    """The name the user wants generated cover letters signed with."""
    row = get_user_row(user_id)
    if row is None:
        return ""
    try:
        return str(row["full_name"] or "").strip()
    except (IndexError, KeyError):
        return ""


def set_full_name(user_id: int, full_name: str) -> str:
    """Store the signing name. Returns the value that was saved ('' clears it)."""
    init_db()
    name = " ".join(str(full_name or "").split())[:MAX_NAME_LENGTH].strip()
    if "\n" in name or "\r" in name:
        name = name.splitlines()[0].strip()
    conn = get_connection()
    try:
        with conn:
            conn.execute("UPDATE users SET full_name = ? WHERE id = ?", (name, user_id))
    finally:
        conn.close()
    return name
