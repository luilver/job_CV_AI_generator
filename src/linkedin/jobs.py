"""Read job postings from LinkedIn's public, logged-out job search.

Why scraping rather than an API: LinkedIn publishes no consumer API for searching
jobs. Its Job Posting API is closed to new partners and is for posting, not
reading. What *is* public is the guest search every signed-out browser uses:

    /jobs-guest/jobs/api/seeMoreJobPostings/search   -> list of job cards
    /jobs-guest/jobs/api/jobPosting/<id>             -> one posting's description

Both answer with HTTP 200 to a plain browser User-Agent and need no cookie, no
account and no API key. The cost is fragility: the markup is not a contract, so
parsing is defensive and every request is rate-limited by REQUEST_PAUSE_SECONDS.

What this module cannot do: submit applications. LinkedIn exposes no API for that,
and Easy Apply automation requires a logged-in member session that would put the
account at risk. The pipeline stops at "here is a matched job and a tailored CV".
"""

from __future__ import annotations

import re
import time
from typing import Any
from urllib.parse import urlencode

import requests
from bs4 import BeautifulSoup

REQUEST_TIMEOUT = 25
REQUEST_PAUSE_SECONDS = 1.2
CARDS_PER_PAGE = 10
MAX_PAGES = 10
MAX_DESCRIPTION_CHARS = 12_000

_SEARCH_ENDPOINT = "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"
_DETAIL_ENDPOINT = "https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{job_id}"
_PUBLIC_JOB_URL = "https://www.linkedin.com/jobs/view/{job_id}"

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

# LinkedIn's "time posted" filter, in seconds. The guest API only accepts its own
# vocabulary, so a day count has to be mapped onto it.
_AGE_FILTERS = {
    1: "r86400",
    2: "r172800",
    3: "r259200",
    7: "r604800",
    14: "r1209600",
    30: "r2592000",
}

_JOB_ID_RE = re.compile(r"(\d{6,})")
_APPLICANTS_RE = re.compile(r"(\d[\d,]*)\s*applicants?", re.I)


def _sleep() -> None:
    time.sleep(REQUEST_PAUSE_SECONDS)


def _get(url: str, params: dict[str, Any] | None = None) -> str | None:
    """GET a guest endpoint. Returns None instead of raising on any HTTP problem."""
    try:
        response = requests.get(url, params=params, headers=_HEADERS, timeout=REQUEST_TIMEOUT)
    except requests.RequestException:
        return None
    if response.status_code != 200:
        return None
    return response.text


def job_id_from_key(job_key: str) -> str:
    match = _JOB_ID_RE.search(str(job_key or ""))
    return match.group(1) if match else ""


def canonical_url(job_id: str) -> str:
    """A stable job URL. The card href carries tracking params we do not want."""
    return _PUBLIC_JOB_URL.format(job_id=job_id)


def _age_filter(days: int) -> str:
    if days in _AGE_FILTERS:
        return _AGE_FILTERS[days]
    ordered = sorted(_AGE_FILTERS)
    nearest = min(ordered, key=lambda value: abs(value - int(days)))
    return _AGE_FILTERS[nearest]


def _clean(text: str | None) -> str:
    return " ".join(str(text or "").split())


def _parse_card(card: Any) -> dict[str, Any] | None:
    urn = str(card.get("data-entity-urn") or "")
    job_id = job_id_from_key(urn)
    if not job_id:
        return None

    title_node = card.select_one("h3.base-search-card__title") or card.select_one("span.sr-only")
    company_node = card.select_one("h4.base-search-card__subtitle")
    location_node = card.select_one("span.job-search-card__location")
    time_node = card.select_one("time[datetime]")
    meta_node = card.select_one("span.base-search-card__metadata")

    return {
        "job_key": f"urn:li:jobPosting:{job_id}",
        "job_id": job_id,
        "title": _clean(title_node.get_text() if title_node else ""),
        "company": _clean(company_node.get_text() if company_node else ""),
        "location": _clean(location_node.get_text() if location_node else ""),
        "posted_on": _clean(time_node.get("datetime") if time_node else ""),
        "url": canonical_url(job_id),
        "applicants": _parse_applicants(_clean(meta_node.get_text() if meta_node else "")),
        "description": "",
    }


def _parse_applicants(text: str) -> int | None:
    """'Over 200 applicants' -> 200. 'Be an early applicant' -> None."""
    match = _APPLICANTS_RE.search(str(text or ""))
    if not match:
        return None
    try:
        return int(match.group(1).replace(",", ""))
    except (TypeError, ValueError):
        return None


def search_page(keyword: str, location: str, *, start: int = 0, remote_only: bool = True, max_age_days: int = 3) -> list[dict[str, Any]]:
    """One page of search results for a single keyword. Returns [] on any failure."""
    params: dict[str, Any] = {
        "keywords": keyword,
        "location": location or "",
        "start": int(start),
        "f_TPR": _age_filter(max_age_days),
    }
    if remote_only:
        params["f_WT"] = 2
    html = _get(_SEARCH_ENDPOINT, params)
    if not html:
        return []
    try:
        soup = BeautifulSoup(html, "html.parser")
    except Exception:
        return []
    cards = soup.select("div.base-search-card[data-entity-urn]")
    if not cards:
        # LinkedIn has changed the card container; fall back to any element that
        # still carries the urn, so a class rename alone does not end discovery.
        cards = soup.select("[data-entity-urn]")
    results: list[dict[str, Any]] = []
    for card in cards:
        parsed = _parse_card(card)
        if parsed:
            results.append(parsed)
    return results


def search(
    keywords: list[str],
    location: str,
    *,
    remote_only: bool = True,
    max_age_days: int = 3,
    pages: int = 2,
) -> list[dict[str, Any]]:
    """Search several keywords and de-duplicate by job id, keeping first sighting."""
    unique: dict[str, dict[str, Any]] = {}
    page_count = max(1, min(int(pages or 1), MAX_PAGES))
    for index, keyword in enumerate(keywords or []):
        for page in range(page_count):
            if index or page:
                _sleep()
            for item in search_page(
                keyword,
                location,
                start=page * CARDS_PER_PAGE,
                remote_only=remote_only,
                max_age_days=max_age_days,
            ):
                unique.setdefault(item["job_id"], item)
            if not unique and page == 0 and index == 0:
                break
    return list(unique.values())


def fetch_detail(job_id: str) -> dict[str, Any]:
    """Read one posting's description. Raises ValueError when it cannot be read."""
    job_id = job_id_from_key(job_id)
    if not job_id:
        raise ValueError("That job id is not a LinkedIn job id.")

    html = _get(_DETAIL_ENDPOINT.format(job_id=job_id))
    if not html:
        raise ValueError(
            "LinkedIn did not return the job description. The posting may have been filled or removed."
        )

    soup = BeautifulSoup(html, "html.parser")
    description_node = soup.select_one(".show-more-less-html__markup")
    description = (
        description_node.get_text("\n", strip=True) if description_node else _clean(soup.get_text(" "))
    )
    if len(description) < 200:
        raise ValueError("That posting did not contain a readable job description.")

    title_node = soup.select_one("h2.topcard__title")
    org_node = soup.select_one(".topcard__org-name-link") or soup.select_one("a.topcard__org-name-link")
    flavor_nodes = soup.select(".topcard__flavor--bullet")
    posted_node = soup.select_one(".posted-time-ago__text")
    applicant_node = soup.select_one(".num-applicants__caption")

    # 'Seniority level' / 'Employment type' are dt/dd pairs after the heading.
    criteria = _criteria_pairs(soup)

    return {
        "job_key": f"urn:li:jobPosting:{job_id}",
        "job_id": job_id,
        "title": _clean(title_node.get_text() if title_node else ""),
        "company": _clean(org_node.get_text() if org_node else ""),
        "location": _clean(flavor_nodes[0].get_text() if flavor_nodes else ""),
        "posted_on": _clean(posted_node.get_text() if posted_node else ""),
        "url": canonical_url(job_id),
        "applicants": _parse_applicants(_clean(applicant_node.get_text() if applicant_node else "")),
        "description": description[:MAX_DESCRIPTION_CHARS],
        "seniority": criteria.get("Seniority level", ""),
        "employment_type": criteria.get("Employment type", ""),
    }


def _criteria_pairs(soup: Any) -> dict[str, str]:
    """Pull the 'Seniority level: Mid-Senior' style facts out of the detail fragment."""
    pairs: dict[str, str] = {}
    for heading in soup.select("h3"):
        label = _clean(heading.get_text())
        if not label:
            continue
        sibling = heading.find_next_sibling()
        if sibling is None:
            continue
        value = _clean(sibling.get_text())
        if value and len(value) < 120:
            pairs[label] = value
    return pairs


def hydrate(jobs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Fetch descriptions for a list of search cards, keeping what could be read."""
    hydrated: list[dict[str, Any]] = []
    for index, job in enumerate(jobs or []):
        if index:
            _sleep()
        try:
            detail = fetch_detail(job.get("job_id", ""))
        except ValueError:
            continue
        merged = {**job, **detail}
        merged.setdefault("description", "")
        if not merged.get("company"):
            merged["company"] = job.get("company", "")
        hydrated.append(merged)
    return hydrated
