"""Source-level TeX number candidate extractor for human review.

This module does not inspect PDFs, does not OCR, and does not make semantic
claims. Every extracted number is a HUMAN_REVIEW item.
"""

from __future__ import annotations

import csv
import re
import uuid
from pathlib import Path
from typing import Any

from x5_surgical_utils import rel_path, utc_now, write_json


NUMBER_RE = re.compile(r"(?<![A-Za-z])[-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?%?")


def _tex_files(case_dir: Path) -> list[Path]:
    draft_dir = case_dir / "paper" / "draft"
    if not draft_dir.exists():
        return []
    return sorted(draft_dir.rglob("*.tex"))


def extract_paper_number_candidates(case_dir: Path) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for path in _tex_files(case_dir):
        text = path.read_text(encoding="utf-8", errors="replace")
        for line_no, line in enumerate(text.splitlines(), start=1):
            for match in NUMBER_RE.finditer(line):
                value = match.group(0)
                context_start = max(0, match.start() - 40)
                context_end = min(len(line), match.end() + 40)
                items.append(
                    {
                        "candidate_id": str(uuid.uuid4()),
                        "source_path": rel_path(path),
                        "line": line_no,
                        "value": value,
                        "context": line[context_start:context_end].strip(),
                        "classification": "HUMAN_REVIEW",
                        "fatal": False,
                        "created_at": utc_now(),
                        "notes": "Source-level TeX number candidate. Manual reconciliation required; not an OCR result.",
                    }
                )
    return items


def write_paper_number_candidates(case_dir: Path, output_dir: Path) -> dict[str, Any]:
    items = extract_paper_number_candidates(case_dir)
    payload = {
        "schema_version": "x5_f2_1_paper_number_candidates_v1",
        "created_at": utc_now(),
        "case_dir": rel_path(case_dir),
        "classification": "HUMAN_REVIEW",
        "fatal_count": 0,
        "candidate_count": len(items),
        "items": items,
        "limitations": [
            "Only paper/draft/*.tex source files are scanned.",
            "No PDF OCR is performed.",
            "No semantic consistency judgment is performed.",
        ],
    }
    write_json(output_dir / "paper_number_candidates.json", payload)
    csv_path = output_dir / "paper_number_candidates.csv"
    with csv_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=["candidate_id", "source_path", "line", "value", "context", "classification", "fatal", "notes"],
        )
        writer.writeheader()
        for item in items:
            writer.writerow({key: item.get(key) for key in writer.fieldnames})
    return payload
