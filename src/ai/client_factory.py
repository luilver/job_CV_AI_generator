from __future__ import annotations

import re
from typing import Any

from openai import OpenAI

from .anthropic_client import AnthropicRESTClient
from .gemini_client import GeminiRESTClient


def call_ai(client: Any, provider: str, model: str, system: str, prompt: str, json_mode: bool = True) -> str:
    if provider in {"Gemini 2.5 Flash", "Anthropic"}:
        content = client.generate(model, system, prompt, json_mode)
    elif provider == "Codex":
        kwargs = {"model": model, "input": [{"role": "developer", "content": system}, {"role": "user", "content": prompt}]}
        if json_mode:
            kwargs["text"] = {"format": {"type": "json_object"}}
        result = client.responses.create(**kwargs)
        content = result.output_text
    else:
        kwargs = {"model": model, "temperature": 0.2, "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}]}
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
    if provider == "Anthropic":
        return AnthropicRESTClient(api_key)
    if provider == "Groq":
        return OpenAI(api_key=api_key, base_url="https://api.groq.com/openai/v1")
    return OpenAI(api_key=api_key)
