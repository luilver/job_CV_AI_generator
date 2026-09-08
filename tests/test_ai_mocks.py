import types
from unittest.mock import MagicMock

from src.ai.client_factory import call_ai, make_client


class DummyChat:
    class completions:
        @staticmethod
        def create(**kwargs):
            class Choice:
                class Message:
                    content = '{"requirements": [{"text": "Python", "status": "match"}]}'

                message = Message()

            class Result:
                choices = [Choice()]

            return Result()


def test_call_ai_gpt():
    client = types.SimpleNamespace()
    client.chat = types.SimpleNamespace(completions=types.SimpleNamespace(create=MagicMock(return_value=types.SimpleNamespace(choices=[types.SimpleNamespace(message=types.SimpleNamespace(content='{"foo":1}'))]))))
    out = call_ai(client, "OpenAI GPT", "gpt-4", "sys", "prompt", json_mode=True)
    assert isinstance(out, str) and "{\"foo\":1}" in out or "{\"foo\":1}" == out


def test_make_client_returns_gemini():
    gem = make_client("Gemini 2.5 Flash", "key")
    assert gem.__class__.__name__ in ("Object", "GeminiRESTClient", "SimpleNamespace") or hasattr(gem, "generate")


def test_make_client_returns_anthropic():
    claude = make_client("Anthropic", "key")
    assert claude.__class__.__name__ == "AnthropicRESTClient"


def test_call_ai_anthropic():
    client = types.SimpleNamespace()
    client.generate = MagicMock(return_value='{"requirements": [{"text": "Python", "status": "match"}]}')
    out = call_ai(client, "Anthropic", "claude-sonnet-5", "sys", "prompt", json_mode=True)
    client.generate.assert_called_once_with("claude-sonnet-5", "sys", "prompt", True)
    assert isinstance(out, str) and "requirements" in out
