import tempfile
from pathlib import Path

import pytest

import src.auth.db as db
from src.auth.auth import signup
from src.history.store import (
    MAX_HISTORY,
    clear_history,
    delete_match,
    get_match,
    list_matches,
    match_score,
    record_match,
)

ANALYSIS = {
    "position_name": "Senior AI Agent Engineer",
    "institution_name": "Oracle",
    "brief_description": "Build AI agents.",
    "requirements": [
        {"text": "Python", "status": "match"},
        {"text": "Kubernetes", "status": "match"},
        {"text": "Go", "status": "missing"},
    ],
    "requirements_extra": "ignored",
}


@pytest.fixture()
def user_id(monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", Path(tempfile.mkdtemp()) / "app.db")
    signup("history@test.dev", "password123")
    return 1


def test_match_score():
    assert match_score(ANALYSIS) == 67
    assert match_score(None) == 0
    assert match_score({"requirements": []}) == 0


def test_record_and_list_match(user_id):
    match_id = record_match(user_id, ANALYSIS, job_url="https://x/jobs/1", job_text="long text")
    matches = list_matches(user_id)
    assert len(matches) == 1
    assert matches[0]["id"] == match_id
    assert matches[0]["company"] == "Oracle"
    assert matches[0]["score"] == 67
    assert matches[0]["requirements_matched"] == 2
    assert matches[0]["requirements_total"] == 3
    assert "long text" not in matches[0]  # heavy column stays out of the list


def test_get_match_decodes_payloads(user_id):
    match_id = record_match(
        user_id,
        ANALYSIS,
        job_url="https://x/jobs/1",
        job_text="long text",
        compensation={"currency": "USD", "total_comp": {"low": 1, "high": 2}, "confidence": "low"},
    )
    record = get_match(user_id, match_id)
    assert record["analysis"]["position_name"] == "Senior AI Agent Engineer"
    assert record["compensation"]["currency"] == "USD"
    assert record["job_text"] == "long text"


def test_get_match_rejects_other_users(user_id, monkeypatch):
    match_id = record_match(user_id, ANALYSIS)
    assert get_match(999, match_id) is None


def test_record_match_requires_an_analysis(user_id):
    with pytest.raises(ValueError):
        record_match(user_id, {})


def test_delete_and_clear(user_id):
    first = record_match(user_id, ANALYSIS)
    second = record_match(user_id, ANALYSIS)
    assert delete_match(user_id, first) is True
    assert delete_match(user_id, first) is False
    assert len(list_matches(user_id)) == 1
    assert clear_history(user_id) == 1
    assert list_matches(user_id) == []


def test_history_is_capped(user_id):
    for _ in range(MAX_HISTORY + 5):
        record_match(user_id, ANALYSIS)
    all_matches = list_matches(user_id, limit=MAX_HISTORY * 2)
    assert len(all_matches) == MAX_HISTORY
    assert all_matches[0]["id"] == max(match["id"] for match in all_matches)
