import re
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

REQUEST_TIMEOUT = 25


def visible_text_from_html(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript", "svg", "iframe", "template"]):
        tag.decompose()
    return re.sub(r"\n{3,}", "\n\n", soup.get_text("\n", strip=True)).strip()


def extract_job_sections(markdown: str) -> str:
    lines = markdown.splitlines()
    keywords = re.compile(r"\b(responsibilities|requirements?|qualifications?|what you.ll do|skills?|about the role)\b", re.I)
    starts = [i for i, line in enumerate(lines) if keywords.search(line)]
    if not starts:
        return markdown.strip()
    start, end = starts[0], len(lines)
    for i in range(start + 1, len(lines)):
        if re.match(r"^#{1,4}\s+", lines[i]) and not keywords.search(lines[i]) and i - start > 4:
            end = i
            break
    selected = "\n".join(lines[start:end]).strip()
    return selected if len(selected) >= 200 else markdown.strip()


def scrape_job(url: str) -> str:
    headers = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120 Safari/537.36"}
    try:
        response = requests.get(url, headers=headers, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        text = visible_text_from_html(response.text)
        if len(text) >= 800:
            return text
    except requests.RequestException:
        pass
    # r.jina.ai needs the full target URL, scheme included — Oracle-style sites
    # refuse plain http, and the reader answers 422 if it cannot connect.
    jina_url = "https://r.jina.ai/" + url
    jina_response = requests.get(jina_url, headers={"User-Agent": "job-cv-matcher/1.0"}, timeout=REQUEST_TIMEOUT)
    jina_response.raise_for_status()
    jina_text = extract_job_sections(jina_response.text)
    if len(jina_text) < 200:
        raise ValueError("The page did not contain enough readable job-description text.")
    return jina_text
