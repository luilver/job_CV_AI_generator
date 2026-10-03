"""The scheduled entry point and its once-a-day rule.

These guard the properties that are invisible until they bite in production:
that a second run the same day cannot mail a duplicate digest, that a disabled
account is left alone, and that one account's crash does not cancel the rest.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from scripts import daily_digest


@pytest.fixture
def accounts(tmp_path, monkeypatch):
    import src.auth.db as db

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "cli.db")
    db.init_db()

    from src.auth.auth import signup
    from src.linkedin import store

    signup("due@jobcv.test", "password123")
    signup("off@jobcv.test", "password123")
    store.save_settings(1, enabled=True, digest_hour=7, digest_tz="UTC")
    store.save_settings(2, enabled=False)
    return 1, 2


def test_only_enabled_accounts_are_eligible(accounts):
    assert daily_digest.eligible_user_ids() == [1]


def test_digest_is_not_due_before_its_hour(accounts):
    from src.linkedin.store import digest_is_due

    before = datetime(2026, 10, 3, 6, 30, tzinfo=timezone.utc)
    due, reason = digest_is_due(1, now=before)
    assert due is False
    assert "07:00" in reason


def test_digest_is_due_at_its_hour(accounts):
    from src.linkedin.store import digest_is_due

    due, _ = digest_is_due(1, now=datetime(2026, 10, 3, 7, 0, tzinfo=timezone.utc))
    assert due is True


def test_digest_is_not_sent_twice_in_a_day(accounts):
    from src.linkedin.store import digest_is_due, mark_digest_sent

    mark_digest_sent(1)
    due, reason = digest_is_due(1, now=datetime(2026, 10, 3, 9, 0, tzinfo=timezone.utc))
    assert due is False
    assert "already sent" in reason


def test_digest_returns_the_next_day(accounts):
    from src.linkedin.store import digest_is_due, mark_digest_sent

    mark_digest_sent(1)
    due, _ = digest_is_due(1, now=datetime(2026, 10, 4, 9, 0, tzinfo=timezone.utc))
    assert due is True


def test_a_disabled_account_is_never_due(accounts):
    from src.linkedin.store import digest_is_due

    due, reason = digest_is_due(2, now=datetime(2026, 10, 3, 9, 0, tzinfo=timezone.utc))
    assert due is False
    assert "switched off" in reason


def test_digest_hour_is_interpreted_in_the_users_timezone(accounts):
    from src.linkedin import store
    from src.linkedin.store import digest_is_due

    # 07:00 in New York is 11:00 UTC; 10:00 UTC is still before the user's hour.
    store.save_settings(1, digest_tz="America/New_York", digest_hour=7)
    due, _ = digest_is_due(1, now=datetime(2026, 10, 3, 10, 0, tzinfo=timezone.utc))
    assert due is False
    due, _ = digest_is_due(1, now=datetime(2026, 10, 3, 12, 0, tzinfo=timezone.utc))
    assert due is True


def test_an_unknown_timezone_falls_back_to_utc(accounts):
    from src.linkedin import store
    from src.linkedin.store import digest_is_due

    store.save_settings(1, digest_tz="Mars/Olympus_Mons", digest_hour=7)
    due, _ = digest_is_due(1, now=datetime(2026, 10, 3, 8, 0, tzinfo=timezone.utc))
    assert due is True


def test_dry_run_does_not_run_anything(accounts, monkeypatch):
    calls = []
    monkeypatch.setattr(daily_digest, "run_discovery", lambda *a, **k: calls.append(a))
    assert daily_digest.main(["--dry-run"]) == 0
    assert calls == []


def test_main_runs_due_accounts_and_skips_the_rest(accounts, monkeypatch):
    from src.linkedin import store

    store.save_settings(1, digest_hour=0)  # due now
    ran = []

    def fake_run(user_id, **kwargs):
        ran.append((user_id, kwargs.get("send_digest")))
        return {"found": 1, "hydrated": 1, "scored": 1, "matches": 0, "kits": 0, "errors": []}

    monkeypatch.setattr(daily_digest, "run_discovery", fake_run)
    assert daily_digest.main([]) == 0
    assert ran == [(1, True)]


def test_no_digest_flag_is_passed_through(accounts, monkeypatch):
    from src.linkedin import store

    store.save_settings(1, digest_hour=0)
    ran = []
    monkeypatch.setattr(
        daily_digest, "run_discovery",
        lambda user_id, **kwargs: ran.append(kwargs.get("send_digest")) or {"errors": []},
    )
    daily_digest.main(["--no-digest"])
    assert ran == [False]


def test_user_id_forces_a_run_before_the_digest_hour(accounts, monkeypatch):
    ran = []
    monkeypatch.setattr(
        daily_digest, "run_discovery",
        lambda user_id, **kwargs: ran.append(user_id) or {"errors": []},
    )
    assert daily_digest.main(["--user-id", "1"]) == 0
    assert ran == [1]


def test_one_account_crashing_does_not_stop_the_next(accounts, monkeypatch):
    from src.linkedin import store

    store.save_settings(1, digest_hour=0)
    store.save_settings(2, enabled=True, digest_hour=0)
    seen = []

    def fake_run(user_id, **kwargs):
        seen.append(user_id)
        if user_id == 1:
            raise RuntimeError("boom")
        return {"errors": []}

    monkeypatch.setattr(daily_digest, "run_discovery", fake_run)
    assert daily_digest.main([]) == 1
    assert seen == [1, 2]