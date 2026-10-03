"""Stage one of the discovery pipeline: a free keyword gate.

Scoring a posting properly costs one LLM call. Running that over every remote
posting LinkedIn returns would cost hundreds of calls a month, so a cheap
heuristic runs first and only its survivors get the expensive treatment.

This is a gate, not a verdict. It reads nothing but the text on the search card,
spends nothing, and is deliberately allowed to be wrong in both directions —
tuned by min_prefilter in the discovery settings.
"""

from __future__ import annotations

import re
from typing import Any, Iterable

# Below this many matched skills, a posting is rejected regardless of ratio: a
# single coincidental word should never open the LLM budget.
MIN_ABSOLUTE_HITS = 3

# How many of the applicant's skills appearing in the title is "as sure as it
# gets". Past this the title term saturates.
TITLE_SATURATION = 6

COVERAGE_WEIGHT = 0.6
TITLE_WEIGHT = 0.4

# Words that carry no signal, so "the" and "and" never count as a shared skill.
STOPWORDS = frozenset(
    """
    a an and are as at be by for from has have in into is it its of on or that the
    their there these they this to we will with you your our us who what when where
    which while who ability able across all also am among any because been before
    being but can could did do does doing done down each few get got if into just
    like make many may might more most much must no nor not now off once only other
    should so some such than then these those through too under until up very via
    were what whom why will work working would you
    experience experienced years year team teams role roles ability knowledge
    skills skill working strong excellent good great new use using used help
    helping build building building solutions solution products product company
    companies environment environments including include includes required
    require requires requirements responsibility responsibilities qualifications
    qualification benefits salary apply applicant applicants remote hybrid onsite
    """.split()
)

# Spellings that mean the same thing in a job posting.
SKILL_ALIASES: dict[str, tuple[str, ...]] = {
    "javascript": ("js", "ecmascript", "es6"),
    "typescript": ("ts",),
    "python": ("py", "python3"),
    "kubernetes": ("k8s",),
    "postgres": ("postgresql", "psql"),
    "postgresql": ("postgres", "psql"),
    "node": ("nodejs", "node.js"),
    "nodejs": ("node", "node.js"),
    "aws": ("amazon web services",),
    "gcp": ("google cloud", "google cloud platform"),
    "azure": ("microsoft azure",),
    "ci/cd": ("ci", "cd", "continuous integration", "continuous delivery"),
    "machine learning": ("ml",),
    "artificial intelligence": ("ai",),
    "rest": ("restful", "rest api", "restful api"),
    "graphql": ("graph ql",),
    "docker": ("containers", "containerization"),
    "terraform": ("iac", "infrastructure as code"),
    "linux": ("unix",),
    "microservices": ("microservice", "micro-services"),
    "databases": ("database", "db", "rdbms"),
    "nosql": ("no-sql",),
    "git": ("version control",),
}


def tokenize(text: str) -> set[str]:
    """Lowercase word tokens with stopwords and one-character noise removed."""
    words = re.findall(r"[a-z0-9][a-z0-9+#.\-]*", str(text or "").lower())
    return {word.strip(".-") for word in words if len(word) > 1 and word not in STOPWORDS}


def _skill_groups(skill: str) -> list[frozenset[str]]:
    """Token sets that each count as evidence of this skill.

    'node.js' yields {node, js} as one group so both halves must be present,
    and the alias 'nodejs' counts as a separate complete group.
    """
    base = tokenize(skill)
    if not base:
        return []
    groups = [base]
    for alias in SKILL_ALIASES.get(skill.lower(), ()):  # exact-match aliases
        alias_tokens = tokenize(alias)
        if alias_tokens:
            groups.append(frozenset(alias_tokens))
    normalised = skill.lower().replace(" ", "")
    for key, aliases in SKILL_ALIASES.items():
        if key.replace(" ", "") != normalised:
            continue
        for alias in aliases:
            alias_tokens = tokenize(alias)
            if alias_tokens:
                groups.append(frozenset(alias_tokens))
    unique: list[frozenset[str]] = []
    for group in groups:
        if group and group not in unique:
            unique.append(group)
    return unique


def matched_skills(skills: Iterable[str], text_tokens: set[str]) -> list[str]:
    """Which of the applicant's skills appear in the given token set."""
    hits: list[str] = []
    for skill in skills:
        for group in _skill_groups(skill):
            if group <= text_tokens:
                hits.append(skill)
                break
    return hits


def prefilter_score(job: dict[str, Any], skills: list[str]) -> int:
    """
    Heuristic 0-100 estimate that a posting fits the applicant.

    Two signals, combined: what share of the applicant's skills the posting asks
    for (coverage), and how many of them appear in the posting's title (the title
    is the strongest single hint that a job is really theirs).

    The absolute value is not a match percentage. With a fifteen-skill profile a
    strong posting scores around 45 and an excellent one near 70, because a single
    posting will never name every skill the applicant has. Treat min_prefilter as
    a gate to keep high recall, not as "80% match".
    """
    if not skills:
        return 0

    title_tokens = tokenize(f"{job.get('title', '')} {job.get('company', '')}")
    body_tokens = tokenize(job.get("description", ""))
    all_tokens = title_tokens | body_tokens

    total = len(skills)
    coverage_hits = matched_skills(skills, all_tokens)
    title_hits = matched_skills(skills, title_tokens)

    if len(coverage_hits) < MIN_ABSOLUTE_HITS:
        return 0

    coverage = len(coverage_hits) / total
    title_ratio = min(1.0, len(title_hits) / max(1, min(total, TITLE_SATURATION)))
    score = 100 * (COVERAGE_WEIGHT * coverage + TITLE_WEIGHT * title_ratio)
    return max(0, min(100, round(score)))


# Job titles that promise engineering work without naming a technology.
# "DevOps Engineer" and "Site Reliability Engineer" match nothing in the skill
# list, yet their descriptions are wall-to-wall Docker and Kubernetes. Skipping
# them would silently cost real matches, so a role word earns a description read.
TITLE_ROLE_SIGNALS: frozenset[str] = frozenset(
    {
        "engineer", "engineering", "developer", "programmer", "devops", "dev",
        "ops", "sre", "reliability", "platform", "infrastructure", "infra",
        "backend", "front", "frontend", "fullstack", "stack", "web", "api",
        "software", "systems", "cloud", "architect", "automation", "dba",
        "database", "data", "machine", "learning", "ai", "ml", "security",
        "mobile", "ios", "android", "embedded", "firmware", "compiler",
        "kubernetes", "container", "microservices", "distributed", "scala",
        "golang", "rust", "java", "python", "django", "flask", "fastapi",
    }
)


def card_relevance(job: dict[str, Any], skills: list[str]) -> bool:
    """
    Yes/no: is this posting worth fetching a description for?

    LinkedIn's search cards carry only a title, a company and a location, so the
    full heuristic is blind here — a title naming one of your skills scores 0
    because the ratio cannot clear the bar. This triage answer is deliberately
    recall-biased, because the cost it protects (one rate-limited HTTP request) is
    far below the cost of being wrong (a match we never read, and never saw).
    """
    if not skills:
        return False

    title_tokens = tokenize(job.get("title", ""))
    if matched_skills(skills, title_tokens):
        return True
    if TITLE_ROLE_SIGNALS & title_tokens:
        return True

    card_tokens = title_tokens | tokenize(job.get("company", "")) | tokenize(job.get("location", ""))
    return len(matched_skills(skills, card_tokens)) >= 2


def triage(jobs: list[dict[str, Any]], skills: list[str]) -> list[dict[str, Any]]:
    """Keep the cards worth a description request."""
    return [job for job in jobs or [] if card_relevance(job, skills)]


def rank(jobs: list[dict[str, Any]], skills: list[str], limit: int = 10, min_score: int = 0) -> list[dict[str, Any]]:
    """
    Score and sort postings, best first, attaching 'prefilter_score'.

    Only the top `limit` survive: this is the list the LLM budget is spent on.
    """
    scored: list[dict[str, Any]] = []
    for job in jobs or []:
        score = prefilter_score(job, skills)
        if score < int(min_score or 0):
            continue
        scored.append({**job, "prefilter_score": score})
    scored.sort(key=lambda item: (-item["prefilter_score"], item.get("title", "").lower()))
    return scored[: max(0, int(limit))]


def suggest_keywords(skills: list[str], limit: int = 4) -> list[str]:
    """
    Search terms to try when the user has not configured any.

    Single-word skills make the best queries; multi-word phrases are kept too but
    rank below the specific ones.
    """
    single = [skill for skill in skills if " " not in skill and len(skill) > 2]
    phrases = [skill for skill in skills if " " in skill]
    return (single + phrases)[: max(1, int(limit))]
