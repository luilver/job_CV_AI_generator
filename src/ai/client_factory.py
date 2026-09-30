from __future__ import annotations

import re
from typing import Any

from openai import OpenAI

from .gemini_client import GeminiRESTClient

# Sentinel provider name used internally for free-trial analyses.
FREE_TIER_PROVIDER = "OpenAI GPT"
FREE_TIER_MODEL = "gpt-4o-mini"


def call_ai(client: Any, provider: str, model: str, system: str, prompt: str, json_mode: bool = True) -> str:
    if provider == "Gemini 2.5 Flash":
        content = client.generate(model, system, prompt, json_mode)
    elif provider == "Codex":
        kwargs = {"model": model, "input": [{"role": "developer", "content": system}, {"role": "user", "content": prompt}]}
        if json_mode:
            kwargs["text"] = {"format": {"type": "json_object"}}
        result = client.responses.create(**kwargs)
        content = result.output_text
    else:
        # Covers OpenAI GPT, Groq, and RemoteAIClient (all share the
        # chat.completions.create interface).
        kwargs: dict[str, Any] = {
            "model": model,
            "temperature": 0.2,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
        }
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        result = client.chat.completions.create(**kwargs)
        content = result.choices[0].message.content
    if not content:
        raise ValueError("The AI returned an empty response.")
    return content


def make_client(provider: str, api_key: str) -> Any:
    if provider == "Gemini 2.5 Flash":
        return GeminiRESTClient(api_key)
    if provider == "Groq":
        return OpenAI(api_key=api_key, base_url="https://api.groq.com/openai/v1")
    return OpenAI(api_key=api_key)


def make_free_tier_client(api_key: str) -> OpenAI:
    """Return a standard OpenAI client using the platform's key for free-trial requests."""
    return OpenAI(api_key=api_key)
