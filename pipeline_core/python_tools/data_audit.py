"""Minimal data presence audit for a case."""

from __future__ import annotations

from pathlib import Path


def audit_data(case_dir: str | Path) -> dict:
    case_dir = Path(case_dir)
    raw = case_dir / "data" / "raw"
    files = [p for p in raw.rglob("*") if p.is_file()] if raw.exists() else []
    return {
        "raw_dir_exists": raw.exists(),
        "raw_file_count": len(files),
        "raw_files": [str(p.relative_to(case_dir)) for p in files],
        "status": "ok" if files else "missing_input",
    }
