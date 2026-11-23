"""Lightweight evidence map writer for X5-F2.1.

This is an artifact inventory, not a full DAG and not a citation matrix.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

from x5_surgical_utils import ROOT, inventory_source_data, rel_path, sha256_path, utc_now, write_json


EVIDENCE_SCHEMA = "x5_f2_1_evidence_map_v1"


def _artifact_type(path: Path) -> str:
    name = path.name.lower()
    suffix = path.suffix.lower()
    parts = {part.lower() for part in path.parts}
    if name == "result_summary.json":
        return "result_summary"
    if name in {"validation_report.json", "validation_report.md", "unified_validation_report.json", "unified_validation_report.md"}:
        return "validation_report"
    if name in {"model_comparison.json", "model_comparison.md"}:
        return "comparison_report"
    if name == "manifest.json" and ("candidate_submit" in parts or "candidate_emergency" in parts):
        return "candidate_manifest"
    if suffix == ".csv" and "tables" in parts:
        return "table"
    if "figures" in parts:
        return "figure"
    if name in {"paper_number_candidates.json", "paper_number_candidates.csv"}:
        return "paper_number_candidates"
    return "artifact"


def _source_script(artifact_type: str) -> str:
    return {
        "result_summary": "run_case.py",
        "validation_report": "validation/model_aware_validator.py",
        "comparison_report": "validation/comparison.py",
        "candidate_manifest": "package_candidate.py",
        "table": "run_case.py",
        "figure": "run_case.py",
        "paper_number_candidates": "validation/paper_number_extractor.py",
    }.get(artifact_type, "run_case.py")


def _candidate_paths(root: Path, extra_artifacts: list[str] | None) -> list[Path]:
    paths: list[Path] = []
    for item in extra_artifacts or []:
        candidate = root / item
        if candidate.is_dir() and (candidate / "manifest.json").exists():
            paths.append(candidate / "manifest.json")
        elif candidate.is_file() and candidate.name == "manifest.json":
            paths.append(candidate)
    return paths


def collect_evidence_paths(
    *,
    root: Path,
    run_dir: Path,
    case_dir: Path,
    extra_artifacts: list[str] | None = None,
) -> list[Path]:
    candidates: list[Path] = [
        run_dir / "output" / "result_summary.json",
        run_dir / "result_summary.json",
        run_dir / "validation_report.json",
        run_dir / "validation_report.md",
        run_dir / "unified_validation_report.json",
        run_dir / "unified_validation_report.md",
        run_dir / "model_comparison.json",
        run_dir / "model_comparison.md",
        run_dir / "paper_number_candidates.json",
        run_dir / "paper_number_candidates.csv",
    ]
    candidates.extend(_candidate_paths(root, extra_artifacts))
    for base in (run_dir / "output", case_dir / "output"):
        if not base.exists():
            continue
        table_dir = base / "tables"
        figure_dir = base / "figures"
        if table_dir.exists():
            candidates.extend(sorted(table_dir.rglob("*.csv")))
        if figure_dir.exists():
            candidates.extend(sorted(path for path in figure_dir.rglob("*") if path.is_file()))
    unique: list[Path] = []
    seen: set[str] = set()
    for path in candidates:
        if not path.exists() or not path.is_file():
            continue
        key = str(path.resolve()).lower()
        if key in seen:
            continue
        seen.add(key)
        unique.append(path)
    return unique


def build_evidence_item(path: Path, *, run_id: str, case_dir: Path, notes: str = "") -> dict[str, Any]:
    artifact_type = _artifact_type(path)
    return {
        "evidence_id": str(uuid.uuid4()),
        "artifact_path": rel_path(path),
        "artifact_type": artifact_type,
        "source_script": _source_script(artifact_type),
        "run_id": run_id,
        "sha256": sha256_path(path),
        "created_at": utc_now(),
        "upstream_inputs": inventory_source_data(case_dir),
        "notes": notes or "Lightweight artifact evidence item; not a citation matrix.",
    }


def write_run_evidence_map(
    *,
    run_dir: Path,
    case_dir: Path,
    run_id: str,
    root: Path = ROOT,
    extra_artifacts: list[str] | None = None,
) -> dict[str, Any]:
    items = [
        build_evidence_item(path, run_id=run_id, case_dir=case_dir)
        for path in collect_evidence_paths(root=root, run_dir=run_dir, case_dir=case_dir, extra_artifacts=extra_artifacts)
    ]
    payload = {
        "schema_version": EVIDENCE_SCHEMA,
        "scope": "run",
        "run_id": run_id,
        "case_dir": rel_path(case_dir),
        "created_at": utc_now(),
        "item_count": len(items),
        "items": items,
        "limitations": [
            "This is a lightweight evidence map, not a full DAG.",
            "This does not generate citation_matrix or citation readiness.",
            "Fatal-run evidence remains in work_tmp and is not promoted to output.",
        ],
    }
    write_json(run_dir / "evidence_map.json", payload)
    return payload


def update_global_evidence_map(*, run_payload: dict[str, Any], root: Path = ROOT) -> dict[str, Any]:
    manifest_path = root / "manifests" / "evidence_map.json"
    existing = {}
    if manifest_path.exists():
        try:
            existing = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
        except json.JSONDecodeError:
            existing = {}
    items = existing.get("items") if isinstance(existing.get("items"), list) else []
    seen = {
        (item.get("run_id"), item.get("artifact_path"), item.get("sha256"))
        for item in items
        if isinstance(item, dict)
    }
    for item in run_payload.get("items", []):
        key = (item.get("run_id"), item.get("artifact_path"), item.get("sha256"))
        if key not in seen:
            items.append(item)
            seen.add(key)
    payload = {
        "schema_version": EVIDENCE_SCHEMA,
        "scope": "global_manifest",
        "created_at": existing.get("created_at") or utc_now(),
        "updated_at": utc_now(),
        "item_count": len(items),
        "items": items,
        "limitations": run_payload.get("limitations", []),
    }
    write_json(manifest_path, payload)
    return payload
