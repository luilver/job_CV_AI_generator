import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest

import src.auth.db as db
import src.linkedin.jobs as job_source
from src.auth.auth import signup
from src.discovery import matcher
from src.discovery.prefilter import (
    card_relevance,
    prefilter_score,
    rank,
    suggest_keywords,
    tokenize,
)
from src.discovery.skills import ensure_skills, extract_skills
from src.linkedin import oauth, store
from src.mail import digest as digest_builder
from src.mail import mailer
from src.profile.cv_store import save_cv

CV_TEX = r"""\documentclass{article}
\begin{document}
\section*{Luilver Garces}
Senior backend engineer with eight years building distributed services.
\begin{itemize}
\item Python, FastAPI, Flask, asyncio
\item PostgreSQL, Redis, SQL tuning
\item Docker, Kubernetes, AWS, Terraform
\item CI/CD with GitHub Actions and Git
\item REST and GraphQL APIs
\end{itemize}
\end{document}
"""

SKILLS = [
    "Python", "FastAPI", "Flask", "PostgreSQL", "Redis", "SQL", "Docker",
    "Kubernetes", "AWS", "Terraform", "CI/CD", "Git", "REST", "GraphQL", "Linux",
]

# A fake AI client shaped like the OpenAI SDK. Skill extraction and requirement
# analysis both go through chat.completions.create with json_mode.
class FakeCompletions:
    def __init__(self, requirements=None):
        self.calls = []
        self.requirements = requirements or [
            {"text": "Python", "status": "match"},
            {"text": "Docker", "status": "match"},
            {"text": "PostgreSQL", "status": "match"},
            {"text": "Kubernetes", "status": "match"},
            {"text": "COBOL", "status": "missing"},
        ]

    def create(self, **kwargs):
        self.calls.append(kwargs)
        messages = kwargs["messages"]
        blob = " ".join(str(m.get("content", "")) for m in messages)
        if "technical and professional skills" in str(messages[0].get("content", "")) or "skills list" in blob:
            import json
            content = json.dumps({"skills": SKILLS})
        else:
            import json
            content = json.dumps({
                "position_name": "Senior Python Engineer",
                "institution_name": "Acme Corp",
                "brief_description": "Build distributed services.",
                "requirements": self.requirements,
            })
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])


def fake_client(requirements=None):
    completions = FakeCompletions(requirements)
    return SimpleNamespace(chat=SimpleNamespace(completions=completions)), completions


CARD_PYTHON = {
    "job_id": "1111111111",
    "job_key": "urn:li:jobPosting:1111111111",
    "title": "Senior Python Engineer",
    "company": "Acme Corp",
    "location": "United States (Remote)",
    "posted_on": "2026-10-02",
    "url": "https://www.linkedin.com/jobs/view/1111111111",
    "applicants": 12,
    "description": "",
}
CARD_MARKETING = {
    "job_id": "2222222222",
    "job_key": "urn:li:jobPosting:2222222222",
    "title": "Growth Marketing Manager",
    "company": "Beta LLC",
    "location": "Austin, TX (Remote)",
    "posted_on": "2026-10-02",
    "url": "https://www.linkedin.com/jobs/view/2222222222",
    "applicants": 40,
    "description": "",
}
DETAIL_PYTHON = {
    "job_key": CARD_PYTHON["job_key"],
    "job_id": CARD_PYTHON["job_id"],
    "title": CARD_PYTHON["title"],
    "company": "Acme Corp",
    "location": "United States",
    "posted_on": "1 day ago",
    "url": CARD_PYTHON["url"],
    "applicants": 12,
    "seniority": "Mid-Senior level",
    "employment_type": "Full-time",
    "description": (
        "We are hiring a senior Python engineer to build distributed services. You will work with "
        "Docker, Kubernetes, PostgreSQL, Redis, AWS and Terraform. You will own CI/CD pipelines "
        "and design REST and GraphQL APIs alongside a team that values clean, tested code and "
        "pragmatic technical leadership in a fast-moving remote-first environment."
    ),
}


@pytest.fixture()
def user_id(monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", Path(tempfile.mkdtemp()) / "app.db")
    signup("jobs@test.dev", "password123")
    save_cv(1, "cv.tex", CV_TEX.encode("utf-8"))
    store.save_linkedin_account(1, "abc123", name="Luilver", email="jobs@test.dev")
    return 1


def _stub_network(monkeypatch, *, score=90):
    monkeypatch.setattr(job_source, "search", lambda *a, **k: [dict(CARD_PYTHON), dict(CARD_MARKETING)])
    monkeypatch.setattr(job_source, "fetch_detail", lambda job_id: dict(DETAIL_PYTHON))
    # The generators validate their output (LaTeX document, letter length), which
    # the fake AI cannot satisfy; stub them so a match keeps its materials.
    monkeypatch.setattr(
        matcher, "generate_tailored_cv",
        lambda *a, **k: "\\documentclass{article}\\begin{document}CV\\end{document}",
    )
    monkeypatch.setattr(matcher, "generate_cover_letter", lambda *a, **k: "Dear team, ...")
    client, completions = fake_client(
        [
            {"text": "Python", "status": "match"},
            {"text": "Docker", "status": "match"},
            {"text": "PostgreSQL", "status": "match"},
            {"text": "Kubernetes", "status": "match"},
            {"text": "COBOL", "status": "match"} if score == 100 else {"text": "COBOL", "status": "missing"},
        ]
    )
    return client, completions


# ── prefilter ────────────────────────────────────────────────────────────────

def test_prefilter_scores_a_real_match():
    job = {**CARD_PYTHON, "description": DETAIL_PYTHON["description"]}
    assert prefilter_score(job, SKILLS) > 30


def test_prefilter_rejects_marketing():
    assert prefilter_score(CARD_MARKETING, SKILLS) == 0


def test_prefilter_needs_three_absolute_hits():
    job = {"title": "Python Developer", "company": "X", "description": "Some Python work."}
    assert prefilter_score(job, SKILLS) == 0


def test_prefilter_respects_min_score():
    jobs = [{**CARD_PYTHON, "description": DETAIL_PYTHON["description"]}, dict(CARD_MARKETING)]
    assert len(rank(jobs, SKILLS, limit=5, min_score=10)) == 1
    assert rank(jobs, SKILLS, limit=5, min_score=0) != []


def test_rank_sorts_by_score_desc():
    jobs = [{**CARD_PYTHON, "description": DETAIL_PYTHON["description"]}, dict(CARD_MARKETING)]
    ranked = rank(jobs, SKILLS, limit=5, min_score=0)
    scores = [item["prefilter_score"] for item in ranked]
    assert scores == sorted(scores, reverse=True)


def test_tokenize_drops_stopwords():
    tokens = tokenize("the and of Python Kubernetes")
    assert "python" in tokens and "kubernetes" in tokens
    assert "the" not in tokens and "and" not in tokens


def test_suggest_keywords_prefers_single_words():
    assert suggest_keywords(SKILLS, 3) == ["Python", "FastAPI", "Flask"]


# ── skills ──────────────────────────────────────────────────────────────────

def test_extract_skills():
    client, _ = fake_client()
    skills = extract_skills(client, "OpenAI GPT", "gpt-4o-mini", "clean cv text")
    assert "Python" in skills and "Kubernetes" in skills


def test_ensure_skills_is_cached(user_id):
    client, completions = fake_client()
    first = ensure_skills(user_id, client, "OpenAI GPT", "gpt-4o-mini", "text", "v1")
    second = ensure_skills(user_id, client, "OpenAI GPT", "gpt-4o-mini", "text", "v1")
    assert first == second
    # One extraction call, not two.
    skill_calls = [
        c for c in completions.calls
        if "technical and professional skills" in str(c["messages"][0].get("content", ""))
    ]
    assert len(skill_calls) == 1


def test_ensure_skills_rebuilds_when_cv_changes(user_id):
    client, completions = fake_client()
    ensure_skills(user_id, client, "OpenAI GPT", "gpt-4o-mini", "text", "v1")
    ensure_skills(user_id, client, "OpenAI GPT", "gpt-4o-mini", "text", "v2")
    skill_calls = [
        c for c in completions.calls
        if "technical and professional skills" in str(c["messages"][0].get("content", ""))
    ]
    assert len(skill_calls) == 2


# ── settings ────────────────────────────────────────────────────────────────

def test_settings_defaults_and_keywords(user_id):
    settings = store.get_settings(user_id)
    assert settings["min_match_score"] == 80
    assert settings["enabled"] == 0
    saved = store.save_settings(user_id, keywords="python, python ,kubernetes,, rest api", enabled=True)
    assert saved["keyword_list"] == ["python", "kubernetes", "rest api"]
    assert saved["enabled"] == 1


def test_settings_clamp(user_id):
    saved = store.save_settings(user_id, min_match_score=500, digest_hour=99, daily_llm_budget=0)
    assert saved["min_match_score"] == 100
    assert saved["digest_hour"] == 23
    assert saved["daily_llm_budget"] == 1


# ── jobs store ──────────────────────────────────────────────────────────────

def test_upsert_job_is_idempotent(user_id):
    first = store.upsert_job(CARD_PYTHON)
    second = store.upsert_job({**CARD_PYTHON, "title": "Renamed"})
    assert first == second
    assert store.get_job(first)["title"] == "Renamed"


def test_unscored_job_ids(user_id):
    job_id = store.upsert_job(CARD_PYTHON)
    assert job_id in store.unscored_job_ids(user_id)
    store.record_evaluation(user_id, job_id, match_score=90)
    assert job_id not in store.unscored_job_ids(user_id)


# ── oauth ───────────────────────────────────────────────────────────────────

def test_oauth_state_roundtrip(user_id):
    store.store_oauth_state(user_id, "nonce-123")
    assert store.consume_oauth_state("nonce-123") == user_id
    # Single use.
    assert store.consume_oauth_state("nonce-123") is None


def test_consume_oauth_state_rejects_unknown():
    assert store.consume_oauth_state("never-issued") is None


def test_disconnect(user_id):
    assert store.get_linkedin_account(user_id)["linkedin_member_id"] == "abc123"
    assert store.disconnect_linkedin(user_id) is True
    assert store.get_linkedin_account(user_id) is None


# ── digest ──────────────────────────────────────────────────────────────────

def test_digest_lists_matches_with_links_and_attachments():
    items = [{
        "title": "Senior Python Engineer", "company": "Acme Corp", "url": "https://linkedin.com/jobs/view/1",
        "match_score": 90, "location": "Remote",
        "analysis": {"requirements": [{"text": "Python", "status": "match"}, {"text": "COBOL", "status": "missing"}]},
        "tailored_cv": "\\documentclass{article}...",
        "cover_letter": "Dear hiring team...",
    }]
    subject, text, html = digest_builder.build_digest(items, full_name="Luilver Garces")
    assert "1 matching remote job today" in subject
    assert "Senior Python Engineer" in text and "90%" in text
    assert "https://linkedin.com/jobs/view/1" in text
    assert "Hi Luilver," in text
    attachments = digest_builder.build_attachments(items)
    names = [a[0] for a in attachments]
    assert "acme-corp-senior-python-engineer-tailored-cv.tex" in names
    assert any(n.endswith("cover-letter.txt") for n in names)


def test_digest_handles_no_matches():
    subject, text, _ = digest_builder.build_digest([])
    assert "No new matching jobs" in subject
    assert "Nothing cleared" in text


def test_digest_slugs_unsafe_names():
    assert digest_builder._slug("C++ / Rust — Ltd!!") == "c-rust-ltd"


# ── end-to-end run ──────────────────────────────────────────────────────────

def test_run_discovery_scores_and_matches(user_id, monkeypatch):
    client, _ = _stub_network(monkeypatch)
    store.save_settings(user_id, enabled=True, keywords="python", min_match_score=80, daily_llm_budget=5)
    sent = {}
    monkeypatch.setattr(digest_builder, "send_digest", lambda email, items, **kw: sent.setdefault("email", email))
    # matcher imports is_smtp_configured from config_loader, so patch it there.
    monkeypatch.setattr(matcher, "is_smtp_configured", lambda: True)

    report = matcher.run_discovery(user_id, client=client, provider="OpenAI GPT", model="gpt-4o-mini")

    assert report["status"] == "ok"
    assert report["found"] == 2
    assert report["matches"] == 1  # only the Python card scores >= 80
    assert report["digest_sent"] is True
    assert sent["email"] == "jobs@test.dev"
    assert any("Senior Python Engineer" in m["title"] for m in matcher.preview_matches(user_id))


def test_run_discovery_generates_kit(user_id, monkeypatch):
    client, _ = _stub_network(monkeypatch)
    store.save_settings(user_id, enabled=True, keywords="python", min_match_score=80, daily_llm_budget=5, daily_gen_budget=1)
    monkeypatch.setattr(matcher, "generate_tailored_cv", lambda *a, **k: "\\documentclass{article}\\begin{document}CV\\end{document}")
    monkeypatch.setattr(matcher, "generate_cover_letter", lambda *a, **k: "Dear team, ...")
    monkeypatch.setattr(digest_builder, "send_digest", lambda *a, **k: "subject")

    report = matcher.run_discovery(user_id, client=client, provider="OpenAI GPT", model="gpt-4o-mini", send_digest=False)
    assert report["kits"] == 1
    matches = matcher.preview_matches(user_id)
    assert matches and matches[0]["tailored_cv"]
    assert matches[0]["cover_letter"] == "Dear team, ..."


def test_run_discovery_does_not_rescore(user_id, monkeypatch):
    client, _ = _stub_network(monkeypatch)
    store.save_settings(user_id, enabled=True, keywords="python", daily_llm_budget=5)
    matcher.run_discovery(user_id, client=client, provider="OpenAI GPT", model="gpt-4o-mini", send_digest=False)
    second = matcher.run_discovery(user_id, client=client, provider="OpenAI GPT", model="gpt-4o-mini", send_digest=False)
    assert second["reason"] == "No new postings since the last run."
    assert second["scored"] == 0


def test_run_skipped_when_disabled(user_id, monkeypatch):
    client, _ = _stub_network(monkeypatch)
    report = matcher.run_discovery(user_id, client=client, provider="OpenAI GPT", model="gpt-4o-mini")
    assert report["status"] == "skipped"
    assert "switched off" in report["reason"]


def test_run_skipped_without_linkedin(user_id, monkeypatch):
    store.disconnect_linkedin(user_id)
    client, _ = _stub_network(monkeypatch)
    store.save_settings(user_id, enabled=True)
    report = matcher.run_discovery(user_id, client=client, provider="OpenAI GPT", model="gpt-4o-mini")
    assert report["status"] == "skipped" and "LinkedIn" in report["reason"]


def test_run_survives_a_broken_posting(user_id, monkeypatch):
    def explode(job_id):
        raise ValueError("LinkedIn did not return the job description.")

    monkeypatch.setattr(job_source, "search", lambda *a, **k: [dict(CARD_PYTHON)])
    monkeypatch.setattr(job_source, "fetch_detail", explode)
    client, _ = fake_client()
    store.save_settings(user_id, enabled=True, keywords="python")
    report = matcher.run_discovery(user_id, client=client, provider="OpenAI GPT", model="gpt-4o-mini", send_digest=False)
    assert report["status"] == "ok"
    assert report["errors"]  # recorded, not raised
    assert report["matches"] == 0


def test_run_honours_daily_llm_budget(user_id, monkeypatch):
    client, _ = _stub_network(monkeypatch)
    store.save_settings(user_id, enabled=True, keywords="python", daily_llm_budget=1)
    matcher.run_discovery(user_id, client=client, provider="OpenAI GPT", model="gpt-4o-mini", send_digest=False)
    # A new posting appears but the budget for the day is spent.
    monkeypatch.setattr(job_source, "search", lambda *a, **k: [{**CARD_PYTHON, "job_id": "3333333333", "job_key": "urn:li:jobPosting:3333333333"}])
    report = matcher.run_discovery(user_id, client=client, provider="OpenAI GPT", model="gpt-4o-mini", send_digest=False)
    assert report["reason"] == "Today's LLM budget is already spent."


def test_card_triage_keeps_engineering_titles_and_drops_the_rest():
    """Triage is recall-biased: a false positive costs one HTTP fetch, a false negative costs a match."""
    wanted = [
        "Senior Python Engineer",
        "Backend Engineer, Python",
        "Platform Engineer (Kubernetes)",
        "DevOps Engineer",
        "Site Reliability Engineer",
        "Data Engineer",
        "Full Stack Engineer",
        "Solutions Architect",
    ]
    unwanted = [
        "Growth Marketing Manager",
        "Sales Development Representative",
        "Customer Success Manager",
        "Recruiter",
        "Warehouse Associate",
        "Financial Analyst",
        "Registered Nurse",
    ]
    for title in wanted:
        job = {"title": title, "company": "Acme", "location": "Remote"}
        assert card_relevance(job, SKILLS) is True, f"should have kept {title!r}"
    for title in unwanted:
        job = {"title": title, "company": "Acme", "location": "Remote"}
        assert card_relevance(job, SKILLS) is False, f"should have dropped {title!r}"


def test_card_triage_is_inert_without_skills():
    job = {"title": "Senior Python Engineer", "company": "Acme", "location": "Remote"}
    assert card_relevance(job, []) is False


def test_a_failed_fetch_is_retried_next_run(user_id, monkeypatch):
    """A transient LinkedIn error must not permanently drop a posting."""
    calls = {"n": 0}

    def flaky(job_id):
        calls["n"] += 1
        if calls["n"] == 1:
            raise ValueError("LinkedIn did not return the job description.")
        return dict(DETAIL_PYTHON)

    monkeypatch.setattr(job_source, "search", lambda *a, **k: [dict(CARD_PYTHON)])
    monkeypatch.setattr(job_source, "fetch_detail", flaky)
    monkeypatch.setattr(matcher, "generate_tailored_cv", lambda *a, **k: "CV")
    monkeypatch.setattr(matcher, "generate_cover_letter", lambda *a, **k: "CL")
    client, _ = fake_client(
        [
            {"text": "Python", "status": "match"},
            {"text": "Docker", "status": "match"},
            {"text": "PostgreSQL", "status": "match"},
            {"text": "Kubernetes", "status": "match"},
            {"text": "COBOL", "status": "missing"},
        ]
    )
    store.save_settings(user_id, enabled=True, keywords="python", min_match_score=80)

    first = matcher.run_discovery(user_id, client=client, provider="OpenAI GPT", model="gpt-4o-mini", send_digest=False)
    assert first["errors"] and first["matches"] == 0

    second = matcher.run_discovery(user_id, client=client, provider="OpenAI GPT", model="gpt-4o-mini", send_digest=False)
    assert second["scored"] == 1
    assert second["matches"] == 1
