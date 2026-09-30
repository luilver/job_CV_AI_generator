"""Check the Google Workspace SMTP credentials without starting the app.

Usage:
    python scripts/check_smtp.py                 # send a test email to the sender
    python scripts/check_smtp.py someone@x.com   # send it somewhere else
    python scripts/check_smtp.py --no-send       # only open/authenticate
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import smtplib

from src.mail import mailer
from src.utils.config_loader import load_smtp_config, smtp_port, smtp_use_starttls


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify the jobcv@luilver.com SMTP setup")
    parser.add_argument("recipient", nargs="?", default=None, help="defaults to SMTP_FROM")
    parser.add_argument("--no-send", action="store_true", help="authenticate only, do not send")
    args = parser.parse_args()

    config = load_smtp_config()
    host = config["SMTP_HOST"]
    user = config["SMTP_USER"]
    port = smtp_port()
    password = config["SMTP_APP_PASSWORD"]

    print(f"host      : {host}:{port} ({'STARTTLS' if smtp_use_starttls() else 'implicit TLS'})")
    print(f"login     : {user}")
    print(f"password  : {'set, ' + str(len(password)) + ' chars' if password else 'MISSING'}")
    print(f"from      : {config['SMTP_FROM']} <{config['SMTP_FROM_NAME']}>")

    if not password:
        print(f"\nFAIL: no credentials found. {mailer.NO_PASSWORD_HINT}")
        return 1
    if mailer._is_google(host) and len(password) != 16:
        print(f"\nWARN: a Google app password is exactly 16 characters (got {len(password)}).")
    if not mailer._is_google(host):
        print(f"NOTE: relay mode — {config['SMTP_FROM']} must be a verified sender on the provider "
              f"(SPF + DKIM DNS records for its domain) or the send will be rejected.")

    try:
        if smtp_use_starttls():
            server = smtplib.SMTP(host, port, timeout=30)
            server.ehlo()
            server.starttls()
            server.ehlo()
        else:
            server = smtplib.SMTP_SSL(host, port, timeout=30)
            server.ehlo()
        with server:
            server.login(user, password)
        print("\nOK: the mail server accepted the credentials.")
    except smtplib.SMTPAuthenticationError as exc:
        print(f"\nFAIL: {mailer.auth_hint(host, config['SMTP_FROM'], mailer._server_detail(exc))}")
        return 1
    except (smtplib.SMTPException, OSError) as exc:
        print(f"\nFAIL: cannot use {host}:{port} — {exc}")
        return 1

    if args.no_send:
        return 0

    recipient = args.recipient or config["SMTP_FROM"]
    try:
        mailer.send_mail(
            recipient,
            "Job-CV AI Generator — SMTP test",
            "This is a test message confirming that jobcv@luilver.com can send through Google SMTP.",
        )
    except mailer.EmailError as exc:
        print(f"\nFAIL: authenticated but the send failed — {exc}")
        return 1
    print(f"OK: test message sent to {recipient}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
