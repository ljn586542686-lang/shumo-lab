"""Read-only hard-error preflight for explicitly submitted-facing paper text.

The checker is intentionally narrow: it catches low-level submission hazards
without rewriting prose, judging mathematical proofs, running OCR, or scanning
internal workflow files that were not explicitly declared as paper sources.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any


DEFAULT_CONFIG_PATH = "paper/paper_preflight_config.json"
TEXT_KINDS = {"tex", "md", "txt", "text"}
SUBMISSION_TIERS = frozenset({"training", "test", "homework", "selection", "formal"})
LOWER_RISK_TIERS = frozenset({"training", "test", "homework"})

HIGH_CONFIDENCE_TERMS = (
    "claude",
    "codex",
    "chatgpt",
    "self_verdict",
    "hard_block",
    "writer prompt",
    "final_submit_allowed",
    "internal workflow",
    "status token",
    "p2-1",
    "p2-2",
    "p2-3",
    "pipeline stabilization",
    "run_case.py",
    "agents.md",
    "git status",
    "commit hash",
)
AMBIGUOUS_TERMS = ("ai", "gate", "red-team", "prompt")
DECEPTIVE_TERMS = (
    "anti-plagiarism bait",
    "anti plagiarism bait",
    "detector trap",
    "deceive detector",
    "bypass ai detector",
    "ai detector bypass",
    "invisible watermark",
    "anti-detection",
)
VISIBLE_PROVENANCE_TERMS = ("provenance fingerprint", "passive fingerprint", "watermark marker")
CORRECT_IDENTITY_NAME = "\u5218\u6bc5\u7136"
KNOWN_WRONG_IDENTITY_NAMES = ("\u5218\u806a\u7136", "\u5218\u7131\u7136")


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


def _source_spec(item: Any) -> tuple[str | None, str]:
    if isinstance(item, str):
        path = item
        return path, Path(path).suffix.lstrip(".").lower()
    if isinstance(item, dict):
        path = item.get("path")
        kind = str(item.get("kind") or Path(str(path or "")).suffix.lstrip(".")).lower()
        return path if isinstance(path, str) else None, kind
    return None, ""


def _extract_title(text: str, kind: str) -> str | None:
    if kind == "tex":
        match = re.search(r"\\title\s*\{([^}]*)\}", text, re.DOTALL)
        return match.group(1).strip() if match else None
    if kind == "md":
        for line in text.splitlines():
            if line.startswith("# "):
                return line[2:].strip()
    match = re.search(r"^title\s*[:=]\s*(.+)$", text, re.IGNORECASE | re.MULTILINE)
    return match.group(1).strip() if match else None


def _has_any_anchor(texts: list[str], anchors: Any) -> bool:
    if not isinstance(anchors, list):
        return False
    candidates = [str(item).strip().lower() for item in anchors if isinstance(item, str) and item.strip()]
    return any(anchor in text.lower() for anchor in candidates for text in texts)


def _identity_fields(text: str) -> list[str]:
    values: list[str] = []
    author = re.findall(r"\\author\s*\{([^}]*)\}", text, re.DOTALL)
    values.extend(item.strip() for item in author if item.strip())
    label_pattern = r"(?:\u4f5c\u8005|author|\u59d3\u540d|name)\s*[:\uff1a=]\s*([^\r\n]+)"
    values.extend(item.strip() for item in re.findall(label_pattern, text, re.IGNORECASE) if item.strip())
    return values


def _class_fields(text: str) -> list[str]:
    pattern = r"(?:\u73ed\u7ea7|class)\s*[:\uff1a=]\s*([^\r\n]+)"
    return [item.strip() for item in re.findall(pattern, text, re.IGNORECASE) if item.strip()]


def _normalize_submission_tier(value: Any) -> tuple[str, bool]:
    """Return a normalized tier and whether it is a known tier.

    A missing tier intentionally defaults to ``training``.  An unknown tier is
    retained for reporting and requires manual review rather than silently
    receiving either a training or formal policy.
    """

    if value is None or not str(value).strip():
        return "training", True
    normalized = str(value).strip().lower()
    return normalized, normalized in SUBMISSION_TIERS


def _record_tier_sensitive_missing(
    *,
    code: str,
    message: str,
    check_name: str,
    tier: str,
    tier_known: bool,
    checks: list[dict[str, Any]],
    blockers: list[str],
    warnings: list[str],
    manual_review_items: list[str],
) -> None:
    """Apply tier severity only to missing submitted-facing explanations."""

    if tier in LOWER_RISK_TIERS or not tier_known:
        warnings.append(code)
        manual_review_items.append(f"{code}: {message} (submission_tier={tier})")
        checks.append({"name": check_name, "status": "MANUAL_REVIEW", "message": code})
        return
    blockers.append(code)
    checks.append({"name": check_name, "status": "FAIL", "message": code})


def _build_result(
    checks: list[dict[str, Any]],
    blockers: list[str],
    warnings: list[str],
    manual_review_items: list[str],
    scanned_sources: list[str],
    skipped_sources: list[dict[str, str]],
    submission_tier: str = "training",
) -> dict[str, Any]:
    unique_blockers = list(dict.fromkeys(blockers))
    if unique_blockers:
        status = "BLOCKED"
    elif manual_review_items:
        status = "MANUAL_REVIEW_REQUIRED"
    else:
        status = "PASS"
    if manual_review_items and "PAPER_PREFLIGHT_MANUAL_REVIEW_REQUIRED" not in warnings:
        warnings.append("PAPER_PREFLIGHT_MANUAL_REVIEW_REQUIRED")
    return {
        "status": status,
        "checks": checks,
        "blockers": unique_blockers,
        "warnings": list(dict.fromkeys(warnings)),
        "manual_review_items": manual_review_items,
        "submission_tier": submission_tier,
        "scanned_sources": scanned_sources,
        "skipped_sources": skipped_sources,
        "final_submit_created": False,
        "limitations": [
            "Only explicitly declared submitted-facing sources are scanned.",
            "No PDF OCR or numeric comparison is performed.",
            "Style, proof validity, and mathematical semantics require manual review.",
        ],
    }


def check_paper_hard_errors(
    case_dir: str | Path,
    *,
    config_path: str | Path | None = None,
) -> dict[str, Any]:
    """Run a read-only hard-error preflight for one case."""

    case_dir = Path(case_dir)
    resolved_config = Path(config_path) if config_path is not None else case_dir / DEFAULT_CONFIG_PATH
    if config_path is not None and not resolved_config.is_absolute():
        resolved_config = case_dir / resolved_config
    config, config_error = _read_json(resolved_config)
    checks: list[dict[str, Any]] = []
    blockers: list[str] = []
    warnings: list[str] = []
    manual_review_items: list[str] = []
    scanned_sources: list[str] = []
    skipped_sources: list[dict[str, str]] = []

    if config_error == "missing":
        warnings.append("PAPER_PREFLIGHT_CONFIG_MISSING")
        manual_review_items.append("PAPER_PREFLIGHT_CONFIG_MISSING: configure submitted-facing paper sources before preflight.")
        return _build_result(checks, blockers, warnings, manual_review_items, scanned_sources, skipped_sources)
    if config_error:
        warnings.append("PAPER_PREFLIGHT_CONFIG_UNREADABLE")
        manual_review_items.append("PAPER_PREFLIGHT_CONFIG_UNREADABLE: repair the configuration before preflight.")
        return _build_result(checks, blockers, warnings, manual_review_items, scanned_sources, skipped_sources)
    assert config is not None
    submission_tier, tier_known = _normalize_submission_tier(config.get("submission_tier"))
    if not tier_known:
        warnings.append("UNKNOWN_SUBMISSION_TIER")
        manual_review_items.append(
            f"UNKNOWN_SUBMISSION_TIER: '{submission_tier}' requires manual review."
        )

    if config.get("enabled") is not True:
        manual_review_items.append("Paper preflight is disabled by configuration.")
        return _build_result(
            checks, blockers, warnings, manual_review_items, scanned_sources, skipped_sources, submission_tier
        )

    declared_sources = config.get("submitted_text_sources", [])
    if not isinstance(declared_sources, list) or not declared_sources:
        manual_review_items.append("No submitted-facing text sources were declared.")
        return _build_result(
            checks, blockers, warnings, manual_review_items, scanned_sources, skipped_sources, submission_tier
        )

    text_sources: list[tuple[str, str, str]] = []
    structured_texts: list[str] = []
    all_scannable_texts: list[str] = []
    for item in declared_sources:
        relative_path, kind = _source_spec(item)
        source_path = _case_relative_path(case_dir, relative_path)
        if source_path is None or not source_path.is_file():
            blockers.append("SUBMITTED_TEXT_SOURCE_MISSING")
            checks.append({"name": "submitted_text_source", "status": "FAIL", "path": str(relative_path)})
            continue
        if kind == "docx":
            warnings.append("DOCX_TEXT_EXTRACTION_NOT_AVAILABLE")
            manual_review_items.append(f"DOCX_TEXT_EXTRACTION_NOT_AVAILABLE: {relative_path}")
            skipped_sources.append({"path": relative_path or "", "reason": "DOCX_TEXT_EXTRACTION_NOT_AVAILABLE"})
            continue
        if kind == "pdf":
            try:
                from pipeline_core.python_tools.pdf_text_extractor import extract_pdf_text

                text = extract_pdf_text(source_path)
            except Exception:
                warnings.append("PDF_TEXT_EXTRACTION_NOT_AVAILABLE")
                manual_review_items.append(f"PDF_TEXT_EXTRACTION_NOT_AVAILABLE: {relative_path}")
                skipped_sources.append({"path": relative_path or "", "reason": "PDF_TEXT_EXTRACTION_NOT_AVAILABLE"})
                continue
            if not text.strip():
                warnings.append("PDF_TEXT_EXTRACTION_NOT_AVAILABLE")
                manual_review_items.append(f"PDF_TEXT_EXTRACTION_NOT_AVAILABLE: {relative_path}")
                skipped_sources.append({"path": relative_path or "", "reason": "PDF_TEXT_EXTRACTION_NOT_AVAILABLE"})
                continue
            scanned_sources.append(relative_path or "")
            all_scannable_texts.append(text)
            checks.append({"name": "submitted_text_source", "status": "PASS", "path": relative_path, "mode": "pdf_forbidden_terms_only"})
            continue
        if kind not in TEXT_KINDS:
            warnings.append("SUBMITTED_TEXT_SOURCE_UNREADABLE")
            manual_review_items.append(f"Unsupported submitted text kind '{kind}' for {relative_path}.")
            skipped_sources.append({"path": relative_path or "", "reason": "SUBMITTED_TEXT_SOURCE_UNREADABLE"})
            continue
        try:
            text = source_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            blockers.append("SUBMITTED_TEXT_SOURCE_UNREADABLE")
            checks.append({"name": "submitted_text_source", "status": "FAIL", "path": relative_path})
            continue
        scanned_sources.append(relative_path or "")
        text_sources.append((relative_path or "", kind, text))
        structured_texts.append(text)
        all_scannable_texts.append(text)
        checks.append({"name": "submitted_text_source", "status": "PASS", "path": relative_path})

    combined_text = "\n".join(all_scannable_texts)
    combined_lower = combined_text.lower()

    forbidden_titles = config.get("forbidden_title_terms", [])
    if isinstance(forbidden_titles, list):
        titles = [title for _path, kind, text in text_sources if (title := _extract_title(text, kind))]
        for title in titles:
            title_lower = title.lower()
            if any(str(term).strip().lower() in title_lower for term in forbidden_titles if str(term).strip()):
                blockers.append("TITLE_FORBIDDEN_EDITION_WORDING")
                checks.append({"name": "title", "status": "FAIL", "message": "forbidden title wording found"})
                break

    expected_identity = config.get("expected_identity", {})
    if isinstance(expected_identity, dict) and expected_identity.get("enabled") is True:
        expected_name = str(expected_identity.get("name") or "").strip()
        expected_class = str(expected_identity.get("class") or "").strip()
        identities = [value for text in structured_texts for value in _identity_fields(text)]
        wrong_name_present = expected_name == CORRECT_IDENTITY_NAME and any(name in combined_text for name in KNOWN_WRONG_IDENTITY_NAMES)
        if wrong_name_present or (identities and (not expected_name or any(expected_name not in value for value in identities))):
            blockers.append("IDENTITY_NAME_MISMATCH")
            checks.append({"name": "identity_name", "status": "FAIL"})
        elif identities:
            checks.append({"name": "identity_name", "status": "PASS"})
        if expected_class:
            classes = [value for text in structured_texts for value in _class_fields(text)]
            if classes and any(expected_class not in value for value in classes):
                blockers.append("IDENTITY_CLASS_MISMATCH")
                checks.append({"name": "identity_class", "status": "FAIL"})
            elif classes:
                checks.append({"name": "identity_class", "status": "PASS"})

    for term in HIGH_CONFIDENCE_TERMS:
        if term in combined_lower:
            blockers.append("INTERNAL_WORKFLOW_TERM_FOUND")
            checks.append({"name": "internal_term", "status": "FAIL", "term": term})
    if re.search(r"\bcommit\s+[0-9a-f]{7,40}\b", combined_lower):
        blockers.append("INTERNAL_WORKFLOW_TERM_FOUND")
        checks.append({"name": "internal_term", "status": "FAIL", "term": "commit hash"})
    for term in AMBIGUOUS_TERMS:
        if re.search(rf"\b{re.escape(term)}\b", combined_text, re.IGNORECASE):
            warnings.append("INTERNAL_WORKFLOW_TERM_AMBIGUOUS")
            manual_review_items.append(f"INTERNAL_WORKFLOW_TERM_AMBIGUOUS: inspect context for '{term}'.")

    if config.get("objective_source_required") is True:
        if not structured_texts:
            manual_review_items.append("Objective-source anchors require a machine-readable text source.")
        elif not _has_any_anchor(structured_texts, config.get("objective_source_anchors", [])):
            _record_tier_sensitive_missing(
                code="OBJECTIVE_SOURCE_EXPLANATION_MISSING",
                message="objective-source explanation anchor is missing",
                check_name="objective_source",
                tier=submission_tier,
                tier_known=tier_known,
                checks=checks,
                blockers=blockers,
                warnings=warnings,
                manual_review_items=manual_review_items,
            )
        else:
            checks.append({"name": "objective_source", "status": "PASS"})

    if config.get("constraint_source_required") is True:
        if not structured_texts:
            manual_review_items.append("Constraint-source anchors require a machine-readable text source.")
        elif not _has_any_anchor(structured_texts, config.get("constraint_source_anchors", [])):
            _record_tier_sensitive_missing(
                code="CONSTRAINT_SOURCE_EXPLANATION_MISSING",
                message="constraint-source explanation anchor is missing",
                check_name="constraint_source",
                tier=submission_tier,
                tier_known=tier_known,
                checks=checks,
                blockers=blockers,
                warnings=warnings,
                manual_review_items=manual_review_items,
            )
        else:
            checks.append({"name": "constraint_source", "status": "PASS"})

    declared_code_files = config.get("declared_code_files", [])
    appendix_code_files = config.get("appendix_code_files", [])
    if not isinstance(declared_code_files, list) or not isinstance(appendix_code_files, list):
        blockers.append("CODE_APPENDIX_SOURCE_MISSING")
    else:
        for relative_path in declared_code_files:
            path = _case_relative_path(case_dir, relative_path)
            if path is None or not path.is_file():
                blockers.append("CODE_APPENDIX_SOURCE_MISSING")
                checks.append({"name": "declared_code", "status": "FAIL", "path": str(relative_path)})
        appendix_markers = config.get("appendix_markers", [])
        appendix_marker_found = _has_any_anchor(structured_texts, appendix_markers)
        if config.get("code_appendix_required") is True and not appendix_code_files and not appendix_marker_found:
            _record_tier_sensitive_missing(
                code="CODE_APPENDIX_REQUIRED_MISSING",
                message="no appendix paths or markers declared",
                check_name="code_appendix",
                tier=submission_tier,
                tier_known=tier_known,
                checks=checks,
                blockers=blockers,
                warnings=warnings,
                manual_review_items=manual_review_items,
            )
        for relative_path in appendix_code_files:
            path = _case_relative_path(case_dir, relative_path)
            if path is None or not path.is_file() or (declared_code_files and relative_path not in declared_code_files):
                blockers.append("CODE_APPENDIX_OUT_OF_SYNC")
                checks.append({"name": "appendix_code", "status": "FAIL", "path": str(relative_path)})
            else:
                checks.append({"name": "appendix_code", "status": "PASS", "path": str(relative_path)})

    if config.get("allow_passive_provenance_internal_only", True) is True:
        if any(term in combined_lower for term in VISIBLE_PROVENANCE_TERMS):
            blockers.append("PROVENANCE_VISIBLE_MARKER_FORBIDDEN")
            checks.append({"name": "provenance", "status": "FAIL"})
    if any(term in combined_lower for term in DECEPTIVE_TERMS):
        blockers.append("DECEPTIVE_ANTI_PLAGIARISM_TEXT_FOUND")
        checks.append({"name": "anti_plagiarism_bait", "status": "FAIL"})

    return _build_result(
        checks, blockers, warnings, manual_review_items, scanned_sources, skipped_sources, submission_tier
    )


def write_paper_hard_error_report(report: dict[str, Any], case_dir: str | Path) -> tuple[Path, Path]:
    """Write a report only when explicitly requested by the CLI caller."""

    output_dir = Path(case_dir) / "output" / "validation"
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "paper_hard_error_report.json"
    markdown_path = output_dir / "paper_hard_error_report.md"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    lines = ["# Paper Hard-Error Report", "", f"- status: `{report['status']}`", "- final_submit_created: `false`", "", "## Blockers", ""]
    lines.extend(f"- `{item}`" for item in report["blockers"]) if report["blockers"] else lines.append("- None")
    lines.extend(["", "## Manual Review Items", ""])
    lines.extend(f"- {item}" for item in report["manual_review_items"]) if report["manual_review_items"] else lines.append("- None")
    markdown_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return json_path, markdown_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only submitted-paper hard-error preflight")
    parser.add_argument("--case-dir", required=True)
    parser.add_argument("--config-path", default=None)
    parser.add_argument("--write-report", action="store_true")
    args = parser.parse_args()
    report = check_paper_hard_errors(args.case_dir, config_path=args.config_path)
    if args.write_report:
        write_paper_hard_error_report(report, args.case_dir)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] in {"PASS", "MANUAL_REVIEW_REQUIRED"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
