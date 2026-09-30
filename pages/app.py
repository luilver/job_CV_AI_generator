"""Streamlit UI entrypoint for Job-CV Matcher & Tailor."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import requests
import streamlit as st

from src.utils.config_loader import (
    load_credentials,
    load_platform_openai_key,
    save_credential,
    read_guidelines,
    read_cover_letter_guidelines,
)
from src.utils.url_utils import normalise_url
from src.parsers.html_parser import scrape_job
from src.ai.client_factory import (
    FREE_TIER_PROVIDER,
    FREE_TIER_MODEL,
    call_ai,
    make_client,
    make_free_tier_client,
)
from src.generators.analysis import analyse_match
from src.generators.cv_cl_generators import generate_tailored_cv, generate_cover_letter
from src.auth.auth import get_full_name, is_logged_in, logout
from src.auth.subscription import (
    can_use_service,
    consume_trial,
    is_subscribed,
    trial_remaining,
    get_subscription,
)
from src.auth.ui import render_auth_forms, render_user_status_sidebar
from src.compensation.estimator import (
    AiCompensationProvider,
    lookup_compensation,
    summarise_compensation,
)
from src.history.store import get_match, list_matches, record_match
from src.payments.checkout_ui import render_subscribe_section
from src.profile.cv_store import describe_cv, get_cv


def initialise_state() -> None:
    defaults = {
        "job_text": "",
        "job_url": "",
        "job_input_mode": "URL",
        "job_description_input": "",
        "cv_text": "",
        "cv_raw": "",
        "cv_name": "",
        "cv_type": "tex",
        "analysis": None,
        "compensation": None,
        "tailored_cv": "",
        "cover_letter": "",
        "cover_letter_instructions": "",
        "show_cover_letter_form": False,
    }
    for key, value in defaults.items():
        st.session_state.setdefault(key, value)


def render_requirements(requirements: list[dict[str, str]]) -> None:
    for requirement in requirements:
        match = requirement.get("status") == "match"
        colour, background, label = (
            ("#15803d", "#f0fdf4", "MATCH") if match else ("#b91c1c", "#fef2f2", "MISSING")
        )
        text = requirement.get("text", "")
        st.markdown(
            f'<div style="background:{background}; border-left:5px solid {colour}; padding:0.65rem 0.9rem; margin:0.45rem 0; border-radius:4px;">'
            f'<span style="color:{colour}; font-weight:700; margin-right:0.7rem;">{label}</span>'
            f'{text}</div>',
            unsafe_allow_html=True,
        )


def render_job_details(
    position_name: str | None,
    institution_name: str | None,
    brief_description: str | None,
    compensation: dict[str, Any] | None = None,
) -> None:
    position = str(position_name or "Job position").strip()
    institution = str(institution_name or "").strip()
    description = str(brief_description or "").strip()
    st.subheader(position)
    if institution:
        st.caption(f"Institution: {institution}")
    summary = summarise_compensation(compensation)
    if summary:
        st.caption(f"💰 {summary}")
        note = str(compensation.get("note", "")).strip() if compensation else ""
        if note:
            st.caption(f"_{note}_")
    if description:
        st.markdown(description)
    else:
        st.caption("No brief description was provided.")


def _lookup_compensation(
    client: Any, provider: str, model: str, result: dict[str, Any], job_text: str
) -> None:
    """Best-effort pay estimate for the role, shown under the company name."""
    st.session_state.compensation = None
    if not st.session_state.get("estimate_compensation", True):
        return
    company = str(result.get("institution_name") or "").strip()
    if not company:
        return
    try:
        with st.spinner("Looking up compensation..."):
            st.session_state.compensation = lookup_compensation(
                AiCompensationProvider(client, provider, model),
                company=company,
                position=str(result.get("position_name") or ""),
                job_text=job_text,
            )
    except Exception:
        st.session_state.compensation = None


def _record_match(user_id: int, result: dict[str, Any], job_url: str, job_text: str) -> None:
    """Add this analysis to the user's match history. Never breaks the analysis."""
    try:
        record_match(
            user_id,
            result,
            job_url=job_url,
            job_text=job_text,
            compensation=st.session_state.get("compensation"),
        )
    except Exception:
        pass


def _letter_stem(state: Any, signer_name: str) -> str:
    """Filename stem for a generated letter: the user's name if there is one."""
    source = signer_name or state.get("cv_name") or "cover_letter"
    slug = re.sub(r"[^A-Za-z0-9._-]+", "_", source).strip("_")
    return slug or Path(state.get("cv_name") or "cover_letter").stem


def _load_pending_match() -> None:
    """Show a match the user picked on the history page instead of a new run."""
    match_id = st.session_state.pop("history_pending_id", None)
    if match_id is None:
        return
    record = get_match(st.session_state["auth_user_id"], match_id)
    if record is None or not record.get("analysis"):
        return
    st.session_state.update(
        job_text=record.get("job_text", ""),
        job_url=record.get("job_url", ""),
        job_description_input=record.get("job_text", "")[:20_000] if not record.get("job_url") else "",
        job_input_mode="URL" if record.get("job_url") else "Paste text",
        analysis=record["analysis"],
        compensation=record.get("compensation"),
        tailored_cv="",
        cover_letter="",
        show_cover_letter_form=False,
    )


# ---------------------------------------------------------------------------
# Sidebar: build the AI client based on the user's plan
# ---------------------------------------------------------------------------

def _build_sidebar(user_id: int) -> tuple[Any, str, str]:
    """
    Render the sidebar configuration and return (ai_client, provider, model).

    - Free-trial users  → RemoteAIClient, no API key needed.
    - Subscribed (basic) → full provider + API key selection.
    - Subscribed (own_key) → full provider + API key selection (they supply key).
    """
    subscribed = is_subscribed(user_id)
    sub = get_subscription(user_id)
    plan = sub["plan"] if sub else "free"

    render_user_status_sidebar(user_id)

    st.sidebar.markdown("---")
    st.sidebar.header("Configuration")

    if not subscribed:
        # Free trial — use the platform's OpenAI key, invisible to the user
        platform_key = load_platform_openai_key()
        if not platform_key:
            st.sidebar.error("Platform API key not configured. Contact support.")
        else:
            st.sidebar.info(f"Free trial — powered by our AI ({FREE_TIER_MODEL}).")
        client = make_free_tier_client(platform_key)
        return client, FREE_TIER_PROVIDER, FREE_TIER_MODEL

    # Paid plan — full provider / model / API key selection
    credentials = load_credentials()

    if plan == "own_key":
        st.sidebar.success("BYO API Key plan — configure your provider below.")
    else:
        st.sidebar.success("Basic plan — configure your preferred AI provider below.")

    provider = st.sidebar.selectbox(
        "AI provider",
        ["OpenAI GPT", "Codex", "Gemini 2.5 Flash", "Groq"],
    )
    credential_name = {
        "OpenAI GPT": "openai_api_key",
        "Codex": "codex_api_key",
        "Gemini 2.5 Flash": "gemini_api_key",
        "Groq": "groq_api_key",
    }[provider]

    api_key = st.sidebar.text_input(
        f"{provider} API Key",
        value=credentials[credential_name],
        type="password",
        help="Saved locally to credentials.json.",
    )
    if api_key.strip() and api_key.strip() != credentials[credential_name]:
        save_credential(credential_name, api_key)
        credentials[credential_name] = api_key.strip()
    if st.sidebar.button("Save API Key"):
        if api_key.strip():
            save_credential(credential_name, api_key)
            credentials[credential_name] = api_key.strip()
            st.sidebar.success("API key saved.")
        else:
            st.sidebar.warning("Enter an API key before saving.")

    if provider == "Codex":
        st.sidebar.info("Codex API access requires a paid API subscription.")
        model = st.sidebar.selectbox(
            "Codex model", ["gpt-5-codex", "gpt-5.3-codex", "codex-mini-latest"], index=0
        )
    elif provider == "Gemini 2.5 Flash":
        model = st.sidebar.selectbox(
            "Gemini model",
            ["gemini-3.6-flash", "gemini-2.5-flash"],
            index=0,
            help="Gemini 2.5 Flash may be unavailable to new users.",
        )
        if model == "gemini-2.5-flash":
            st.sidebar.warning("Gemini 2.5 Flash is legacy — use Gemini 3.6 Flash if you get a 404.")
    elif provider == "Groq":
        model = st.sidebar.selectbox(
            "Groq model",
            ["llama-3.3-70b-versatile", "openai/gpt-oss-120b", "openai/gpt-oss-20b"],
            index=0,
        )
    else:
        model = st.sidebar.selectbox(
            "GPT model", ["gpt-4o", "gpt-4o-mini", "gpt-4-turbo"], index=0
        )

    if not api_key.strip():
        st.sidebar.warning("Enter an API key to run analyses.")

    client = make_client(provider, api_key.strip())
    return client, provider, model


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    st.set_page_config(page_title="Job-CV Matcher & Tailor", layout="wide")
    initialise_state()

    # ── Auth gate ────────────────────────────────────────────────────────────
    if not is_logged_in():
        st.title("Job-CV Matcher & Tailor")
        st.info("Please log in or create a free account to continue.")
        authenticated = render_auth_forms()
        if authenticated:
            st.rerun()
        return

    user_id: int = st.session_state["auth_user_id"]

    # ── Subscription gate ────────────────────────────────────────────────────
    if not can_use_service(user_id):
        st.title("Job-CV Matcher & Tailor")
        st.warning("Your free trial is exhausted. Subscribe to keep going.")
        render_user_status_sidebar(user_id)
        render_subscribe_section(user_id)
        return

    # ── Build sidebar (returns client/provider/model) ────────────────────────
    client, provider, model = _build_sidebar(user_id)

    # ── CV from the profile page (sidebar) ───────────────────────────────────
    st.sidebar.markdown("---")
    cv_record = get_cv(user_id)
    if cv_record is None:
        st.sidebar.warning("No CV saved on your profile yet.")
    else:
        st.sidebar.success(f"CV: {describe_cv(cv_record)}")
        st.session_state.update(
            cv_raw=cv_record["raw"],
            cv_text=cv_record["text"],
            cv_name=cv_record["filename"],
            cv_type=cv_record["filetype"],
        )
    if st.sidebar.button("Manage CV", key="sidebar_manage_cv"):
        st.switch_page("pages/profile.py")
    saved_matches = len(list_matches(user_id))
    st.sidebar.caption(f"Matches saved: {saved_matches}")
    if st.sidebar.button("Match history", key="sidebar_history"):
        st.switch_page("pages/history.py")
    st.sidebar.toggle(
        "Estimate compensation",
        value=True,
        key="estimate_compensation",
        help=(
            "After an analysis, ask the AI for a pay estimate for the role and show it "
            "under the company name. Uses one extra AI request."
        ),
    )

    if cv_record is None:
        st.title("Job-CV Matcher & Tailor")
        st.warning("Add your CV to run an analysis — both `.tex` and `.pdf` are supported.")
        if st.button("Go to your profile", type="primary", key="app_goto_profile"):
            st.switch_page("pages/profile.py")
        return

    _load_pending_match()

    # ── Header ───────────────────────────────────────────────────────────────
    st.title("Job-CV Matcher & Tailor")
    remaining = trial_remaining(user_id)
    subscribed = is_subscribed(user_id)
    if not subscribed and remaining > 0:
        st.caption(
            f"Free trial: **{remaining}** {'analysis' if remaining == 1 else 'analyses'} remaining. "
            "Each click of Analyze Match uses one."
        )
    else:
        st.caption(
            f"Using **{st.session_state.cv_name}** from your profile. "
            "Compare it with a live job description, then generate a focused version."
        )

    # ── Job input ────────────────────────────────────────────────────────────
    job_input_mode = st.radio(
        "Job description source",
        ["URL", "Paste text"],
        key="job_input_mode",
        horizontal=True,
        help="Choose whether to fetch the description from a web page or use text you already have.",
    )
    if job_input_mode == "URL":
        url = st.text_input(
            "Job URL", value=st.session_state.job_url, placeholder="https://example.com/job"
        )
        pasted_job = ""
    else:
        url = ""
        pasted_job = st.text_area(
            "Job description",
            key="job_description_input",
            height=260,
            placeholder="Paste the full job description here...",
            help="Include the responsibilities, requirements, and qualifications where available.",
        )

    analyse = st.button("Analyze Match", type="primary")
    if analyse:
        # Validate inputs
        needs_api_key = is_subscribed(user_id) and provider != FREE_TIER_PROVIDER
        api_key_missing = needs_api_key and not _get_current_api_key(provider)
        if api_key_missing:
            st.error("Enter an API key for the selected provider in the sidebar.")
        elif not st.session_state.cv_text:
            st.error("Add your CV in the Profile page first.")
        elif job_input_mode == "URL" and not url.strip():
            st.error("Enter a job URL first.")
        elif job_input_mode == "Paste text" and not pasted_job.strip():
            st.error("Paste a job description first.")
        else:
            try:
                if job_input_mode == "URL":
                    clean_url = normalise_url(url)
                    with st.spinner("Scraping the job description..."):
                        if st.session_state.job_url == clean_url and st.session_state.job_text:
                            job_text = st.session_state.job_text
                        else:
                            job_text = scrape_job(clean_url)
                else:
                    clean_url = ""
                    job_text = pasted_job.strip()

                with st.spinner("Comparing the job with your CV..."):
                    result = analyse_match(client, provider, model, job_text, st.session_state.cv_text)

                # Consume a trial use if the user is on the free tier
                if not subscribed:
                    consume_trial(user_id)

                st.session_state.update(
                    job_text=job_text,
                    job_url=clean_url if job_input_mode == "URL" else "",
                    analysis=result,
                    compensation=None,
                    tailored_cv="",
                    cover_letter="",
                    show_cover_letter_form=False,
                )
                st.success("Analysis complete.")
                _lookup_compensation(client, provider, model, result, job_text)
                _record_match(user_id, result, clean_url, job_text)
            except requests.RequestException as exc:
                if "generativelanguage.googleapis.com" in str(exc):
                    st.error(f"Gemini API request failed: {exc}")
                else:
                    st.error(f"Could not fetch the job page: {exc}")
            except Exception as exc:
                st.error(f"Analysis failed: {exc}")

    # ── Results ──────────────────────────────────────────────────────────────
    result = st.session_state.analysis
    if result:
        left, right = st.columns([1.35, 1])
        with left:
            render_job_details(
                result.get("position_name"),
                result.get("institution_name"),
                result.get("brief_description"),
                st.session_state.get("compensation"),
            )
            st.subheader("Requirement match")
            render_requirements(result["requirements"])
        with right:
            st.subheader("Next step")
            st.write(
                "Generate a version that emphasizes the strongest truthful evidence in your CV."
            )

            # Cover letter
            signer_name = get_full_name(user_id)
            if not signer_name:
                st.caption("Add your name in the Profile page to sign the cover letter.")
            if st.button("Create Cover Letter"):
                st.session_state.show_cover_letter_form = True
            if st.session_state.show_cover_letter_form:
                st.session_state.cover_letter_instructions = st.text_area(
                    "What should the cover letter include or avoid?",
                    value=st.session_state.cover_letter_instructions,
                    height=140,
                    placeholder="e.g. emphasize my transition from academia to industry; avoid mentioning relocation.",
                    help="Combined with the job description, your CV, and CL_guidelines.md.",
                )
                if st.button("Generate Cover Letter", type="secondary"):
                    try:
                        with st.spinner("Generating cover letter..."):
                            st.session_state.cover_letter = generate_cover_letter(
                                client,
                                provider,
                                model,
                                st.session_state.job_text,
                                st.session_state.cv_text,
                                st.session_state.cover_letter_instructions,
                                read_cover_letter_guidelines(),
                                signer_name=signer_name,
                            )
                    except Exception as exc:
                        st.error(f"Could not generate the cover letter: {exc}")

            if st.session_state.cover_letter:
                st.download_button(
                    "Download cover letter (.txt)",
                    data=st.session_state.cover_letter.encode("utf-8"),
                    file_name=f"{_letter_stem(st.session_state, signer_name)}_cover_letter.txt",
                    mime="text/plain",
                )
                with st.expander("Preview cover letter"):
                    st.text(st.session_state.cover_letter)

            # Tailored CV
            if st.button("Generate tailored CV version?"):
                try:
                    with st.spinner("Generating tailored LaTeX..."):
                        st.session_state.tailored_cv = generate_tailored_cv(
                            client,
                            provider,
                            model,
                            st.session_state.job_text,
                            st.session_state.cv_raw,
                            read_guidelines(),
                            source_kind=st.session_state.cv_type,
                        )
                except Exception as exc:
                    st.error(f"Could not generate the tailored CV: {exc}")

            if st.session_state.tailored_cv:
                filename = f"{Path(st.session_state.cv_name or 'tailored_cv').stem}_tailored.tex"
                st.download_button(
                    "Download tailored CV (.tex)",
                    data=st.session_state.tailored_cv.encode("utf-8"),
                    file_name=filename,
                    mime="application/x-tex",
                )
                with st.expander("Preview generated LaTeX"):
                    st.code(st.session_state.tailored_cv, language="latex")


def _get_current_api_key(provider: str) -> str:
    """Helper — read the current API key from credentials for the given provider."""
    credentials = load_credentials()
    name = {
        "OpenAI GPT": "openai_api_key",
        "Codex": "codex_api_key",
        "Gemini 2.5 Flash": "gemini_api_key",
        "Groq": "groq_api_key",
    }.get(provider, "")
    return credentials.get(name, "").strip()


if __name__ == "__main__":
    main()
