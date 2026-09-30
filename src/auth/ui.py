"""Streamlit UI components for authentication (login / signup forms)."""

from __future__ import annotations

import streamlit as st

from src.auth.auth import confirm_email, issue_verification, login, logout, signup, is_logged_in
from src.auth.subscription import get_subscription, trial_remaining, is_subscribed


def render_confirmation(token: str) -> bool:
    """Confirm an account from an emailed token and show the outcome. Returns success."""
    ok, msg = confirm_email(token)
    (st.success if ok else st.error)(msg)
    return ok


def render_auth_forms(rerun_on_success: bool = True, form_prefix: str = "") -> bool:
    """
    Render login/signup forms.
    Returns True if the user is now authenticated.

    Pass rerun_on_success=False to get control back after a successful login
    instead of the page rerunning (the landing page redirects to the app).
    form_prefix keeps the widget keys unique when a page renders the forms twice.
    """
    if is_logged_in():
        return True

    tab_login, tab_signup = st.tabs(["Log In", "Sign Up"])

    with tab_login:
        with st.form(f"{form_prefix}login_form"):
            email = st.text_input("Email", key=f"{form_prefix}login_email")
            password = st.text_input("Password", type="password", key=f"{form_prefix}login_pw")
            submitted = st.form_submit_button("Log In", type="primary")
        if submitted:
            ok, msg = login(email, password)
            if ok:
                if not rerun_on_success:
                    return True
                st.success(msg)
                st.rerun()
            else:
                st.error(msg)
        with st.expander("Did not get the confirmation email?"):
            with st.form(f"{form_prefix}resend_form"):
                resend_email = st.text_input("Email", key=f"{form_prefix}resend_email")
                resend = st.form_submit_button("Resend confirmation email")
            if resend:
                ok, msg = issue_verification(resend_email)
                if ok:
                    st.success(msg)
                else:
                    st.error(msg)

    with tab_signup:
        with st.form(f"{form_prefix}signup_form"):
            email_s = st.text_input("Email", key=f"{form_prefix}signup_email")
            password_s = st.text_input("Password (min 8 chars)", type="password", key=f"{form_prefix}signup_pw")
            submitted_s = st.form_submit_button("Create Account", type="primary")
        if submitted_s:
            ok, msg = signup(email_s, password_s)
            if ok:
                st.success(msg)
            else:
                st.error(msg)

    return False


def render_user_status_sidebar(user_id: int) -> None:
    """Show subscription status and logout button in the sidebar."""
    sub = get_subscription(user_id)
    plan = sub["plan"] if sub else "free"
    email = st.session_state.get("auth_email", "")

    st.sidebar.markdown("---")
    st.sidebar.markdown(f"**Logged in as:** {email}")

    if is_subscribed(user_id):
        label = "Basic ($5/mo)" if plan == "basic" else "BYO Key ($1/mo)"
        st.sidebar.success(f"Plan: {label}")
    else:
        remaining = trial_remaining(user_id)
        if remaining > 0:
            st.sidebar.info(f"Free trial: {remaining} analysis left")
        else:
            st.sidebar.warning("Free trial exhausted — subscribe to continue.")

    if st.sidebar.button("Log Out", key="sidebar_logout"):
        logout()
        st.rerun()
