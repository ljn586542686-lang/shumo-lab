"""Canonical result authority index for candidate packages.

This module keeps question-specific result payloads unchanged and records one
authoritative pointer for each ``(question_id, artifact_role)`` scope.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


SCHEMA_VERSION = "x5_f0_canonical_result_index_v1"
RESULT_SCHEMA_ID = "x5_f0_canonical_result_schema_v1"
PUBLIC_MACHINE_FIELDS = frozenset({
    "result_schema_id", "producer_schema_version", "generated_at", "created_at",
    "run_id", "case_id", "status", "success", "metrics", "primary_results",
    "limitations", "model_id", "model_family", "fatal_count", "warning_count",
})
PRIMARY_ROLE = "primary_result"
VALIDATION_ROLE = "validation_evidence"
ALLOWED_ROLES = {PRIMARY_ROLE, VALIDATION_ROLE}


class CanonicalResultIndexBlocked(RuntimeError):
    def __init__(self, result: dict[str, Any]) -> None:
        self.result = result
        super().__init__(json.dumps(result, ensure_ascii=False))


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _payload(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _case_relative(case_dir: Path, path: Path) -> str:
    return path.resolve().relative_to(case_dir.resolve()).as_posix()


def _declared_artifacts(case_dir: Path, config: dict[str, Any]) -> list[dict[str, Any]]:
    raw = config.get("authoritative_result_artifacts", [])
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise CanonicalResultIndexBlocked({"status": "BLOCKED", "code": "CANONICAL_ARTIFACT_DECLARATION_INVALID"})
    rows: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            raise CanonicalResultIndexBlocked({"status": "BLOCKED", "code": "CANONICAL_ARTIFACT_DECLARATION_INVALID"})
        question_id = item.get("question_id")
        role = item.get("artifact_role")
        path_text = item.get("path")
        if not isinstance(question_id, str) or not question_id.strip() or role not in ALLOWED_ROLES:
            raise CanonicalResultIndexBlocked({"status": "BLOCKED", "code": "CANONICAL_ARTIFACT_SCOPE_INVALID"})
        if not isinstance(path_text, str) or not path_text.strip() or Path(path_text).is_absolute() or ".." in Path(path_text).parts:
            raise CanonicalResultIndexBlocked({"status": "BLOCKED", "code": "CANONICAL_ARTIFACT_PATH_INVALID"})
        rows.append({"question_id": question_id, "artifact_role": role, "path": path_text})
    return rows


def _discover(case_dir: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    summary = case_dir / "output" / "result_summary.json"
    if summary.is_file():
        data = _payload(summary) or {}
        question_id = data.get("question_id") or data.get("question") or data.get("case_id") or "case"
        rows.append({"question_id": str(question_id), "artifact_role": PRIMARY_ROLE, "path": _case_relative(case_dir, summary)})
    return rows


def _entries(case_dir: Path, config: dict[str, Any], run_id: str) -> list[dict[str, Any]]:
    declared = _declared_artifacts(case_dir, config)
    rows = declared if declared else _discover(case_dir)
    if not rows:
        raise CanonicalResultIndexBlocked({"status": "BLOCKED", "code": "CANONICAL_REQUIRED_SCOPE_MISSING"})
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault((row["question_id"], row["artifact_role"]), []).append(row)

    required_raw = config.get("authoritative_required_scopes")
    required = required_raw if isinstance(required_raw, list) else list(grouped)
    required_keys: list[tuple[str, str]] = []
    for scope in required:
        if not isinstance(scope, (list, tuple)) or len(scope) != 2 or scope[1] not in ALLOWED_ROLES:
            raise CanonicalResultIndexBlocked({"status": "BLOCKED", "code": "CANONICAL_REQUIRED_SCOPE_INVALID"})
        required_keys.append((str(scope[0]), str(scope[1])))

    blockers: list[str] = []
    entries: list[dict[str, Any]] = []
    for key in required_keys:
        matches = grouped.get(key, [])
        if not matches:
            blockers.append(f"CANONICAL_AUTHORITY_MISSING:{key[0]}:{key[1]}")
        elif len(matches) > 1:
            blockers.append(f"CANONICAL_AUTHORITY_DUPLICATE:{key[0]}:{key[1]}")
        else:
            row = matches[0]
            artifact = (case_dir / row["path"]).resolve()
            if not artifact.is_file() or case_dir.resolve() not in artifact.parents:
                blockers.append(f"CANONICAL_ARTIFACT_MISSING:{key[0]}:{key[1]}")
                continue
            data = _payload(artifact)
            if data is None:
                blockers.append(f"CANONICAL_ARTIFACT_MALFORMED:{key[0]}:{key[1]}")
                continue
            declared_role = data.get("artifact_role")
            if declared_role in ALLOWED_ROLES and declared_role != key[1]:
                blockers.append(f"CANONICAL_ROLE_MISMATCH:{key[0]}:{key[1]}")
                continue
            entries.append({
                "question_id": key[0],
                "artifact_role": key[1],
                "artifact_path": row["path"],
                "artifact_schema": data.get("schema_version"),
                "result_schema_id": data.get("result_schema_id"),
                "case_id": data.get("case_id"),
                "run_id": data.get("run_id") or run_id,
                "sha256": _sha256(artifact),
                "status": data.get("status") or data.get("freeze_status") or "UNKNOWN",
            })
    if blockers:
        raise CanonicalResultIndexBlocked({"status": "BLOCKED", "code": "CANONICAL_AUTHORITY_SCOPE_INVALID", "blockers": blockers})
    return entries


def build_index(case_dir: Path, config: dict[str, Any], run_id: str) -> dict[str, Any]:
    if not config.get("authoritative_result_artifacts") and not config.get("authoritative_required_scopes") and not (case_dir / "output" / "result_summary.json").is_file():
        # Explicit legacy compatibility for pre-R1 training fixtures. This is
        # not an authority and finalization must reject it; it prevents a
        # silent fallback while keeping unrelated legacy package tests valid.
        return {
            "schema_version": SCHEMA_VERSION,
            "status": "LEGACY_UNAVAILABLE",
            "authority_scope_key": ["question_id", "artifact_role"],
            "result_authority": "primary_result",
            "validation_authority": "validation_evidence",
            "entries": [],
            "fallback_policy": "EXPLICIT_LEGACY_UNAVAILABLE",
            "run_id": run_id,
        }
    entries = _entries(case_dir, config, run_id)
    return {
        "schema_version": SCHEMA_VERSION,
        "status": "PASS",
        "authority_scope_key": ["question_id", "artifact_role"],
        "result_authority": "primary_result",
        "validation_authority": "validation_evidence",
        "entries": entries,
        "fallback_policy": "NONE",
    }


def validate_index(index_path: Path, case_dir: Path) -> tuple[list[str], dict[str, Any] | None]:
    try:
        data = json.loads(index_path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return ["CANONICAL_INDEX_UNREADABLE"], None
    if not isinstance(data, dict) or data.get("schema_version") != SCHEMA_VERSION:
        return ["CANONICAL_INDEX_SCHEMA_INVALID"], data if isinstance(data, dict) else None
    if data.get("status") != "PASS":
        return ["CANONICAL_INDEX_LEGACY_UNAVAILABLE"], data
    entries = data.get("entries")
    if not isinstance(entries, list) or not entries:
        return ["CANONICAL_INDEX_ENTRIES_MISSING"], data
    seen: set[tuple[str, str]] = set()
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
        elif _sha256(artifact) != expected:
            blockers.append(f"CANONICAL_HASH_MISMATCH:{scope[0]}:{scope[1]}")
    return list(dict.fromkeys(blockers)), data
