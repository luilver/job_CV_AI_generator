"""Send transactional email through the Google Workspace SMTP relay."""

from __future__ import annotations

import smtplib
from email.message import EmailMessage

from src.utils.config_loader import confirm_base_url, load_smtp_config, smtp_port, smtp_use_starttls

TOKEN_TTL_HOURS = 24

GOOGLE_HOSTS = ("smtp.gmail.com", "smtp.googlemail.com")

GOOGLE_AUTH_HINT = (
    "SMTP needs a 16-character App Password, not the account password: Google Account -> "
    "Security -> 2-Step Verification -> App passwords -> generate one for Mail. App passwords are "
    "an admin-controlled feature: if the page says the setting is not available, the domain is on "
    "the free Cloud Identity tier or the Workspace admin has disabled app passwords."
)
RELAY_AUTH_HINT = (
    "Check the relay credentials, and make sure {sender} is a verified domain/sender on the "
    "relay account (SPF and DKIM records) or the provider will reject the message."
)
NO_PASSWORD_HINT = (
    "Set SMTP_APP_PASSWORD in .streamlit/secrets.toml or credentials.json "
    "(for Google: an App Password; for a relay: the provider's SMTP key)."
)


class EmailError(RuntimeError):
    """Raised when a message could not be handed to the SMTP server."""


def _server_detail(exc: BaseException) -> str:
    """Google puts the useful part in the SMTP payload; keep it readable."""
    detail = str(exc).strip()
    if exc.args and isinstance(exc.args[-1], bytes):
        detail = f"{detail} {exc.args[-1].decode('utf-8', 'replace')}".strip()
    return " ".join(detail.split())


def is_configured() -> bool:
    return bool(load_smtp_config().get("SMTP_APP_PASSWORD"))


def confirmation_url(token: str) -> str:
    # Defaults to the app's /confirm route. When APP_CONFIRM_BASE_URL is set the link
    # goes to that hostname instead, so it can be reachable without a Cloudflare login.
    return f"{confirm_base_url()}?verify={token}"


def _auth_headers(config: dict[str, str]) -> tuple[str, str, int, bool]:
    return (
        config.get("SMTP_HOST") or "smtp.gmail.com",
        config.get("SMTP_USER") or "jobcv@luilver.com",
        smtp_port(),
        smtp_use_starttls(),
    )


def _is_google(host: str) -> bool:
    return host.strip().lower() in GOOGLE_HOSTS


def auth_hint(host: str, sender: str, detail: str = "") -> str:
    """The reason a login failed, phrased for the host that refused it."""
    body = GOOGLE_AUTH_HINT if _is_google(host) else RELAY_AUTH_HINT.format(sender=sender)
    if detail:
        return f"{detail}. {body}"
    return body


def send_mail(to_email: str, subject: str, body_text: str, body_html: str = "") -> None:
    """Send one message. Raises EmailError with a readable reason on failure."""
    config = load_smtp_config()
    password = config.get("SMTP_APP_PASSWORD", "")
    if not password:
        raise EmailError(f"Email is not configured. {NO_PASSWORD_HINT}")

    host, user, port, use_starttls = _auth_headers(config)
    sender = config.get("SMTP_FROM") or user
    sender_name = config.get("SMTP_FROM_NAME") or "Job-CV AI Generator"

    message = EmailMessage()
    message["From"] = f"{sender_name} <{sender}>"
    message["To"] = to_email
    message["Subject"] = subject
    message.set_content(body_text)
    if body_html:
        message.add_alternative(body_html, subtype="html")

    try:
        if use_starttls:
            with smtplib.SMTP(host, port, timeout=30) as server:
                server.ehlo()
                server.starttls()
                server.ehlo()
                if user:
                    server.login(user, password)
                server.send_message(message)
        else:
            with smtplib.SMTP_SSL(host, port, timeout=30) as server:
                server.ehlo()
                if user:
                    server.login(user, password)
                server.send_message(message)
    except smtplib.SMTPAuthenticationError as exc:
        raise EmailError(auth_hint(host, sender, _server_detail(exc))) from exc
    except smtplib.SMTPResponseException as exc:
        raise EmailError(f"{host} refused the message: {auth_hint(host, sender, _server_detail(exc))}") from exc
    except smtplib.SMTPException as exc:
        raise EmailError(f"{host} refused the message: {_server_detail(exc)}") from exc
    except OSError as exc:
        raise EmailError(f"Could not reach {host}:{port} — {exc}") from exc


def send_verification_email(to_email: str, name: str = "", token: str = "") -> str:
    """Email a fresh confirmation link. Returns the link that was sent."""
    link = confirmation_url(token)
    greeting = f"Hi {name.split()[0]}," if name.strip() else "Hi,"
    subject = "Confirm your email — Job-CV AI Generator"

    text = f"""{greeting}

Welcome to Job-CV AI Generator. Please confirm your email address to finish creating your account:

{link}

The link is valid for {TOKEN_TTL_HOURS} hours. If it expires, request a new one from the login screen.

If you did not create this account, you can ignore this email.

— The Job-CV AI Generator team"""

    html = f"""<p>{greeting}</p>
<p>Welcome to Job-CV AI Generator. Please confirm your email address to finish creating your
account:</p>
<p><a href="{link}">Confirm my email address</a></p>
<p style="color:#666;font-size:0.85rem;">The link is valid for {TOKEN_TTL_HOURS} hours. If it expires,
request a new one from the login screen.</p>
<p style="color:#666;font-size:0.85rem;">If you did not create this account, you can ignore this email.</p>
<p>— The Job-CV AI Generator team</p>"""

    send_mail(to_email, subject, text, html)
    return link
