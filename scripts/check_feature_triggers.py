"""Lightweight gate: verify FEATURES-matrix entries carry required evidence.

Scope: reads a features matrix YAML (see templates/case_FEATURES.yaml for the
per-case schema this is derived from) and checks, per case:

  - every required_features / top-level `features` entry marked
    SHOULD_AND_DONE has a non-empty evidence_paths list
  - every entry marked NOT_REQUIRED_AND_SKIPPED (or, in the per-case
    conditional_features schema, any status) has a non-empty skip_reason
  - every entry marked SHOULD_BUT_MISSING is reported but does not by
    itself HARD_BLOCK (it is a known-gap finding, not a schema violation)

Does not touch math_core, does not re-run any solver, does not modify any
report or result file. Read-only.

Exit code: 0 if no HARD_BLOCK, 1 if any HARD_BLOCK found.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

try:
    import yaml
except ModuleNotFoundError as exc:
    if exc.name != "yaml":
        raise
    yaml = None


PY_YAML_DEPENDENCY_ERROR = (
    "PyYAML is required for YAML feature trigger files. "
    "Install it with: pip install PyYAML"
)


def _require_pyyaml() -> Any:
    if yaml is None:
        raise RuntimeError(PY_YAML_DEPENDENCY_ERROR)
    return yaml

VALID_STATUSES = {
    "SHOULD_AND_DONE",
    "SHOULD_BUT_MISSING",
    "NOT_REQUIRED_AND_SKIPPED",
    "OVER_TRIGGERED",
    "EVIDENCE_UNAVAILABLE_ON_SOURCE_BRANCH",
}

STATUSES_REQUIRING_EVIDENCE = {"SHOULD_AND_DONE", "OVER_TRIGGERED"}
STATUSES_REQUIRING_SKIP_REASON = {"NOT_REQUIRED_AND_SKIPPED"}


class Finding:
    def __init__(self, case_id: str, feature_id: str, code: str, message: str, hard_block: bool):
        self.case_id = case_id
        self.feature_id = feature_id
        self.code = code
        self.message = message
        self.hard_block = hard_block

    def __str__(self) -> str:
        tag = "HARD_BLOCK" if self.hard_block else "NOTE"
        return f"[{tag}] {self.case_id} / {self.feature_id}: {self.code} -- {self.message}"


def check_case(case: dict) -> list[Finding]:
    findings: list[Finding] = []
    case_id = case.get("case_id", "<missing_case_id>")

    if "case_id" not in case:
        findings.append(Finding(case_id, "-", "MISSING_CASE_ID", "case entry has no case_id", True))

    features = case.get("features")
    if features is None:
        # per-case schema variant: required_features / conditional_features lists
        features = {}
        for group_name in ("required_features", "conditional_features"):
            for entry in case.get(group_name, []) or []:
                fid = entry.get("feature_id", "<missing_feature_id>")
                features[fid] = entry

    if not features:
        findings.append(Finding(case_id, "-", "NO_FEATURES_BLOCK", "case has no features/required_features data", True))
        return findings

    for feature_id, info in features.items():
        status = info.get("status", "")
        if status not in VALID_STATUSES:
            findings.append(Finding(case_id, feature_id, "INVALID_STATUS", f"status={status!r} not in allowed set", True))
            continue

        if status in STATUSES_REQUIRING_EVIDENCE:
            evidence = info.get("evidence_paths") or []
            if not evidence:
                findings.append(Finding(
                    case_id, feature_id, "MISSING_EVIDENCE",
                    f"status={status} but evidence_paths is empty", True,
                ))

        if status in STATUSES_REQUIRING_SKIP_REASON:
            skip_reason = (info.get("skip_reason") or "").strip()
            if not skip_reason:
                findings.append(Finding(
                    case_id, feature_id, "MISSING_SKIP_REASON",
                    f"status={status} but skip_reason is empty (silent skip not allowed)", True,
                ))

        if status == "SHOULD_BUT_MISSING":
            findings.append(Finding(
                case_id, feature_id, "KNOWN_GAP",
                "marked SHOULD_BUT_MISSING -- known gap, not a schema violation", False,
            ))

        if status == "EVIDENCE_UNAVAILABLE_ON_SOURCE_BRANCH":
            findings.append(Finding(
                case_id, feature_id, "EVIDENCE_UNAVAILABLE",
                "source branch evidence unavailable at audit time -- treat as open item, not PASS", False,
            ))

    return findings


def load_matrix(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as f:
        data = _require_pyyaml().safe_load(f)

    if isinstance(data, dict) and "problems" in data:
        return data["problems"]
    if isinstance(data, dict) and "case_id" in data:
        return [data]
    if isinstance(data, list):
        return data
    raise ValueError(f"unrecognized matrix structure in {path}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "matrix_paths", nargs="+", type=Path,
        help="one or more FEATURES matrix YAML files to check",
    )
    args = parser.parse_args()

    try:
        _require_pyyaml()
    except RuntimeError as exc:
        print(f"[DEPENDENCY_ERROR] {exc}", file=sys.stderr)
        return 2

    all_findings: list[Finding] = []
    for matrix_path in args.matrix_paths:
        if not matrix_path.exists():
            print(f"[HARD_BLOCK] file not found: {matrix_path}")
            all_findings.append(Finding(str(matrix_path), "-", "FILE_NOT_FOUND", "matrix file missing", True))
            continue
        cases = load_matrix(matrix_path)
        for case in cases:
            all_findings.extend(check_case(case))

    hard_blocks = [f for f in all_findings if f.hard_block]
    notes = [f for f in all_findings if not f.hard_block]

    for f in all_findings:
        print(f)

    print()
    print(f"Total findings: {len(all_findings)} ({len(hard_blocks)} HARD_BLOCK, {len(notes)} NOTE)")

    return 1 if hard_blocks else 0


if __name__ == "__main__":
    sys.exit(main())
