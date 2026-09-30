import re
from typing import Any

from src.ai.client_factory import call_ai


def generate_tailored_cv(
    client: Any,
    provider: str,
    model: str,
    job: str,
    raw_cv: str,
    guidelines: str,
    source_kind: str = "tex",
) -> str:
    system = "You are an expert CV editor. Return only complete compilable LaTeX source, with no Markdown fences or explanation. Never invent employers, dates, degrees, skills, metrics, or achievements."
    if source_kind == "pdf":
        source_label = "ORIGINAL CV (plain text extracted from a PDF, no LaTeX source available):"
        instructions = (
            "The CV below was extracted from a PDF, so the original LaTeX source is not available. "
            "Write a complete, self-contained LaTeX document from scratch, including the preamble and the document environment."
        )
    else:
        source_label = "ORIGINAL CV (.tex):"
        instructions = (
            "Preserve valid LaTeX structure and commands where practical. Reorder or rewrite truthful "
            "content to emphasize relevant experience, but do not add unsupported claims. Return only the "
            "complete .tex file, including its preamble and document environment."
        )
    prompt = f"""Create a tailored version of the original CV for the job below.
Follow every applicable instruction in CV_guidelines.md. {instructions}

CV_guidelines.md:
---
{guidelines}
---

JOB DESCRIPTION:
---
{job}
---

{source_label}
---
{raw_cv}
---"""
    content = call_ai(client, provider, model, system, prompt, json_mode=False)
    content = re.sub(r"^```(?:latex|tex)?\s*|\s*```$", "", content.strip(), flags=re.I | re.S).strip()
    if "\\begin{document}" not in content or "\\end{document}" not in content:
        raise ValueError("The AI did not return a complete LaTeX document.")
    return content


def generate_cover_letter(
    client: Any,
    provider: str,
    model: str,
    job: str,
    cv: str,
    user_instructions: str,
    guidelines: str,
    signer_name: str = "",
) -> str:
    system = (
        "You are an expert cover-letter writer. Return only the finished cover letter as plain text, "
        "with no Markdown fences, meta-commentary, headings such as 'Cover Letter', or placeholders. "
        "Be specific, concise, professional, and truthful. Never invent experience or qualifications."
    )
    signer = " ".join(str(signer_name or "").split())
    if signer:
        signoff_rule = f"End with a professional sign-off that uses exactly this name: {signer}"
    else:
        signoff_rule = "End with a professional sign-off, but do not invent a person's name"
    prompt = f"""Write a tailored cover letter for this job application.

Follow the local cover-letter guidelines below whenever they are relevant. Also follow the applicant's specific instructions. Use evidence from the CV and connect it naturally to the job description. Do not claim skills or experience that are not supported by the CV. Do not include an address block, date, or recipient details unless the applicant explicitly asks for them. {signoff_rule}.

CL_guidelines.md:
---
{guidelines}
---

APPLICANT'S SPECIFIC INSTRUCTIONS:
---
{user_instructions.strip() or "No additional instructions were provided."}
---

JOB DESCRIPTION:
---
{job}
---

CLEAN CV TEXT:
---
{cv}
---"""
    content = call_ai(client, provider, model, system, prompt, json_mode=False)
    content = re.sub(r"^```(?:text|markdown)?\s*|\s*```$", "", content.strip(), flags=re.I | re.S).strip()
    if len(content) < 100:
        raise ValueError("The AI returned an unexpectedly short cover letter.")
    return content
