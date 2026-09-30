"""Test-wide guard rails.

Two hazards are neutralised here:

1. `st.secrets` exports every secret into `os.environ` the first time it is parsed
   (streamlit/runtime/secrets.py::_maybe_set_environment_variable). Without this,
   a test that merely reads secrets would feed real SMTP values into later tests.
2. Tests must never send real mail. Any test that signs up hits the mailer, so it
   is stubbed globally; tests that assert on the payload override this fixture.
"""

from __future__ import annotations

import os

import pytest

SECRET_PREFIXES = ("SMTP_", "APP_BASE_URL", "PAYPAL_", "PLATFORM_", "FREE_TIER_", "OPENAI_")


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "smtp_level: test drives the SMTP layer itself and needs the real send_verification_email",
    )


@pytest.fixture(autouse=True)
def clean_secret_environment(monkeypatch):
    """Keep Streamlit's side effect of exporting secrets into the environment."""
    for key in [k for k in os.environ if k.startswith(SECRET_PREFIXES)]:
        monkeypatch.delenv(key, raising=False)


@pytest.fixture(autouse=True)
def no_real_email(request, monkeypatch):
    """Stub the mailer so no test can send mail through a real provider.

    Tests marked smtp_level assert on the message itself, so they keep the real
    function (their SMTP server is faked instead).
    """
    from src.mail import mailer

    sent: list[tuple] = []

    if request.node.get_closest_marker("smtp_level"):
        return sent

    def fake_send_verification(to_email: str, name: str = "", token: str = ""):
        sent.append((to_email, name, token))
        return f"http://localhost:8501/confirm?verify={token}"

    monkeypatch.setattr(mailer, "send_verification_email", fake_send_verification)
    return sent
