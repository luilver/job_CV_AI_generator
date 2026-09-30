"""Compensation lookup for a position, shown under the company name.

The lookup goes through a provider so the data source can be swapped without
touching the UI. The default provider asks the configured AI for an estimate
based on publicly known pay for the company, role and location. A licensed
salary API only has to expose ``lookup()`` with the same signature to replace
it (see ``CompensationProvider``).
"""

from __future__ import annotations

from typing import Any, Protocol

from src.ai.client_factory import call_ai
from src.generators.analysis import get_json_object

JOB_TEXT_LIMIT = 4000
CONFIDENCE_LEVELS = ("low", "medium", "high")

_CURRENCY_SYMBOLS = {"USD": "$", "EUR": "€", "GBP": "£", "BRL": "R$"}


class CompensationProvider(Protocol):
    """Anything able to return compensation data for a role."""

    def lookup(self, *, company: str, position: str, job_text: str) -> dict[str, Any] | None:
        ...


# ---------------------------------------------------------------------------
# Normalisation
# ---------------------------------------------------------------------------

def _as_amount(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, str):
        digits = "".join(ch for ch in value if ch.isdigit())
        if not digits:
            return None
        value = int(digits)
    if isinstance(value, float):
        value = int(round(value))
    if not isinstance(value, int):
        return None
    return value if value > 0 else None


def _as_range(value: Any, *flat_keys: str) -> dict[str, int] | None:
    if isinstance(value, dict):
        low = _as_amount(value.get("low", value.get("min")))
        high = _as_amount(value.get("high", value.get("max")))
    else:
        low = high = _as_amount(value)
    if low is not None and high is not None and low > high:
        low, high = high, low
    if low is None and high is None:
        return None
    return {"low": low or high, "high": high or low}


def _as_text(value: Any, limit: int = 120) -> str:
    if value is None or isinstance(value, (bool, list, dict)):
        return ""
    text = " ".join(str(value).split())
    return text[:limit].strip()


def normalise_compensation(raw: Any, company: str = "") -> dict[str, Any] | None:
    """Turn a provider payload into the shape the UI renders, or None."""
    if not isinstance(raw, dict) or raw.get("available") is False:
        return None

    total = _as_range(raw.get("total_comp_range")) or _as_range(
        {"low": raw.get("total_comp_low"), "high": raw.get("total_comp_high")}
    )
    base = _as_range(raw.get("base_range")) or _as_range(
        {"low": raw.get("base_low"), "high": raw.get("base_high")}
    )
    median = _as_amount(raw.get("median_total_comp"))

    if total is None and base is None and median is None:
        return None

    if total is None and median is not None:
        total = {"low": median, "high": median}

    confidence = _as_text(raw.get("confidence")).lower()
    if confidence not in CONFIDENCE_LEVELS:
        confidence = "low"

    return {
        "company": _as_text(raw.get("company"), 80) or company.strip(),
        "currency": _as_text(raw.get("currency"), 8).upper() or "USD",
        "total_comp": total,
        "base_salary": base,
        "median_total_comp": median,
        "level": _as_text(raw.get("level") or raw.get("typical_level"), 40),
        "location": _as_text(raw.get("location"), 80),
        "confidence": confidence,
        "note": _as_text(raw.get("note") or raw.get("source_note"), 200),
    }


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------

def format_amount(value: int, currency: str = "USD") -> str:
    symbol = _CURRENCY_SYMBOLS.get(currency.upper(), "")
    if value >= 1_000_000:
        amount = f"{value / 1_000_000:.2f}".rstrip("0").rstrip(".")
        return f"{symbol}{amount}M"
    if value >= 1_000:
        return f"{symbol}{value / 1_000:.0f}K"
    return f"{symbol}{value:,}"


def _format_range(value: dict[str, int], currency: str) -> str:
    if value["low"] == value["high"]:
        return format_amount(value["low"], currency)
    return f"{format_amount(value['low'], currency)}–{format_amount(value['high'], currency)}"


def summarise_compensation(comp: dict[str, Any] | None) -> str:
    """One-line summary rendered under the company name. Empty if unusable."""
    if not comp:
        return ""
    currency = comp.get("currency", "USD")
    parts: list[str] = []
    if comp.get("total_comp"):
        parts.append(f"Estimated total comp {_format_range(comp['total_comp'], currency)}")
    if comp.get("base_salary"):
        parts.append(f"base {_format_range(comp['base_salary'], currency)}")
    if comp.get("level"):
        parts.append(comp["level"])
    if comp.get("location"):
        parts.append(comp["location"])
    parts.append(f"{currency}")
    parts.append(f"{comp.get('confidence', 'low')} confidence")
    return " · ".join(parts)


# ---------------------------------------------------------------------------
# Default provider: AI estimate
# ---------------------------------------------------------------------------

_SYSTEM = (
    "You are a compensation analyst. Return only valid JSON, with no Markdown or commentary. "
    "Use your knowledge of publicly reported pay for the company, role, seniority and location. "
    "Never invent precise figures you are not confident about: prefer wide ranges and low confidence."
)

_PROMPT = """Estimate the pay for this position, based on publicly known compensation for that company and role.

Return exactly this JSON shape, with every number as a plain integer in {currency} per year:
{{"available": true, "company": "", "currency": "{currency}", "total_comp_range": {{"low": 0, "high": 0}}, "base_range": {{"low": 0, "high": 0}}, "median_total_comp": 0, "level": "", "location": "", "confidence": "low|medium|high", "note": ""}}

Rules:
- "available" must be false if the company or role is too vague to estimate.
- Fill "total_comp_range" and "base_range" only with figures you actually believe; use 0 for anything unknown.
- "median_total_comp" is the midpoint of your best estimate for total cash + equity per year.
- "note" is one short sentence naming what the estimate is based on.

COMPANY:
{company}

POSITION:
{position}

JOB DESCRIPTION:
---
{job}
---"""


def estimate_compensation(
    client: Any,
    provider: str,
    model: str,
    *,
    company: str,
    position: str = "",
    job_text: str = "",
) -> dict[str, Any] | None:
    """Default provider: ask the configured AI for a pay estimate."""
    company = company.strip()
    if not company:
        return None

    prompt = _PROMPT.format(
        currency="USD",
        company=company,
        position=position.strip() or "not specified",
        job=(job_text or "")[:JOB_TEXT_LIMIT],
    )
    try:
        raw = get_json_object(call_ai(client, provider, model, _SYSTEM, prompt))
    except (ValueError, KeyError, TypeError):
        return None
    return normalise_compensation(raw, company)


def lookup_compensation(
    compensation_provider: CompensationProvider,
    *,
    company: str,
    position: str = "",
    job_text: str = "",
) -> dict[str, Any] | None:
    """Call any provider implementing the protocol and normalise its payload."""
    raw = compensation_provider.lookup(company=company, position=position, job_text=job_text)
    return normalise_compensation(raw, company)


class AiCompensationProvider:
    """Default provider — an AI estimate from the client's own AI settings."""

    def __init__(self, client: Any, provider: str, model: str) -> None:
        self.client = client
        self.provider = provider
        self.model = model

    def lookup(self, *, company: str, position: str = "", job_text: str = "") -> dict[str, Any] | None:
        return estimate_compensation(
            self.client,
            self.provider,
            self.model,
            company=company,
            position=position,
            job_text=job_text,
        )
