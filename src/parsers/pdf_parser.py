"""Extract plain text from a PDF CV."""

from __future__ import annotations

import io
import re

from pypdf import PdfReader

MIN_TEXT_LENGTH = 120


def normalise_extracted_text(text: str) -> str:
    """Tidy the raw text a PDF reader returns: de-hyphenate, trim, reflow."""
    text = text.replace("­", "")
    text = re.sub(r"(\w)-\n(\w)", r"\1\2", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"[ ]*\n[ ]*", "\n", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def extract_pdf_text(data: bytes) -> str:
    """Return the text of a PDF, or raise ValueError if there is no usable text."""
    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            try:
                reader.decrypt("")
            except Exception as exc:  # pragma: no cover - depends on the file
                raise ValueError("The PDF is password-protected.") from exc
        text = normalise_extracted_text("\n".join((page.extract_text() or "") for page in reader.pages))
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError("The PDF could not be read — it may be corrupted.") from exc

    if len(text) < MIN_TEXT_LENGTH:
        raise ValueError(
            "No text could be extracted from that PDF. If it is a scan, upload a .tex version instead."
        )
    return text
