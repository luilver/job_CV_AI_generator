"""Profile view: account details and the stored CV (.tex or .pdf)."""

from __future__ import annotations

import streamlit as st

from src.auth.auth import MAX_NAME_LENGTH, get_full_name, is_logged_in, set_full_name
from src.auth.db import init_db
from src.auth.subscription import get_subscription, is_subscribed, trial_remaining
from src.auth.ui import render_auth_forms, render_user_status_sidebar
from src.payments.checkout_ui import render_subscribe_section
from src.profile.cv_store import (
    ALLOWED_TYPES,
    MAX_CV_BYTES,
    MIME_TYPES,
    delete_cv,
    describe_cv,
    get_cv,
    save_cv,
)

st.set_page_config(page_title="Profile", page_icon="👤", layout="wide")
init_db()

PREVIEW_CHARS = 4000


def render_account(user_id: int) -> None:
    st.subheader("Account")
    sub = get_subscription(user_id)
    plan = sub["plan"] if sub else "free"
    subscribed = is_subscribed(user_id)
    remaining = trial_remaining(user_id)

    col_email, col_plan, col_trial = st.columns(3)
    col_email.metric("Email", st.session_state.get("auth_email", "—"))
    if subscribed:
        col_plan.metric("Plan", "Basic" if plan == "basic" else "BYO Key")
        col_trial.metric("Analyses", "Unlimited")
    else:
        col_plan.metric("Plan", "Free trial")
        col_trial.metric("Analyses left", remaining)

    name_column, name_action = st.columns([4, 1])
    # The key carries a version counter so a save re-initialises the box with the
    # normalised value (a widget's own state cannot be overwritten once created).
    version = st.session_state.get("profile_name_version", 0)
    name_column.text_input(
        "Your name",
        value=get_full_name(user_id),
        key=f"profile_name_{version}",
        placeholder="e.g. Jane Doe",
        max_chars=MAX_NAME_LENGTH,
        help="Used to sign the cover letters this app generates. Leave empty to keep letters unsigned.",
    )
    if name_action.button("Save name", key="profile_name_save"):
        saved = set_full_name(user_id, st.session_state.get(f"profile_name_{version}", ""))
        st.session_state["profile_name_version"] = version + 1
        flash(
            f"Cover letters will be signed as **{saved}**." if saved else "Name cleared — cover letters will be left unsigned."
        )
        st.rerun()

    if not subscribed and remaining == 0:
        st.warning("Your free trial is exhausted. Subscribe to keep using the service.")
        render_subscribe_section(user_id)


def render_cv(user_id: int) -> None:
    st.subheader("Your CV")
    st.caption("One CV is stored on your profile and used for every analysis. Both LaTeX (.tex) and PDF are supported.")

    record = get_cv(user_id)
    if record:
        col_info, col_actions = st.columns([3, 2])
        col_info.metric("Saved file", record["filename"])
        col_info.caption(
            f"Type: {record['filetype'].upper()} · Size: {record['size_bytes'] / 1024:.0f} KB · "
            f"Last updated: {record['updated_at']} UTC"
        )
        col_actions.download_button(
            "Download",
            data=record["data"],
            file_name=record["filename"],
            mime=MIME_TYPES.get(record["filetype"], "application/octet-stream"),
            key="profile_cv_download",
        )
        with st.expander("Preview extracted text"):
            st.text(record["text"][:PREVIEW_CHARS])

    uploaded = st.file_uploader(
        "Replace CV" if record else "Upload CV",
        type=list(ALLOWED_TYPES),
        key="profile_cv_upload",
        help=f"Maximum {MAX_CV_BYTES // (1024 * 1024)} MB. PDFs must contain selectable text, not a scan.",
    )
    if uploaded is not None and st.button("Save CV", type="primary", key="profile_cv_save"):
        try:
            with st.spinner("Saving your CV…"):
                save_cv(user_id, uploaded.name, uploaded.getvalue())
            flash("CV saved. It will be used for every analysis.")
            st.rerun()
        except ValueError as exc:
            st.error(str(exc))
        except Exception as exc:
            st.error(f"Could not save the CV: {exc}")

    if record:
        st.checkbox("Yes, delete my stored CV", key="profile_cv_confirm")
        if st.button("Delete CV", key="profile_cv_delete"):
            if st.session_state.get("profile_cv_confirm"):
                delete_cv(user_id)
                st.session_state.pop("profile_cv_confirm", None)
                flash("CV deleted.")
                st.rerun()
            else:
                st.warning("Tick the confirmation box first.")

    if st.button("Open the App", key="profile_open_app"):
        st.switch_page("pages/app.py")


def flash(message: str, level: str = "success") -> None:
    """Queue a message to show after the rerun that follows an action."""
    st.session_state["profile_flash"] = (level, message)


def render_flash() -> None:
    pending = st.session_state.pop("profile_flash", None)
    if not pending:
        return
    level, message = pending
    getattr(st, level)(message)


def main() -> None:
    st.title("Profile")
    render_flash()

    if not is_logged_in():
        st.info("Please log in or create a free account to see your profile.")
        if render_auth_forms():
            st.rerun()
        return

    user_id: int = st.session_state["auth_user_id"]
    render_user_status_sidebar(user_id)
    render_account(user_id)
    st.markdown("---")
    render_cv(user_id)


if __name__ == "__main__":
    main()
