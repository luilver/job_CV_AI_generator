from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Dict

try:
    import streamlit as st
except Exception:  # pragma: no cover - streamlit may not be available in tests
    st = None

APP_ROOT = Path(__file__).resolve().parents[2]
CREDENTIALS_PATH = APP_ROOT / "credentials.json"

def load_platform_openai_key() -> str:
    """
    Return the platform-owned OpenAI API key used to serve free-trial requests.

    Resolution order (first match wins):
      1. Streamlit secrets  – PLATFORM_OPENAI_API_KEY
      2. credentials.json   – PLATFORM_OPENAI_API_KEY
      3. Environment var    – PLATFORM_OPENAI_API_KEY
      4. Falls back to the regular openai_api_key if set (convenient for local dev)
    """
    key = "PLATFORM_OPENAI_API_KEY"

    # 1) Streamlit secrets
    if st is not None and hasattr(st, "secrets"):
        try:
            secrets_obj = st.secrets
        except Exception:
            secrets_obj = None
        if secrets_obj is not None:
            try:
                if key in secrets_obj:
                    return str(secrets_obj[key])
            except Exception:
                pass

    # 2) credentials.json
    if CREDENTIALS_PATH.exists():
        try:
            data = json.loads(CREDENTIALS_PATH.read_text(encoding="utf-8"))
            if isinstance(data, dict) and data.get(key):
                return str(data[key])
        except (OSError, json.JSONDecodeError):
            pass

    # 3) Environment variable
    if key in os.environ:
        return os.environ[key]

    # 4) Fall back to the regular openai_api_key (local dev convenience)
    return load_credentials().get("openai_api_key", "")


def load_credentials() -> Dict[str, str]:
    defaults = {
        "openai_api_key": "",
        "codex_api_key": "",
        "gemini_api_key": "",
        "groq_api_key": "",
    }
    # 1) Streamlit secrets (Cloud)
    if st is not None and hasattr(st, "secrets"):
        try:
            secrets_obj = st.secrets
        except Exception:
            secrets_obj = None
        if secrets_obj is not None:
            found_any = False
            for key in defaults:
                try:
                    # Access by key directly; avoid truthiness checks that call __len__
                    if key in secrets_obj:
                        defaults[key] = str(secrets_obj[key])
                        found_any = True
                except Exception:
                    # If accessing secrets triggers parsing errors for a key, ignore and continue
                    continue
            # Only trust Streamlit secrets when they actually contain at least one credential
            if found_any:
                return defaults
    # 2) credentials.json
    if CREDENTIALS_PATH.exists():
        try:
            data = json.loads(CREDENTIALS_PATH.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                defaults.update({k: str(v) for k, v in data.items() if k in defaults and v is not None})
        except (OSError, json.JSONDecodeError):
            pass
        return defaults
    # 3) Environment variables
    env_map = {
        "openai_api_key": "OPENAI_API_KEY",
        "codex_api_key": "CODEX_API_KEY",
        "gemini_api_key": "GEMINI_API_KEY",
        "groq_api_key": "GROQ_API_KEY",
    }
    for k, env in env_map.items():
        if env in os.environ:
            defaults[k] = os.environ[env]
    return defaults


def save_credential(name: str, value: str) -> None:
    credentials = load_credentials()
    credentials[name] = value.strip()
    try:
        CREDENTIALS_PATH.write_text(json.dumps(credentials, indent=2) + "\n", encoding="utf-8")
    except OSError:
        raise


def read_guidelines() -> str:
    path = APP_ROOT / "CV_guidelines.md"
    return path.read_text(encoding="utf-8") if path.exists() else "No CV_guidelines.md was provided. Use clean, truthful, ATS-friendly LaTeX."


def read_cover_letter_guidelines() -> str:
    path = APP_ROOT / "CL_guidelines.md"
    return path.read_text(encoding="utf-8") if path.exists() else "No CL_guidelines.md was provided. Write a concise, professional, truthful cover letter."


# ---------------------------------------------------------------------------
# Outbound email (Google Workspace SMTP)
# ---------------------------------------------------------------------------

SMTP_DEFAULTS = {
    "SMTP_HOST": "smtp.gmail.com",
    "SMTP_PORT": "465",
    "SMTP_USER": "jobcv@luilver.com",
    "SMTP_APP_PASSWORD": "",
    "SMTP_FROM": "jobcv@luilver.com",
    "SMTP_FROM_NAME": "Job-CV AI Generator",
    "SMTP_USE_STARTTLS": "false",
}


def _lookup_secret(*keys: str) -> str:
    """Streamlit secrets -> credentials.json -> environment variable.

    Accepts several names per setting so the usual spelling variants all work.
    """
    for key in keys:
        if st is not None and hasattr(st, "secrets"):
            try:
                secrets_obj = st.secrets
            except Exception:
                secrets_obj = None
            if secrets_obj is not None:
                try:
                    if key in secrets_obj:
                        value = str(secrets_obj[key]).strip()
                        if value:
                            return value
                except Exception:
                    pass

        if CREDENTIALS_PATH.exists():
            try:
                data = json.loads(CREDENTIALS_PATH.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    value = str(data.get(key, "") or "").strip()
                    if value:
                        return value
            except (OSError, json.JSONDecodeError):
                pass

        value = os.environ.get(key, "").strip()
        if value:
            return value
    return ""


# Google prints app passwords in groups of four; the spaces are not part of the secret.
_APP_PASSWORD_KEYS = ("SMTP_APP_PASSWORD", "SMTP_PASSWORD", "GOOGLE_APP_PASSWORD")


def _clean_app_password(raw: str) -> str:
    return "".join(str(raw or "").split()).strip("\"'")


def load_smtp_config() -> Dict[str, str]:
    """
    Return the outbound email configuration.

    Only the app password is really required; host, port, sender and sender name
    fall back to the jobcv@luilver.com defaults.
    """
    config = dict(SMTP_DEFAULTS)
    for key in config:
        if key == "SMTP_APP_PASSWORD":
            value = _clean_app_password(_lookup_secret(*_APP_PASSWORD_KEYS))
        else:
            value = _lookup_secret(key)
        if value:
            config[key] = value
    if not config["SMTP_FROM"]:
        config["SMTP_FROM"] = config["SMTP_USER"] or SMTP_DEFAULTS["SMTP_FROM"]
    return config


def is_smtp_configured() -> bool:
    return bool(load_smtp_config().get("SMTP_APP_PASSWORD"))


def app_base_url() -> str:
    """Public URL of this deployment, used to build links inside emails."""
    base = _lookup_secret("APP_BASE_URL") or "http://localhost:8501"
    return base.rstrip("/")


def confirm_base_url() -> str:
    """
    Base URL for the confirmation link.

    Defaults to the app itself (/confirm route). Set APP_CONFIRM_BASE_URL when the
    link must live on a hostname that is reachable without a Cloudflare Access
    login, e.g. https://confirm.luilver.com
    """
    override = _lookup_secret("APP_CONFIRM_BASE_URL")
    if override:
        return override.rstrip("/")
    return f"{app_base_url()}/confirm"


def smtp_port() -> int:
    raw = load_smtp_config().get("SMTP_PORT", "465")
    try:
        return int(str(raw).strip())
    except (TypeError, ValueError):
        return 465


def smtp_use_starttls() -> bool:
    return load_smtp_config().get("SMTP_USE_STARTTLS", "false").strip().lower() in {"1", "true", "yes", "on"}
