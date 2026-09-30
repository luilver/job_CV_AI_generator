"""SMTP sending through the Google Workspace relay."""

from __future__ import annotations

import os
import smtplib
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.mail import mailer
from src.mail.mailer import is_configured
from src.utils.config_loader import load_smtp_config
from src.utils import config_loader


class FakeSMTP:
    instances: list["FakeSMTP"] = []

    def __init__(self, host, port, timeout=None):
        self.host, self.port, self.timeout = host, port, timeout
        self.messages, self.logins, self.started_tls = [], [], False
        FakeSMTP.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def ehlo(self):
        return 250, b"ok"

    def starttls(self):
        self.started_tls = True

    def login(self, user, password):
        self.logins.append((user, password))

    def send_message(self, message):
        self.messages.append(message)


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    FakeSMTP.instances.clear()
    # Pretend nothing is configured: no Streamlit secrets, no credentials.json.
    monkeypatch.setattr(config_loader, "_lookup_secret", lambda *keys: "")
    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)
    monkeypatch.setattr(smtplib, "SMTP_SSL", FakeSMTP)
    yield


def configure(monkeypatch, **overrides):
    values = {
        "SMTP_HOST": "smtp.gmail.com",
        "SMTP_PORT": "465",
        "SMTP_USER": "jobcv@luilver.com",
        "SMTP_APP_PASSWORD": "abcd efgh ijkl mnop",  # Google groups of four
        "SMTP_FROM": "jobcv@luilver.com",
        "SMTP_FROM_NAME": "Job-CV AI Generator",
        "SMTP_USE_STARTTLS": "false",
        "APP_BASE_URL": "https://jobcv.example.com",
    }
    values.update(overrides)
    monkeypatch.setattr(config_loader, "_lookup_secret", lambda *keys: next((values[k] for k in keys if values.get(k)), ""))
    return values


def test_defaults_are_google_workspace():
    config = config_loader.load_smtp_config()
    assert config["SMTP_HOST"] == "smtp.gmail.com"
    assert config["SMTP_PORT"] == "465"
    assert config["SMTP_USER"] == "jobcv@luilver.com"
    assert config["SMTP_FROM"] == "jobcv@luilver.com"
    assert config_loader.is_smtp_configured() is False


def test_env_var_is_used(monkeypatch):
    monkeypatch.setattr(
        config_loader,
        "_lookup_secret",
        lambda *keys: next((os.environ[k].strip() for k in keys if os.environ.get(k, "").strip()), ""),
    )
    monkeypatch.setenv("SMTP_APP_PASSWORD", "from-env")
    assert config_loader.is_smtp_configured() is True


def test_missing_password_raises_clear_error():
    with pytest.raises(mailer.EmailError) as exc:
        mailer.send_mail("a@b.com", "hi", "body")
    assert "SMTP_APP_PASSWORD" in str(exc.value)
    assert not FakeSMTP.instances


@pytest.mark.smtp_level
def test_sends_with_implicit_tls_and_login(monkeypatch):
    configure(monkeypatch)
    link = mailer.send_verification_email("user@example.com", "Jane Doe", "tok123")
    assert link == "https://jobcv.example.com/confirm?verify=tok123"

    server = FakeSMTP.instances[-1]
    assert (server.host, server.port) == ("smtp.gmail.com", 465)
    assert server.started_tls is False
    assert server.logins == [("jobcv@luilver.com", "abcdefghijklmnop")], "spaces are stripped"

    message = server.messages[0]
    assert message["To"] == "user@example.com"
    assert message["From"] == "Job-CV AI Generator <jobcv@luilver.com>"
    assert "Confirm your email" in message["Subject"]
    body = message.get_body(preferencelist=("plain",)).get_content()
    assert "https://jobcv.example.com/confirm?verify=tok123" in body
    assert "Hi Jane," in body
    html = message.get_body(preferencelist=("html",)).get_content()
    assert f'href="https://jobcv.example.com/confirm?verify=tok123"' in html
    assert "abcde" not in message["From"], "the app password must never leak into headers"


def test_starttls_port_587(monkeypatch):
    configure(monkeypatch, SMTP_PORT="587", SMTP_USE_STARTTLS="true")
    mailer.send_mail("a@b.com", "s", "t")
    server = FakeSMTP.instances[-1]
    assert server.port == 587
    assert server.started_tls is True
    assert server.logins == [("jobcv@luilver.com", "abcdefghijklmnop")], "spaces are stripped"


def test_password_spaces_and_quotes_are_stripped(monkeypatch):
    configure(monkeypatch, SMTP_APP_PASSWORD=' "abcd efgh ijkl mnop" ')
    assert load_smtp_config()["SMTP_APP_PASSWORD"] == "abcdefghijklmnop"
    mailer.send_mail("a@b.com", "s", "t")
    assert FakeSMTP.instances[-1].logins == [("jobcv@luilver.com", "abcdefghijklmnop")]


def test_alternative_password_key_is_accepted(monkeypatch):
    values = {"SMTP_USER": "jobcv@luilver.com", "SMTP_PASSWORD": "zyxwvutsrqponmlk"}
    monkeypatch.setattr(config_loader, "_lookup_secret", lambda *keys: next((values[k] for k in keys if values.get(k)), ""))
    assert load_smtp_config()["SMTP_APP_PASSWORD"] == "zyxwvutsrqponmlk"
    assert is_configured() is True


def test_relay_auth_error_mentions_spf_dkim(monkeypatch):
    configure(monkeypatch, SMTP_HOST="smtp.resend.com", SMTP_USER="resend", SMTP_APP_PASSWORD="re_123")

    class Denying(FakeSMTP):
        def login(self, user, password):
            raise smtplib.SMTPAuthenticationError(535, b"535 incorrect")

    monkeypatch.setattr(smtplib, "SMTP_SSL", Denying)
    with pytest.raises(mailer.EmailError) as exc:
        mailer.send_mail("a@b.com", "s", "t")
    message = str(exc.value)
    assert "SPF" in message and "DKIM" in message and "jobcv@luilver.com" in message
    assert "App Password" not in message, "the Google hint must not appear for a relay"


def test_google_hint_names_the_tier_problem():
    hint = mailer.auth_hint("smtp.gmail.com", "jobcv@luilver.com", "535")
    assert "App Password" in hint and "Cloud Identity" in hint


def test_auth_error_explains_app_password(monkeypatch):
    configure(monkeypatch)

    class Denying(FakeSMTP):
        def login(self, user, password):
            raise smtplib.SMTPAuthenticationError(535, b"5.7.8 Username and Password not accepted")

    monkeypatch.setattr(smtplib, "SMTP_SSL", Denying)
    with pytest.raises(mailer.EmailError) as exc:
        mailer.send_mail("a@b.com", "s", "t")
    message = str(exc.value)
    assert "App Password" in message and "not the account password" in message
    assert "535" in message and "2-Step Verification" in message


def test_sender_falls_back_to_username(monkeypatch):
    configure(monkeypatch, SMTP_FROM="")
    mailer.send_mail("a@b.com", "s", "t")
    assert FakeSMTP.instances[-1].messages[0]["From"].endswith("<jobcv@luilver.com>")


@pytest.mark.smtp_level
def test_greeting_without_name(monkeypatch):
    configure(monkeypatch)
    mailer.send_verification_email("a@b.com", "", "tok")
    body = FakeSMTP.instances[-1].messages[0].get_body(preferencelist=("plain",)).get_content()
    assert "Hi," in body


def test_smtp_error_is_wrapped(monkeypatch):
    configure(monkeypatch)

    class Refusing(FakeSMTP):
        def send_message(self, message):
            raise smtplib.SMTPAuthenticationError(535, b"5.7.8 Username and Password not accepted")

    monkeypatch.setattr(smtplib, "SMTP_SSL", Refusing)
    with pytest.raises(mailer.EmailError) as exc:
        mailer.send_mail("a@b.com", "s", "t")
    assert "Username and Password not accepted" in str(exc.value)


def test_connection_error_is_wrapped(monkeypatch):
    configure(monkeypatch)

    class Unreachable(FakeSMTP):
        def __init__(self, host, port, timeout=None):
            raise OSError("Connection refused")

    monkeypatch.setattr(smtplib, "SMTP_SSL", Unreachable)
    with pytest.raises(mailer.EmailError) as exc:
        mailer.send_mail("a@b.com", "s", "t")
    assert "smtp.gmail.com:465" in str(exc.value)


def test_confirm_base_url_override_wins(monkeypatch):
    configure(monkeypatch, APP_CONFIRM_BASE_URL="https://confirm.luilver.com/")
    assert config_loader.confirm_base_url() == "https://confirm.luilver.com"
    assert mailer.confirmation_url("tok") == "https://confirm.luilver.com?verify=tok"


def test_confirm_base_url_defaults_to_the_confirm_route(monkeypatch):
    configure(monkeypatch)
    assert config_loader.confirm_base_url() == "https://jobcv.example.com/confirm"
    assert mailer.confirmation_url("tok") == "https://jobcv.example.com/confirm?verify=tok"


def test_base_url_strips_trailing_slash(monkeypatch):
    configure(monkeypatch, APP_BASE_URL="https://jobcv.example.com/")
    assert mailer.confirmation_url("t") == "https://jobcv.example.com/confirm?verify=t"


def test_base_url_defaults_to_localhost():
    assert config_loader.app_base_url() == "http://localhost:8501"
    assert mailer.confirmation_url("t") == "http://localhost:8501/confirm?verify=t"


def test_bad_port_falls_back(monkeypatch):
    configure(monkeypatch, SMTP_PORT="not-a-port")
    assert config_loader.smtp_port() == 465
