"""
OpenAI-compatible client that proxies requests to a self-hosted remote server
(e.g. Ollama with the OpenAI-compat endpoint, or any OpenAI-API-compatible
gateway).  Used for the free trial tier so users don't need their own API key.
"""

from __future__ import annotations

import base64
from typing import Any

import requests as _requests


class RemoteAIClient:
    """
    Minimal OpenAI-chat-completion-compatible client for a remote server.

    The remote server must expose:
        POST <base_url>/v1/chat/completions

    Authentication is done via HTTP Basic Auth when both user and password
    are provided, or Bearer token when only a password/token is given.
    """

    def __init__(self, base_url: str, user: str = "", password: str = "") -> None:
        self.base_url = base_url.rstrip("/")
        self._auth_header = _build_auth_header(user, password)

    # --- OpenAI-SDK-shaped thin shim so call_ai() can use it unchanged -------

    @property
    def chat(self) -> "_ChatNamespace":
        return _ChatNamespace(self.base_url, self._auth_header)


def _build_auth_header(user: str, password: str) -> dict[str, str]:
    if user and password:
        token = base64.b64encode(f"{user}:{password}".encode()).decode()
        return {"Authorization": f"Basic {token}"}
    if password:
        return {"Authorization": f"Bearer {password}"}
    return {}


# ---------------------------------------------------------------------------
# Thin namespace shims so existing code can call:
#   client.chat.completions.create(model=..., messages=..., ...)
# ---------------------------------------------------------------------------

class _ChatNamespace:
    def __init__(self, base_url: str, auth: dict[str, str]) -> None:
        self.completions = _CompletionsNamespace(base_url, auth)


class _CompletionsNamespace:
    def __init__(self, base_url: str, auth: dict[str, str]) -> None:
        self._base_url = base_url
        self._auth = auth

    def create(
        self,
        *,
        model: str,
        messages: list[dict[str, str]],
        temperature: float = 0.2,
        response_format: dict | None = None,
        **_kwargs: Any,
    ) -> "_ChatCompletionResponse":
        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
        }
        if response_format:
            payload["response_format"] = response_format

        headers = {"Content-Type": "application/json", **self._auth}
        resp = _requests.post(
            f"{self._base_url}/v1/chat/completions",
            json=payload,
            headers=headers,
            timeout=120,
        )
        resp.raise_for_status()
        data = resp.json()
        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ValueError(f"Remote server returned unexpected shape: {data}") from exc
        return _ChatCompletionResponse(content)


class _ChatCompletionResponse:
    def __init__(self, content: str) -> None:
        self.choices = [_Choice(content)]


class _Choice:
    def __init__(self, content: str) -> None:
        self.message = _Message(content)


class _Message:
    def __init__(self, content: str) -> None:
        self.content = content
