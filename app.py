"""Streamlit UI entrypoint for Job-CV Matcher & Tailor."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import requests
import streamlit as st

from src.utils.config_loader import load_credentials, save_credential, read_guidelines, read_cover_letter_guidelines
from src.utils.url_utils import normalise_url
from src.parsers.latex_parser import clean_latex
from src.parsers.html_parser import scrape_job
from src.ai.client_factory import make_client
from src.generators.analysis import analyse_match
from src.generators.cv_cl_generators import generate_tailored_cv, generate_cover_letter


def initialise_state() -> None:
    defaults = {
        "job_text": "",
        "job_url": "",
        "job_input_mode": "URL",
        "job_description_input": "",
        "cv_text": "",
        "cv_raw": "",
        "cv_name": "",
        "analysis": None,
        "tailored_cv": "",
        "cover_letter": "",
        "cover_letter_instructions": "",
        "show_cover_letter_form": False,
        "source_signature": "",
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
            unsafe_allow_html=True
        )
        
def render_job_details(
    position_name: str | None,
    institution_name: str | None,
    brief_description: str | None,
) -> None:
    """Render the extracted job summary before the skill comparison."""
    position = str(position_name or "Job position").strip()
    institution = str(institution_name or "").strip()
    description = str(brief_description or "").strip()

    st.subheader(position)
    if institution:
        st.caption(f"Institution: {institution}")
    if description:
        st.markdown(description)
    else:
        st.caption("No brief description was provided.")


def main() -> None:
    st.set_page_config(page_title="Job-CV Matcher & Tailor", layout="wide")
    initialise_state()
    credentials = load_credentials()
    st.title("Job-CV Matcher & Tailor")
    st.caption("Compare a LaTeX CV with a live job description, then generate a focused version.")
    with st.sidebar:
        st.header("Configuration")
        provider = st.selectbox("AI provider", ["OpenAI GPT", "Codex", "Gemini 2.5 Flash", "Groq"])
        credential_name = {
            "OpenAI GPT": "openai_api_key",
            "Codex": "codex_api_key",
            "Gemini 2.5 Flash": "gemini_api_key",
            "Groq": "groq_api_key",
        }[provider]
        api_key = st.text_input(
            f"{provider} API Key",
            value=credentials[credential_name],
            type="password",
            help="Saved locally to credentials.json when entered or when you click Save API Key.",
        )
        if api_key.strip() and api_key.strip() != credentials[credential_name]:
            save_credential(credential_name, api_key)
            credentials[credential_name] = api_key.strip()
        if st.button("Save API Key"):
            if api_key.strip():
                save_credential(credential_name, api_key)
                credentials[credential_name] = api_key.strip()
                st.success("API key saved locally.")
            else:
                st.warning("Enter an API key before saving.")
        if provider == "Codex":
            st.info("Codex API access is not free-tier supported. A ChatGPT/Codex subscription does not automatically provide an API key.")
            model = st.selectbox("Codex model", ["gpt-5-codex", "gpt-5.3-codex", "codex-mini-latest"], index=0)
        elif provider == "Gemini 2.5 Flash":
            model = st.selectbox(
                "Gemini model",
                ["gemini-3.6-flash", "gemini-2.5-flash"],
                index=0,
                help="Gemini 2.5 Flash is retained for existing projects, but Google may reject it for new users.",
            )
            if model == "gemini-2.5-flash":
                st.warning("Gemini 2.5 Flash is a legacy choice and may be unavailable to new users. Use Gemini 3.6 Flash if you receive a 404.")
        elif provider == "Groq":
            model = st.selectbox("Groq model", ["llama-3.3-70b-versatile", "openai/gpt-oss-120b", "openai/gpt-oss-20b"], index=0)
        else:
            model = st.selectbox("GPT model", ["gpt-4o", "gpt-4o-mini", "gpt-4-turbo"], index=0)
        uploaded = st.file_uploader("Upload CV (.tex)", type=["tex"])
        if uploaded:
            raw = uploaded.getvalue().decode("utf-8", errors="replace")
            signature = hashlib.sha256(raw.encode("utf-8")).hexdigest()
            if signature != st.session_state.source_signature:
                st.session_state.update(
                    cv_raw=raw,
                    cv_text=clean_latex(raw),
                    cv_name=uploaded.name,
                    source_signature=signature,
                    analysis=None,
                    tailored_cv="",
                    cover_letter="",
                    cover_letter_instructions="",
                    show_cover_letter_form=False,
                )
            st.success(f"Loaded {uploaded.name}")
    job_input_mode = st.radio(
        "Job description source",
        ["URL", "Paste text"],
        key="job_input_mode",
        horizontal=True,
        help="Choose whether to fetch the description from a web page or use text you already have.",
    )
    if job_input_mode == "URL":
        url = st.text_input("Job URL", value=st.session_state.job_url, placeholder="https://example.com/job")
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
        if not api_key:
            st.error("Enter an API key for the selected provider in the sidebar.")
        elif not st.session_state.cv_text:
            st.error("Upload a .tex CV in the sidebar first.")
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
                    result = analyse_match(make_client(provider, api_key), provider, model, job_text, st.session_state.cv_text)
                st.session_state.update(
                    job_text=job_text,
                    job_url=clean_url,
                    analysis=result,
                    tailored_cv="",
                    cover_letter="",
                    show_cover_letter_form=False,
                )
                st.success("Analysis complete.")
            except requests.RequestException as exc:
                if "generativelanguage.googleapis.com" in str(exc):
                    st.error(f"Gemini API request failed: {exc}")
                else:
                    st.error(f"Could not fetch the job page: {exc}")
            except Exception as exc:
                st.error(f"Analysis failed: {exc}")
    result = st.session_state.analysis
    if result:
        left, right = st.columns([1.35, 1])
        with left:
            render_job_details(result.get("position_name"), result.get("institution_name"), result.get("brief_description"))
            st.subheader("Requirement match")
            render_requirements(result["requirements"])
        with right:
            st.subheader("Next step")
            st.write("Generate a version that emphasizes the strongest truthful evidence in your CV.")
            if st.button("Create Cover Letter"):
                st.session_state.show_cover_letter_form = True
            if st.session_state.show_cover_letter_form:
                st.session_state.cover_letter_instructions = st.text_area(
                    "What should the cover letter include or avoid?",
                    value=st.session_state.cover_letter_instructions,
                    height=140,
                    placeholder="For example: emphasize my transition from academia to applied work; avoid mentioning relocation.",
                    help="These instructions are combined with the job description, your CV, and CL_guidelines.md.",
                )
                if st.button("Generate Cover Letter", type="secondary"):
                    if not api_key:
                        st.error("Enter an API key for the selected provider in the sidebar.")
                    else:
                        try:
                            with st.spinner("Generating cover letter..."):
                                st.session_state.cover_letter = generate_cover_letter(
                                    make_client(provider, api_key),
                                    provider,
                                    model,
                                    st.session_state.job_text,
                                    st.session_state.cv_text,
                                    st.session_state.cover_letter_instructions,
                                    read_cover_letter_guidelines(),
                                )
                        except Exception as exc:
                            st.error(f"Could not generate the cover letter: {exc}")
            if st.session_state.cover_letter:
                st.download_button(
                    "Download cover letter (.txt)",
                    data=st.session_state.cover_letter.encode("utf-8"),
                    file_name=f"{Path(st.session_state.cv_name or 'cover_letter').stem}_cover_letter.txt",
                    mime="text/plain",
                )
                with st.expander("Preview cover letter"):
                    st.text(st.session_state.cover_letter)
            if st.button("Generate tailored CV version?"):
                if not api_key:
                    st.error("Enter an API key for the selected provider in the sidebar.")
                else:
                    try:
                        with st.spinner("Generating tailored LaTeX..."):
                            st.session_state.tailored_cv = generate_tailored_cv(make_client(provider, api_key), provider, model, st.session_state.job_text, st.session_state.cv_raw, read_guidelines())
                    except Exception as exc:
                        st.error(f"Could not generate the tailored CV: {exc}")
            if st.session_state.tailored_cv:
                filename = f"{Path(st.session_state.cv_name or 'tailored_cv').stem}_tailored.tex"
                st.download_button("Download tailored CV (.tex)", data=st.session_state.tailored_cv.encode("utf-8"), file_name=filename, mime="application/x-tex")
                with st.expander("Preview generated LaTeX"):
                    st.code(st.session_state.tailored_cv, language="latex")


if __name__ == "__main__":
    main()
