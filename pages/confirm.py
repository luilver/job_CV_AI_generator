"""Public email-confirmation route.

Kept on its own path (/confirm) so it can be exempted from Cloudflare Access while
the rest of the app stays private: the emailed link is
{APP_BASE_URL}/confirm?verify=<token>.
"""

from __future__ import annotations

import streamlit as st

from src.auth.auth import is_logged_in
from src.auth.ui import render_auth_forms, render_confirmation

st.set_page_config(
    page_title="Confirm your email — Job-CV AI Generator",
    page_icon="✉️",
    initial_sidebar_state="collapsed",
)


def main() -> None:
    st.title("Confirm your email")

    token = st.query_params.get("verify", "")
    if token:
        render_confirmation(token)
    else:
        st.error(
            "This link is missing its confirmation code. Use the link from your email, "
            "or request a new one below."
        )

    st.markdown("---")
    if st.button("Back to the home page"):
        st.switch_page("landing.py")

    if not is_logged_in():
        with st.expander("Already confirmed? Log in", expanded=not token):
            if render_auth_forms(rerun_on_success=False, form_prefix="confirm_"):
                st.switch_page("pages/app.py")


if __name__ == "__main__":
    main()
