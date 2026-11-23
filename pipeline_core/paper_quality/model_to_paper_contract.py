"""Bind case-local approved results to the existing writer handoff.

The shared validator discovers a case-local handoff and reads its declared
source bindings. It never recomputes or edits canonical results. Case facts,
source identities, authority state, and claim boundaries remain case-local.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any, Iterable

from pipeline_core.python_tools.transition_case import (
    preflight_public_stage,
    validate_authoritative_state,
)


SCHEMA_VERSION = "formal_model_to_paper_contract_v1"
STALE_RESULT_CONFLICT = "STALE_RESULT_CONFLICT"
SECTION_START = "## F.8 CANONICAL_RESULT_TO_PAPER_BINDING"
SECTION_END = "<!-- END CANONICAL_RESULT_TO_PAPER_BINDING -->"
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$", flags=re.IGNORECASE)


class ModelToPaperContractBlocked(RuntimeError):
    def __init__(self, result: dict[str, Any]) -> None:
        self.result = result
        super().__init__(json.dumps(result, ensure_ascii=False))


def _blocked(code: str, **extra: Any) -> ModelToPaperContractBlocked:
    return ModelToPaperContractBlocked({"status": "BLOCKED", "code": code, **extra})


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise _blocked("CANONICAL_SOURCE_UNREADABLE", path=str(path)) from exc


def _case_id(case_dir: Path) -> str:
    config_path = case_dir / "case_config.json"
    if config_path.is_file():
        try:
            payload = json.loads(config_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            payload = {}
        if isinstance(payload, dict) and str(payload.get("case_id", "")).strip():
            return str(payload["case_id"]).strip()
    return case_dir.name


def _require_file(case_dir: Path, relative: Path) -> Path:
    root = case_dir.resolve()
    path = (root / relative).resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise _blocked("CANONICAL_SOURCE_PATH_INVALID", path=relative.as_posix()) from exc
    if not path.is_file():
        raise _blocked("CANONICAL_SOURCE_MISSING", path=relative.as_posix())
    return path


def _relative_file(case_dir: Path, candidate: Path) -> Path:
    root = case_dir.resolve()
    path = candidate.resolve()
    try:
        relative = path.relative_to(root)
    except ValueError as exc:
        raise _blocked("CANONICAL_SOURCE_PATH_INVALID", path=str(candidate)) from exc
    if not path.is_file():
        raise _blocked("CANONICAL_SOURCE_MISSING", path=relative.as_posix())
    return relative


def _load_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise _blocked("CASE_CONTRACT_INVALID", path=str(path)) from exc
    if not isinstance(payload, dict):
        raise _blocked("CASE_CONTRACT_SCHEMA_INVALID", path=str(path))
    return payload


def _load_case_config(case_dir: Path) -> dict[str, Any]:
    path = case_dir / "case_config.json"
    return _load_json(path) if path.is_file() else {}


def _discover_handoff(case_dir: Path, config: dict[str, Any]) -> Path:
    configured = config.get("model_to_paper_contract_path") or config.get("writer_handoff_path")
    candidates: list[Path] = []
    if isinstance(configured, str) and configured.strip():
        candidates.append(case_dir / configured)
    candidates.append(case_dir / "handoff" / "RESULT_HANDOFF.md")
    candidates.extend(sorted(case_dir.rglob("*WRITER_HANDOFF*.md")))
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    raise _blocked("WRITER_HANDOFF_MISSING", case_id=_case_id(case_dir))


def _resolve_declared_path(case_dir: Path, handoff: Path, raw_path: str) -> Path:
    raw = Path(raw_path.strip().strip("`"))
    candidates = [case_dir / raw, handoff.parent / raw]
    for candidate in candidates:
        if candidate.is_file():
            return _relative_file(case_dir, candidate)
    raise _blocked("CANONICAL_SOURCE_MISSING", path=raw.as_posix())


def _source_row(case_dir: Path, relative: Path, expected_sha256: str, section: str) -> dict[str, str]:
    path = _require_file(case_dir, relative)
    actual_sha256 = _sha256(path)
    expected = expected_sha256.strip().lower()
    if not _SHA256_RE.fullmatch(expected):
        raise _blocked("CANONICAL_SOURCE_SHA256_INVALID", path=relative.as_posix())
    if actual_sha256 != expected:
        raise _blocked(
            "CANONICAL_SOURCE_SHA256_MISMATCH",
            path=relative.as_posix(),
            expected_sha256=expected,
            actual_sha256=actual_sha256,
        )
    return {"path": relative.as_posix(), "sha256": actual_sha256, "section": section}


def _parse_handoff_table_sources(case_dir: Path, handoff: Path, text: str) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for line in text.splitlines():
        if "|" not in line:
            continue
        cells = [cell.strip().strip("`") for cell in line.strip().strip("|").split("|")]
        hashes = [cell for cell in cells if _SHA256_RE.fullmatch(cell)]
        if len(hashes) != 1:
            continue
        path_cells = [
            cell
            for cell in cells
            if ("/" in cell or "\\" in cell or cell.lower().endswith((".md", ".json", ".txt", ".csv")))
            and not _SHA256_RE.fullmatch(cell)
        ]
        if not path_cells:
            continue
        raw_path = path_cells[0]
        relative = _resolve_declared_path(case_dir, handoff, raw_path)
        descriptions = [cell for cell in cells if cell not in {raw_path, hashes[0]} and cell]
        section = descriptions[-1] if descriptions else "case-local canonical source"
        rows.append(_source_row(case_dir, relative, hashes[0], section))
    unique: dict[str, dict[str, str]] = {}
    for row in rows:
        unique[row["path"]] = row
    return list(unique.values())


def _load_canonical_sources(case_dir: Path, handoff: Path, text: str, config: dict[str, Any]) -> list[dict[str, str]]:
    declared = config.get("current_result_sources")
    rows: list[dict[str, str]] = []
    if isinstance(declared, list) and declared:
        for item in declared:
            if not isinstance(item, dict):
                raise _blocked("CASE_CONTRACT_SCHEMA_INVALID", field="current_result_sources")
            raw_path = str(item.get("path", "")).strip()
            expected = str(item.get("sha256", "")).strip()
            if not raw_path or not expected:
                raise _blocked("CASE_CONTRACT_SCHEMA_INVALID", field="current_result_sources")
            relative = _resolve_declared_path(case_dir, handoff, raw_path)
            section = str(item.get("artifact_role") or item.get("question_id") or "case-local canonical source")
            rows.append(_source_row(case_dir, relative, expected, section))
    if not rows:
        rows = _parse_handoff_table_sources(case_dir, handoff, text)
    if not rows:
        raise _blocked("CANONICAL_SOURCE_BINDINGS_MISSING", path=str(handoff))
    return rows


def _find_claim_boundary(case_dir: Path, handoff: Path, text: str, sources: list[dict[str, str]]) -> dict[str, str]:
    for row in sources:
        if "claim_boundary" in row["path"].lower():
            return row
    candidates = sorted(
        path for path in case_dir.rglob("*.md") if "claim_boundary" in path.name.lower() and path != handoff
    )
    for candidate in candidates:
        relative = _relative_file(case_dir, candidate)
        if relative.as_posix() in text or relative.name in text:
            return {"path": relative.as_posix(), "sha256": _sha256(candidate), "section": "claim boundary"}
    raise _blocked("CLAIM_BOUNDARY_BINDING_MISSING", handoff=str(handoff))


def _authority_snapshot(config: dict[str, Any], handoff_text: str) -> dict[str, Any]:
    fields = (
        "current_writer_authority",
        "track_b_status",
        "track_c_status",
        "final_submission",
        "official_submission_mode",
        "human_gate_status",
    )
    snapshot = {key: config[key] for key in fields if key in config}
    if not any(token in handoff_text for token in ("RESULT_AUTHORITY", "CURRENT_WRITER_AUTHORITY", "WRITER_CONTRACT_REQUIRED")):
        raise _blocked("AUTHORITY_BOUNDARY_MISSING")
    return snapshot


def _supporting_sources(case_dir: Path) -> list[dict[str, str]]:
    manifests = sorted(case_dir.rglob("writer_manifest.json"))
    if not manifests:
        return []
    payload = _load_json(manifests[0])
    rows: list[dict[str, str]] = []
    for item in payload.get("supporting_source_bindings", []):
        if not isinstance(item, dict):
            raise _blocked("WRITER_SUPPORTING_BINDINGS_INVALID", path=str(manifests[0]))
        raw_path = str(item.get("path", "")).strip()
        expected = str(item.get("sha256", "")).strip()
        if raw_path and expected:
            relative = _relative_file(case_dir, case_dir / raw_path)
            rows.append(_source_row(case_dir, relative, expected, "writer supporting source"))
    return rows


def build_contract(case_dir: Path) -> dict[str, Any]:
    case_dir = case_dir.resolve()
    config = _load_case_config(case_dir)
    handoff = _discover_handoff(case_dir, config)
    handoff_text = _read(handoff)
    canonical_sources = _load_canonical_sources(case_dir, handoff, handoff_text, config)
    claim_boundary = _find_claim_boundary(case_dir, handoff, handoff_text, canonical_sources)
    authority = _authority_snapshot(config, handoff_text)
    # Older case-local handoffs predate the explicit marker. Preserve their
    # existing fail-closed semantics with the shared default policy.
    stale_policy = "STALE_RESULT_CONFLICT"
    handoff_relative = _relative_file(case_dir, handoff)
    return {
        "schema_version": SCHEMA_VERSION,
        "case_id": _case_id(case_dir),
        "writer_contract_required": True,
        "gpt_reminder_required_for_canonical_result": False,
        "canonical_sources": canonical_sources,
        "supporting_sources": _supporting_sources(case_dir),
        "claim_boundary": claim_boundary,
        "authority": authority,
        "writer_handoff": {
            "path": handoff_relative.as_posix(),
            "sha256": _sha256(handoff),
        },
        "stale_input_policy": stale_policy,
        "status": "PASS",
    }


def render_binding_section(contract: dict[str, Any]) -> str:
    lines = [
        SECTION_START,
        "",
        "本节由现有 case-local contract 生成，是 writer 的 canonical source 与 claim boundary 绑定。",
        "",
        "- `WRITER_CONTRACT_REQUIRED`: `YES`",
        "- `GPT_REMINDER_REQUIRED_FOR_CANONICAL_RESULT`: `NO`",
        f"- `STALE_INPUT_POLICY`: `{contract.get('stale_input_policy', STALE_RESULT_CONFLICT)}`",
        "",
        "### Canonical source identity",
        "",
        "| CANONICAL_SOURCE_PATH | CANONICAL_SOURCE_SHA256 | SOURCE_SECTION |",
        "|---|---|---|",
    ]
    lines.extend(f"| `{row['path']}` | `{row['sha256']}` | {row['section']} |" for row in contract["canonical_sources"])
    claim = contract.get("claim_boundary")
    if isinstance(claim, dict):
        lines.extend(["", f"- `CLAIM_BOUNDARY_PATH`: `{claim['path']}`", f"- `CLAIM_BOUNDARY_SHA256`: `{claim['sha256']}`"])
    lines.extend(
        [
            "",
            "### Writer precedence and fail-closed rule",
            "",
            "- The writer must validate this existing case-local contract before writing numerical results or claim strength.",
            "- Conflicting result sources must stop with `STALE_RESULT_CONFLICT`; the writer must not silently choose or overwrite either source.",
            "",
            SECTION_END,
            "",
        ]
    )
    return "\n".join(lines)


def bind_existing_writer_handoff(case_dir: Path) -> Path:
    contract = build_contract(case_dir)
    handoff = _require_file(case_dir, Path(contract["writer_handoff"]["path"]))
    text = _read(handoff)
    for row in contract["canonical_sources"]:
        if row["path"] not in text or row["sha256"].lower() not in text.lower():
            raise _blocked("CANONICAL_SOURCE_IDENTITY_NOT_BOUND", path=row["path"])
    if SECTION_START in text:
        return handoff
    handoff.write_text(text.rstrip() + "\n\n" + render_binding_section(contract), encoding="utf-8")
    return handoff


def _validate_writer_manifest_bindings(path: Path, contract: dict[str, Any]) -> None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise _blocked("WRITER_MANIFEST_INVALID", path=str(path)) from exc
    if not isinstance(payload, dict) or not str(payload.get("case_id", "")).strip():
        raise _blocked("WRITER_MANIFEST_CASE_MISMATCH", path=str(path))
    if payload.get("case_id") != contract.get("case_id"):
        raise _blocked("WRITER_MANIFEST_CASE_MISMATCH", path=str(path))
    if payload.get("writer_schema_version") != "WRITER_SCHEMA_V2":
        raise _blocked("WRITER_SCHEMA_V2_REQUIRED", path=str(path))
    if "writer_generation_mode" in payload and payload.get("writer_generation_mode") != "independent_from_bound_sources":
        raise _blocked("WRITER_GENERATION_MODE_NOT_BOUND", path=str(path))
    if "manual_draft_consumed" in payload and payload.get("manual_draft_consumed") is not False:
        raise _blocked("MANUAL_DRAFT_CONSUMPTION_NOT_EXCLUDED", path=str(path))
    expected = {(row["path"], row["sha256"]) for row in contract["canonical_sources"]}
    bindings = payload.get("source_bindings")
    if bindings is None:
        return
    if not isinstance(bindings, list):
        raise _blocked("WRITER_SOURCE_BINDINGS_INVALID", path=str(path))
    actual: set[tuple[str, str]] = set()
    for row in bindings:
        if not isinstance(row, dict):
            raise _blocked("WRITER_SOURCE_BINDINGS_INVALID", path=str(path))
        actual.add((str(row.get("path", "")), str(row.get("sha256", "")).lower()))
    if actual != expected:
        raise ModelToPaperContractBlocked(
            {
                "status": "BLOCKED",
                "code": "WRITER_SOURCE_BINDINGS_MISMATCH",
                "expected": sorted(expected),
                "actual": sorted(actual),
            }
        )


def _configured_case_path(case_dir: Path, config: dict[str, Any], field: str) -> Path | None:
    raw = config.get(field)
    if not isinstance(raw, str) or not raw.strip():
        return None
    candidate = Path(raw.strip())
    if candidate.is_absolute() or ".." in candidate.parts:
        raise _blocked("TRACK_B_PATH_INVALID", field=field)
    resolved = (case_dir / candidate).resolve()
    try:
        resolved.relative_to(case_dir.resolve())
    except ValueError as exc:
        raise _blocked("TRACK_B_PATH_INVALID", field=field) from exc
    return resolved


def _first_existing(candidates: Iterable[Path]) -> Path | None:
    return next((path.resolve() for path in candidates if path.is_file()), None)


def _validate_track_b_writer_authority(case_dir: Path, manifest_path: Path, config: dict[str, Any]) -> None:
    tier = str(config.get("submission_tier") or "training").strip().lower()
    required = tier in {"selection", "formal"} or config.get("track_b_required") is True
    if not required:
        return

    configured_review = _configured_case_path(case_dir, config, "track_b_review_path")
    configured_authorization = _configured_case_path(case_dir, config, "track_b_to_track_c_path")
    review = configured_review or _first_existing(
        sorted(case_dir.glob("paper/**/00_TRACK_B_REVIEW.md"))
    )
    authorization = configured_authorization or _first_existing(
        [case_dir / "handoff" / "TRACK_B_TO_TRACK_C.md"]
    )
    if review is None:
        raise _blocked("TRACK_B_REVIEW_MISSING")
    if authorization is None:
        raise _blocked("TRACK_B_TO_TRACK_C_AUTHORIZATION_MISSING")

    review_text = _read(review)
    if not re.search(
        r"(?im)^\s*TRACK_B_(?:REVIEW_VERDICT|DISPOSITION)\s*=\s*PASS(?:_WITH_LIMITATIONS)?\s*$",
        review_text,
    ):
        raise _blocked("TRACK_B_REVIEW_NOT_PASSED", path=str(review))
    authorization_text = _read(authorization)
    if not re.search(
        r"(?im)^\s*TRACK_C_EXECUTION_AUTHORIZATION\s*=\s*YES\s*$",
        authorization_text,
    ):
        raise _blocked("TRACK_C_EXECUTION_NOT_AUTHORIZED", path=str(authorization))

    manifest = _load_json(manifest_path)
    bindings = manifest.get("supporting_source_bindings")
    if not isinstance(bindings, list):
        raise _blocked("TRACK_B_SUPPORTING_BINDINGS_MISSING", path=str(manifest_path))
    actual = {
        (str(item.get("path") or ""), str(item.get("sha256") or "").lower())
        for item in bindings
        if isinstance(item, dict)
    }
    for required_path in (review, authorization):
        relative = _relative_file(case_dir, required_path).as_posix()
        identity = (relative, _sha256(required_path))
        if identity not in actual:
            raise _blocked("TRACK_B_SUPPORTING_BINDING_MISMATCH", path=relative)


def _validate_literature_stage(config: dict[str, Any]) -> None:
    tier = str(config.get("submission_tier") or "training").strip().lower()
    required = tier in {"selection", "formal"} or config.get("literature_stage_required") is True
    if not required:
        return
    status = str(config.get("literature_status") or "NOT_RUN").strip().upper()
    if status not in {"NOT_RUN", "GAP_OPEN", "VERIFIED", "NOT_REQUIRED"}:
        raise _blocked("LITERATURE_STATUS_INVALID", literature_status=status)
    if status == "NOT_RUN":
        raise _blocked("LITERATURE_STAGE_NOT_RUN")
    if status == "GAP_OPEN":
        raise _blocked("LITERATURE_GAP_OPEN")


def validate_writer_input(case_dir: Path, input_paths: Iterable[str | Path] = ()) -> dict[str, Any]:
    workflow_state = validate_authoritative_state(case_dir)
    if workflow_state.get("status") == "FAIL":
        raise _blocked(
            "WORKFLOW_STATE_INVALID",
            reason=workflow_state.get("reason"),
            workflow_state=workflow_state,
        )
    if workflow_state.get("status") == "PASS":
        paper_preflight = preflight_public_stage("paper", workflow_state)
        if paper_preflight.get("status") != "PASS":
            raise _blocked(
                "PAPER_STAGE_NOT_ALLOWED_BY_CURRENT_STATE",
                workflow_state=workflow_state,
                stage_preflight=paper_preflight,
            )
    contract = build_contract(case_dir)
    handoff = _require_file(case_dir, Path(contract["writer_handoff"]["path"]))
    handoff_text = _read(handoff)
    for row in contract["canonical_sources"]:
        if row["path"] not in handoff_text or row["sha256"].lower() not in handoff_text.lower():
            raise _blocked("CANONICAL_SOURCE_IDENTITY_NOT_BOUND", path=row["path"])
    paths = [
        (case_dir / raw).resolve() if not Path(raw).is_absolute() else Path(raw).resolve()
        for raw in input_paths
    ]
    manifests = [path for path in paths if path.name == "writer_manifest.json"]
    if len(manifests) > 1:
        raise _blocked("WRITER_MANIFEST_REQUIRED", count=len(manifests))
    if manifests:
        _validate_writer_manifest_bindings(manifests[0], contract)
        _validate_track_b_writer_authority(case_dir, manifests[0], _load_case_config(case_dir))
        _validate_literature_stage(_load_case_config(case_dir))
    return {
        "status": "PASS",
        "code": "WRITER_INPUT_CONTRACT_READY",
        "writer_contract_required": True,
        "real_writer_consumption_proven": False,
        "canonical_sources": contract["canonical_sources"],
        "stale_conflicts": [],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate the case-local canonical-to-paper writer contract")
    parser.add_argument("--case-dir", required=True)
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--validate", action="store_true")
    parser.add_argument("--input", action="append", default=[])
    args = parser.parse_args()
    case_dir = Path(args.case_dir)
    if args.write:
        path = bind_existing_writer_handoff(case_dir)
        print(json.dumps({"status": "PASS", "code": "MODEL_TO_PAPER_CONTRACT_GENERATED", "path": str(path)}, ensure_ascii=False))
    if args.validate:
        print(json.dumps(validate_writer_input(case_dir, args.input), ensure_ascii=False))
    if not args.write and not args.validate:
        print(json.dumps(build_contract(case_dir), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
