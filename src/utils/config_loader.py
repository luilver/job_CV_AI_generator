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


def load_credentials() -> Dict[str, str]:
    defaults = {
        "openai_api_key": "",
        "codex_api_key": "",
        "gemini_api_key": "",
        "groq_api_key": "",
        "anthropic_api_key": "",
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
        "anthropic_api_key": "ANTHROPIC_API_KEY",
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
