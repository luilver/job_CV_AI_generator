"""Render the Job Discovery pages for real.

The pages are the only part of this feature a user touches, and a wrong column
name or a stale key fails silently in every unit test above. Streamlit's own
AppTest runs the actual script, so these catch the errors that matter: the page
raising on real rows, or drawing the wrong thing.
"""

from __future__ import annotations

import pathlib
import tempfile

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
JOBS_PAGE = REPO_ROOT / "pages" / "jobs.py"
CALLBACK_PAGE = REPO_ROOT / "pages" / "linkedin_callback.py"

CV_TEXT = (
    r"\documentclass{article}\begin{document}"
    "Python engineer with years of experience building backend services, APIs and "
    "data pipelines with PostgreSQL, Docker and Kubernetes on AWS."
    r"\end{document}"
)


@pytest.fixture(autouse=True)
def _ignore_real_linkedin_secrets(monkeypatch):
    """Keep these tests hermetic.

    AppTest loads the repository's real .streamlit/secrets.toml, so a developer
    machine with LinkedIn credentials would flip the page into the configured
    branch and break assertions that describe the unconfigured state.
    """
    import src.utils.config_loader as cfg

    monkeypatch.setattr(cfg, "is_linkedin_configured", lambda: False)
    monkeypatch.setattr(cfg, "linkedin_client_id", lambda: "")
    monkeypatch.setattr(cfg, "linkedin_client_secret", lambda: "")


@pytest.fixture
def app_user(tmp_path, monkeypatch):
    """A signed-up user with a CV, on a throwaway database."""
    import src.auth.db as db

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "ui.db")
    db.init_db()

    from src.auth.auth import signup
    from src.profile.cv_store import save_cv

    signup("dev@jobcv.test", "password123")
    save_cv(1, "cv.tex", CV_TEXT.encode())
    return 1


def _run(page, user_id: int):
    from streamlit.testing.v1 import AppTest

    at = AppTest.from_file(str(page), default_timeout=60)
    at.session_state["auth_user_id"] = user_id
    at.session_state["auth_email"] = "dev@jobcv.test"
    at.run()
    assert not at.exception, f"{page.name} raised: {[e.value for e in at.exception]}"
    assert not at.error, f"{page.name} drew an error block: {[e.value for e in at.error]}"
    return at


def _connect_and_score(user_id: int) -> None:
    from src.linkedin import store

    store.save_linkedin_account(
        user_id, "abc123", name="Luilver Garces", email="dev@jobcv.test"
    )
    store.save_settings(user_id, enabled=True, keywords="python", min_match_score=80)
    store.save_skills(user_id, ["Python", "PostgreSQL", "Docker", "Kubernetes"], "2026-01-01")

    job_id = store.upsert_job(
        {
            "job_key": "urn:li:jobPosting:4448467811",
            "job_id": "4448467811",
            "title": "Senior Python Engineer",
            "company": "Acme",
            "location": "Remote, US",
            "posted_on": "2026-09-30",
            "url": "https://www.linkedin.com/jobs/view/4448467811",
            "description": "We need a Python engineer. " * 40,
        }
    )
    store.record_evaluation(
        user_id,
        job_id,
        prefilter_score=47,
        match_score=88,
        matched_count=4,
        total_count=5,
        analysis={
            "summary": "Strong Python and Kubernetes match.",
            "requirements": [
                {"requirement": "Python", "status": "match", "evidence": "5 yrs"},
                {"requirement": "Go", "status": "missing"},
                {"requirement": "Terraform", "status": "partial"},
            ],
        },
    )
    store.save_kit(
        user_id,
        job_id,
        r"\documentclass{article}\begin{document}CV\end{document}",
        "Dear team, I would like to apply.",
    )


def test_jobs_page_renders_when_nothing_is_connected(app_user):
    at = _run(JOBS_PAGE, app_user)
    headers = [h.value for h in at.subheader]
    assert headers == ["1. LinkedIn", "2. What to look for", "3. Run a search", "4. Matches"]
    # Nothing connected yet: it should say so and not offer to run.
    assert any("Connect LinkedIn" in w.value or "not configured" in w.value for w in at.warning)
    assert not any("Run now" in b.label for b in at.button)
    # Step 3 must explain why there is no button, not render an empty heading.
    assert any("Run now** button appears once" in w.value for w in at.warning)


def test_check_it_button_reports_an_unconnected_host(app_user):
    at = _run(JOBS_PAGE, app_user)
    check = [b for b in at.button if b.label == "Check it"]
    assert check, "the Check it button should always be available"

    at = check[0].click().run()
    assert not at.exception, [e.value for e in at.exception]
    errors = [e.value for e in at.error]
    assert any("credentials are missing" in e for e in errors)
    assert any("connected on THIS host" in e for e in errors)


def test_jobs_page_renders_matches_and_materials(app_user):
    _connect_and_score(app_user)
    at = _run(JOBS_PAGE, app_user)

    assert any("Luilver Garces" in s.value for s in at.success)
    assert any("Apply on LinkedIn" in m.value for m in at.markdown)
    assert any("4448467811" in m.value for m in at.markdown)


def test_jobs_page_hides_matches_below_the_threshold(app_user):
    from src.linkedin import store

    _connect_and_score(app_user)
    # A rejected posting must never surface at an 80% threshold.
    other = store.upsert_job(
        {
            "job_key": "urn:li:jobPosting:999",
            "job_id": "999",
            "title": "Growth Marketing Manager",
            "company": "Beta",
            "location": "Remote",
            "description": "Sales.",
        }
    )
    store.record_evaluation(
        app_user, other, prefilter_score=3, match_score=12, matched_count=0, total_count=3,
        analysis={"summary": "No."},
    )

    at = _run(JOBS_PAGE, app_user)
    assert not any("Growth Marketing Manager" in m.value for m in at.markdown)


def test_callback_page_without_a_code_renders_cleanly(app_user):
    at = _run(CALLBACK_PAGE, app_user)
    assert any("Waiting for LinkedIn" in i.value for i in at.info)


def test_callback_page_reports_a_refusal(app_user):
    from streamlit.testing.v1 import AppTest

    at = AppTest.from_file(str(CALLBACK_PAGE), default_timeout=60)
    at.session_state["auth_user_id"] = app_user
    at.query_params["error"] = "user_cancelled_login"
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    assert any("refused" in e.value for e in at.error)


def test_callback_completes_without_a_session(app_user, monkeypatch):
    """The redirect is a new session, so the callback must not require a login."""
    from streamlit.testing.v1 import AppTest

    import src.linkedin.oauth as oauth
    import src.linkedin.store as store

    calls = {}

    def fake_connect(user_id, code, state):
        calls["args"] = (user_id, code, state)
        return {"member_name": "Luilver Garces", "linkedin_member_id": "abc123"}

    monkeypatch.setattr(oauth, "connect", fake_connect)
    monkeypatch.setattr(store, "peek_oauth_state", lambda state: app_user)
    monkeypatch.setattr(
        store,
        "get_linkedin_account",
        lambda user_id: {"member_name": "Luilver Garces", "linkedin_member_id": "abc123"},
    )

    at = AppTest.from_file(str(CALLBACK_PAGE), default_timeout=60)
    at.query_params["code"] = "auth-code"
    at.query_params["state"] = "a-state"
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    assert "auth_user_id" not in at.session_state
    assert calls["args"] == (app_user, "auth-code", "a-state")
    assert any("Connected as" in s.value for s in at.success)


def test_callback_rejects_an_expired_state(app_user, monkeypatch):
    from streamlit.testing.v1 import AppTest

    import src.linkedin.store as store

    monkeypatch.setattr(store, "peek_oauth_state", lambda state: None)

    at = AppTest.from_file(str(CALLBACK_PAGE), default_timeout=60)
    at.query_params["code"] = "auth-code"
    at.query_params["state"] = "stale"
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    assert any("expired" in e.value for e in at.error)
