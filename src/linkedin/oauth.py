"""Link a user's LinkedIn identity to their account (Sign In with LinkedIn).

This is deliberately identity-only. LinkedIn's consumer OpenID Connect product
grants exactly three scopes -- openid, profile, email -- so there is nothing here
that reads the member's network, feed, or messages, and nothing here can search
or apply to jobs. The access token is discarded the moment the userinfo response
has been read; only the stable member id and a name/email snapshot are kept.

Endpoints come from LinkedIn's OpenID discovery document:
    authorization  https://www.linkedin.com/oauth/v2/authorization
    token          https://www.linkedin.com/oauth/v2/accessToken
    userinfo       https://api.linkedin.com/v2/userinfo
"""

from __future__ import annotations

import secrets
from urllib.parse import urlencode

import requests

from src.linkedin.store import consume_oauth_state, save_linkedin_account, store_oauth_state
from src.utils.config_loader import (
    LINKEDIN_AUTHORIZE_URL,
    LINKEDIN_SCOPES,
    LINKEDIN_TOKEN_URL,
    LINKEDIN_USERINFO_URL,
    is_linkedin_configured,
    linkedin_client_id,
    linkedin_client_secret,
    linkedin_redirect_uri,
    linkedin_setup_hint,
)

REQUEST_TIMEOUT = 25
STATE_BYTES = 24


def _require_configuration() -> None:
    if not is_linkedin_configured():
        raise ValueError(linkedin_setup_hint())


def authorization_url(user_id: int) -> str:
    """Create a one-time state and return the LinkedIn URL to send the browser to."""
    _require_configuration()
    state = secrets.token_urlsafe(STATE_BYTES)
    store_oauth_state(user_id, state)
    query = urlencode(
        {
            "response_type": "code",
            "client_id": linkedin_client_id(),
            "redirect_uri": linkedin_redirect_uri(),
            "state": state,
            "scope": LINKEDIN_SCOPES,
        }
    )
    return f"{LINKEDIN_AUTHORIZE_URL}?{query}"


def connect(user_id: int, code: str, state: str) -> dict[str, str]:
    """Finish the connect flow: validate state, exchange the code, read the profile.

    Returns the saved identity. Raises ValueError with a message meant for the
    user on anything that goes wrong.
    """
    _require_configuration()

    owner = consume_oauth_state(state)
    if owner is None:
        raise ValueError(
            "That LinkedIn link has expired or was already used. Start the connection again from the Jobs page."
        )
    if int(owner) != int(user_id):
        # A valid state belonging to somebody else's session is an attack, not a glitch.
        raise ValueError("That LinkedIn connection attempt does not belong to this account.")

    code = str(code or "").strip()
    if not code:
        raise ValueError("LinkedIn did not return an authorization code.")

    try:
        token_response = requests.post(
            LINKEDIN_TOKEN_URL,
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": linkedin_redirect_uri(),
                "client_id": linkedin_client_id(),
                "client_secret": linkedin_client_secret(),
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=REQUEST_TIMEOUT,
        )
    except requests.RequestException as exc:
        raise ValueError(f"Could not reach LinkedIn to finish the connection: {exc}") from exc

    if token_response.status_code != 200:
        raise ValueError(_token_error(token_response))

    payload = _json_object(token_response)
    access_token = str(payload.get("access_token") or "").strip()
    if not access_token:
        raise ValueError("LinkedIn did not return an access token.")

    try:
        profile_response = requests.get(
            LINKEDIN_USERINFO_URL,
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=REQUEST_TIMEOUT,
        )
    except requests.RequestException as exc:
        raise ValueError(f"Could not read the LinkedIn profile: {exc}") from exc
    finally:
        # The token has done its only job; it is never stored on disk.
        access_token = ""

    if profile_response.status_code != 200:
        raise ValueError("LinkedIn accepted the login but would not return the profile.")

    profile = _json_object(profile_response)
    member_id = str(profile.get("sub") or "").strip()
    if not member_id:
        raise ValueError("LinkedIn did not return a member id, so the account could not be identified.")

    return save_linkedin_account(
        user_id,
        member_id,
        name=str(profile.get("name") or "").strip(),
        email=str(profile.get("email") or "").strip(),
        picture=str(profile.get("picture") or "").strip(),
        scopes=LINKEDIN_SCOPES,
    )


def _json_object(response: requests.Response) -> dict:
    try:
        payload = response.json()
    except ValueError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _token_error(response: requests.Response) -> str:
    """Turn LinkedIn's OAuth error body into something the user can act on."""
    detail = str(_json_object(response).get("error_description") or "").strip()
    if not detail:
        detail = f"LinkedIn returned HTTP {response.status_code}."
    if response.status_code == 400 and "redirect" in detail.lower():
        return (
            f"{detail} Register this exact URL on your LinkedIn app: {linkedin_redirect_uri()}"
        )
    if "redirect_uri_mismatch" in detail:
        return (
            "LinkedIn rejected the redirect URL. On your LinkedIn app, set the redirect URL to "
            f"{linkedin_redirect_uri()} and try again."
        )
    return f"LinkedIn refused the connection — {detail}"
