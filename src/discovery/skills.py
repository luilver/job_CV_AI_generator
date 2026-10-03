"""Turn a stored CV into a reusable list of skills.

Discovery runs every day, and each run needs to know what the applicant can do.
Re-asking the model for that every day would be wasteful, so the list is extracted
once and cached against the CV version it came from. Changing the CV invalidates
the cache automatically.
"""

from __future__ import annotations

import json
import re
from typing import Any

from src.ai.client_factory import call_ai
from src.generators.analysis import get_json_object
from src.linkedin.store import get_skills, save_skills, skills_are_current

MAX_SKILLS = 60
MIN_SKILLS = 3

SYSTEM = (
    "You extract a candidate's technical and professional skills from their CV. "
    "Return only valid JSON, with no Markdown or commentary."
)


def _prompt(cv_text: str) -> str:
    return f"""List the concrete skills this candidate demonstrably has, most relevant first.

Rules:
- Each entry must be a short skill phrase of at most 4 words (for example "Python", "Kubernetes", "CI/CD", "machine learning").
- Only include skills the CV gives real evidence for. Do not infer seniority.
- Include technical skills, tools, languages, methods, and domain knowledge.
- Exclude soft adjectives ("team player", "hardworking") and anything unevidenced.
- Return between 10 and {MAX_SKILLS} entries, with no duplicates.

Return exactly this JSON shape:
{{"skills": ["Python", "FastAPI", "Kubernetes"]}}

CLEAN CV TEXT:
---
{cv_text}
---"""


def extract_skills(client: Any, provider: str, model: str, cv_text: str) -> list[str]:
    """One LLM call returning a clean list of skills."""
    data = get_json_object(call_ai(client, provider, model, SYSTEM, _prompt(cv_text)))
    raw = data.get("skills")
    if not isinstance(raw, list):
        raise ValueError("The AI did not return a skills list.")

    cleaned: list[str] = []
    seen: set[str] = set()
    for item in raw:
        if isinstance(item, dict):
            item = item.get("name") or item.get("skill") or ""
        text = " ".join(str(item or "").split())[:80]
        if not text or len(text) < 2:
            continue
        if not re.search(r"[A-Za-z]", text):
            continue
        key = text.lower()
        if key in seen:
            continue
        seen.add(key)
        cleaned.append(text)
    if len(cleaned) < MIN_SKILLS:
        raise ValueError("The AI did not return enough usable skills from that CV.")
    return cleaned[:MAX_SKILLS]


def ensure_skills(user_id: int, client: Any, provider: str, model: str, cv_text: str, cv_updated_at: str = "") -> list[str]:
    """Cached skill extraction: returns the stored list unless the CV changed."""
    if skills_are_current(user_id, cv_updated_at):
        existing = get_skills(user_id)
        if len(existing) >= MIN_SKILLS:
            return existing
    return save_skills(user_id, extract_skills(client, provider, model, cv_text), cv_updated_at)


def parse_skills_json(raw: str) -> list[str]:
    """Decode a stored skills column, tolerating anything unreadable."""
    if not raw:
        return []
    try:
        loaded = json.loads(raw)
    except (TypeError, ValueError):
        return []
    return [str(item).strip() for item in loaded if str(item).strip()] if isinstance(loaded, list) else []
