"""Deterministic, bounded semantic re-entry checks for R4B.

The checker intentionally supports only a small machine-readable grammar.  It
never searches the paper globally and never treats display values or sidecars
as authoritative result values.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from pathlib import Path
from typing import Any

from canonical_result_index import PUBLIC_MACHINE_FIELDS

SCHEMA_VERSION = "s0_r4b_structured_semantic_reentry_v1"
SEMANTIC_VERDICTS = {"SEMANTIC_PASS", "SEMANTIC_BLOCK", "REVIEW_REQUIRED", "NOT_APPLICABLE"}


def _sha(path: Path) -> str | None:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def _json(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _path(base: Path, value: Any) -> Path | None:
    if not isinstance(value, str) or not value.strip():
        return None
    p = Path(value)
    if p.is_absolute() or ".." in p.parts:
        return None
    root = base.resolve()
    result = (root / p).resolve()
    try:
        result.relative_to(root)
    except ValueError:
        return None
    return result


def _pointer(value: Any, pointer: Any) -> Any:
    if not isinstance(pointer, str) or (pointer and not pointer.startswith("/")):
        raise ValueError("invalid JSON Pointer")
    current = value
    for token in pointer.split("/")[1:]:
        token = token.replace("~1", "/").replace("~0", "~")
        if isinstance(current, list):
            current = current[int(token)]
        elif isinstance(current, dict) and token in current:
            current = current[token]
        else:
            raise KeyError(token)
    return current


def _markers(text: str, claim_id: str) -> tuple[str | None, list[str]]:
    start_pattern = rf"(?m)^[ \t]*% CLAIM_START:{re.escape(claim_id)}[ \t]*\r?$"
    end_pattern = rf"(?m)^[ \t]*% CLAIM_END:{re.escape(claim_id)}[ \t]*\r?$"
    starts = list(re.finditer(start_pattern, text))
    ends = list(re.finditer(end_pattern, text))
    if len(starts) != 1 or len(ends) != 1 or starts[0].end() >= ends[0].start():
        return None, ["R4B_PAPER_MARKER_INVALID"]
    return text[starts[0].end():ends[0].start()], []


def _number_matches(text: str, symbol: str) -> list[tuple[str, float]]:
    return [(operator, value) for operator, value, _, _ in _number_matches_with_spans(text, symbol)]


def _number_matches_with_spans(text: str, symbol: str) -> list[tuple[str, float, int, int]]:
    s = re.escape(symbol.lstrip("\\"))
    pat = rf"(?<![A-Za-z0-9_])(?:\\?{s})(?![A-Za-z0-9_])\s*(=|>=|<=|>|<)\s*([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)"
    return [(m.group(1), float(m.group(2)), m.start(), m.end()) for m in re.finditer(pat, text)]


def _interval_matches(text: str, symbol: str) -> list[tuple[float, float]]:
    return [(lower, upper) for lower, upper, _, _ in _interval_matches_with_spans(text, symbol)]


def _interval_matches_with_spans(text: str, symbol: str) -> list[tuple[float, float, int, int]]:
    s = re.escape(symbol.lstrip("\\"))
    pat = rf"([+-]?(?:\d+(?:\.\d*)?|\.\d+))\s*<=\s*(?<![A-Za-z0-9_])(?:\\?{s})(?![A-Za-z0-9_])\s*<=\s*([+-]?(?:\d+(?:\.\d*)?|\.\d+))"
    return [(float(m.group(1)), float(m.group(2)), m.start(), m.end()) for m in re.finditer(pat, text)]


def _observations(text: str, symbol: str) -> list[tuple[str, tuple[float, float] | tuple[str, float]]]:
    """Extract interval and scalar observations without double-counting intervals."""
    intervals = _interval_matches_with_spans(text, symbol)
    observations: list[tuple[str, tuple[float, float] | tuple[str, float]]] = [
        ("INTERVAL", (lo, hi)) for lo, hi, _, _ in intervals
    ]
    for operator, value, start, end in _number_matches_with_spans(text, symbol):
        if any(left <= start and end <= right for _, _, left, right in intervals):
            continue
        observations.append(("SCALAR", (operator, value)))
    return observations


def _comparison_tolerance(constraint: dict[str, Any]) -> tuple[float, float] | None:
    """Return the explicit R2A v2 comparison tolerance only."""
    raw = constraint.get("comparison_tolerance")
    if raw is None:
        return None
    if not isinstance(raw, dict):
        return None
    abs_tol = raw.get("abs_tolerance", 0.0)
    rel_tol = raw.get("rel_tolerance", 0.0)
    if (
        not isinstance(abs_tol, (int, float))
        or isinstance(abs_tol, bool)
        or not isinstance(rel_tol, (int, float))
        or isinstance(rel_tol, bool)
        or not math.isfinite(float(abs_tol))
        or not math.isfinite(float(rel_tol))
        or float(abs_tol) < 0
        or float(rel_tol) < 0
    ):
        return None
    return float(abs_tol), float(rel_tol)


def _bound_tolerance(constraint: dict[str, Any], approved: float) -> float | None:
    tolerances = _comparison_tolerance(constraint)
    if tolerances is None:
        if constraint.get("constraint_type") != "FROZEN_VALUE" and "comparison_tolerance" not in constraint:
            return 0.0
        return None
    abs_tol, rel_tol = tolerances
    return max(abs_tol, rel_tol * abs(float(approved)))


def _resolve_value(index: dict[str, Any], case_dir: Path, ref: dict[str, Any]) -> tuple[Any, str | None]:
    qid, role = ref.get("question_id"), ref.get("artifact_role")
    matches = [e for e in index.get("entries", []) if isinstance(e, dict) and e.get("question_id") == qid and e.get("artifact_role") == role]
    if len(matches) != 1:
        return None, "R4B_CANONICAL_SCOPE_INVALID"
    entry = matches[0]
    if ref.get("run_id") is not None and ref.get("run_id") != entry.get("run_id"):
        return None, "R4B_RUN_ID_MISMATCH"
    if ref.get("case_id") is not None and ref.get("case_id") != entry.get("case_id"):
        return None, "R4B_CASE_ID_MISMATCH"
    expected_schema = entry.get("result_schema_id")
    if not isinstance(expected_schema, str) or not expected_schema:
        return None, "R4B_RESULT_SCHEMA_ID_MISSING"
    declared_schema = ref.get("result_schema_id")
    if declared_schema is not None and declared_schema != expected_schema:
        return None, "R4B_RESULT_SCHEMA_ID_MISMATCH"
    artifact = _path(case_dir, entry.get("artifact_path"))
    if artifact is None or not artifact.is_file() or _sha(artifact) != entry.get("sha256"):
        return None, "R4B_CANONICAL_HASH_MISMATCH"
    payload = _json(artifact)
    if payload is None:
        return None, "R4B_CANONICAL_ARTIFACT_UNREADABLE"
    if ref.get("run_id") is not None and payload.get("run_id") != ref.get("run_id"):
        return None, "R4B_RUN_ID_MISMATCH"
    if ref.get("case_id") is not None and payload.get("case_id") != ref.get("case_id"):
        return None, "R4B_CASE_ID_MISMATCH"
    field = ref.get("field_reference")
    if not isinstance(field, str) or not field.startswith("/") or field.split("/")[1] not in PUBLIC_MACHINE_FIELDS:
        return None, "R4B_FIELD_REFERENCE_INVALID"
    try:
        value = _pointer(payload, field)
    except (KeyError, IndexError, ValueError, TypeError):
        return None, "R4B_FIELD_MISSING"
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return None, "R4B_FIELD_TYPE_INVALID"
    return value, None


def validate_structured_semantic_reentry(
    reentry_path: str | Path,
    *,
    repo_root: str | Path | None = None,
    trusted_index_path: str | Path | None = None,
    trusted_index_sha256: str | None = None,
    trusted_handoff_path: str | Path | None = None,
    trusted_handoff_sha256: str | None = None,
) -> dict[str, Any]:
    sidecar = Path(reentry_path).expanduser().resolve()
    root = Path(repo_root).expanduser().resolve() if repo_root else sidecar.parent
    payload = _json(sidecar) or {}
    blockers: list[str] = []
    reviews: list[str] = []
    evidence: list[dict[str, Any]] = []
    if payload.get("schema_version") != SCHEMA_VERSION:
        blockers.append("R4B_REENTRY_SCHEMA_INVALID")
    paper_meta = payload.get("paper_source")
    paper = _path(sidecar.parent, paper_meta.get("path")) if isinstance(paper_meta, dict) else None
    paper_bytes = paper.read_bytes() if paper and paper.is_file() else None
    if paper_bytes is None or not isinstance(paper_meta, dict) or _sha(paper) != paper_meta.get("sha256"):
        blockers.append("R4B_PAPER_SOURCE_INVALID")
        text = ""
    else:
        try:
            text = paper_bytes.decode("utf-8")
        except UnicodeDecodeError:
            blockers.append("R4B_PAPER_SOURCE_UNREADABLE")
            text = ""
    handoff_path = Path(trusted_handoff_path).resolve() if trusted_handoff_path else _path(sidecar.parent, payload.get("claim_handoff_path"))
    handoff = _json(handoff_path) if handoff_path else None
    if handoff_path is None or handoff is None:
        blockers.append("R4B_HANDOFF_UNREADABLE")
    elif trusted_handoff_sha256 and _sha(handoff_path) != trusted_handoff_sha256:
        blockers.append("R4B_HANDOFF_PROVENANCE_MISMATCH")
    index_path = Path(trusted_index_path).resolve() if trusted_index_path else None
    if index_path is None and isinstance(payload.get("result_authority"), dict):
        index_path = _path(sidecar.parent, payload["result_authority"].get("index_path"))
    index = _json(index_path) if index_path else None
    if index_path is None or index is None:
        blockers.append("R4B_INDEX_UNREADABLE")
    elif trusted_index_sha256 and _sha(index_path) != trusted_index_sha256:
        blockers.append("R4B_INDEX_PROVENANCE_MISMATCH")
    case_dir = root
    if isinstance(payload.get("result_authority"), dict):
        case_dir = _path(root, payload["result_authority"].get("case_dir")) or root
    claims = handoff.get("claims") if isinstance(handoff, dict) else []
    if not isinstance(claims, list):
        blockers.append("R4B_CLAIMS_INVALID")
        claims = []
    any_constraint = False
    for claim in claims:
        if not isinstance(claim, dict):
            reviews.append("R4B_CLAIM_REVIEW_REQUIRED")
            continue
        constraints = claim.get("approved_constraints")
        if constraints is None:
            reviews.append(f"R4B_V1_CONSTRAINTS_UNKNOWN:{claim.get('claim_id')}")
            continue
        if not isinstance(constraints, list):
            blockers.append("R4B_CONSTRAINT_SCHEMA_INVALID")
            continue
        if not constraints:
            continue
        any_constraint = True
        span = claim.get("paper_span_reference")
        body, marker_errors = _markers(text, str(claim.get("claim_id") or ""))
        blockers.extend(marker_errors)
        if body is None or not isinstance(span, dict):
            reviews.append(f"R4B_PAPER_SPAN_REVIEW_REQUIRED:{claim.get('claim_id')}")
            continue
        if str(span.get("claim_id") or "") != str(claim.get("claim_id") or ""):
            blockers.extend(["BLOCK_SCHEMA_INVALID", "R4B_PAPER_SPAN_CLAIM_ID_MISMATCH"])
            continue
        for constraint in constraints:
            if not isinstance(constraint, dict):
                blockers.append("R4B_CONSTRAINT_SCHEMA_INVALID")
                continue
            ctype = constraint.get("constraint_type")
            symbol = constraint.get("symbol")
            if ctype not in {"SIGN", "LOWER_BOUND", "UPPER_BOUND", "INTERVAL", "FROZEN_VALUE"}:
                blockers.append("BLOCK_SCHEMA_INVALID")
                continue
            if not isinstance(symbol, str) or not symbol.strip():
                blockers.append("BLOCK_SCHEMA_INVALID:R4B_CONSTRAINT_SYMBOL_REQUIRED")
                continue
            if ctype in {"LOWER_BOUND", "UPPER_BOUND"}:
                value = constraint.get("value")
                if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(float(value)):
                    blockers.append("BLOCK_SCHEMA_INVALID:R4B_CONSTRAINT_VALUE_REQUIRED")
                    continue
            elif ctype == "SIGN":
                if constraint.get("sign") not in {"NON_NEGATIVE", "NON_POSITIVE"}:
                    blockers.append("BLOCK_SCHEMA_INVALID:R4B_CONSTRAINT_SIGN_REQUIRED")
                    continue
            elif ctype == "INTERVAL":
                lower, upper = constraint.get("lower"), constraint.get("upper")
                if (
                    not isinstance(lower, (int, float)) or isinstance(lower, bool)
                    or not isinstance(upper, (int, float)) or isinstance(upper, bool)
                    or not math.isfinite(float(lower)) or not math.isfinite(float(upper))
                    or float(lower) > float(upper)
                ):
                    blockers.append("BLOCK_SCHEMA_INVALID:R4B_INTERVAL_BOUNDS_REQUIRED")
                    continue
            elif ctype == "FROZEN_VALUE":
                if not isinstance(constraint.get("canonical_value_reference"), dict):
                    blockers.extend(["BLOCK_SCHEMA_INVALID", "R4B_CANONICAL_REFERENCE_MISSING"])
                    continue
                if _comparison_tolerance(constraint) is None:
                    reviews.append(f"R4B_FROZEN_TOLERANCE_EXPLICIT_REQUIRED:{claim.get('claim_id')}")
                    continue
            observations = _observations(body, symbol)
            if len(observations) > 1:
                reviews.append(f"R4B_MULTIPLE_TARGET_STATEMENTS_REVIEW_REQUIRED:{claim.get('claim_id')}:{symbol}")
                continue
            if len(observations) == 0:
                reviews.append(f"R4B_UNPARSEABLE_CONSTRAINT:{claim.get('claim_id')}")
                continue
            observation_kind, observed = observations[0]
            if ctype == "FROZEN_VALUE":
                if not isinstance(index, dict) or not isinstance(constraint.get("canonical_value_reference"), dict):
                    blockers.extend(["BLOCK_SCHEMA_INVALID", "R4B_CANONICAL_REFERENCE_MISSING"])
                    continue
                auth, error = _resolve_value(index, case_dir, constraint["canonical_value_reference"])
                if error:
                    blockers.append(error)
                    if error == "R4B_RESULT_SCHEMA_ID_MISMATCH":
                        blockers.append("BLOCK_SCHEMA_INVALID")
                    elif error == "R4B_RESULT_SCHEMA_ID_MISSING":
                        blockers.pop()
                        reviews.append("R4B_RESULT_SCHEMA_ID_UNKNOWN")
                    continue
                if not (isinstance(observed, tuple) and len(observed) == 2 and isinstance(observed[0], str)):
                    reviews.append(f"R4B_FROZEN_OPERATOR_REVIEW_REQUIRED:{claim.get('claim_id')}")
                    continue
                if observed[0] != "=":
                    reviews.append(f"R4B_FROZEN_OPERATOR_REVIEW_REQUIRED:{claim.get('claim_id')}")
                    continue
                observed_value = observed[1]
                effective_tolerance = _bound_tolerance(constraint, float(auth))
                if effective_tolerance is None:
                    blockers.append("R4B_TOLERANCE_SCHEMA_INVALID")
                    continue
                diff = abs(float(observed_value) - float(auth))
                if diff > effective_tolerance:
                    blockers.append("R4B_FROZEN_VALUE_OUTSIDE_TOLERANCE")
                evidence.append({"claim_id": claim.get("claim_id"), "constraint": ctype, "authoritative_value": auth, "observed": observed, "difference": diff, "effective_tolerance": effective_tolerance})
                continue
            if ctype not in {"LOWER_BOUND", "UPPER_BOUND", "SIGN", "INTERVAL"}:
                blockers.append("R4B_CONSTRAINT_SCHEMA_INVALID")
                continue
            if ctype == "INTERVAL":
                lo, hi = constraint.get("lower"), constraint.get("upper")
                if not isinstance(lo, (int, float)) or not isinstance(hi, (int, float)) or float(lo) > float(hi):
                    blockers.append("R4B_CONSTRAINT_SCHEMA_INVALID")
                    continue
                tol = _bound_tolerance(constraint, max(abs(float(lo)), abs(float(hi))))
                if tol is None:
                    blockers.append("R4B_TOLERANCE_SCHEMA_INVALID")
                    continue
                if isinstance(observed, tuple) and len(observed) == 2 and isinstance(observed[0], str):
                    if observed[0] != "=":
                        reviews.append(f"R4B_INTERVAL_OPERATOR_REVIEW_REQUIRED:{claim.get('claim_id')}")
                        continue
                    point = observed[1]
                    ok = float(lo) - tol <= point <= float(hi) + tol
                    evidence.append({"claim_id": claim.get("claim_id"), "constraint": ctype, "observed": observed, "passed": bool(ok)})
                    if not ok:
                        blockers.append(f"R4B_CONSTRAINT_VIOLATION:{claim.get('claim_id')}:{ctype}")
                    continue
                if isinstance(observed, tuple):
                    obs_lo, obs_hi = observed
                    if obs_lo >= float(lo) - tol and obs_hi <= float(hi) + tol:
                        evidence.append({"claim_id": claim.get("claim_id"), "constraint": ctype, "observed": observed, "passed": True})
                    elif obs_hi < float(lo) - tol or obs_lo > float(hi) + tol:
                        blockers.append(f"R4B_CONSTRAINT_VIOLATION:{claim.get('claim_id')}:{ctype}")
                        evidence.append({"claim_id": claim.get("claim_id"), "constraint": ctype, "observed": observed, "passed": False})
                    else:
                        reviews.append(f"R4B_INTERVAL_REVIEW_REQUIRED:{claim.get('claim_id')}")
                    continue
                point = observed[1]
                ok = float(lo) - tol <= point <= float(hi) + tol
                evidence.append({"claim_id": claim.get("claim_id"), "constraint": ctype, "observed": observed, "passed": bool(ok)})
                if not ok:
                    blockers.append(f"R4B_CONSTRAINT_VIOLATION:{claim.get('claim_id')}:{ctype}")
                continue

            operator, value = observed
            approved = constraint.get("value", 0.0)
            if ctype == "SIGN":
                sign = constraint.get("sign", "NON_NEGATIVE")
                if sign not in {"NON_NEGATIVE", "NON_POSITIVE"}:
                    blockers.append("R4B_CONSTRAINT_SCHEMA_INVALID")
                    continue
                bound_type = "LOWER_BOUND" if sign == "NON_NEGATIVE" else "UPPER_BOUND"
                approved = 0.0
            else:
                bound_type = ctype
            if not isinstance(approved, (int, float)) or isinstance(approved, bool) or not math.isfinite(float(approved)):
                blockers.append("R4B_CONSTRAINT_SCHEMA_INVALID")
                continue
            tol = _bound_tolerance(constraint, float(approved))
            if tol is None:
                blockers.append("R4B_TOLERANCE_SCHEMA_INVALID")
                continue
            blocker_start = len(blockers)
            review_start = len(reviews)
            if operator == "=":
                if bound_type == "LOWER_BOUND":
                    ok = value >= float(approved) - tol
                else:
                    ok = value <= float(approved) + tol
                if not ok:
                    blockers.append(f"R4B_CONSTRAINT_VIOLATION:{claim.get('claim_id')}:{ctype}")
            elif bound_type == "LOWER_BOUND":
                if operator in {">", ">="}:
                    ok = value >= float(approved) - tol
                    if not ok:
                        reviews.append(f"R4B_OPERATOR_REVIEW_REQUIRED:{claim.get('claim_id')}:{ctype}")
                else:
                    if value < float(approved) - tol:
                        blockers.append(f"R4B_CONSTRAINT_VIOLATION:{claim.get('claim_id')}:{ctype}")
                    else:
                        reviews.append(f"R4B_OPERATOR_REVIEW_REQUIRED:{claim.get('claim_id')}:{ctype}")
            else:
                if operator in {"<", "<="}:
                    ok = value <= float(approved) + tol
                    if not ok:
                        reviews.append(f"R4B_OPERATOR_REVIEW_REQUIRED:{claim.get('claim_id')}:{ctype}")
                else:
                    if value > float(approved) + tol:
                        blockers.append(f"R4B_CONSTRAINT_VIOLATION:{claim.get('claim_id')}:{ctype}")
                    else:
                        reviews.append(f"R4B_OPERATOR_REVIEW_REQUIRED:{claim.get('claim_id')}:{ctype}")
            evidence.append({"claim_id": claim.get("claim_id"), "constraint": ctype, "observed": observed, "approved": approved, "passed": len(blockers) == blocker_start and len(reviews) == review_start})
    if blockers:
        verdict = "SEMANTIC_BLOCK"
    elif reviews:
        verdict = "REVIEW_REQUIRED"
    elif not any_constraint:
        verdict = "NOT_APPLICABLE"
    else:
        verdict = "SEMANTIC_PASS"
    return {
        "schema_version": SCHEMA_VERSION,
        "status": verdict,
        "verdict": verdict,
        "blockers": list(dict.fromkeys(blockers)),
        "review_items": list(dict.fromkeys(reviews)),
        "evidence": evidence,
        "fallback": {"FALLBACK_USED": False, "DISPLAY_VALUE_USED": False, "RAW_RESULT_FALLBACK": False},
        "downstream_finalization_allowed": verdict in {"SEMANTIC_PASS", "NOT_APPLICABLE"},
    }
