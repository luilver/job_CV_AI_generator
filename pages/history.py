"""History view: every match analysis the user has run."""

from __future__ import annotations

import streamlit as st

from src.auth.auth import is_logged_in
from src.auth.ui import render_auth_forms, render_user_status_sidebar
from src.compensation.estimator import summarise_compensation
from src.history.store import clear_history, delete_match, get_match, list_matches

st.set_page_config(page_title="Match History", page_icon="🗂️", layout="wide")


def _label(record: dict) -> str:
    position = record["position_name"] or "Untitled position"
    company = f" — {record['company']}" if record["company"] else ""
    return f"{position}{company} · {record['score']}%"


def render_list(user_id: int) -> None:
    matches = list_matches(user_id)
    if not matches:
        st.info("No matches yet. Run an analysis in the app and it will be saved here.")
        if st.button("Open the App", type="primary", key="history_empty_open"):
            st.switch_page("pages/app.py")
        return

    st.caption(f"{len(matches)} saved {'match' if len(matches) == 1 else 'matches'} (newest first).")

    left, right = st.columns([3, 2])
    with left:
        selected_id = st.selectbox(
            "Select a match",
            [record["id"] for record in matches],
            format_func=lambda match_id: _label(next(r for r in matches if r["id"] == match_id)),
            key="history_select",
        )
        if st.button("Open in the App", key="history_open_app"):
            st.session_state["history_pending_id"] = selected_id
            st.switch_page("pages/app.py")
        if st.button("Delete this match", key="history_delete"):
            delete_match(user_id, selected_id)
            st.rerun()

    with right:
        record = next((r for r in matches if r["id"] == selected_id), None)
        if record:
            st.metric("Match", f"{record['score']}%")
            st.caption(
                f"{record['requirements_matched']} of {record['requirements_total']} requirements matched"
            )
            st.caption(f"Saved: {record['created_at']} UTC")
            if record["job_url"]:
                st.markdown(f"[Job posting]({record['job_url']})")
            summary = summarise_compensation(record["compensation"])
            if summary:
                st.caption(f"💰 {summary}")

    detail = get_match(user_id, selected_id)
    if detail:
        analysis = detail["analysis"]
        st.markdown("---")
        st.subheader(analysis.get("position_name") or "Position")
        company = analysis.get("institution_name")
        if company:
            st.caption(f"Institution: {company}")
        brief = analysis.get("brief_description")
        if brief:
            st.markdown(str(brief))
        st.subheader("Requirement match")
        for requirement in analysis.get("requirements", []):
            is_match = str(requirement.get("status", "")).lower() == "match"
            colour, background, label = (
                ("#15803d", "#f0fdf4", "MATCH")
                if is_match
                else ("#b91c1c", "#fef2f2", "MISSING")
            )
            st.markdown(
                f'<div style="background:{background}; border-left:5px solid {colour}; '
                f'padding:0.65rem 0.9rem; margin:0.45rem 0; border-radius:4px;">'
                f'<span style="color:{colour}; font-weight:700; margin-right:0.7rem;">{label}</span>'
                f'{requirement.get("text", "")}</div>',
                unsafe_allow_html=True,
            )
        with st.expander("Job description used for this match"):
            st.text(detail.get("job_text", "")[:8000])


def main() -> None:
    st.title("Match History")

    if not is_logged_in():
        st.info("Please log in or create a free account to see your match history.")
        if render_auth_forms():
            st.rerun()
        return

    user_id: int = st.session_state["auth_user_id"]
    render_user_status_sidebar(user_id)
    render_list(user_id)

    matches = list_matches(user_id)
    if matches:
        st.markdown("---")
        if st.checkbox("Yes, delete my entire history", key="history_confirm_clear"):
            if st.button("Delete all matches", key="history_clear"):
                removed = clear_history(user_id)
                st.session_state.pop("history_confirm_clear", None)
                st.success(f"Deleted {removed} saved {'match' if removed == 1 else 'matches'}.")
                st.rerun()


if __name__ == "__main__":
    main()
