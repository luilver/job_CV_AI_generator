"""Subscription and free-trial management."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from src.auth.db import get_connection


def get_subscription(user_id: int) -> sqlite3.Row | None:
    conn = get_connection()
    try:
        return conn.execute(
            "SELECT * FROM subscriptions WHERE user_id = ?", (user_id,)
        ).fetchone()
    finally:
        conn.close()


def is_subscribed(user_id: int) -> bool:
    """Return True if the user has an active paid subscription."""
    sub = get_subscription(user_id)
    if sub is None:
        return False
    if sub["plan"] not in ("basic", "own_key"):
        return False
    if sub["status"] != "active":
        return False
    end = sub["current_period_end"]
    if end:
        try:
            expiry = datetime.fromisoformat(end).replace(tzinfo=timezone.utc)
            if expiry < datetime.now(tz=timezone.utc):
                return False
        except ValueError:
            pass
    return True


def trial_remaining(user_id: int) -> int:
    """Return how many free trial uses are left."""
    sub = get_subscription(user_id)
    if sub is None:
        return 0
    return max(0, sub["trial_limit"] - sub["trial_uses"])


def can_use_service(user_id: int) -> bool:
    """Return True if the user can run an analysis (subscribed or trial left)."""
    return is_subscribed(user_id) or trial_remaining(user_id) > 0


def consume_trial(user_id: int) -> None:
    """Increment the trial usage counter by 1."""
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE subscriptions SET trial_uses = trial_uses + 1, updated_at = datetime('now') WHERE user_id = ?",
            (user_id,),
        )
        conn.commit()
    finally:
        conn.close()


def activate_subscription(user_id: int, plan: str, paypal_sub_id: str | None = None) -> None:
    """Mark a user as having an active paid plan."""
    from datetime import timedelta

    now = datetime.now(tz=timezone.utc)
    period_end = (now + timedelta(days=31)).isoformat()
    conn = get_connection()
    try:
        conn.execute(
            """
            UPDATE subscriptions
               SET plan = ?, status = 'active', paypal_sub_id = ?,
                   current_period_end = ?, updated_at = datetime('now')
             WHERE user_id = ?
            """,
            (plan, paypal_sub_id, period_end, user_id),
        )
        conn.commit()
    finally:
        conn.close()


def set_own_api_key_flag(user_id: int, flag: bool) -> None:
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE users SET own_api_key = ? WHERE id = ?",
            (int(flag), user_id),
        )
        conn.commit()
    finally:
        conn.close()
