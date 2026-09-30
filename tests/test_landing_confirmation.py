"""Landing page handles confirmation links served from the bare hostname.

Emailed links point at {APP_CONFIRM_BASE_URL}?verify=..., a hostname dedicated to
public confirmation access, which serves landing.py at /. That makes the query
parameter path load-bearing rather than legacy, so it gets its own test.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import src.auth.db as db
from src.auth import auth
from src.auth.auth import get_user_row
from streamlit.testing.v1 import AppTest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def temp_db(monkeypatch, tmp_path):
    original = db.DB_PATH
    db.DB_PATH = tmp_path / "app.db"
    db.init_db()
    yield
    db.DB_PATH = original


@pytest.fixture
def query_params(monkeypatch):
    """Inject query parameters into the script under test."""
    import streamlit as st

    class FakeQueryParams(dict):
        def get(self, key, default=None):
            return dict.get(self, key, default)

    params = FakeQueryParams()
    monkeypatch.setattr(st, "query_params", params)
    return params


def _unconfirmed_account() -> str:
    ok, message = auth.signup("newcomer@example.com", "correct horse battery")
    assert ok, message
    auth.issue_verification("newcomer@example.com")
    return _row("newcomer@example.com")["verification_token"]


def _row(email: str):
    from src.auth.auth import _lookup_by_email

    return _lookup_by_email(email)


def test_query_parameter_confirms_the_account(query_params):
    query_params["verify"] = _unconfirmed_account()

    app = AppTest.from_file(str(ROOT / "landing.py"), default_timeout=30)
    app.run()

    assert auth.is_verified(_row("newcomer@example.com")["id"])
    assert any("Email confirmed" in message.value for message in app.success)


def test_invalid_token_is_reported_and_leaves_the_account_unverified(query_params):
    _unconfirmed_account()
    query_params["verify"] = "not-a-real-token"

    app = AppTest.from_file(str(ROOT / "landing.py"), default_timeout=30)
    app.run()

    assert not auth.is_verified(_row("newcomer@example.com")["id"])
    assert any("not valid" in message.value for message in app.error)
