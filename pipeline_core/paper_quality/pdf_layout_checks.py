"""Auditable PDF production checks with deliberately bounded claims.

The module checks actual XeLaTeX logs and an actual PDF artifact.  It does not
claim pixel-level clipping detection, automatic pagination, or page-design
optimisation; those remain editorial review tasks.
"""

from __future__ import annotations

import hashlib
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any


class PDFLayoutCheckError(ValueError):
    """Raised when a compiled PDF or its log cannot satisfy hard checks."""


MANUAL_VISUAL_REVIEW_SCHEMA = "manual_pdf_visual_review_v2"
REQUIRED_VISUAL_CHECKS = {
    "black_background_absent",
    "cropping_absent",
    "garbled_text_absent",
    "unexpected_blank_page_absent",
    "abnormal_bottom_whitespace_absent",
    "figures_and_tables_readable",
    "figure_label_language_consistent",
    "formula_overflow_absent",
    "formula_numbering_and_reference_consistent",
    "paragraph_indent_consistent",
    "title_abstract_keywords_layout_consistent",
    "citation_marker_consistent",
    "internal_engineering_terms_absent",
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _report(*, status: str, warnings: list[dict[str, Any]] | None = None, **extra: Any) -> dict[str, Any]:
    return {"status": status, "warnings": warnings or [], **extra}


def _find_pdfinfo() -> str | None:
    """Find a real pdfinfo executable, including the local runtime wrapper layout."""

    discovered = shutil.which("pdfinfo")
    if discovered is None:
        return None
    command = Path(discovered)
    candidates: list[Path] = []
    if command.suffix.lower() in {".cmd", ".bat"}:
        try:
            candidates.append(command.parents[2] / "native" / "poppler" / "Library" / "bin" / "pdfinfo.exe")
        except IndexError:
            pass
    candidates.append(command)
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    return None


def check_xelatex_log(log_path: str | Path, *, strict_layout: bool = False) -> dict[str, Any]:
    path = Path(log_path)
    if not path.is_file():
        raise PDFLayoutCheckError(f"PDF_LOG_MISSING: {path}")
    text = path.read_text(encoding="utf-8", errors="replace")
    findings = {
        "overfull": len(re.findall(r"Overfull \\[hv]box", text, flags=re.IGNORECASE)),
        "underfull": len(re.findall(r"Underfull \\[hv]box", text, flags=re.IGNORECASE)),
        "missing_files": len(re.findall(r"(?:File|figure).*not found|I can't find file", text, flags=re.IGNORECASE)),
        "fatal_errors": len(re.findall(r"Fatal error|Emergency stop", text, flags=re.IGNORECASE)),
        "undefined_references": len(re.findall(r"(?:Reference|Citation).*(?:undefined|not found)", text, flags=re.IGNORECASE)),
    }
    hard = {key: value for key, value in findings.items() if key in {"missing_files", "fatal_errors", "undefined_references"} and value}
    if strict_layout and findings["overfull"]:
        hard["overfull"] = findings["overfull"]
    warnings = [
        {"code": "XELATEX_BOX_WARNING", "detail": f"{key}={value}"}
        for key, value in findings.items()
        if key in {"overfull", "underfull"} and value
    ]
    return _report(
        status="FAIL" if hard else ("WARNING" if warnings else "PASS"),
        warnings=warnings,
        hard_findings=hard,
        findings=findings,
        path=str(path),
    )


def check_pdf_artifact(pdf_path: str | Path, *, allow_blank_pages: set[int] | None = None) -> dict[str, Any]:
    """Check a real PDF is readable and has nonzero page geometry.

    Empty extracted text is only reported as a warning.  A page can contain a
    diagram or scanned figure, so this function does not misclassify it as a
    production failure merely from text extraction.
    """

    path = Path(pdf_path)
    if not path.is_file() or path.stat().st_size == 0:
        raise PDFLayoutCheckError(f"PDF_ARTIFACT_MISSING: {path}")
    reader = None
    try:
        from pypdf import PdfReader

        reader = PdfReader(str(path))
    except ImportError:
        try:
            from PyPDF2 import PdfReader  # type: ignore[no-redef]

            reader = PdfReader(str(path))
        except ImportError:
            reader = None
        except Exception as exc:
            raise PDFLayoutCheckError(f"PDF_ARTIFACT_UNREADABLE: {path}") from exc
    except Exception as exc:  # pypdf uses multiple parser exception types
        raise PDFLayoutCheckError(f"PDF_ARTIFACT_UNREADABLE: {path}") from exc

    if reader is None:
        pdfinfo = _find_pdfinfo()
        if pdfinfo is None:
            raise PDFLayoutCheckError("PDF_RUNTIME_UNAVAILABLE: pypdf_or_pdfinfo")
        completed = subprocess.run(
            [pdfinfo, str(path)], text=True, capture_output=True, check=False, timeout=30
        )
        page_match = re.search(r"^Pages:\s*(\d+)\s*$", completed.stdout, flags=re.MULTILINE)
        size_match = re.search(r"^Page size:\s*([0-9.]+)\s+x\s+([0-9.]+)", completed.stdout, flags=re.MULTILINE)
        if completed.returncode != 0 or page_match is None or size_match is None:
            raise PDFLayoutCheckError(f"PDF_ARTIFACT_UNREADABLE: {path}")
        if int(page_match.group(1)) <= 0:
            raise PDFLayoutCheckError("PDF_PAGE_COUNT_ZERO")
        if float(size_match.group(1)) <= 0 or float(size_match.group(2)) <= 0:
            raise PDFLayoutCheckError("PDF_PAGE_GEOMETRY_INVALID")
        return _report(
            status="PASS",
            warnings=[],
            page_count=int(page_match.group(1)),
            page_geometry_valid=True,
            textless_pages=[],
            path=str(path),
            reader_backend="pdfinfo",
            capability_boundary="no_pixel_level_clipping_or_auto_pagination_claim",
        )

    if not reader.pages:
        raise PDFLayoutCheckError("PDF_PAGE_COUNT_ZERO")
    allowed = allow_blank_pages or set()
    zero_geometry: list[int] = []
    textless_pages: list[int] = []
    for index, page in enumerate(reader.pages, 1):
        width = float(page.mediabox.width)
        height = float(page.mediabox.height)
        if width <= 0 or height <= 0:
            zero_geometry.append(index)
        try:
            text = page.extract_text() or ""
        except Exception:
            text = ""
        if not text.strip() and index not in allowed:
            textless_pages.append(index)
    if zero_geometry:
        raise PDFLayoutCheckError(f"PDF_PAGE_GEOMETRY_INVALID: {zero_geometry}")
    warnings = []
    if textless_pages:
        warnings.append({"code": "PDF_TEXTLESS_PAGE_REVIEW", "detail": str(textless_pages)})
    return _report(
        status="WARNING" if warnings else "PASS",
        warnings=warnings,
        page_count=len(reader.pages),
        page_geometry_valid=True,
        textless_pages=textless_pages,
        path=str(path),
        reader_backend="pypdf",
        capability_boundary="no_pixel_level_clipping_or_auto_pagination_claim",
    )


def check_page_layout_manifest(manifest: dict[str, Any]) -> dict[str, Any]:
    """Retain the former manifest audit as optional advisory evidence only."""

    pages = manifest.get("pages")
    if not isinstance(pages, list) or not pages:
        raise PDFLayoutCheckError("PDF_PAGE_MANIFEST_MISSING")
    warnings: list[dict[str, Any]] = []
    for index, page in enumerate(pages, 1):
        if not isinstance(page, dict):
            raise PDFLayoutCheckError(f"PDF_PAGE_ENTRY_INVALID: {index}")
        if float(page.get("text_coverage_ratio", 1.0)) <= 0.01 and not page.get("intentionally_blank", False):
            warnings.append({"code": "MANIFEST_POSSIBLE_BLANK_PAGE", "detail": str(page.get("page", index))})
    return _report(
        status="WARNING" if warnings else "PASS",
        warnings=warnings,
        page_count=len(pages),
        capability_boundary="MANUAL_MANIFEST_ADVISORY_ONLY",
    )


def validate_manual_visual_review(review: dict[str, Any], pdf_path: str | Path) -> dict[str, Any]:
    """Validate a full-page visual receipt bound to the exact compiled PDF.

    This does not automate visual judgement.  It makes that judgement explicit,
    complete over every page, and hash-bound so a later PDF rewrite invalidates
    the receipt instead of silently inheriting an old PASS.
    """

    path = Path(pdf_path)
    if review.get("schema_version") != MANUAL_VISUAL_REVIEW_SCHEMA:
        return _report(status="FAIL", reason="PDF_VISUAL_REVIEW_SCHEMA_INVALID")
    if str(review.get("status") or "").strip().upper() != "PASS":
        return _report(status="FAIL", reason="PDF_VISUAL_REVIEW_NOT_PASSED")
    if review.get("pdf_sha256") != _sha256(path):
        return _report(status="FAIL", reason="PDF_VISUAL_REVIEW_HASH_MISMATCH")

    artifact = check_pdf_artifact(path)
    page_count = artifact["page_count"]
    if review.get("page_count") != page_count:
        return _report(status="FAIL", reason="PDF_VISUAL_REVIEW_PAGE_COUNT_MISMATCH")
    reviewed_pages = review.get("reviewed_pages")
    if reviewed_pages != list(range(1, page_count + 1)):
        return _report(status="FAIL", reason="PDF_VISUAL_REVIEW_INCOMPLETE_PAGE_COVERAGE")

    checks = review.get("checks")
    if not isinstance(checks, dict) or any(checks.get(key) is not True for key in REQUIRED_VISUAL_CHECKS):
        return _report(status="FAIL", reason="PDF_VISUAL_REVIEW_REQUIRED_CHECK_MISSING")
    defects = review.get("defects")
    if not isinstance(defects, list) or defects:
        return _report(status="FAIL", reason="PDF_VISUAL_REVIEW_DEFECTS_OPEN")
    if not str(review.get("reviewer") or "").strip() or not str(review.get("reviewed_at") or "").strip():
        return _report(status="FAIL", reason="PDF_VISUAL_REVIEW_IDENTITY_MISSING")
    if review.get("human_review_required_before_gate4") is not True:
        return _report(status="FAIL", reason="PDF_GATE4_HUMAN_REVIEW_BOUNDARY_MISSING")
    return _report(
        status="PASS",
        page_count=page_count,
        reviewed_pages=reviewed_pages,
        pdf_sha256=review["pdf_sha256"],
        reviewer=review["reviewer"],
        capability_boundary="FULL_PAGE_VISUAL_RECEIPT_DOES_NOT_REPLACE_HUMAN_GATE4",
    )


def build_layout_report(
    log_paths: list[str | Path],
    *,
    pdf_path: str | Path | None = None,
    page_manifest: dict[str, Any] | None = None,
    manual_visual_review: dict[str, Any] | None = None,
    require_manual_visual_review: bool = False,
) -> dict[str, Any]:
    logs = [check_xelatex_log(path, strict_layout=require_manual_visual_review) for path in log_paths]
    pdf = check_pdf_artifact(pdf_path) if pdf_path is not None else {
        "status": "NOT_RUN",
        "warnings": [{"code": "PDF_ARTIFACT_NOT_SUPPLIED", "detail": "real PDF check not run"}],
    }
    manifest = check_page_layout_manifest(page_manifest) if page_manifest is not None else {
        "status": "NOT_RUN",
        "warnings": [],
    }
    if manual_visual_review is not None:
        if pdf_path is None:
            visual = _report(status="FAIL", reason="PDF_VISUAL_REVIEW_WITHOUT_PDF")
        else:
            visual = validate_manual_visual_review(manual_visual_review, pdf_path)
    elif require_manual_visual_review:
        visual = _report(status="FAIL", reason="PDF_VISUAL_REVIEW_REQUIRED")
    else:
        visual = _report(status="NOT_RUN")
    hard_fail = (
        any(item["status"] == "FAIL" for item in logs)
        or pdf.get("status") == "FAIL"
        or visual.get("status") == "FAIL"
    )
    warnings = [warning for item in [*logs, pdf, manifest, visual] for warning in item.get("warnings", [])]
    return _report(
        status="FAIL" if hard_fail else ("WARNING" if warnings else "PASS"),
        warnings=warnings,
        xelatex_logs=logs,
        pdf_artifact=pdf,
        page_layout_manifest=manifest,
        manual_visual_review=visual,
    )
