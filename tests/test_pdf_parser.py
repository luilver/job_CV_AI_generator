import io

import pytest
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from src.parsers.pdf_parser import extract_pdf_text, normalise_extracted_text


def _make_pdf(lines: list[str]) -> bytes:
    """Build a minimal single-page PDF with a text layer (no extra deps)."""
    content = "BT /F1 12 Tf 72 720 Td 14 TL\n"
    for line in lines:
        escaped = line.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")
        content += f"({escaped}) Tj T*\n"
    content += "ET"

    writer = PdfWriter()
    stream = DecodedStreamObject()
    stream.set_data(content.encode("latin-1"))
    stream_ref = writer._add_object(stream)  # noqa: SLF001
    font = DictionaryObject(
        {NameObject("/Type"): NameObject("/Font"), NameObject("/Subtype"): NameObject("/Type1")}
    )
    font_ref = writer._add_object(font)  # noqa: SLF001

    page = writer.add_blank_page(width=595, height=842)
    page[NameObject("/Contents")] = stream_ref
    page[NameObject("/Resources")] = DictionaryObject(
        {NameObject("/Font"): DictionaryObject({NameObject("/F1"): font_ref})}
    )

    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def test_normalise_extracted_text_dehyphenates_and_trims():
    cleaned = normalise_extracted_text("Experi-\nence\n\n\n\n   with   Python  \n")
    assert cleaned.startswith("Experience")
    assert "with Python" in cleaned
    assert "\n\n\n" not in cleaned


def test_extract_pdf_text_roundtrip():
    data = _make_pdf(
        [
            "Jane Doe - Senior Data Scientist",
            "Experience: built pipelines in Python and SQL for 5 years.",
            "Education: MSc Computer Science, University of Somewhere.",
        ]
    )
    text = extract_pdf_text(data)
    assert "Jane Doe" in text
    assert "Python" in text
    assert "\n\n\n" not in text


def test_extract_pdf_text_rejects_garbage():
    with pytest.raises(ValueError):
        extract_pdf_text(b"this is not a pdf at all")
