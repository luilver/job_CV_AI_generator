"""OAuth redirect target for Sign In with LinkedIn.

LinkedIn sends the browser here with ?code=...&state=... (or ?error=...).
Streamlit multipage apps do not route query params themselves, so this page is
the whole handler: it exchanges the code, links the identity, and sends the user
on to the jobs page.

Two things make this page different from every other page:

* A redirect is a full browser navigation, so Streamlit starts a *new session*
  here. ``st.session_state["auth_user_id"]`` is gone even when the user was
  logged in a moment ago. Requiring a login would swallow the ``code`` while the
  user types it in. The OAuth ``state`` is the proof of identity instead: it is
  single-use, short-lived, and stored against exactly one user id.
* Clearing the query params triggers a rerun. The outcome is therefore kept in
  session_state so the rerun does not replay (or lose) the exchange.
"""

from __future__ import annotations

import streamlit as st

from src.auth.db import init_db
from src.linkedin.oauth import connect
from src.linkedin.store import get_linkedin_account, peek_oauth_state

st.set_page_config(page_title="LinkedIn", page_icon="🔗", layout="centered")
init_db()

_PARAM_KEYS = ("code", "state", "error", "error_description")
_RESULT_KEY = "linkedin_callback_result"


def _clear_params() -> None:
    """Drop the one-time values from the address bar now that they are handled."""
    for key in _PARAM_KEYS:
        if key in st.query_params:
            del st.query_params[key]


def _show(result: dict) -> None:
    st.title("LinkedIn")
    user_id = result.get("connected_user_id")
    if user_id:
        account = get_linkedin_account(int(user_id)) or {}
        who = account.get("member_name") or account.get("linkedin_member_id") or "your account"
        st.success(
            f"Connected as **{who}**. Nothing beyond your name, email and member id is stored."
        )
        if st.button("Go to Job Discovery", type="primary"):
            st.switch_page("pages/jobs.py")
        return
    st.error(result.get("error") or "The LinkedIn connection did not complete.")
    st.caption("You can try again from the Job Discovery page.")


result = st.session_state.get(_RESULT_KEY)

if result is None:
    error = str(st.query_params.get("error", "") or "")
    error_description = str(st.query_params.get("error_description", "") or "")
    code = str(st.query_params.get("code", "") or "")
    state = str(st.query_params.get("state", "") or "")

    if error:
        detail = f" ({error_description})" if error_description else ""
        result = {"error": f"LinkedIn refused the request: {error}{detail}"}
    elif not code:
        # A manual visit or a stale tab: nothing to finish.
        st.title("LinkedIn")
        st.info("Waiting for LinkedIn…")
        st.caption("Start the connection from the Job Discovery page.")
        st.stop()
    else:
        owner = peek_oauth_state(state)
        session_user = st.session_state.get("auth_user_id")
        if owner is None:
            result = {
                "error": "That LinkedIn link has expired or was already used. "
                "Start the connection again from the Job Discovery page."
            }
        elif session_user and int(session_user) != int(owner):
            result = {"error": "That LinkedIn connection attempt belongs to a different account."}
        else:
            try:
                with st.spinner("Finishing the LinkedIn connection…"):
                    connect(owner, code, state)
            except Exception as exc:  # noqa: BLE001 - surfaced to the user below
                result = {"error": f"The LinkedIn connection failed: {exc}"}
            else:
                result = {"connected_user_id": int(owner)}

    st.session_state[_RESULT_KEY] = result
    _clear_params()

_show(result)
