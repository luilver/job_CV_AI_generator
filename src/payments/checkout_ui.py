"""Streamlit PayPal checkout UI components."""

from __future__ import annotations

import streamlit as st

from src.payments.paypal import (
    PLAN_PRICES,
    capture_order,
    create_order,
    get_approval_url,
    record_order,
    update_order_status,
)
from src.auth.subscription import activate_subscription, set_own_api_key_flag


def _app_base_url() -> str:
    try:
        base = st.secrets.get("APP_BASE_URL", "") or ""
    except Exception:
        base = ""
    return base.rstrip("/") or "http://localhost:8501"


def render_subscribe_section(user_id: int) -> None:
    """
    Render the subscription plans and handle PayPal order creation / capture.
    Called from the main app when the user is logged in but not subscribed and
    has no trial uses remaining.
    """
    st.subheader("Choose a plan to continue")

    col1, col2 = st.columns(2)

    with col1:
        st.markdown(
            """
            <div style="border:2px solid #6366f1; border-radius:12px; padding:1.5rem; text-align:center;">
                <h3 style="color:#6366f1; margin:0;">Basic</h3>
                <p style="font-size:2rem; font-weight:700; margin:0.5rem 0;">$5<span style="font-size:1rem; font-weight:400;">/mo</span></p>
                <p style="color:#555; font-size:0.9rem;">We provide the AI. You bring your CV.</p>
                <ul style="text-align:left; font-size:0.9rem; padding-left:1.2rem;">
                    <li>Unlimited analyses</li>
                    <li>CV tailoring & cover letters</li>
                    <li>All AI providers supported</li>
                </ul>
            </div>
            """,
            unsafe_allow_html=True,
        )
        if st.button("Subscribe – $5/mo", key="sub_basic", type="primary", use_container_width=True):
            _initiate_paypal_checkout(user_id, "basic")

    with col2:
        st.markdown(
            """
            <div style="border:2px solid #10b981; border-radius:12px; padding:1.5rem; text-align:center;">
                <h3 style="color:#10b981; margin:0;">BYO API Key</h3>
                <p style="font-size:2rem; font-weight:700; margin:0.5rem 0;">$1<span style="font-size:1rem; font-weight:400;">/mo</span></p>
                <p style="color:#555; font-size:0.9rem;">Use your own API key on any provider.</p>
                <ul style="text-align:left; font-size:0.9rem; padding-left:1.2rem;">
                    <li>Unlimited analyses</li>
                    <li>CV tailoring & cover letters</li>
                    <li>Your API key, your costs</li>
                </ul>
            </div>
            """,
            unsafe_allow_html=True,
        )
        if st.button("Subscribe – $1/mo", key="sub_own_key", type="primary", use_container_width=True):
            _initiate_paypal_checkout(user_id, "own_key")

    # --- handle PayPal return (order capture) ---
    params = st.query_params
    if "token" in params and "pending_plan" in st.session_state:
        order_id = params["token"]
        plan = st.session_state.pop("pending_plan")
        with st.spinner("Confirming payment with PayPal…"):
            try:
                result = capture_order(order_id)
                status = result.get("status", "")
                if status == "COMPLETED":
                    update_order_status(order_id, "COMPLETED")
                    activate_subscription(user_id, plan, paypal_sub_id=order_id)
                    if plan == "own_key":
                        set_own_api_key_flag(user_id, True)
                    st.success("Payment confirmed. Your subscription is now active!")
                    st.query_params.clear()
                    st.rerun()
                else:
                    st.error(f"Payment not completed (status: {status}). Please try again.")
            except Exception as exc:
                st.error(f"Payment capture failed: {exc}")

    if "paymentCancelled" in params:
        st.warning("Payment cancelled. You can try again whenever you're ready.")
        st.query_params.clear()


def _initiate_paypal_checkout(user_id: int, plan: str) -> None:
    base = _app_base_url()
    return_url = f"{base}/?plan={plan}"
    cancel_url = f"{base}/?paymentCancelled=1"
    try:
        with st.spinner("Creating PayPal order…"):
            order = create_order(plan, return_url, cancel_url)
        record_order(user_id, order["id"], plan)
        approval_url = get_approval_url(order)
        if approval_url:
            st.session_state["pending_plan"] = plan
            st.markdown(
                f'<meta http-equiv="refresh" content="0; url={approval_url}">',
                unsafe_allow_html=True,
            )
            st.markdown(
                f"[Click here if not redirected automatically]({approval_url})",
                unsafe_allow_html=True,
            )
        else:
            st.error("Could not obtain a PayPal approval URL. Check your PayPal credentials.")
    except Exception as exc:
        st.error(f"PayPal error: {exc}")
