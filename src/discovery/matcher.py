"""The daily discovery run: search, gate, score, prepare, digest.

One pass, in order:

1. Read the user's settings, LinkedIn connection and CV.
2. Search LinkedIn's public guest job search for the configured keywords.
3. Drop postings this user has already scored, so nothing is paid for twice.
4. Card triage — a recall-biased yes/no on the title alone. Search cards carry no
   description, so the real prefilter cannot be run yet; triage only decides
   which postings are worth an HTTP request.
5. Fetch descriptions for the survivors, capped at MAX_HYDRATE.
6. Stage one — the free prefilter in prefilter.py — keeps the best few within
   the daily LLM budget.
7. Stage two — analyse_match() per survivor, one LLM call each — and keep the
   postings at or above min_match_score.
8. Generate a tailored CV and cover letter for the top few matches.
9. Email one digest listing the matches with those files attached.

Every stage is individually guarded: one unreadable posting, one failed model
call, or one email that will not send must not lose the whole day's work.

Every posting this run considers is recorded, including the ones turned down.
That is what makes a run idempotent — without it, the same rejected posting would
be re-read every day for as long as the account exists.
"""

from __future__ import annotations

import logging
from typing import Any

from src.ai.client_factory import (
    FREE_TIER_MODEL,
    FREE_TIER_PROVIDER,
    call_ai,
    make_free_tier_client,
)
from src.discovery.prefilter import rank, suggest_keywords, triage
from src.discovery.skills import ensure_skills
from src.generators.analysis import analyse_match
from src.generators.cv_cl_generators import generate_cover_letter, generate_tailored_cv
from src.history.store import match_score
from src.linkedin import jobs as job_source
from src.linkedin.store import (
    candidates_for_digest,
    get_kit,
    get_linkedin_account,
    get_settings,
    kit_count_today,
    list_matching_jobs,
    mark_digest_sent,
    mark_in_digest,
    mark_rejected,
    mark_run,
    record_evaluation,
    reset_digest_flags,
    save_kit,
    unscored_job_ids,
    upsert_job,
)
from src.mail import digest as digest_builder
from src.profile.cv_store import get_cv
from src.utils.config_loader import (
    app_base_url,
    is_smtp_configured,
    load_platform_openai_key,
    read_cover_letter_guidelines,
    read_guidelines,
)

log = logging.getLogger(__name__)

MAX_SEARCH_KEYWORDS = 4
MAX_HYDRATE = 25


def _resolve_client(client: Any, provider: str, model: str) -> tuple[Any, str, str]:
    """Fall back to the platform key when the caller did not supply one."""
    if client is not None:
        return client, provider or FREE_TIER_PROVIDER, model or FREE_TIER_MODEL
    key = load_platform_openai_key()
    if not key:
        raise ValueError(
            "No AI provider is available. Set PLATFORM_OPENAI_API_KEY (or an AI key) so the daily run can score matches."
        )
    return make_free_tier_client(key), FREE_TIER_PROVIDER, FREE_TIER_MODEL


def _user_email(user_id: int) -> str:
    from src.auth.auth import get_user_row

    row = get_user_row(user_id)
    return str(row["email"] or "") if row is not None else ""


def run_discovery(
    user_id: int,
    *,
    client: Any = None,
    provider: str = "",
    model: str = "",
    send_digest: bool = True,
) -> dict[str, Any]:
    """
    Run discovery once for one user.

    Returns a report dict. It always has 'status'; a skip is 'status': 'skipped'
    with a 'reason', and a completed run is 'status': 'ok'. Never raises for a
    per-posting or per-email failure — those are recorded in 'errors'.
    """
    report: dict[str, Any] = {
        "status": "skipped",
        "reason": "",
        "found": 0,
        "hydrated": 0,
        "prefiltered": 0,
        "scored": 0,
        "matches": 0,
        "kits": 0,
        "errors": [],
    }

    settings = get_settings(user_id)
    if not int(settings.get("enabled") or 0):
        report["reason"] = "Discovery is switched off for this account."
        return report
    if get_linkedin_account(user_id) is None:
        report["reason"] = "No LinkedIn account is connected."
        return report

    cv_record = get_cv(user_id)
    if cv_record is None:
        report["reason"] = "No CV is stored on the profile."
        return report

    client, provider, model = _resolve_client(client, provider, model)

    try:
        skills = ensure_skills(user_id, client, provider, model, cv_record["text"], cv_record.get("updated_at", ""))
    except Exception as exc:
        report["reason"] = f"The skill profile could not be built: {exc}"
        return report
    if not skills:
        report["reason"] = "The skill profile is empty."
        return report

    keywords = settings["keyword_list"] or suggest_keywords(skills, MAX_SEARCH_KEYWORDS)
    if not keywords:
        report["reason"] = "No search keywords are available."
        return report

    # ── Search ───────────────────────────────────────────────────────────────
    try:
        found = job_source.search(
            keywords,
            settings["location"],
            remote_only=bool(settings["remote_only"]),
            max_age_days=int(settings["max_age_days"]),
            pages=int(settings["search_pages"]),
        )
    except Exception as exc:
        report["reason"] = f"LinkedIn could not be searched: {exc}"
        return report

    report["found"] = len(found)
    if not found:
        report["status"] = "ok"
        mark_run(user_id, report)
        return report

    # Store every posting we found before asking which ones are new: a posting that
    # is not in the table yet cannot appear in the "never scored" lookup.
    row_ids: dict[str, int] = {}
    for item in found:
        try:
            row_ids[str(item.get("job_id") or "")] = upsert_job(item)
        except ValueError as exc:
            report["errors"].append(str(exc))

    pending = unscored_job_ids(user_id)
    fresh: list[dict[str, Any]] = [
        {**item, "row_id": row_ids[str(item.get("job_id") or "")]}
        for item in found
        if row_ids.get(str(item.get("job_id") or "")) in pending
    ]

    if not fresh:
        report["status"] = "ok"
        report["reason"] = "No new postings since the last run."
        mark_run(user_id, report)
        return report

    # ── Card triage: decide what is worth a description request ────────────
    llm_budget = max(0, int(settings["daily_llm_budget"]) - int(_scored_today(user_id)))
    if llm_budget == 0:
        report["status"] = "ok"
        report["reason"] = "Today's LLM budget is already spent."
        mark_run(user_id, report)
        return report

    worth_reading = triage(fresh, skills)
    # Anything turned down here is recorded as seen so tomorrow does not re-read it.
    turned_down = [int(job["row_id"]) for job in fresh if job not in worth_reading]
    mark_rejected(user_id, turned_down)
    if not worth_reading:
        report["status"] = "ok"
        report["reason"] = "No new posting was relevant enough to read."
        mark_run(user_id, report)
        return report

    # ── Fetch descriptions, capped so one busy day cannot hammer LinkedIn ────
    # A fetch that fails is NOT a decision: the posting stays unscored and is
    # retried tomorrow, because a transient LinkedIn error must not silently drop
    # a match. Only postings we actually got to read are ever marked rejected.
    report["hydrated"] = 0
    described: list[dict[str, Any]] = []
    for card in worth_reading[:MAX_HYDRATE]:
        try:
            detail = job_source.fetch_detail(card.get("job_id", ""))
        except ValueError as exc:
            report["errors"].append(f"{card.get('title', 'job')}: {exc}")
            continue
        described.append({**card, **detail, "row_id": int(card["row_id"])})
        report["hydrated"] = len(described)

    # ── Stage one: the real prefilter, now that descriptions are in hand ────
    gate = int(settings["min_prefilter"])
    scored_all = rank(described, skills, limit=len(described), min_score=0)
    mark_rejected(
        user_id,
        [int(job["row_id"]) for job in scored_all if int(job["prefilter_score"]) < gate],
    )
    candidates = [job for job in scored_all if int(job["prefilter_score"]) >= gate][:llm_budget]
    report["prefiltered"] = len(candidates)
    if not candidates:
        report["status"] = "ok"
        report["reason"] = "No new posting cleared the keyword prefilter."
        mark_run(user_id, report)
        return report

    # ── Stage two: one LLM call per surviving candidate ────────────────────
    threshold = int(settings["min_match_score"])
    matches: list[dict[str, Any]] = []
    scored = 0

    for candidate in candidates:
        job_id = int(candidate["row_id"])
        description = str(candidate.get("description") or "")
        if len(description) < 200:
            report["errors"].append(f"{candidate.get('title', 'job')}: description too short to score.")
            continue

        try:
            job_id = upsert_job(candidate)
        except ValueError:
            pass

        try:
            analysis = analyse_match(client, provider, model, description, cv_record["text"])
        except Exception as exc:
            report["errors"].append(f"{candidate.get('title', 'job')}: {exc}")
            continue

        scored += 1
        score = match_score(analysis)
        requirements = [r for r in analysis.get("requirements", []) if isinstance(r, dict)]
        record_evaluation(
            user_id,
            job_id,
            prefilter_score=int(candidate.get("prefilter_score") or 0),
            match_score=score,
            matched_count=sum(1 for r in requirements if str(r.get("status", "")).lower() == "match"),
            total_count=len(requirements),
            analysis=analysis,
        )
        if score >= threshold:
            matches.append({**candidate, "row_id": job_id, "match_score": score, "analysis": analysis})

    report["scored"] = scored
    report["matches"] = len(matches)
    report["status"] = "ok"

    # ── Application kits ────────────────────────────────────────────────────
    matches = _generate_kits(user_id, matches, client, provider, model, settings, cv_record, report)

    # ── Digest ──────────────────────────────────────────────────────────────
    if send_digest and int(settings.get("digest_enabled") or 0):
        _send_digest(user_id, matches, settings, report)

    if not matches:
        report["reason"] = report["reason"] or f"Nothing scored at or above {threshold}%."

    mark_run(user_id, report)
    return report


def _scored_today(user_id: int) -> int:
    from src.linkedin.store import evaluations_today

    return evaluations_today(user_id)


def _generate_kits(
    user_id: int,
    matches: list[dict[str, Any]],
    client: Any,
    provider: str,
    model: str,
    settings: dict[str, Any],
    cv_record: dict[str, Any],
    report: dict[str, Any],
) -> list[dict[str, Any]]:
    """Attach a tailored CV and cover letter to the top matches, within budget."""
    if not int(settings.get("generate_materials") or 0) or int(settings.get("daily_gen_budget") or 0) <= 0:
        return []

    from src.auth.auth import get_full_name

    remaining = max(0, int(settings["daily_gen_budget"]) - int(kit_count_today(user_id)))
    signer = get_full_name(user_id)
    prepared: list[dict[str, Any]] = []

    for match in matches:
        if remaining <= 0:
            break
        job_id = int(match["row_id"])
        existing = get_kit(user_id, job_id)
        if existing and existing.get("tailored_cv"):
            prepared.append({**match, "tailored_cv": existing["tailored_cv"], "cover_letter": existing.get("cover_letter", "")})
            continue
        try:
            tailored = generate_tailored_cv(
                client,
                provider,
                model,
                str(match.get("description") or ""),
                str(cv_record.get("raw") or ""),
                read_guidelines(),
                source_kind=str(cv_record.get("filetype") or "tex"),
            )
        except Exception as exc:
            report["errors"].append(f"{match.get('title', 'job')}: tailored CV failed — {exc}")
            continue
        try:
            letter = generate_cover_letter(
                client,
                provider,
                model,
                str(match.get("description") or ""),
                cv_record["text"],
                "",
                read_cover_letter_guidelines(),
                signer_name=signer,
            )
        except Exception as exc:
            report["errors"].append(f"{match.get('title', 'job')}: cover letter failed — {exc}")
            letter = ""

        save_kit(user_id, job_id, tailored, letter)
        prepared.append({**match, "tailored_cv": tailored, "cover_letter": letter})
        remaining -= 1
        report["kits"] = int(report.get("kits") or 0) + 1

    return prepared


def _send_digest(user_id: int, matches: list[dict[str, Any]], settings: dict[str, Any], report: dict[str, Any]) -> None:
    """Email this run's matches. The flag is only set once the send succeeds."""
    if not matches:
        return

    email = _user_email(user_id)
    if not email:
        report["errors"].append("The account has no email address for the digest.")
        return
    if not is_smtp_configured():
        report["errors"].append("Email is not configured, so the digest could not be sent.")
        return

    from src.auth.auth import get_full_name

    try:
        digest_builder.send_digest(
            email,
            matches,
            full_name=get_full_name(user_id),
            app_url=f"{app_base_url()}/jobs",
        )
    except Exception as exc:
        report["errors"].append(f"The digest could not be sent: {exc}")
        return
    mark_in_digest(user_id, [int(item["row_id"]) for item in matches])
    mark_digest_sent(user_id)
    report["digest_sent"] = True


def preview_matches(user_id: int, limit: int = 20, min_score: int = 0) -> list[dict[str, Any]]:
    """Matches for the Jobs page, with any generated materials joined in."""
    return list_matching_jobs(user_id, limit=limit, min_score=min_score)


def pending_digest_items(user_id: int, min_score: int) -> list[dict[str, Any]]:
    return candidates_for_digest(user_id, min_score)


def clear_digest_marks(user_id: int) -> None:
    reset_digest_flags(user_id)
