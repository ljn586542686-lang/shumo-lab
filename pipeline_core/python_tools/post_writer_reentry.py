"""Deterministic post-writer reentry binding checks.

This module checks identity and structured bindings only.  It deliberately
does not inspect claim meaning, run an NLP/LLM reviewer, or rewrite any
artifact.  The sidecar is a small, explicit handoff between an external
writer and the existing freeze/finalization consumer.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from canonical_result_index import SCHEMA_VERSION as CANONICAL_INDEX_SCHEMA_VERSION


SCHEMA_VERSION = "s0_r4a_post_writer_reentry_v1"
R1_AUTHORITY = "R1_CANONICAL_INDEX"


class PostWriterReentryBlocked(ValueError):
    """Raised by the strict wrapper when a reentry binding is invalid."""

    def __init__(self, result: dict[str, Any]) -> None:
        self.result = result
        super().__init__(json.dumps(result, ensure_ascii=False, sort_keys=True))


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _read_json_bytes_once(path: Path) -> tuple[bytes | None, str | None, dict[str, Any] | None]:
    try:
        data = path.read_bytes()
    except OSError:
        return None, None, None
    digest = hashlib.sha256(data).hexdigest()
    try:
        value = json.loads(data.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return data, digest, None
    return data, digest, value if isinstance(value, dict) else None


def _validate_index_payload(index: dict[str, Any] | None, case_dir: Path) -> list[str]:
    if index is None:
        return ["CANONICAL_INDEX_UNREADABLE"]
    if index.get("schema_version") != CANONICAL_INDEX_SCHEMA_VERSION:
        return ["CANONICAL_INDEX_SCHEMA_INVALID"]
    if index.get("status") != "PASS":
        return ["CANONICAL_INDEX_LEGACY_UNAVAILABLE"]
    entries = index.get("entries")
    if not isinstance(entries, list) or not entries:
        return ["CANONICAL_INDEX_ENTRIES_MISSING"]
    seen: set[tuple[Any, Any]] = set()
    blockers: list[str] = []
    for entry in entries:
        if not isinstance(entry, dict):
            blockers.append("CANONICAL_INDEX_ENTRY_INVALID")
            continue
        scope = (entry.get("question_id"), entry.get("artifact_role"))
        if scope in seen:
            blockers.append(f"CANONICAL_AUTHORITY_DUPLICATE:{scope[0]}:{scope[1]}")
        seen.add(scope)
        path_text = entry.get("artifact_path")
        if not isinstance(path_text, str) or Path(path_text).is_absolute() or ".." in Path(path_text).parts:
            blockers.append(f"CANONICAL_ARTIFACT_PATH_INVALID:{scope[0]}:{scope[1]}")
            continue
        artifact = (case_dir / path_text).resolve()
        expected = entry.get("sha256")
        if not artifact.is_file() or not isinstance(expected, str):
            blockers.append(f"CANONICAL_ARTIFACT_MISSING_OR_HASH_MISSING:{scope[0]}:{scope[1]}")
        elif sha256_file(artifact) != expected:
            blockers.append(f"CANONICAL_HASH_MISMATCH:{scope[0]}:{scope[1]}")
    return list(dict.fromkeys(blockers))


def sha256_file(path: Path) -> str | None:
    try:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()
    except OSError:
        return None


def compute_contract_digest(payload: dict[str, Any]) -> str:
    """Digest the sidecar without its self-referential ``contract_digest``."""

    body = dict(payload)
    body.pop("contract_digest", None)
    canonical = json.dumps(body, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _resolve(base: Path, value: Any) -> Path | None:
    if not isinstance(value, str) or not value.strip():
        return None
    path = Path(value)
    return path.resolve(strict=False) if path.is_absolute() else (base / path).resolve(strict=False)


def _valid_sha(value: Any) -> bool:
    if not isinstance(value, str) or len(value) != 64:
        return False
    try:
        int(value, 16)
    except ValueError:
        return False
    return True


def _exact_marker_matches(text: str, claim_id: str, kind: str) -> list[re.Match[str]]:
    """Find complete marker lines for exactly one claim id."""
    if kind not in {"START", "END"}:
        return []
    pattern = rf"(?m)^[ \t]*% CLAIM_{kind}:{re.escape(claim_id)}[ \t]*\r?$"
    return list(re.finditer(pattern, text))


def _result_scope_map(index: dict[str, Any]) -> dict[tuple[str, str], list[dict[str, Any]]]:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for entry in index.get("entries", []) if isinstance(index.get("entries"), list) else []:
        if isinstance(entry, dict):
            scope = (entry.get("question_id"), entry.get("artifact_role"))
            grouped.setdefault(scope, []).append(entry)
    return grouped


def validate_post_writer_reentry(
    reentry_path: str | Path,
    *,
    repo_root: str | Path | None = None,
    trusted_index_path: str | Path | None = None,
    trusted_index_sha256: str | None = None,
    trusted_handoff_path: str | Path | None = None,
    trusted_handoff_sha256: str | None = None,
) -> dict[str, Any]:
    """Validate one post-writer sidecar and all of its deterministic bindings."""

    sidecar = Path(reentry_path).expanduser().resolve(strict=False)
    root = Path(repo_root).expanduser().resolve(strict=False) if repo_root is not None else sidecar.parent
    payload = _read_json(sidecar)
    blockers: list[str] = []
    warnings: list[str] = []
    paper_path: Path | None = None
    index_path: Path | None = None
    handoff_path: Path | None = None
    index: dict[str, Any] | None = None
    handoff: dict[str, Any] | None = None
    paper_bytes: bytes | None = None
    trusted_index = Path(trusted_index_path).expanduser().resolve(strict=False) if trusted_index_path is not None else None
    trusted_handoff = Path(trusted_handoff_path).expanduser().resolve(strict=False) if trusted_handoff_path is not None else None

    if payload is None:
        blockers.append("POST_WRITER_REENTRY_UNREADABLE")
        payload = {}
    if payload.get("schema_version") != SCHEMA_VERSION:
        blockers.append("POST_WRITER_REENTRY_SCHEMA_INVALID")
    if payload.get("status") != "READY":
        blockers.append("POST_WRITER_REENTRY_STATUS_INVALID")

    declared_digest = payload.get("contract_digest")
    if not _valid_sha(declared_digest):
        blockers.append("POST_WRITER_REENTRY_DIGEST_MISSING")
    elif compute_contract_digest(payload) != declared_digest.lower():
        blockers.append("POST_WRITER_REENTRY_DIGEST_MISMATCH")

    paper = payload.get("paper_source")
    if not isinstance(paper, dict):
        blockers.append("POST_WRITER_PAPER_IDENTITY_MISSING")
    else:
        paper_path = _resolve(sidecar.parent, paper.get("path"))
        expected = paper.get("sha256")
        if paper_path is not None:
            try:
                paper_bytes = paper_path.read_bytes()
                actual = hashlib.sha256(paper_bytes).hexdigest()
            except OSError:
                actual = None
        else:
            actual = None
        if paper_path is None or actual is None or not _valid_sha(expected):
            blockers.append("POST_WRITER_PAPER_IDENTITY_INVALID")
        elif actual.lower() != expected.lower():
            blockers.append("POST_WRITER_PAPER_HASH_MISMATCH")

    authority = payload.get("result_authority")
    if not isinstance(authority, dict) or authority.get("source") != R1_AUTHORITY:
        blockers.append("POST_WRITER_RESULT_AUTHORITY_INVALID")
    else:
        index_path = _resolve(sidecar.parent, authority.get("index_path"))
        case_dir = _resolve(root, authority.get("case_dir"))
        if index_path is None or case_dir is None:
            blockers.append("POST_WRITER_CANONICAL_INDEX_BINDING_MISSING")
        else:
            if trusted_index is not None and index_path != trusted_index:
                blockers.append("POST_WRITER_RESULT_AUTHORITY_PROVENANCE_MISMATCH")
            else:
                _, actual_index_sha256, index = _read_json_bytes_once(index_path)
                if trusted_index_sha256 is not None and (
                    not _valid_sha(trusted_index_sha256)
                    or actual_index_sha256 is None
                    or actual_index_sha256.lower() != trusted_index_sha256.lower()
                ):
                    blockers.append("POST_WRITER_RESULT_AUTHORITY_PROVENANCE_MISMATCH")
                else:
                    if index is None:
                        blockers.append("CANONICAL_INDEX_UNREADABLE")
                    else:
                        if index.get("schema_version") != CANONICAL_INDEX_SCHEMA_VERSION:
                            blockers.append("CANONICAL_INDEX_SCHEMA_INVALID")
                        if index.get("authority_scope_key") != ["question_id", "artifact_role"]:
                            blockers.append("CANONICAL_INDEX_SCOPE_KEY_INVALID")
                        if index.get("result_authority") != "primary_result":
                            blockers.append("CANONICAL_INDEX_RESULT_AUTHORITY_INVALID")
                        blockers.extend(_validate_index_payload(index, case_dir))
                        refs = authority.get("references")
                        if not isinstance(refs, list) or not refs:
                            blockers.append("POST_WRITER_RESULT_REFERENCES_MISSING")
                        else:
                            grouped = _result_scope_map(index or {})
                            for ref in refs:
                                if not isinstance(ref, dict):
                                    blockers.append("POST_WRITER_RESULT_REFERENCE_INVALID")
                                    continue
                                question_id = ref.get("question_id")
                                expected_role = ref.get("artifact_role")
                                resolved_role = ref.get("resolved_artifact_role", expected_role)
                                if resolved_role != expected_role:
                                    blockers.append(f"CANONICAL_ROLE_MISMATCH:{question_id}:{expected_role}")
                                    continue
                                matches = grouped.get((question_id, expected_role), [])
                                if not matches:
                                    blockers.append(f"CANONICAL_AUTHORITY_MISSING:{question_id}:{expected_role}")
                                elif len(matches) != 1:
                                    blockers.append(f"CANONICAL_AUTHORITY_DUPLICATE:{question_id}:{expected_role}")

    handoff_value = payload.get("claim_handoff_path")
    required_claim_ids = payload.get("required_claim_ids")
    if not isinstance(required_claim_ids, list) or not required_claim_ids or any(
        not isinstance(item, str) or not item.strip() for item in required_claim_ids
    ):
        blockers.append("POST_WRITER_REQUIRED_CLAIM_IDS_MISSING")
        required_claim_ids = []
    required_claim_ids = [str(item).strip() for item in required_claim_ids]
    handoff_path = _resolve(sidecar.parent, handoff_value)
    if handoff_path is None:
        blockers.append("POST_WRITER_CLAIM_HANDOFF_MISSING")
    else:
        if trusted_handoff is not None and handoff_path != trusted_handoff:
            blockers.append("POST_WRITER_CLAIM_HANDOFF_PROVENANCE_MISMATCH")
        else:
            _, actual_handoff_sha256, handoff = _read_json_bytes_once(handoff_path)
            if trusted_handoff_sha256 is not None and (
                not _valid_sha(trusted_handoff_sha256)
                or actual_handoff_sha256 is None
                or actual_handoff_sha256.lower() != trusted_handoff_sha256.lower()
            ):
                blockers.append("POST_WRITER_CLAIM_HANDOFF_PROVENANCE_MISMATCH")
            if handoff is None:
                blockers.append("POST_WRITER_CLAIM_HANDOFF_UNREADABLE")
            else:
                if handoff.get("status") != "PASS" or handoff.get("result_authority") != R1_AUTHORITY:
                    blockers.append("POST_WRITER_CLAIM_HANDOFF_INVALID")
                claims = handoff.get("claims")
                if not isinstance(claims, list) or not claims:
                    blockers.append("POST_WRITER_CLAIMS_MISSING")
                else:
                    claim_ids: set[str] = set()
                    anchor_ids: set[str] = set()
                    for claim in claims:
                        if not isinstance(claim, dict):
                            blockers.append("POST_WRITER_CLAIM_INVALID")
                            continue
                        claim_id = str(claim.get("claim_id") or "").strip()
                        if not claim_id or claim_id in claim_ids:
                            blockers.append("POST_WRITER_DUPLICATE_CLAIM_ID" if claim_id else "POST_WRITER_CLAIM_ID_MISSING")
                        claim_ids.add(claim_id)
                        if claim.get("claim_check_status") != "PASS":
                            blockers.append(f"POST_WRITER_CLAIM_NOT_APPROVED:{claim_id}")
                        trace = claim.get("traceability")
                        primary = trace.get("primary_claim_anchor") if isinstance(trace, dict) else None
                        if not isinstance(primary, dict):
                            blockers.append(f"POST_WRITER_PRIMARY_ANCHOR_MISSING:{claim_id}")
                            continue
                        anchor_id = str(primary.get("anchor_id") or "").strip()
                        anchor_claim_id = str(primary.get("claim_id") or "").strip()
                        if not anchor_id:
                            blockers.append(f"POST_WRITER_PRIMARY_ANCHOR_MISSING:{claim_id}")
                        if anchor_claim_id != claim_id:
                            blockers.append(f"POST_WRITER_CLAIM_ANCHOR_MISMATCH:{claim_id}")
                        if primary.get("role") != "PRIMARY_CLAIM_ANCHOR":
                            blockers.append(f"POST_WRITER_PRIMARY_ANCHOR_INVALID:{claim_id}")
                        if anchor_id and anchor_id in anchor_ids:
                            blockers.append(f"POST_WRITER_DUPLICATE_PRIMARY_ANCHOR:{anchor_id}")
                        anchor_ids.add(anchor_id)
                        if not str(primary.get("reference") or "").strip():
                            blockers.append(f"POST_WRITER_PRIMARY_ANCHOR_UNLOCATABLE:{claim_id}")
                        secondary = trace.get("secondary_references", []) if isinstance(trace, dict) else []
                        if not isinstance(secondary, list) or any(
                            not isinstance(item, dict) or not str(item.get("reference") or item.get("anchor_id") or "").strip()
                            for item in secondary
                        ):
                            blockers.append(f"POST_WRITER_SECONDARY_REFERENCE_INVALID:{claim_id}")
                    if set(required_claim_ids) != claim_ids:
                        blockers.append("POST_WRITER_REQUIRED_CLAIM_SET_MISMATCH")

                if paper_bytes is not None and not any(
                    token == "POST_WRITER_CLAIM_HANDOFF_PROVENANCE_MISMATCH" for token in blockers
                ):
                    bounded_mode = bool(payload.get("require_bounded_markers")) or any(
                        isinstance(c, dict) and isinstance(c.get("paper_span_reference"), dict)
                        for c in (claims if isinstance(claims, list) else [])
                    )
                    if bounded_mode:
                        paper_text = None
                        try:
                            paper_text = paper_bytes.decode("utf-8")
                        except UnicodeDecodeError:
                            blockers.append("POST_WRITER_PAPER_SOURCE_UNREADABLE")
                        spans: list[tuple[int, int, str]] = []
                        for claim in claims if isinstance(claims, list) else []:
                            cid = str(claim.get("claim_id") or "") if isinstance(claim, dict) else ""
                            if paper_text is None:
                                continue
                            starts = _exact_marker_matches(paper_text, cid, "START")
                            ends = _exact_marker_matches(paper_text, cid, "END")
                            if len(starts) != 1 or len(ends) != 1 or starts[0].end() >= ends[0].start():
                                blockers.append(f"POST_WRITER_BOUNDED_SPAN_INVALID:{cid}")
                            else:
                                spans.append((starts[0].start(), ends[0].start(), cid))
                        for left, right in zip(sorted(spans), sorted(spans)[1:]):
                            if right[0] < left[1]:
                                blockers.append("POST_WRITER_BOUNDED_SPAN_OVERLAP")
                    for claim in claims if isinstance(claims, list) else []:
                        trace = claim.get("traceability") if isinstance(claim, dict) else None
                        primary = trace.get("primary_claim_anchor") if isinstance(trace, dict) else None
                        reference = str(primary.get("reference") or "") if isinstance(primary, dict) else ""
                        marker = reference.split("#", 1)[1] if "#" in reference else reference
                        if not bounded_mode and (not marker or marker.encode("utf-8") not in paper_bytes):
                            claim_id = str(claim.get("claim_id") or "") if isinstance(claim, dict) else ""
                            blockers.append(f"POST_WRITER_PAPER_ANCHOR_NOT_FOUND:{claim_id}")

    unique = list(dict.fromkeys(blockers))
    fallback = {
        "FALLBACK_USED": False,
        "LATEST_FILE_SELECTED": False,
        "RAW_RESULT_SELECTED": False,
        "ANCHOR_GUESSED": False,
        "HASH_AUTO_UPDATED": False,
        "DOWNSTREAM_FINALIZATION_ALLOWED": not unique,
    }
    return {
        "status": "PASS" if not unique else "BLOCKED",
        "schema_version": payload.get("schema_version"),
        "reentry_path": str(sidecar),
        "paper_path": str(paper_path) if paper_path else None,
        "canonical_index_path": str(index_path) if index_path else None,
        "claim_handoff_path": str(handoff_path) if handoff_path else None,
        "trusted_index_path": str(trusted_index) if trusted_index else None,
        "trusted_handoff_path": str(trusted_handoff) if trusted_handoff else None,
        "paper_anchor_checked": paper_bytes is not None and handoff is not None,
        "paper_anchor_passed": not any(item.startswith("POST_WRITER_PAPER_ANCHOR_NOT_FOUND:") for item in blockers),
        "blockers": unique,
        "warnings": warnings,
        "fallback": fallback,
        "semantic_claim_recheck_performed": False,
        "inc_e_included": False,
        "downstream_gate_reached": True,
    }


def require_valid_post_writer_reentry(
    reentry_path: str | Path,
    *,
    repo_root: str | Path | None = None,
) -> dict[str, Any]:
    result = validate_post_writer_reentry(reentry_path, repo_root=repo_root)
    if result["status"] != "PASS":
        raise PostWriterReentryBlocked(result)
    return result
