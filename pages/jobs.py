"""Job Discovery: connect LinkedIn, run a search, and review daily matches.

Nothing on this page submits anything to LinkedIn. It searches the public guest
job search, scores what it finds against the stored CV, writes the tailored CV
and cover letter to disk, and links out so the user can apply themselves.
"""

from __future__ import annotations

import logging
from typing import Any

import streamlit as st

from src.auth.auth import is_logged_in
from src.auth.db import init_db
from src.auth.ui import render_auth_forms, render_user_status_sidebar
from src.discovery.matcher import MAX_SEARCH_KEYWORDS, run_discovery
from src.discovery.prefilter import suggest_keywords
from src.discovery.skills import get_skills
from src.linkedin.jobs import canonical_url, job_id_from_key, search_page
from src.linkedin.oauth import authorization_url
from src.linkedin.store import (
    delete_skill_profile,
    disconnect_linkedin,
    get_kit,
    get_linkedin_account,
    get_settings,
    last_run_report,
    list_matching_jobs,
    save_settings,
)
from src.profile.cv_store import get_cv
from src.utils.config_loader import app_base_url, is_linkedin_configured, linkedin_setup_hint

log = logging.getLogger(__name__)

st.set_page_config(page_title="Job Discovery", page_icon="🔍", layout="wide")
init_db()


def _apply_link(record: dict[str, Any]) -> str:
    """The public posting URL. The row keeps `url`; fall back to the job key."""
    url = str(record.get("url") or "")
    if url:
        return url
    job_id = job_id_from_key(str(record.get("job_key") or ""))
    return canonical_url(job_id) if job_id else ""


def _guard_login() -> int:
    """Return the logged-in user id, or render the auth forms and stop."""
    if not is_logged_in():
        st.title("Job Discovery")
        st.info("Log in to connect LinkedIn and start matching.")
        if render_auth_forms():
            st.rerun()
        st.stop()
    user_id = st.session_state["auth_user_id"]
    render_user_status_sidebar(user_id)
    return int(user_id)


def _render_linkedin(user_id: int) -> None:
    st.subheader("1. LinkedIn")
    account = get_linkedin_account(user_id)

    if account is not None:
        col_a, col_b, col_c = st.columns([3, 1, 1])
        with col_a:
            st.success(f"Connected as **{account.get('member_name') or 'LinkedIn member'}**")
            if account.get("member_email"):
                st.caption(
                    f"{account['member_email']} · member id {account['linkedin_member_id']}"
                )
        with col_b:
            if st.button("Disconnect", key="li_disconnect"):
                disconnect_linkedin(user_id)
                delete_skill_profile(user_id)
                st.rerun()
        with col_c:
            if st.button("Refresh ↻", key="li_refresh"):
                st.rerun()
        st.caption(
            "Only your name, email and member id are stored. The access token is "
            "discarded after the profile is read. Nothing here reads your feed or "
            "messages, and nothing here applies to jobs for you."
        )
        if st.button("Check it", key="li_check"):
            _render_connection_check(user_id)
        return

    if not is_linkedin_configured():
        st.warning(linkedin_setup_hint())
        if st.button("Check it", key="li_check"):
            _render_connection_check(user_id)
        return

    st.write("Connect LinkedIn to identify your account. It is a one-time sign-in.")
    target = authorization_url(user_id)
    st.markdown(f'<a href="{target}" target="_blank">Connect LinkedIn</a>', unsafe_allow_html=True)
    st.caption(
        f"You will be sent back to `{app_base_url()}/linkedin_callback`. "
        "You can disconnect at any time."
    )
    _warn_if_host_mismatch()
    if st.button("Check it", key="li_check"):
        _render_connection_check(user_id)


def _serving_host() -> str:
    """The Host header the browser used, or "" if it is unavailable."""
    try:
        return str(st.context.headers.get("Host", "") or "")
    except Exception:
        return ""


def _render_connection_check(user_id: int) -> None:
    """A pass/fail checklist for the LinkedIn connection and the search path.

    The most common failure is silent: the sign-in completes on a different host
    than the one you are looking at, so no account is stored here. This says so.
    """
    checks: list[tuple[bool, str]] = []

    configured = is_linkedin_configured()
    checks.append(
        (
            configured,
            "LinkedIn app credentials are present"
            if configured
            else "LinkedIn app credentials are missing — set LINKEDIN_CLIENT_ID and "
            "LINKEDIN_CLIENT_SECRET in secrets",
        )
    )

    account = get_linkedin_account(user_id)
    if account is not None:
        who = account.get("member_name") or account.get("linkedin_member_id") or "member"
        checks.append((True, f"LinkedIn account is connected on this host as {who}"))
    else:
        checks.append(
            (
                False,
                "No LinkedIn account is connected on THIS host — if you signed in "
                "somewhere else (for example the deployed site), it landed in that "
                "database, not this one",
            )
        )

    base = app_base_url()
    host = _serving_host()
    checks.append(
        (
            bool(host) and (base.endswith(host) or host in base),
            f"Sign-in returns to {base} (this page is served from {host or 'unknown'})",
        )
    )

    cv = get_cv(user_id)
    checks.append((cv is not None, "A CV is on file to match against" if cv else "No CV on file — upload one on the Profile page"))

    st.markdown("**Connection check**")
    for ok, message in checks:
        (st.success if ok else st.error)(message)

    if not configured:
        return

    settings = get_settings(user_id) or {}
    keyword = next(
        (k.strip() for k in str(settings.get("keywords") or "").split(",") if k.strip()),
        "software engineer",
    )
    try:
        cards = search_page(keyword, "", remote_only=True)
    except Exception as exc:  # pragma: no cover - network dependent
        st.error(f"LinkedIn guest search probe failed: {exc}")
        return
    if cards:
        st.success(f'LinkedIn guest search is reachable — {len(cards)} cards for "{keyword}"')
    else:
        st.warning(
            f'LinkedIn guest search returned no cards for "{keyword}". It may be '
            "rate-limiting, or the page markup changed."
        )



def _warn_if_host_mismatch() -> None:
    """Explain the trap where LinkedIn returns to a different host than this one.

    Running locally with APP_BASE_URL set to the production domain sends the OAuth
    callback to production, so the connection can never complete in this session.
    """
    base = app_base_url()
    host = _serving_host()
    if not host or base.endswith(host) or host in base:
        return
    st.warning(
        f"This app is being served from **{host}**, but `APP_BASE_URL` is **{base}**. "
        f"LinkedIn will send you back to {base}/linkedin_callback, so the connection will "
        "not complete on this host. Either use the app at that address, or add "
        f'`LINKEDIN_REDIRECT_URI = "http://{host}/linkedin_callback"` to your secrets and '
        "register that same URL on the LinkedIn app."
    )


def _render_settings(user_id: int) -> None:
    st.subheader("2. What to look for")
    settings = get_settings(user_id)
    cv = get_cv(user_id)
    skills = get_skills(user_id)
    editable = cv is not None

    if cv is None:
        st.warning("Upload a CV on the Profile page first — every match is scored against it.")
    else:
        st.caption(f"Scoring against **{cv['filename']}**.")

    if skills:
        st.caption(f"Skills on file ({len(skills)}): " + ", ".join(skills[:14]))
    elif cv is not None:
        st.info("Your skill profile will be built from your CV on the first run.")

    suggested = suggest_keywords(skills, MAX_SEARCH_KEYWORDS) if skills else []
    if suggested:
        st.caption("Suggested: " + ", ".join(suggested))

    keywords = st.text_input(
        "Search keywords",
        value=settings.get("keywords") or ", ".join(suggested),
        help=f"Comma separated, up to {MAX_SEARCH_KEYWORDS}. Leave blank to use the suggested terms.",
        disabled=not editable,
    )

    c1, c2, c3 = st.columns(3)
    threshold = c1.number_input(
        "Minimum match %",
        min_value=1,
        max_value=100,
        value=int(settings["min_match_score"]),
        step=5,
        help="Only postings at or above this are kept and emailed.",
        disabled=not editable,
    )
    llm_budget = c2.number_input(
        "Daily AI reviews",
        min_value=1,
        max_value=50,
        value=int(settings["daily_llm_budget"]),
        help="Each review is one AI call. Free prefiltering runs first, so only the best candidates are charged.",
        disabled=not editable,
    )
    gen_budget = c3.number_input(
        "CV + letter per day",
        min_value=0,
        max_value=10,
        value=int(settings["daily_gen_budget"]),
        help="0 disables writing a tailored CV and cover letter.",
        disabled=not editable,
    )

    o1, o2, o3 = st.columns([1, 1, 2])
    enabled = o1.toggle("Daily discovery", bool(settings["enabled"]), key="set_enabled", disabled=not editable)
    digest_enabled = o2.toggle(
        "Email the digest",
        bool(settings["digest_enabled"]),
        key="set_digest",
        help=f"Sent around {settings['digest_hour']:02d}:00 UTC with the tailored files attached.",
        disabled=not editable,
    )
    remote_only = o3.toggle("Remote only", bool(settings["remote_only"]), key="set_remote", disabled=not editable)

    if st.button("Save settings", type="primary", key="save_settings", disabled=not editable):
        save_settings(
            user_id,
            enabled=enabled,
            digest_enabled=digest_enabled,
            keywords=keywords,
            min_match_score=int(threshold),
            daily_llm_budget=int(llm_budget),
            daily_gen_budget=int(gen_budget),
            remote_only=remote_only,
        )
        st.success("Settings saved.")


def _render_matches(user_id: int, threshold: int) -> None:
    st.subheader("4. Matches")
    matches = list_matching_jobs(user_id, limit=50, min_score=threshold)
    if not matches:
        st.info(
            "No matches yet. Run a search above, or wait for the daily run. "
            f"Anything at or above {threshold}% will appear here."
        )
        return

    st.caption(f"{len(matches)} posting{'s' if len(matches) != 1 else ''} at or above {threshold}%.")
    for match in matches:
        job_id = int(match["id"])
        score = int(match["match_score"] or 0)
        title = match["title"] or "Untitled role"
        header = f"**{title}** · {score}%"
        with st.expander(header):
            meta = " · ".join(
                part for part in (match.get("company"), match.get("location"), match.get("posted_on")) if part
            )
            if meta:
                st.caption(meta)

            counts = []
            if match.get("total_count"):
                counts.append(f"{match['matched_count']}/{match['total_count']} requirements met")
            if match.get("prefilter_score"):
                counts.append(f"keyword score {match['prefilter_score']}")
            if counts:
                st.caption(" · ".join(counts))

            link = _apply_link(match)
            if link:
                st.markdown(
                    f'<a href="{link}" target="_blank">Apply on LinkedIn ↗</a>',
                    unsafe_allow_html=True,
                )

            description = str(match.get("description") or "")
            if description:
                with st.expander("Job description"):
                    st.text(description)

            _render_requirements(match.get("analysis") or {})

            kit = get_kit(user_id, job_id)
            if kit:
                st.markdown("**Your materials**")
                c1, c2 = st.columns(2)
                with c1:
                    st.download_button(
                        "Download CV (.tex)",
                        data=str(kit["tailored_cv"] or ""),
                        file_name=f"cv_{_slug(title)}.tex",
                        mime="application/x-tex",
                        key=f"dl_cv_{job_id}",
                        use_container_width=True,
                    )
                with c2:
                    st.download_button(
                        "Download cover letter (.md)",
                        data=str(kit["cover_letter"] or ""),
                        file_name=f"cover_letter_{_slug(title)}.md",
                        mime="text/markdown",
                        key=f"dl_cl_{job_id}",
                        use_container_width=True,
                    )
                    st.download_button(
                        "Download letter (.txt)",
                        data=str(kit["cover_letter"] or ""),
                        file_name=f"cover_letter_{_slug(title)}.txt",
                        mime="text/plain",
                        key=f"dl_txt_{job_id}",
                    )
            else:
                st.caption("No tailored CV written for this one (daily limit reached, or disabled).")


def _slug(value: str) -> str:
    keep = [ch.lower() if ch.isalnum() else "_" for ch in str(value)[:40]]
    return "".join(keep).strip("_") or "job"


def _render_requirements(analysis: dict[str, Any]) -> None:
    requirements = [r for r in analysis.get("requirements", []) if isinstance(r, dict)]
    if not requirements:
        return
    st.markdown("**Requirement breakdown**")
    for requirement in requirements:
        status = str(requirement.get("status", "")).lower()
        icon = {"match": "✅", "missing": "❌", "partial": "🟡"}.get(status, "•")
        skill = requirement.get("requirement") or requirement.get("text") or "—"
        evidence = requirement.get("evidence") or ""
        if evidence:
            st.markdown(f"{icon} **{skill}** — {evidence}")
        else:
            st.markdown(f"{icon} **{skill}**")
    summary = analysis.get("summary")
    if summary:
        st.markdown(f"**Why it fits:** {summary}")


def _render_run(user_id: int, *, can_run: bool) -> None:
    st.subheader("3. Run a search")
    last = last_run_report(user_id)
    if last:
        st.caption(f"Last run: {last.get('last_run_at', 'unknown')} — {last.get('status')}")
        if last.get("reason"):
            st.caption(last["reason"])

    if not can_run:
        missing = []
        if get_linkedin_account(user_id) is None:
            missing.append("connect LinkedIn in step 1")
        if get_cv(user_id) is None:
            missing.append("upload your CV on the Profile page")
        st.warning("The **Run now** button appears once you " + " and ".join(missing) + ".")
        return

    if st.button("Run now", type="primary", key="run_discovery"):
        with st.spinner("Searching LinkedIn and reviewing postings…"):
            try:
                report = run_discovery(user_id, send_digest=True)
            except Exception as exc:  # a bad day must not take the page down
                log.exception("discovery run failed")
                st.error(f"The run failed: {exc}")
                return

        if report["errors"]:
            st.warning(f"{len(report['errors'])} posting(s) could not be read; the rest completed.")
            for message in report["errors"][:5]:
                st.caption(f"· {message}")

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Found", report["found"])
        c2.metric("Read", report["hydrated"])
        c3.metric("Reviewed", report["scored"])
        c4.metric("Matched", report["matches"])

        if report["status"] == "ok" and not report["matches"]:
            st.info(report["reason"] or "No posting reached your match threshold today.")
        elif report.get("digest_sent"):
            st.success("Matches emailed with the tailored CV and cover letter attached.")
        elif report["matches"]:
            st.success(f"{report['matches']} match(es) ready below.")


def main() -> None:
    st.title("Job Discovery")
    st.write(
        "Find remote roles that match your CV, then write the tailored CV and "
        "cover letter for each one. You apply yourself — nothing is submitted for you."
    )
    user_id = _guard_login()

    settings = get_settings(user_id)
    threshold = int(settings["min_match_score"])

    _render_linkedin(user_id)
    st.divider()

    can_run = get_linkedin_account(user_id) is not None and get_cv(user_id) is not None
    _render_settings(user_id)
    st.divider()

    _render_run(user_id, can_run=can_run)
    st.divider()

    _render_matches(user_id, threshold)
    st.divider()
    st.caption(
        "Postings are read from LinkedIn's public guest job search. Nothing is "
        "applied to on your behalf, and no LinkedIn credentials are stored."
    )


main()