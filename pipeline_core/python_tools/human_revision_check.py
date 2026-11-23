"""Optional HUMAN-REV-01 configuration and human revision record checker.

The checker is read-only unless ``--write-report`` is explicitly supplied. It
never creates or completes a human revision record and never creates
``final_submit/``.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


VALID_TIERS = frozenset({"training", "test", "homework", "selection", "formal"})
VALID_MODES = frozenset({"off", "brief", "full"})
RECOMMENDED_TIERS = frozenset({"selection", "formal"})
DEFAULT_RECORD_PATH = "human_review/human_revision_record.json"


def _read_json(path: Path) -> tuple[dict[str, Any] | None, str | None]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        return None, "missing"
    except (OSError, json.JSONDecodeError):
        return None, "unreadable"
    if not isinstance(payload, dict):
        return None, "unreadable"
    return payload, None


def _case_relative_path(case_dir: Path, value: Any) -> Path | None:
    if not isinstance(value, str) or not value.strip():
        return None
    candidate = Path(value.strip())
    if candidate.is_absolute() or ".." in candidate.parts:
        return None
    root = case_dir.resolve()
    resolved = (root / candidate).resolve()
    try:
        resolved.relative_to(root)
    except ValueError:
        return None
    return resolved


def _append_check(checks: list[dict[str, Any]], name: str, status: str, message: str) -> None:
    checks.append({"name": name, "status": status, "message": message})


def _record_is_complete(record: dict[str, Any]) -> bool:
    scope = record.get("scope")
    return (
        record.get("status") == "COMPLETED"
        and isinstance(record.get("reviewer_name"), str)
        and bool(record["reviewer_name"].strip())
        and isinstance(record.get("manual_confirmation"), str)
        and bool(record["manual_confirmation"].strip())
        and isinstance(scope, list)
        and bool(scope)
        and all(isinstance(item, str) and item.strip() for item in scope)
    )


def check_human_revision(case_dir: str | Path) -> dict[str, Any]:
    """Check optional human revision configuration without modifying the case."""

    case_dir = Path(case_dir)
    config, config_error = _read_json(case_dir / "case_config.json")
    checks: list[dict[str, Any]] = []
    blockers: list[str] = []
    warnings: list[str] = []
    manual_review_items: list[str] = []

    if config_error == "missing":
        config = {}
    elif config_error:
        config = {}
        warnings.append("HUMAN_REVISION_REQUIRED_MISSING")
        manual_review_items.append("case_config.json is unreadable; human revision requirements need manual review.")
        _append_check(checks, "case_config", "MANUAL_REVIEW", "HUMAN_REVISION_REQUIRED_MISSING")
    assert config is not None

    tier = str(config.get("submission_tier") or "training").strip().lower()
    mode = str(config.get("human_revision_mode") or "off").strip().lower()
    raw_required = config.get("human_revision_required", False)
    required = raw_required is True
    record_value = config.get("human_revision_record_path", DEFAULT_RECORD_PATH)
    record_path = _case_relative_path(case_dir, record_value)
    record_display = str(record_value).strip() if isinstance(record_value, str) else ""
    completed = False

    if tier not in VALID_TIERS:
        warnings.append("UNKNOWN_SUBMISSION_TIER")
        manual_review_items.append(f"UNKNOWN_SUBMISSION_TIER: '{tier}' requires manual review.")
        _append_check(checks, "submission_tier", "MANUAL_REVIEW", "UNKNOWN_SUBMISSION_TIER")
    if mode not in VALID_MODES or not isinstance(raw_required, bool):
        code = "HUMAN_REVISION_REQUIRED_MISSING"
        if required:
            blockers.append(code)
            _append_check(checks, "human_revision_config", "FAIL", code)
        else:
            warnings.append(code)
            manual_review_items.append("Human revision mode or required flag is invalid and needs manual review.")
            _append_check(checks, "human_revision_config", "MANUAL_REVIEW", code)

    if required:
        if record_path is None:
            blockers.append("HUMAN_REVISION_REQUIRED_MISSING")
            _append_check(checks, "human_revision_record", "FAIL", "HUMAN_REVISION_REQUIRED_MISSING")
        else:
            record, record_error = _read_json(record_path)
            if record_error == "missing":
                blockers.append("HUMAN_REVISION_RECORD_MISSING")
                _append_check(checks, "human_revision_record", "FAIL", "HUMAN_REVISION_RECORD_MISSING")
            elif record_error or record is None:
                blockers.append("HUMAN_REVISION_RECORD_UNREADABLE")
                _append_check(checks, "human_revision_record", "FAIL", "HUMAN_REVISION_RECORD_UNREADABLE")
            elif str(record.get("source") or "").strip().lower() != "human":
                blockers.append("HUMAN_REVISION_RECORD_NOT_HUMAN")
                _append_check(checks, "human_revision_record", "FAIL", "HUMAN_REVISION_RECORD_NOT_HUMAN")
            elif not _record_is_complete(record):
                blockers.append("HUMAN_REVISION_RECORD_INCOMPLETE")
                _append_check(checks, "human_revision_record", "FAIL", "HUMAN_REVISION_RECORD_INCOMPLETE")
            else:
                completed = True
                _append_check(checks, "human_revision_record", "PASS", "Human revision record is complete.")
    elif mode == "off" and tier not in RECOMMENDED_TIERS:
        warnings.append("HUMAN_REVISION_NOT_REQUESTED")
        _append_check(checks, "human_revision", "SKIPPED", "HUMAN_REVISION_NOT_REQUESTED")
    else:
        record, record_error = _read_json(record_path) if record_path is not None else (None, "missing")
        if record_error is None and record is not None and str(record.get("source") or "").strip().lower() == "human" and _record_is_complete(record):
            completed = True
            _append_check(checks, "human_revision_record", "PASS", "Human revision record is complete.")
        else:
            warnings.append("HUMAN_REVISION_RECOMMENDED")
            manual_review_items.append(
                f"HUMAN_REVISION_RECOMMENDED: tier={tier}, mode={mode}; no valid completed human record was found."
            )
            _append_check(checks, "human_revision", "MANUAL_REVIEW", "HUMAN_REVISION_RECOMMENDED")

    unique_blockers = list(dict.fromkeys(blockers))
    unique_warnings = list(dict.fromkeys(warnings))
    if unique_blockers:
        status = "BLOCKED"
    elif manual_review_items:
        status = "MANUAL_REVIEW_REQUIRED"
    elif completed:
        status = "PASS"
    else:
        status = "SKIPPED"

    return {
        "status": status,
        "checks": checks,
        "blockers": unique_blockers,
        "warnings": unique_warnings,
        "manual_review_items": manual_review_items,
        "human_revision": {
            "required": required,
            "mode": mode,
            "record_path": record_display,
            "completed": completed,
        },
        "final_submit_created": False,
    }


def write_human_revision_report(report: dict[str, Any], case_dir: str | Path) -> tuple[Path, Path]:
    """Write reports only when explicitly requested by the caller."""

    output_dir = Path(case_dir) / "output" / "validation"
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "human_revision_check_report.json"
    markdown_path = output_dir / "human_revision_check_report.md"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    lines = [
        "# Human Revision Check Report",
        "",
        f"- status: `{report['status']}`",
        f"- required: `{str(report['human_revision']['required']).lower()}`",
        f"- mode: `{report['human_revision']['mode']}`",
        f"- completed: `{str(report['human_revision']['completed']).lower()}`",
        "- final_submit_created: `false`",
        "",
        "## Blockers",
        "",
    ]
    lines.extend(f"- `{item}`" for item in report["blockers"]) if report["blockers"] else lines.append("- None")
    markdown_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return json_path, markdown_path


def main() -> int:
    parser = argparse.ArgumentParser(description="P2-6 optional human revision checker")
    parser.add_argument("--case-dir", required=True)
    parser.add_argument("--write-report", action="store_true")
    args = parser.parse_args()
    report = check_human_revision(args.case_dir)
    if args.write_report:
        write_human_revision_report(report, args.case_dir)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] in {"PASS", "SKIPPED", "MANUAL_REVIEW_REQUIRED"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
