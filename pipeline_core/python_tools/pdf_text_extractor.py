"""Extract problem statement text when a local PDF parser is available."""

from __future__ import annotations

from pathlib import Path


def extract_pdf_text(pdf_path: str | Path) -> str:
    path = Path(pdf_path)
    if not path.exists():
        raise FileNotFoundError(path)
    try:
        from pypdf import PdfReader
    except Exception as exc:  # pragma: no cover - depends on local environment
        raise RuntimeError("pypdf is required for PDF extraction.") from exc
    reader = PdfReader(str(path))
    return "\n".join(page.extract_text() or "" for page in reader.pages)
