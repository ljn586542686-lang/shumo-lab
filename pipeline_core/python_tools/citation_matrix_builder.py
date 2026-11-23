"""Create a citation matrix scaffold."""

from __future__ import annotations

from pathlib import Path


def ensure_citation_matrix(case_dir: str | Path) -> Path:
    target = Path(case_dir) / "literature" / "citation_matrix.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        target.write_text(
            "# Citation Matrix\n\n| Reference | Supports | Paper location | Direct support | Included in bibliography |\n|---|---|---|---|---|\n",
            encoding="utf-8",
        )
    return target
