"""Basic citation file presence checks."""

from __future__ import annotations

from pathlib import Path


def audit_citations(case_dir: str | Path) -> dict:
    case_dir = Path(case_dir)
    matrix = case_dir / "literature" / "citation_matrix.md"
    bib_candidates = [
        case_dir / "paper" / "references.bib",
        case_dir / "literature" / "selected_references.bib",
    ]
    return {
        "citation_matrix_exists": matrix.exists(),
        "bib_exists": any(p.exists() for p in bib_candidates),
        "status": "ok" if matrix.exists() and any(p.exists() for p in bib_candidates) else "warning",
    }
