"""Read-only P2-5A wrapper around the canonical claim-check enforcement gate.

The wrapper only locates case-declared claim cards, validates safe paths and
role separation, and delegates settlement decisions to
``pipeline_core.claim_check.enforce_claim_check``.  It does not calculate red
flags, modify papers, or create ``final_submit/``.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from pipeline_core.claim_check.enforce_claim_check import enforce_claim_ready_for_writing


def _read_json(path: Path) -> tuple[dict[str, Any] | None, str | None]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        return None, "missing"
    except json.JSONDecodeError:
        return None, "malformed"
    except OSError:
        return None, "unreadable"
    if not isinstance(payload, dict):
        return None, "not_object"
    return payload, None


def _case_relative_path(case_dir: Path, value: Any) -> Path | None:
    if not isinstance(value, str) or not value.strip():
        return None
    candidate = Path(value)
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


def _settlement_from_card(card: dict[str, Any]) -> dict[str, Any] | None:
    """Extract an existing settlement without recomputing claim-check logic."""

    required = ("status", "human_decision", "allowed_claim_strength")
    if not all(isinstance(card.get(key), str) and card[key].strip() for key in required):
        return None

    if "red_flag_count" in card or "red_flag_ids" in card:
        count = card.get("red_flag_count")
        flag_ids = card.get("red_flag_ids")
        if isinstance(count, bool) or not isinstance(count, int) or count < 0 or not isinstance(flag_ids, list):
            return None
        if not all(isinstance(item, str) for item in flag_ids):
            return None
    else:
        machine_flags = card.get("machine_red_flags")
        if not isinstance(machine_flags, list):
            return None
        red_flags = [
            item for item in machine_flags
            if isinstance(item, dict) and item.get("triggered") is True and item.get("severity") == "RED"
        ]
        count = len(red_flags)
        flag_ids = [str(item.get("flag_id") or "(unnamed)") for item in red_flags]

    return {
        "status": card["status"],
        "human_decision": card["human_decision"],
        "recommended_decision": str(card.get("recommended_decision") or "PENDING"),
        "allowed_claim_strength": card["allowed_claim_strength"],
        "red_flag_count": count,
        "red_flag_ids": flag_ids,
    }


def _verdict_code(verdict: str) -> str | None:
    mappings = {
        "CLAIM_CHECK_REQUIRED_BUT_MISSING": "CLAIM_CHECK_REQUIRED_BUT_MISSING",
        "CORE_CLAIM_CLASSIFICATION_SELF_FILLED": "CORE_CLAIM_CLASSIFICATION_SELF_FILLED",
        "CORE_CLAIM_CLASSIFICATION_PROVIDER_MISSING": "CORE_CLAIM_CLASSIFICATION_PROVIDER_MISSING",
        "CLAIM_CHECK_UNRESOLVED_BLOCK": "CLAIM_CHECK_UNRESOLVED_BLOCK",
        "CLAIM_REJECTED_OR_NOT_ALLOWED": "CLAIM_REJECTED_OR_NOT_ALLOWED",
        "CLAIM_WORDING_EXCEEDS_ALLOWED_STRENGTH": "CLAIM_WORDING_EXCEEDS_ALLOWED_STRENGTH",
    }
    return next((code for token, code in mappings.items() if token in verdict), None)


def _claim_summary(claim: dict[str, Any], status: str, **extra: Any) -> dict[str, Any]:
    summary = {
        "claim_id": str(claim.get("claim_id") or "(unnamed)"),
        "claim_type": str(claim.get("claim_type") or ""),
        "status": status,
    }
    # Preserve the concrete artifact pointers for downstream package binding.
    # This is metadata propagation only; claim settlement remains owned by the
    # existing enforcement gate above.
    for field in ("claim_card_path", "proposed_wording_path"):
        value = claim.get(field)
        if isinstance(value, str) and value.strip():
            summary[field] = value
    summary.update(extra)
    return summary


def check_claim_check_preflight(case_dir: str | Path) -> dict[str, Any]:
    """Run read-only claim-check enforcement for configured core claims."""

    case_dir = Path(case_dir)
    config, config_error = _read_json(case_dir / "case_config.json")
    if config_error:
        config = {}

    checks: list[dict[str, Any]] = []
    blockers: list[str] = []
    warnings: list[str] = []
    manual_review_items: list[str] = []
    claims: list[dict[str, Any]] = []

    required = config.get("claim_check_required") is True
    core_claims = config.get("core_claims", [])
    if not isinstance(core_claims, list):
        blockers.append("CLAIM_CARD_SCHEMA_ERROR")
        _append_check(checks, "core_claims", "FAIL", "CLAIM_CARD_SCHEMA_ERROR")
        core_claims = []
    if required and not core_claims:
        blockers.append("CLAIM_CHECK_REQUIRED_BUT_NO_CORE_CLAIMS")
        _append_check(checks, "core_claims", "FAIL", "CLAIM_CHECK_REQUIRED_BUT_NO_CORE_CLAIMS")

    for raw_claim in core_claims:
        if not isinstance(raw_claim, dict):
            blockers.append("CLAIM_CARD_SCHEMA_ERROR")
            _append_check(checks, "core_claim", "FAIL", "CLAIM_CARD_SCHEMA_ERROR")
            claims.append({"claim_id": "(invalid)", "status": "BLOCKED"})
            continue
        claim = raw_claim
        claim_id = str(claim.get("claim_id") or "").strip()
        claim_type = str(claim.get("claim_type") or "").strip()
        is_core = claim.get("is_core_conclusion") is True
        generator_role = str(claim.get("generator_role_id") or "").strip()
        adversarial_role = str(claim.get("adversarial_role_id") or "").strip()
        provider_role = str(claim.get("claim_type_provider_role_id") or "").strip()

        if not claim_id or not claim_type:
            blockers.append("CLAIM_CARD_SCHEMA_ERROR")
            _append_check(checks, "claim_declaration", "FAIL", "CLAIM_CARD_SCHEMA_ERROR")
            claims.append(_claim_summary(claim, "BLOCKED"))
            continue
        if is_core and (not generator_role or not adversarial_role or generator_role == adversarial_role):
            blockers.append("CORE_CLAIM_CLASSIFICATION_SELF_FILLED")
            _append_check(checks, f"claim:{claim_id}", "FAIL", "CORE_CLAIM_CLASSIFICATION_SELF_FILLED")
            claims.append(_claim_summary(claim, "BLOCKED"))
            continue
        if is_core and not provider_role:
            blockers.append("CORE_CLAIM_CLASSIFICATION_PROVIDER_MISSING")
            _append_check(checks, f"claim:{claim_id}", "FAIL", "CORE_CLAIM_CLASSIFICATION_PROVIDER_MISSING")
            claims.append(_claim_summary(claim, "BLOCKED"))
            continue
        if is_core and generator_role == provider_role:
            blockers.append("CORE_CLAIM_CLASSIFICATION_SELF_FILLED")
            _append_check(checks, f"claim:{claim_id}", "FAIL", "CORE_CLAIM_CLASSIFICATION_SELF_FILLED")
            claims.append(_claim_summary(claim, "BLOCKED"))
            continue

        configured_card_path = claim.get("claim_card_path")
        if not isinstance(configured_card_path, str) or not configured_card_path.strip():
            blockers.append("CLAIM_CHECK_REQUIRED_BUT_MISSING")
            _append_check(checks, f"claim:{claim_id}", "FAIL", "CLAIM_CHECK_REQUIRED_BUT_MISSING")
            claims.append(_claim_summary(claim, "BLOCKED"))
            continue
        card_path = _case_relative_path(case_dir, configured_card_path)
        if card_path is None:
            blockers.append("CLAIM_CARD_UNREADABLE")
            _append_check(checks, f"claim:{claim_id}", "FAIL", "CLAIM_CARD_UNREADABLE")
            claims.append(_claim_summary(claim, "BLOCKED"))
            continue
        if not card_path.is_file():
            blockers.append("CLAIM_CARD_MISSING")
            _append_check(checks, f"claim:{claim_id}", "FAIL", "CLAIM_CARD_MISSING")
            claims.append(_claim_summary(claim, "BLOCKED", claim_card_path=str(card_path)))
            continue
        card, card_error = _read_json(card_path)
        if card_error == "unreadable":
            blockers.append("CLAIM_CARD_UNREADABLE")
            _append_check(checks, f"claim:{claim_id}", "FAIL", "CLAIM_CARD_UNREADABLE")
            claims.append(_claim_summary(claim, "BLOCKED", claim_card_path=str(card_path)))
            continue
        if card_error or card is None:
            blockers.append("CLAIM_CARD_SCHEMA_ERROR")
            _append_check(checks, f"claim:{claim_id}", "FAIL", "CLAIM_CARD_SCHEMA_ERROR")
            claims.append(_claim_summary(claim, "BLOCKED", claim_card_path=str(card_path)))
            continue

        settlement = _settlement_from_card(card)
        if settlement is None:
            blockers.append("CLAIM_CARD_SCHEMA_ERROR")
            _append_check(checks, f"claim:{claim_id}", "FAIL", "CLAIM_CARD_SCHEMA_ERROR")
            claims.append(_claim_summary(claim, "BLOCKED", claim_card_path=str(card_path)))
            continue

        wording_path = _case_relative_path(case_dir, claim.get("proposed_wording_path"))
        if wording_path is None or not wording_path.is_file():
            blockers.append("CLAIM_EVIDENCE_PATH_UNREADABLE")
            _append_check(checks, f"claim:{claim_id}", "FAIL", "CLAIM_EVIDENCE_PATH_UNREADABLE")
            claims.append(_claim_summary(claim, "BLOCKED", claim_card_path=str(card_path)))
            continue
        try:
            proposed_wording = wording_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            blockers.append("CLAIM_EVIDENCE_PATH_UNREADABLE")
            _append_check(checks, f"claim:{claim_id}", "FAIL", "CLAIM_EVIDENCE_PATH_UNREADABLE")
            claims.append(_claim_summary(claim, "BLOCKED", claim_card_path=str(card_path)))
            continue

        verdict = enforce_claim_ready_for_writing(
            claim_id=claim_id,
            claim_type=claim_type,
            is_core_conclusion=is_core,
            claim_check_result=settlement,
            proposed_wording=proposed_wording,
            claim_type_provider_role_id=provider_role or None,
            generator_role_id=generator_role,
        )
        verdict_code = _verdict_code(verdict.verdict)
        if verdict.is_pass:
            _append_check(checks, f"claim:{claim_id}", "PASS", verdict.verdict)
            claims.append(
                _claim_summary(
                    claim,
                    "PASS",
                    verdict=verdict.verdict,
                    claim_card_path=str(card_path),
                    proposed_wording_path=str(wording_path),
                )
            )
        elif verdict.is_block or verdict.is_fail:
            code = verdict_code or "CLAIM_CHECK_MANUAL_REVIEW_REQUIRED"
            blockers.append(code)
            _append_check(checks, f"claim:{claim_id}", "FAIL", code)
            claims.append(
                _claim_summary(
                    claim,
                    "BLOCKED",
                    verdict=verdict.verdict,
                    claim_card_path=str(card_path),
                    proposed_wording_path=str(wording_path),
                )
            )
        else:
            warnings.append("CLAIM_CHECK_MANUAL_REVIEW_REQUIRED")
            manual_review_items.append(f"{claim_id}: {verdict.verdict}")
            _append_check(checks, f"claim:{claim_id}", "MANUAL_REVIEW", "CLAIM_CHECK_MANUAL_REVIEW_REQUIRED")
            claims.append(
                _claim_summary(
                    claim,
                    "MANUAL_REVIEW_REQUIRED",
                    verdict=verdict.verdict,
                    claim_card_path=str(card_path),
                    proposed_wording_path=str(wording_path),
                )
            )

    unique_blockers = list(dict.fromkeys(blockers))
    if unique_blockers:
        status = "BLOCKED"
    elif manual_review_items:
        status = "MANUAL_REVIEW_REQUIRED"
    elif claims:
        status = "PASS"
    else:
        status = "SKIPPED"
    return {
        "status": status,
        "checks": checks,
        "blockers": unique_blockers,
        "warnings": list(dict.fromkeys(warnings)),
        "manual_review_items": manual_review_items,
        "claims": claims,
        "final_submit_created": False,
        "limitations": [
            "This wrapper delegates claim settlement to enforce_claim_check.",
            "It does not recompute red flags or approve human decisions.",
            "It never creates final_submit.",
        ],
    }


def write_claim_check_preflight_report(report: dict[str, Any], case_dir: str | Path) -> tuple[Path, Path]:
    """Write an explicit report only when the CLI caller opts in."""

    output_dir = Path(case_dir) / "output" / "validation"
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "claim_check_preflight_report.json"
    markdown_path = output_dir / "claim_check_preflight_report.md"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    lines = [
        "# Claim Check Preflight Report",
        "",
        f"- status: `{report['status']}`",
        "- final_submit_created: `false`",
        "",
        "## Blockers",
        "",
    ]
    lines.extend(f"- `{item}`" for item in report["blockers"]) if report["blockers"] else lines.append("- None")
    markdown_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return json_path, markdown_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only claim-check preflight wrapper")
    parser.add_argument("--case-dir", required=True)
    parser.add_argument("--write-report", action="store_true")
    args = parser.parse_args()
    report = check_claim_check_preflight(args.case_dir)
    if args.write_report:
        write_claim_check_preflight_report(report, args.case_dir)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] in {"PASS", "SKIPPED", "MANUAL_REVIEW_REQUIRED"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
