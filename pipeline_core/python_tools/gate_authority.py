"""Optional single authority record for human Gates.

Legacy cases remain readable from their Markdown gate files.  A case that
declares ``gate_authority_required`` instead uses one JSON record as its gate
status authority, with the Markdown review bound as immutable evidence.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from pipeline_core.quality_gates.result_acceptance_contract import (
    ResultAcceptanceContractError,
    validate_result_acceptance_contract,
)


AUTHORITY_RELATIVE_PATH = Path("human_review") / "gate_authority.json"
AUTHORITY_SCHEMA_VERSION = "human_gate_authority_v1"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _fail(reason: str, **extra: Any) -> dict[str, Any]:
    return {"status": "FAIL", "reason": reason, **extra}


def evaluate_gate_authority(
    case_dir: str | Path,
    *,
    case_id: str | None,
    gate_id: str,
    evidence_relative_path: str,
) -> dict[str, Any] | None:
    """Return None for legacy cases, otherwise a fail-closed authority verdict."""

    root = Path(case_dir)
    authority_path = root / AUTHORITY_RELATIVE_PATH
    config_required = False
    try:
        config = json.loads((root / "case_config.json").read_text(encoding="utf-8-sig"))
        config_required = isinstance(config, dict) and config.get("gate_authority_required") is True
    except (OSError, UnicodeError, json.JSONDecodeError):
        pass
    if not authority_path.is_file():
        return _fail("GATE_AUTHORITY_MISSING") if config_required else None
    try:
        payload = json.loads(authority_path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return _fail("GATE_AUTHORITY_INVALID")
    if not isinstance(payload, dict) or payload.get("schema_version") != AUTHORITY_SCHEMA_VERSION:
        return _fail("GATE_AUTHORITY_INVALID")
    if not case_id or payload.get("case_id") != case_id:
        return _fail("GATE_AUTHORITY_CASE_MISMATCH")
    gates = payload.get("gates")
    record = gates.get(gate_id) if isinstance(gates, dict) else None
    if not isinstance(record, dict):
        return _fail("GATE_AUTHORITY_GATE_MISSING")
    if record.get("status") != "APPROVED":
        return _fail("GATE_AUTHORITY_NOT_APPROVED")
    if not isinstance(record.get("confirmed_by"), str) or not record["confirmed_by"].strip():
        return _fail("GATE_AUTHORITY_CONFIRMER_MISSING")
    if not isinstance(record.get("confirmed_at"), str) or not record["confirmed_at"].strip():
        return _fail("GATE_AUTHORITY_TIME_MISSING")
    if record.get("evidence_path") != evidence_relative_path:
        return _fail("GATE_AUTHORITY_EVIDENCE_PATH_MISMATCH")
    evidence = root / evidence_relative_path
    if not evidence.is_file() or record.get("evidence_sha256") != _sha256(evidence):
        return _fail("GATE_AUTHORITY_EVIDENCE_HASH_MISMATCH")
    semantic: dict[str, Any] | None = None
    if gate_id == "GATE3":
        raw_contract = record.get("semantic_acceptance_contract")
        if not isinstance(raw_contract, dict):
            return _fail("GATE3_SEMANTIC_ACCEPTANCE_MISSING")
        try:
            semantic = validate_result_acceptance_contract(raw_contract)
        except ResultAcceptanceContractError as exc:
            return _fail(str(exc))
        if semantic["status"] != "PASS":
            return _fail("GATE3_SEMANTIC_ACCEPTANCE_INCOMPLETE", semantic_acceptance=semantic)
    return {
        "status": "PASS",
        "reason": "GATE_AUTHORITY_BOUND_AND_APPROVED",
        "authority_path": AUTHORITY_RELATIVE_PATH.as_posix(),
        "confirmed_by": record["confirmed_by"],
        "confirmed_at": record["confirmed_at"],
        "semantic_acceptance": semantic,
    }
