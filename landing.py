"""Landing page for Job-CV AI Generator."""

from __future__ import annotations

import streamlit as st

from src.auth.auth import is_logged_in, logout
from src.auth.ui import render_auth_forms, render_confirmation, render_user_status_sidebar
from src.auth.subscription import is_subscribed, trial_remaining, get_subscription
from src.payments.checkout_ui import render_subscribe_section

st.set_page_config(
    page_title="Job-CV AI Generator",
    page_icon="📄",
    layout="wide",
    initial_sidebar_state="collapsed",
)


def handle_email_confirmation() -> None:
    """Legacy entry point for links sent before the dedicated /confirm route existed."""
    token = st.query_params.get("verify", "")
    if not token:
        return
    render_confirmation(token)
    with st.expander("Log in to continue", expanded=True):
        if render_auth_forms(rerun_on_success=False, form_prefix="legacy_confirm_"):
            st.switch_page("pages/app.py")


def render_account_menu() -> None:
    """The three-dots menu in the top right: log in, sign up, log out."""
    with st.popover("⋮", help="Account", use_container_width=False, width="content"):
        if not is_logged_in():
            st.markdown("**Log in or create an account**")
            if render_auth_forms(rerun_on_success=False):
                st.switch_page("pages/app.py")
            return

        user_id = st.session_state["auth_user_id"]
        sub = get_subscription(user_id)
        plan = sub["plan"] if sub else "free"
        st.markdown(f"**{st.session_state.get('auth_email', '')}**")
        if is_subscribed(user_id):
            st.caption(f"Plan: {'Basic' if plan == 'basic' else 'BYO Key'}")
        else:
            st.caption(f"Free trial: {trial_remaining(user_id)} analysis left")
        if st.button("Open the App", type="primary", key="menu_open_app"):
            st.switch_page("pages/app.py")
        if st.button("Log Out", key="menu_logout"):
            logout()
            st.rerun()


# ── Styles ────────────────────────────────────────────────────────────────────
st.markdown(
    """
    <style>
    .hero { text-align: center; padding: 3rem 1rem 2rem; }
    .hero h1 { font-size: 3rem; font-weight: 800; margin-bottom: 0.5rem; }
    .hero p  { font-size: 1.2rem; color: #555; max-width: 620px; margin: 0 auto 1.5rem; }
    .badge   { display:inline-block; background:#6366f1; color:#fff;
               border-radius:999px; padding:0.35rem 1rem; font-size:0.85rem;
               margin-bottom:1rem; font-weight:600; }
    .feature-card {
        background: #f8fafc;
        border: 1px solid #e2e8f0;
        border-radius: 12px;
        padding: 1.4rem;
        height: 100%;
    }
    .feature-card h4 { margin-top: 0; }
    .price-box {
        border-radius: 12px;
        padding: 1.8rem;
        text-align: center;
        margin-bottom: 1rem;
    }
    .price-box.basic  { border: 2px solid #6366f1; }
    .price-box.own    { border: 2px solid #10b981; }
    .price-amount     { font-size: 2.5rem; font-weight: 800; }
    .price-period     { font-size: 1rem; font-weight: 400; color: #777; }
    div[data-testid="stElementContainer"]:has(div[data-testid="stPopover"]) {
        display: flex; justify-content: flex-end;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

# ── Account menu (three dots, top right) ──────────────────────────────────────
_, menu_column = st.columns([11, 1])
with menu_column:
    render_account_menu()

handle_email_confirmation()

st.markdown(
    """
    <div class="hero">
        <div class="badge">AI-powered job applications</div>
        <h1>Land the Job You Deserve</h1>
        <p>
            Upload your LaTeX CV, paste a job URL and let our AI analyse the match,
            tailor your CV, and write a compelling cover letter — in seconds.
        </p>
    </div>
    """,
    unsafe_allow_html=True,
)

# ── Features ─────────────────────────────────────────────────────────────────
st.markdown("### What you get")
f1, f2, f3 = st.columns(3)
with f1:
    st.markdown(
        """<div class="feature-card">
            <h4>Match Analysis</h4>
            <p>See exactly which job requirements you meet and which you're missing,
            highlighted clearly so you know where to focus.</p>
        </div>""",
        unsafe_allow_html=True,
    )
with f2:
    st.markdown(
        """<div class="feature-card">
            <h4>Tailored CV</h4>
            <p>Get a LaTeX-ready CV version that surfaces the strongest, most relevant
            truthful evidence for each role.</p>
        </div>""",
        unsafe_allow_html=True,
    )
with f3:
    st.markdown(
        """<div class="feature-card">
            <h4>Cover Letter</h4>
            <p>AI-generated cover letter guided by your instructions — professional,
            specific, and ready to send.</p>
        </div>""",
        unsafe_allow_html=True,
    )

st.markdown("<br>", unsafe_allow_html=True)

# ── Pricing ───────────────────────────────────────────────────────────────────
st.markdown("### Pricing")
pc_free, pc_own, pc_basic = st.columns([1, 1, 1])

with pc_free:
    st.markdown(
        """<div class="price-box">
            <div style="color:#94a3b8; font-weight:600; margin-bottom:0.3rem;">FREE TRIAL</div>
            <div class="price-amount">3</div>
            <div class="price-period">free analyses</div>
            <hr style="margin:1rem 0;">
            <ul style="text-align:left; font-size:0.9rem;">
                <li>Full match analysis</li>
                <li>CV tailoring</li>
                <li>Cover letter generation</li>
                <li>No credit card required</li>
            </ul>
        </div>""",
        unsafe_allow_html=True,
    )

with pc_own:
    st.markdown(
        """<div class="price-box own">
            <div style="color:#10b981; font-weight:600; margin-bottom:0.3rem;">BYO API KEY</div>
            <div class="price-amount" style="color:#10b981;">$1</div>
            <div class="price-period">/month</div>
            <hr style="margin:1rem 0;">
            <ul style="text-align:left; font-size:0.9rem;">
                <li>Unlimited analyses</li>
                <li>CV tailoring & cover letters</li>
                <li>Use your own API key</li>
                <li>Pay only for your own token usage</li>
            </ul>
        </div>""",
        unsafe_allow_html=True,
    )

with pc_basic:
    st.markdown(
        """<div class="price-box basic">
            <div style="color:#6366f1; font-weight:600; margin-bottom:0.3rem;">BASIC</div>
            <div class="price-amount" style="color:#6366f1;">$5</div>
            <div class="price-period">/month</div>
            <hr style="margin:1rem 0;">
            <ul style="text-align:left; font-size:0.9rem;">
                <li>Unlimited analyses</li>
                <li>CV tailoring & cover letters</li>
                <li>All AI providers supported</li>
                <li>We handle the API costs</li>
            </ul>
        </div>""",
        unsafe_allow_html=True,
    )

st.markdown("<br>", unsafe_allow_html=True)
st.markdown("---")

# ── Auth / Subscription / CTA ─────────────────────────────────────────────────
if is_logged_in():
    user_id = st.session_state["auth_user_id"]
    render_user_status_sidebar(user_id)

    subscribed = is_subscribed(user_id)
    remaining = trial_remaining(user_id)

    if subscribed or remaining > 0:
        sub = get_subscription(user_id)
        plan = sub["plan"] if sub else "free"
        if subscribed:
            label = "Basic" if plan == "basic" else "BYO Key"
            st.success(f"You're on the **{label}** plan. Everything is unlocked.")
        else:
            st.info(f"You have **{remaining} free trial** {'analysis' if remaining == 1 else 'analyses'} remaining.")

        if st.button("Open the App", type="primary", use_container_width=False, key="cta_open_app"):
            st.switch_page("pages/app.py")
    else:
        st.warning("Your free trial has been used. Subscribe to keep using the service.")
        render_subscribe_section(user_id)
else:
    st.markdown("### Get started — it's free")
    st.caption(
        "Three free analyses, no credit card required. "
        "Open the **⋮** menu in the top right corner to log in or create an account."
    )

# ── Footer ────────────────────────────────────────────────────────────────────
st.markdown("<br><br>", unsafe_allow_html=True)
st.markdown(
    "<div style='text-align:center; color:#94a3b8; font-size:0.8rem;'>"
    "Job-CV AI Generator &nbsp;|&nbsp; Payments processed securely via PayPal"
    "</div>",
    unsafe_allow_html=True,
)
