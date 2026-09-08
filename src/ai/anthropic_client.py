import requests


class AnthropicRESTClient:
    """Small dependency-free Anthropic client using the official Messages REST endpoint."""

    API_URL = "https://api.anthropic.com/v1/messages"
    API_VERSION = "2023-06-01"

    def __init__(self, api_key: str):
        self.api_key = api_key

    def generate(self, model: str, system: str, prompt: str, json_mode: bool) -> str:
        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": self.API_VERSION,
            "Content-Type": "application/json",
        }
        payload = {
            "model": model,
            "max_tokens": 4000 if "haiku" in model else 8000,
            "system": system,
            "messages": [{"role": "user", "content": prompt}],
        }
        response = None
        for attempt in range(2):
            try:
                response = requests.post(self.API_URL, headers=headers, json=payload, timeout=120)
                break
            except requests.exceptions.ReadTimeout:
                if attempt == 1:
                    raise TimeoutError("Anthropic took too long to generate the response.") from None
        assert response is not None
        if not response.ok:
            raise ValueError(f"Anthropic API error ({response.status_code}): {response.text[:500]}")
        data = response.json()
        try:
            blocks = data["content"]
            text = "".join(block.get("text", "") for block in blocks if block.get("type") == "text")
        except (KeyError, TypeError) as exc:
            raise ValueError(f"Anthropic returned no text: {data}") from exc
        if not text:
            raise ValueError(f"Anthropic returned no text: {data}")
        return text