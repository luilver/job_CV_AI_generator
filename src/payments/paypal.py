"""PayPal REST API integration for one-time orders and subscriptions."""

from __future__ import annotations

import os
import requests
import streamlit as st

PAYPAL_API_BASE = os.environ.get("PAYPAL_API_BASE", "https://api-m.sandbox.paypal.com")

PLAN_PRICES = {
    "basic": {"amount": "5.00", "currency": "USD", "label": "$5 / month"},
    "own_key": {"amount": "1.00", "currency": "USD", "label": "$1 / month (BYO API Key)"},
}


def _get_access_token() -> str:
    client_id = _paypal_client_id()
    client_secret = _paypal_client_secret()
    resp = requests.post(
        f"{PAYPAL_API_BASE}/v1/oauth2/token",
        auth=(client_id, client_secret),
        data={"grant_type": "client_credentials"},
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()["access_token"]


def _paypal_client_id() -> str:
    try:
        return st.secrets.get("PAYPAL_CLIENT_ID", "") or os.environ.get("PAYPAL_CLIENT_ID", "")
    except Exception:
        return os.environ.get("PAYPAL_CLIENT_ID", "")


def _paypal_client_secret() -> str:
    try:
        return st.secrets.get("PAYPAL_CLIENT_SECRET", "") or os.environ.get("PAYPAL_CLIENT_SECRET", "")
    except Exception:
        return os.environ.get("PAYPAL_CLIENT_SECRET", "")


def create_order(plan: str, return_url: str, cancel_url: str) -> dict:
    """Create a PayPal order and return the full response dict."""
    price = PLAN_PRICES[plan]
    token = _get_access_token()
    payload = {
        "intent": "CAPTURE",
        "purchase_units": [
            {
                "amount": {
                    "currency_code": price["currency"],
                    "value": price["amount"],
                },
                "description": f"Job-CV AI Generator – {price['label']}",
            }
        ],
        "application_context": {
            "return_url": return_url,
            "cancel_url": cancel_url,
            "brand_name": "Job-CV AI Generator",
            "user_action": "PAY_NOW",
        },
    }
    resp = requests.post(
        f"{PAYPAL_API_BASE}/v2/checkout/orders",
        json=payload,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()


def capture_order(order_id: str) -> dict:
    """Capture a previously approved PayPal order."""
    token = _get_access_token()
    resp = requests.post(
        f"{PAYPAL_API_BASE}/v2/checkout/orders/{order_id}/capture",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()


def get_approval_url(order: dict) -> str | None:
    for link in order.get("links", []):
        if link.get("rel") == "approve":
            return link["href"]
    return None


def record_order(user_id: int, order_id: str, plan: str) -> None:
    from src.auth.db import get_connection

    conn = get_connection()
    try:
        conn.execute(
            "INSERT OR IGNORE INTO paypal_orders (user_id, order_id, plan, status) VALUES (?, ?, ?, 'CREATED')",
            (user_id, order_id, plan),
        )
        conn.commit()
    finally:
        conn.close()


def update_order_status(order_id: str, status: str) -> None:
    from src.auth.db import get_connection

    conn = get_connection()
    try:
        conn.execute(
            "UPDATE paypal_orders SET status = ? WHERE order_id = ?",
            (status, order_id),
        )
        conn.commit()
    finally:
        conn.close()
