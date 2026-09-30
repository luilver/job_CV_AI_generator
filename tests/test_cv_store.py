import sys
import tempfile
from pathlib import Path

import pytest

import src.auth.db as db
from src.auth.auth import signup
from src.profile.cv_store import delete_cv, describe_cv, get_cv, save_cv

TEX_CV = r"""
\documentclass{article}
\begin{document}
\section{Experience}
Senior data scientist with \textbf{8 years} of Python and SQL experience, building
production data pipelines and machine-learning models for retail and logistics teams.
Led a team of four engineers and cut report latency by 60 percent.
\section{Education}
MSc Computer Science, University of Somewhere. BSc Statistics.
\end{document}
"""


@pytest.fixture()
def user_id(monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", Path(tempfile.mkdtemp()) / "app.db")
    signup("store@test.dev", "password123")
    return 1


def test_detect_filetype_rejects_other_extensions():
    with pytest.raises(ValueError):
        save_cv(1, "cv.docx", b"whatever")


def test_save_and_get_tex_cv(user_id):
    summary = save_cv(user_id, "cv.tex", TEX_CV.encode("utf-8"))
    assert summary["filetype"] == "tex"
    assert "Senior data scientist" in summary["text"]

    record = get_cv(user_id)
    assert record["filename"] == "cv.tex"
    assert "\\documentclass" in record["raw"]
    assert "\\begin{document}" not in record["text"]
    assert "\\textbf" not in record["text"]
    assert "Experience" in record["text"]
    assert record["data"] == TEX_CV.encode("utf-8")
    assert "cv.tex" in describe_cv(record)


def test_saving_twice_replaces_the_previous_cv(user_id):
    save_cv(user_id, "cv.tex", TEX_CV.encode("utf-8"))
    save_cv(user_id, "newer.tex", TEX_CV.replace("8 years", "9 years").encode("utf-8"))
    record = get_cv(user_id)
    assert record["filename"] == "newer.tex"
    assert "9 years" in record["text"]


def test_get_cv_returns_none_when_empty(user_id):
    assert get_cv(user_id) is None
    assert describe_cv(None) == "No CV saved yet"


def test_pdf_cv_exposes_text_as_raw_not_binary(user_id):
    from tests.test_pdf_parser import _make_pdf

    pdf = _make_pdf(
        [
            "Jane Doe - Senior Data Scientist",
            "Experience: eight years of Python and SQL pipelines for retail teams.",
            "Led four engineers and cut report latency by sixty percent.",
        ]
    )
    save_cv(user_id, "jane.pdf", pdf)
    record = get_cv(user_id)
    assert record["filetype"] == "pdf"
    assert record["raw"].startswith("Jane Doe")
    assert record["data"] == pdf
    assert record["data"][:4] == b"%PDF"


def test_delete_cv(user_id):
    save_cv(user_id, "cv.tex", TEX_CV.encode("utf-8"))
    assert delete_cv(user_id) is True
    assert get_cv(user_id) is None
    assert delete_cv(user_id) is False


def test_rejects_oversized_upload(user_id):
    from src.profile.cv_store import MAX_CV_BYTES

    with pytest.raises(ValueError):
        save_cv(user_id, "huge.tex", b"a" * (MAX_CV_BYTES + 1))
