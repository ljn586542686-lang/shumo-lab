"""Build an empty evidence map scaffold."""

from __future__ import annotations

from pathlib import Path


HEADER = "| Paper location | Claim/value | Source code | Source output | Source table | Source figure | Literature | Human decision | Risk |\n|---|---|---|---|---|---|---|---|---|\n"


def build_empty_evidence_map(case_dir: str | Path) -> Path:
    case_dir = Path(case_dir)
    target = case_dir / "evidence_map.md"
    if not target.exists():
        target.write_text("# Evidence Map\n\n" + HEADER, encoding="utf-8")
    return target
