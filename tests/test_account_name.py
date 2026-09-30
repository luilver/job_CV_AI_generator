import sqlite3
import tempfile
from pathlib import Path

import pytest

import src.auth.db as db
from src.auth.auth import (
    MAX_NAME_LENGTH,
    get_full_name,
    get_user_row,
    set_full_name,
    signup,
)
from src.generators.cv_cl_generators import generate_cover_letter


class _RecordingClient:
    """OpenAI-shaped client that records the prompt and returns a long letter."""

    def __init__(self, reply: str = "Dear team,\n" + "I am writing to apply for the role. " * 5 + "\nKind regards,"):
        self.reply = reply
        self.prompt = ""
        client = self

        class _Completions:
            @staticmethod
            def create(**kwargs):
                client.prompt = kwargs["messages"][1]["content"]
                return type(
                    "R",
                    (),
                    {"choices": [type("C", (), {"message": type("M", (), {"content": client.reply})()})()]},
                )()

        self.chat = type("Chat", (), {"completions": _Completions()})()


@pytest.fixture()
def user_id(monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", Path(tempfile.mkdtemp()) / "app.db")
    signup("name@test.dev", "password123")
    return 1


def test_full_name_roundtrip(user_id):
    assert get_full_name(user_id) == ""
    assert set_full_name(user_id, "  Jane   Doe  ") == "Jane Doe"
    assert get_full_name(user_id) == "Jane Doe"
    assert set_full_name(user_id, "") == ""
    assert get_full_name(user_id) == ""


def test_full_name_is_truncated_and_single_line(user_id):
    saved = set_full_name(user_id, "A" * 200 + "\nSecond Line")
    assert len(saved) == MAX_NAME_LENGTH
    assert "\n" not in saved


def test_full_name_for_unknown_user():
    assert get_full_name(4242) == ""


def test_migration_adds_full_name_to_existing_database(monkeypatch):
    """A database created before the column existed must be upgraded in place."""
    path = Path(tempfile.mkdtemp()) / "old.db"
    legacy = sqlite3.connect(str(path))
    legacy.execute(
        """
        CREATE TABLE users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            own_api_key INTEGER NOT NULL DEFAULT 0
        )
        """
    )
    legacy.execute(
        "INSERT INTO users (email, password_hash) VALUES ('old@test.dev', 'hash')"
    )
    legacy.commit()
    legacy.close()

    monkeypatch.setattr(db, "DB_PATH", path)
    db.init_db()
    columns = {row["name"] for row in db.get_connection().execute("PRAGMA table_info(users)")}
    assert "full_name" in columns
    assert get_full_name(1) == ""
    assert set_full_name(1, "Grace Hopper") == "Grace Hopper"
    assert get_full_name(1) == "Grace Hopper"
    assert get_user_row(1)["email"] == "old@test.dev"


def test_cover_letter_is_signed_with_the_profile_name():
    client = _RecordingClient()
    generate_cover_letter(
        client, "OpenAI GPT", "gpt-4o", "job text", "cv text", "", "guidelines",
        signer_name="Jane Doe",
    )
    assert "uses exactly this name: Jane Doe" in client.prompt


def test_cover_letter_without_a_name_forbids_inventing_one():
    client = _RecordingClient()
    generate_cover_letter(client, "OpenAI GPT", "gpt-4o", "job text", "cv text", "", "guidelines")
    assert "do not invent a person's name" in client.prompt
    assert "uses exactly this name" not in client.prompt
