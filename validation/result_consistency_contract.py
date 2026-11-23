"""Canonical result-consistency checks for submitted-facing artifacts.

``output/result_summary.json`` is the only numeric source of truth.  This
module may use an adapter-produced summary only when its adapter report says
validation may continue.  It never reruns models, performs PDF OCR, changes
source artifacts, or creates ``final_submit/``.
"""

from __future__ import annotations

import argparse
import json
import math
import re
from pathlib import Path
from typing import Any


DEFAULT_MANIFEST_PATH = "output/validation/result_consistency_manifest.json"
DEFAULT_RESULT_PATH = "output/result_summary.json"
ADAPTED_RESULT_PATH = "output/adapted_result_summary.json"
ADAPTER_REPORT_PATH = "output/result_summary_adapter_report.json"

NUMBER_RE = re.compile(r"[-+]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?(?:[eE][-+]?\d+)?%?")
_MISSING = object()


def _read_json(path: Path) -> tuple[dict[str, Any] | None, str | None]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        return None, "missing"
    except (OSError, json.JSONDecodeError) as exc:
        return None, str(exc)
    if not isinstance(payload, dict):
        return None, "top level is not an object"
    return payload, None


def _case_relative_path(case_dir: Path, value: Any) -> Path | None:
    if not isinstance(value, str) or not value.strip():
        return None
    relative = Path(value)
    if relative.is_absolute() or ".." in relative.parts:
        return None
    return case_dir / relative


def _artifact_path(case_dir: Path, output_root: Path, value: Any) -> Path | None:
    """Resolve staged ``output/...`` artifacts while keeping case sources local."""

    if not isinstance(value, str) or not value.strip():
        return None
    relative = Path(value)
    if relative.is_absolute() or ".." in relative.parts:
        return None
    root = output_root if relative.parts and relative.parts[0] == "output" else case_dir
    return root / relative


def _json_pointer(payload: Any, pointer: Any) -> Any:
    if not isinstance(pointer, str) or not pointer.startswith("/"):
        return _MISSING
    current = payload
    for raw_part in pointer[1:].split("/"):
        part = raw_part.replace("~1", "/").replace("~0", "~")
        if isinstance(current, dict):
            if part not in current:
                return _MISSING
            current = current[part]
        elif isinstance(current, list):
            if not part.isdigit():
                return _MISSING
            index = int(part)
            if index >= len(current):
                return _MISSING
            current = current[index]
        else:
            return _MISSING
    return current


def _result_source(case_dir: Path) -> tuple[dict[str, Any] | None, str | None, str | None]:
    result_path = case_dir / DEFAULT_RESULT_PATH
    payload, error = _read_json(result_path)
    if error is None:
        return payload, DEFAULT_RESULT_PATH, None
    if error != "missing":
        return None, DEFAULT_RESULT_PATH, "RESULT_SUMMARY_UNREADABLE"

    adapter_report, adapter_error = _read_json(case_dir / ADAPTER_REPORT_PATH)
    if adapter_error is None and adapter_report and adapter_report.get("validation_continue_allowed") is True:
        adapted, adapted_error = _read_json(case_dir / ADAPTED_RESULT_PATH)
        if adapted_error is None:
            return adapted, ADAPTED_RESULT_PATH, None
        return None, ADAPTED_RESULT_PATH, "RESULT_SUMMARY_UNREADABLE"
    return None, DEFAULT_RESULT_PATH, "RESULT_SUMMARY_MISSING"


def _is_fatal_result(payload: dict[str, Any]) -> bool:
    validation_status = str(payload.get("validation_status") or "").lower()
    result_status = str(payload.get("status") or "").lower()
    fatal_items = payload.get("fatal_items")
    return "fatal" in validation_status or result_status == "fatal" or bool(fatal_items)


def _numeric_rendering_matches(text: str, value: float, precision: int, unit: str) -> tuple[bool, bool]:
    """Return ``(match, ambiguous)`` for a canonical numeric value in text."""

    candidates: list[float] = []
    percent_context = "%" in unit or "percent" in unit.lower() or "percentage" in unit.lower()
    for token in NUMBER_RE.findall(text):
        is_percent = token.endswith("%")
        try:
            candidate = float(token.rstrip("%").replace(",", ""))
        except ValueError:
            continue
        if is_percent:
            if not percent_context:
                continue
            candidate = candidate / 100.0 if abs(value) <= 1 else candidate
        candidates.append(candidate)

    if not candidates:
        return False, False
    rounded_value = round(value, precision)
    matches = [candidate for candidate in candidates if round(candidate, precision) == rounded_value]
    if matches:
        return True, len(matches) > 1
    return False, False


def _check_result_fatal(payload: dict[str, Any], checks: list[dict[str, Any]], blockers: list[str]) -> None:
    if _is_fatal_result(payload):
        blockers.append("RESULT_SUMMARY_FATAL_STATUS")
        checks.append({
            "name": "result_summary_not_fatal",
            "status": "FAIL",
            "message": "result_summary reports fatal validation status or fatal_items.",
        })
    else:
        checks.append({"name": "result_summary_not_fatal", "status": "PASS", "message": "no fatal result status found"})


def check_result_consistency(
    case_dir: str | Path,
    *,
    manifest_path: str | Path | None = None,
    output_root: str | Path | None = None,
) -> dict[str, Any]:
    """Check manifest-bound text and code references against the canonical result.

    The function is read-only.  A manifest is required because unanchored text
    scanning would be too permissive to act as a consistency contract.
    """

    case_dir = Path(case_dir)
    output_root = Path(output_root) if output_root is not None else case_dir
    checks: list[dict[str, Any]] = []
    blockers: list[str] = []
    warnings: list[str] = []
    manual_review_items: list[str] = []

    result_payload, canonical_path, result_error = _result_source(output_root)
    if result_error:
        blockers.append(result_error)
        checks.append({"name": "canonical_result_summary", "status": "FAIL", "message": result_error})
    else:
        checks.append({"name": "canonical_result_summary", "status": "PASS", "path": canonical_path})

    resolved_manifest_path = Path(manifest_path) if manifest_path is not None else case_dir / DEFAULT_MANIFEST_PATH
    if manifest_path is not None and not resolved_manifest_path.is_absolute():
        resolved_manifest_path = case_dir / resolved_manifest_path
    manifest, manifest_error = _read_json(resolved_manifest_path)
    if manifest_error == "missing":
        blockers.append("RESULT_CONTRACT_MANIFEST_MISSING")
        checks.append({"name": "manifest", "status": "FAIL", "message": "manifest is missing"})
    elif manifest_error:
        blockers.append("RESULT_CONTRACT_MANIFEST_UNREADABLE")
        checks.append({"name": "manifest", "status": "FAIL", "message": manifest_error})
    else:
        checks.append({"name": "manifest", "status": "PASS", "path": str(resolved_manifest_path)})

    if result_payload is not None:
        _check_result_fatal(result_payload, checks, blockers)
    if result_payload is None or manifest is None:
        return _build_result(
            checks, blockers, warnings, manual_review_items, canonical_path, str(resolved_manifest_path)
        )

    manifest_case_id = manifest.get("case_id")
    result_case_id = result_payload.get("case_id")
    if manifest_case_id and result_case_id and manifest_case_id != result_case_id:
        blockers.append("RESULT_CONTRACT_UNRESOLVED")
        checks.append({"name": "case_id_binding", "status": "FAIL", "message": "manifest case_id differs from result_summary"})

    reported_values = manifest.get("reported_values", [])
    text_sources = manifest.get("text_sources", [])
    anchors = manifest.get("anchors", [])
    producer_scripts = manifest.get("producer_scripts", [])
    code_output_paths = manifest.get("code_output_paths", [])
    appendix_code_paths = manifest.get("appendix_code_paths", [])
    uniqueness_claims = manifest.get("uniqueness_claims", [])
    collection_fields = {
        "reported_values": reported_values,
        "text_sources": text_sources,
        "anchors": anchors,
        "producer_scripts": producer_scripts,
        "code_output_paths": code_output_paths,
        "appendix_code_paths": appendix_code_paths,
        "uniqueness_claims": uniqueness_claims,
    }
    if any(not isinstance(value, list) for value in collection_fields.values()):
        blockers.append("RESULT_CONTRACT_UNRESOLVED")
        checks.append({"name": "manifest_collections", "status": "FAIL", "message": "manifest collection fields must be lists"})
        return _build_result(
            checks, blockers, warnings, manual_review_items, canonical_path, str(resolved_manifest_path)
        )
    if not reported_values:
        blockers.append("RESULT_CONTRACT_UNRESOLVED")
        checks.append({"name": "reported_values", "status": "FAIL", "message": "manifest must bind at least one canonical reported value"})
        return _build_result(
            checks, blockers, warnings, manual_review_items, canonical_path, str(resolved_manifest_path)
        )

    values_by_id: dict[str, dict[str, Any]] = {}
    for item in reported_values:
        if not isinstance(item, dict) or not str(item.get("value_id") or "").strip():
            blockers.append("RESULT_CONTRACT_UNRESOLVED")
            continue
        value_id = str(item["value_id"])
        pointer_value = _json_pointer(result_payload, item.get("json_pointer"))
        if pointer_value is _MISSING:
            blockers.append("REPORTED_VALUE_POINTER_INVALID")
            checks.append({"name": f"reported_value:{value_id}", "status": "FAIL", "message": "json_pointer is unresolved"})
            continue
        if isinstance(pointer_value, bool) or not isinstance(pointer_value, (int, float)) or not math.isfinite(float(pointer_value)):
            blockers.append("RESULT_CONTRACT_UNRESOLVED")
            checks.append({"name": f"reported_value:{value_id}", "status": "FAIL", "message": "json_pointer must resolve to a finite numeric value"})
            continue
        precision = item.get("display_precision", 2)
        if not isinstance(precision, int) or precision < 0 or precision > 12:
            blockers.append("RESULT_CONTRACT_UNRESOLVED")
            checks.append({"name": f"reported_value:{value_id}", "status": "FAIL", "message": "display_precision must be an integer from 0 to 12"})
            continue
        values_by_id[value_id] = {
            "value": float(pointer_value),
            "precision": precision,
            "unit": str(item.get("unit") or ""),
        }
        checks.append({"name": f"reported_value:{value_id}", "status": "PASS", "path": item.get("json_pointer")})

    source_texts: dict[str, str] = {}
    unsupported_source_ids: set[str] = set()
    for item in text_sources:
        if not isinstance(item, dict) or not str(item.get("source_id") or "").strip():
            blockers.append("RESULT_CONTRACT_UNRESOLVED")
            continue
        source_id = str(item["source_id"])
        source_path = _artifact_path(case_dir, output_root, item.get("path"))
        if source_path is None or not source_path.is_file():
            blockers.append("SOURCE_TEXT_MISSING")
            checks.append({"name": f"text_source:{source_id}", "status": "FAIL", "message": "text source is missing"})
            continue
        source_kind = str(item.get("kind") or source_path.suffix.lstrip(".")).lower()
        if source_kind not in {"tex", "md", "txt", "text"}:
            unsupported_source_ids.add(source_id)
            checks.append({"name": f"text_source:{source_id}", "status": "MANUAL_REVIEW", "path": str(source_path)})
            manual_review_items.append(
                f"text source '{source_id}' has unsupported kind '{source_kind}'; no PDF OCR or binary document numeric scan was performed."
            )
            continue
        try:
            source_texts[source_id] = source_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            blockers.append("SOURCE_TEXT_MISSING")
            checks.append({"name": f"text_source:{source_id}", "status": "FAIL", "message": "text source is unreadable"})
            continue
        checks.append({"name": f"text_source:{source_id}", "status": "PASS", "path": str(source_path)})

    for anchor in anchors:
        if not isinstance(anchor, dict):
            blockers.append("RESULT_CONTRACT_UNRESOLVED")
            continue
        if not anchor.get("required", False):
            continue
        anchor_id = str(anchor.get("anchor_id") or "(unnamed)")
        value_id = str(anchor.get("value_id") or "")
        source_id = str(anchor.get("source_id") or "")
        if value_id not in values_by_id:
            blockers.append("REPORTED_VALUE_REFERENCE_MISSING")
            checks.append({"name": f"anchor:{anchor_id}", "status": "FAIL", "message": "anchor value_id is missing"})
            continue
        if source_id not in source_texts:
            if source_id in unsupported_source_ids:
                checks.append({"name": f"anchor:{anchor_id}", "status": "MANUAL_REVIEW", "message": "source kind is not machine-readable"})
                continue
            blockers.append("SOURCE_TEXT_MISSING")
            checks.append({"name": f"anchor:{anchor_id}", "status": "FAIL", "message": "anchor source_id is missing"})
            continue
        value = values_by_id[value_id]
        matches, ambiguous = _numeric_rendering_matches(source_texts[source_id], value["value"], value["precision"], value["unit"])
        if not matches:
            numeric_tokens = NUMBER_RE.findall(source_texts[source_id])
            code = "ANCHOR_VALUE_NOT_FOUND_IN_TEXT" if not numeric_tokens else "RESULT_VALUE_MISMATCH"
            blockers.append(code)
            checks.append({"name": f"anchor:{anchor_id}", "status": "FAIL", "message": code})
        else:
            checks.append({"name": f"anchor:{anchor_id}", "status": "PASS", "message": "canonical rendering found"})
            if ambiguous:
                manual_review_items.append(f"anchor '{anchor_id}' has multiple matching numeric renderings; confirm context manually.")

    if not anchors:
        manual_review_items.append("No submitted-facing anchors were declared; confirm whether result values are intentionally not shown in paper text.")

    for script in producer_scripts:
        path = _artifact_path(case_dir, output_root, script)
        if path is None or not path.is_file():
            blockers.append("CODE_FILE_MISSING")
            checks.append({"name": "producer_script", "status": "FAIL", "path": str(script)})
        else:
            checks.append({"name": "producer_script", "status": "PASS", "path": str(path)})

    for output_path in code_output_paths:
        path = _artifact_path(case_dir, output_root, output_path)
        if path is None or not path.is_file() or path.stat().st_size <= 0:
            blockers.append("CODE_OUTPUT_BINDING_MISSING")
            checks.append({"name": "code_output", "status": "FAIL", "path": str(output_path)})
        else:
            checks.append({"name": "code_output", "status": "PASS", "path": str(path)})

    for appendix_path in appendix_code_paths:
        path = _artifact_path(case_dir, output_root, appendix_path)
        if path is None or not path.is_file():
            blockers.append("APPENDIX_CODE_SYNC_MISSING")
            checks.append({"name": "appendix_code", "status": "FAIL", "path": str(appendix_path)})
        else:
            checks.append({"name": "appendix_code", "status": "PASS", "path": str(path)})

    for claim in uniqueness_claims:
        if not isinstance(claim, dict):
            blockers.append("RESULT_CONTRACT_UNRESOLVED")
            continue
        if not claim.get("required", False):
            continue
        evidence_path = _artifact_path(case_dir, output_root, claim.get("verification_evidence_path"))
        if evidence_path is None or not evidence_path.is_file() or evidence_path.stat().st_size <= 0:
            blockers.append("UNIQUE_OPTIMUM_EVIDENCE_MISSING")
            checks.append({"name": f"uniqueness:{claim.get('claim_id', '(unnamed)')}", "status": "FAIL"})
        else:
            checks.append({"name": f"uniqueness:{claim.get('claim_id', '(unnamed)')}", "status": "MANUAL_REVIEW"})
            manual_review_items.append("Uniqueness/equivalent-optimum evidence exists but its mathematical validity requires manual review.")

    if manual_review_items:
        warnings.append("RESULT_CONSISTENCY_MANUAL_REVIEW_REQUIRED")
    return _build_result(
        checks, blockers, warnings, manual_review_items, canonical_path, str(resolved_manifest_path)
    )


def _build_result(
    checks: list[dict[str, Any]],
    blockers: list[str],
    warnings: list[str],
    manual_review_items: list[str],
    canonical_result_summary: str | None,
    manifest_path: str,
) -> dict[str, Any]:
    unique_blockers = list(dict.fromkeys(blockers))
    status = "BLOCKED" if unique_blockers else ("MANUAL_REVIEW_REQUIRED" if manual_review_items else "PASS")
    return {
        "status": status,
        "checks": checks,
        "blockers": unique_blockers,
        "warnings": list(dict.fromkeys(warnings)),
        "manual_review_items": manual_review_items,
        "canonical_result_summary": canonical_result_summary,
        "manifest_path": manifest_path,
        "final_submit_created": False,
        "limitations": [
            "The checker never reruns a model or modifies source artifacts.",
            "PDF OCR numeric comparison is intentionally not performed.",
            "Mathematical proof and claim semantics remain manual review items.",
        ],
    }


def write_result_consistency_report(report: dict[str, Any], case_dir: str | Path) -> tuple[Path, Path]:
    """Write an explicit report only when the caller opts in."""

    output_dir = Path(case_dir) / "output" / "validation"
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "result_consistency_report.json"
    markdown_path = output_dir / "result_consistency_report.md"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    lines = [
        "# Result Consistency Report",
        "",
        f"- status: `{report['status']}`",
        f"- canonical_result_summary: `{report['canonical_result_summary']}`",
        f"- final_submit_created: `false`",
        "",
        "## Blockers",
        "",
    ]
    if report["blockers"]:
        lines.extend(f"- `{item}`" for item in report["blockers"])
    else:
        lines.append("- None")
    lines.extend(["", "## Manual Review Items", ""])
    if report["manual_review_items"]:
        lines.extend(f"- {item}" for item in report["manual_review_items"])
    else:
        lines.append("- None")
    markdown_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return json_path, markdown_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only result consistency contract checker")
    parser.add_argument("--case-dir", required=True)
    parser.add_argument("--manifest-path", default=None)
    parser.add_argument("--write-report", action="store_true")
    args = parser.parse_args()

    report = check_result_consistency(args.case_dir, manifest_path=args.manifest_path)
    if args.write_report:
        write_result_consistency_report(report, args.case_dir)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] in {"PASS", "MANUAL_REVIEW_REQUIRED"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
