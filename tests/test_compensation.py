import types

from src.compensation.estimator import (
    estimate_compensation,
    format_amount,
    lookup_compensation,
    normalise_compensation,
    summarise_compensation,
)


def _fake_client(content: str):
    return types.SimpleNamespace(
        chat=types.SimpleNamespace(
            completions=types.SimpleNamespace(
                create=lambda **kwargs: types.SimpleNamespace(
                    choices=[types.SimpleNamespace(message=types.SimpleNamespace(content=content))]
                )
            )
        )
    )


def test_normalise_compensation_from_nested_ranges():
    comp = normalise_compensation(
        {
            "available": True,
            "company": "Google",
            "currency": "usd",
            "total_comp_range": {"low": 260000, "high": 180000},
            "base_range": {"low": "150,000", "high": 180000},
            "median_total_comp": 220000,
            "level": "L4",
            "location": "Zurich",
            "confidence": "Medium",
            "note": "Publicly reported bands.",
        }
    )
    assert comp["currency"] == "USD"
    assert comp["total_comp"] == {"low": 180000, "high": 260000}
    assert comp["base_salary"] == {"low": 150000, "high": 180000}
    assert comp["confidence"] == "medium"


def test_normalise_compensation_returns_none_without_numbers():
    assert normalise_compensation({"available": False, "company": "X"}) is None
    assert normalise_compensation({"total_comp_range": {"low": 0, "high": 0}}) is None
    assert normalise_compensation("nonsense") is None


def test_summarise_compensation_line():
    comp = normalise_compensation(
        {"currency": "USD", "median_total_comp": 220000, "level": "L4", "confidence": "low"}
    )
    summary = summarise_compensation(comp)
    assert "Estimated total comp $220K" in summary
    assert "L4" in summary
    assert "low confidence" in summary
    assert summarise_compensation(None) == ""


def test_format_amount_scales():
    assert format_amount(950, "USD") == "$950"
    assert format_amount(15_000, "USD") == "$15K"
    assert format_amount(1_850_000, "USD") == "$1.85M"
    assert format_amount(2_000_000, "EUR") == "€2M"


def test_lookup_compensation_uses_any_provider():
    class StubProvider:
        def lookup(self, *, company, position="", job_text=""):
            return {"total_comp_range": {"low": 100000, "high": 120000}, "confidence": "high"}

    comp = lookup_compensation(StubProvider(), company="Stripe", position="Engineer")
    assert comp["company"] == "Stripe"
    assert comp["confidence"] == "high"
    assert comp["total_comp"] == {"low": 100000, "high": 120000}


def test_estimate_compensation_parses_ai_json():
    payload = (
        '{"available": true, "company": "Stripe", "currency": "USD", '
        '"median_total_comp": 210000, "level": "L4", "confidence": "medium"}'
    )
    comp = estimate_compensation(
        _fake_client(payload),
        "OpenAI GPT",
        "gpt-4o-mini",
        company="Stripe",
        position="Software Engineer",
        job_text="Build things",
    )
    assert comp["median_total_comp"] == 210000
    assert comp["level"] == "L4"


def test_estimate_compensation_skips_without_company():
    assert estimate_compensation(_fake_client("{}"), "OpenAI GPT", "gpt-4o-mini", company="  ") is None
