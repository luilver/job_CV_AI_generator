"""The daily digest: matched remote jobs, with a tailored CV already attached.

One message a day, listing only postings that cleared the configured match
threshold, each with its requirement breakdown, its public apply link, and — when
materials were generated — the tailored CV and cover letter as attachments.

LinkedIn has no API for submitting an application, so the digest gets a person to
the last possible click: open the posting, upload the attached CV, send. Anything
that tried to submit on the user's behalf would need their member session and
would put the account at risk for no gain.
"""

from __future__ import annotations

import html
import re
from typing import Any

from src.mail import mailer

APPLY_CTA = "Apply on LinkedIn"
PREVIEW_CHARS = 320
MAX_REQUIREMENTS_IN_DIGEST = 6
MAX_ATTACHMENT_BYTES = 3 * 1024 * 1024

DIGEST_FROM_NAME = "Job-CV AI Generator"


def _slug(value: str, fallback: str = "job") -> str:
    cleaned = re.sub(r"[^A-Za-z0-9]+", "-", str(value or "")).strip("-").lower()
    return (cleaned[:48] or fallback).strip("-")


def _clip(value: str, limit: int = PREVIEW_CHARS) -> str:
    text = " ".join(str(value or "").split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def build_digest(items: list[dict[str, Any]], *, full_name: str = "", app_url: str = "") -> tuple[str, str, str]:
    """Render (subject, plain text, html) for a set of matched postings."""
    greeting = f"Hi {full_name.split()[0]}," if full_name.strip() else "Hi,"

    if not items:
        subject = "No new matching jobs today — Job-CV AI Generator"
        text = f"""{greeting}

Nothing cleared your match threshold in the latest search.

That is normal — most postings are not for you. Tomorrow's digest will pick up
anything new. If you expected matches, lower the minimum score or broaden the
keywords on the Jobs page.
"""
        return subject, text, _wrap_html(greeting, "<p>Nothing cleared your match threshold in the latest search.</p>")

    subject = f"{len(items)} matching remote job{'s' if len(items) != 1 else ''} today — Job-CV AI Generator"

    text_lines = [greeting, "", f"{len(items)} remote postings matched your CV at or above your threshold:", ""]
    html_rows: list[str] = []

    for index, item in enumerate(items, start=1):
        title = str(item.get("title") or "Untitled role")
        company = str(item.get("company") or "Unknown company")
        score = int(item.get("match_score") or 0)
        url = str(item.get("url") or "")
        location = str(item.get("location") or "")
        analysis = item.get("analysis") if isinstance(item.get("analysis"), dict) else {}
        requirements = [r for r in analysis.get("requirements", []) if isinstance(r, dict)]

        text_lines.append(f"{index}. {title} — {company}  [{score}% match]")
        if location:
            text_lines.append(f"   {location}")
        matched = [str(r.get("text", "")) for r in requirements if str(r.get("status", "")).lower() == "match"]
        missing = [str(r.get("text", "")) for r in requirements if str(r.get("status", "")).lower() != "match"]
        if matched:
            text_lines.append(f"   Matched: {_clip(', '.join(matched[:MAX_REQUIREMENTS_IN_DIGEST]))}")
        if missing:
            text_lines.append(f"   Missing: {_clip(', '.join(missing[:MAX_REQUIREMENTS_IN_DIGEST]))}")
        if item.get("tailored_cv"):
            text_lines.append("   Attached: tailored CV (.tex) + cover letter (.txt)")
        text_lines.append(f"   {APPLY_CTA}: {url}")
        text_lines.append("")

        chips = "".join(
            f'<span style="display:inline-block;padding:2px 8px;margin:2px 4px 2px 0;border-radius:10px;'
            f'font-size:0.78rem;background:{"#e6f4ea" if r.get("status") == "match" else "#fce8e6"};'
            f'color:{"#137333" if r.get("status") == "match" else "#c5221f"}">{html.escape(str(r.get("text", ""))[:90])}</span>'
            for r in requirements[:MAX_REQUIREMENTS_IN_DIGEST]
        )
        attachment_note = (
            '<p style="font-size:0.85rem;color:#666;">Tailored CV and cover letter are attached to this email.</p>'
            if item.get("tailored_cv")
            else ""
        )
        html_rows.append(
            f"""
      <div style="border:1px solid #e0e0e0;border-radius:8px;padding:14px 16px;margin:0 0 14px 0;">
        <h3 style="margin:0 0 4px 0;font-size:1.05rem;">
          {html.escape(title)} <span style="color:#137333;">[{score}%]</span>
        </h3>
        <p style="margin:0 0 6px 0;color:#555;font-size:0.9rem;">
          {html.escape(company)}{' · ' + html.escape(location) if location else ''}
        </p>
        <div style="margin:6px 0 10px 0;">{chips}</div>
        {attachment_note}
        <a href="{html.escape(url)}" style="display:inline-block;background:#0a66c2;color:#fff;
           padding:8px 14px;border-radius:6px;text-decoration:none;font-weight:600;font-size:0.9rem;">{APPLY_CTA}</a>
      </div>"""
        )

    footer = (
        "LinkedIn does not offer an API to submit applications, so these links open the posting for you to "
        "upload the attached CV and send it yourself.\n\nOpen your matches and settings: "
        + (app_url or "the Jobs page in the app")
    )

    text_lines.append(footer)
    text = "\n".join(text_lines)

    body_html = _wrap_html(greeting, "".join(html_rows) + f"<p style='color:#666;font-size:0.85rem;'>{html.escape(footer)}</p>")
    return subject, text, body_html


def _wrap_html(greeting: str, inner: str) -> str:
    return f"""<div style="font-family:-apple-system,Segoe UI,Helvetica,Arial,sans-serif;max-width:640px;
   margin:0 auto;color:#222;line-height:1.5;">
  <p>{html.escape(greeting)}</p>
  {inner}
  <p style="color:#999;font-size:0.8rem;margin-top:24px;">— {DIGEST_FROM_NAME}</p>
</div>"""


def build_attachments(items: list[dict[str, Any]]) -> tuple[tuple[str, str, str], ...]:
    """Collect the generated CVs and cover letters as mail attachments."""
    attachments: list[tuple[str, str, str]] = []
    for item in items:
        stem = _slug(f"{item.get('company') or ''}-{item.get('title') or ''}")
        cv = str(item.get("tailored_cv") or "")
        letter = str(item.get("cover_letter") or "")
        if cv and len(cv.encode("utf-8")) <= MAX_ATTACHMENT_BYTES:
            attachments.append((f"{stem}-tailored-cv.tex", cv, "application/x-tex"))
        if letter and len(letter.encode("utf-8")) <= MAX_ATTACHMENT_BYTES:
            attachments.append((f"{stem}-cover-letter.txt", letter, "text/plain"))
    return tuple(attachments)


def send_digest(
    to_email: str,
    items: list[dict[str, Any]],
    *,
    full_name: str = "",
    app_url: str = "",
) -> str:
    """Build and send one digest. Returns the subject that was used."""
    subject, text, body = build_digest(items, full_name=full_name, app_url=app_url)
    mailer.send_mail(to_email, subject, text, body, build_attachments(items))
    return subject
