"""Shared helpers for the X5-F0 surgical runner layer.

This module is intentionally small and conservative. It provides file
inventory, risk reporting, asset manifest updates, guarded promotion, simple
PDF checks, result bad-value scans, and subprocess timeout support. It does
not run models, create final_submit, or pass human gates.
"""

from __future__ import annotations

import csv
import hashlib
import json
import math
import os
import shutil
import subprocess
import sys
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent
ASSET_TYPES = ["result", "figure", "table", "paper_pdf", "candidate_package"]
RESULT_SUMMARY_REQUIRED_FIELDS = ("schema_version", "generated_at", "run_id", "case_id")


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def timestamp_id(prefix: str = "run") -> str:
    return f"{prefix}_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}"


def read_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    for encoding in ("utf-8", "utf-8-sig"):
        try:
            return json.loads(path.read_text(encoding=encoding))
        except (json.JSONDecodeError, UnicodeDecodeError):
            continue
    return default


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def rel_path(path: Path, root: Path = ROOT) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.resolve().as_posix()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_path(path: Path) -> str | None:
    if not path.exists():
        return None
    if path.is_file():
        return sha256_file(path)
    h = hashlib.sha256()
    for item in sorted(p for p in path.rglob("*") if p.is_file()):
        h.update(rel_path(item, path).encode("utf-8", errors="replace"))
        h.update(sha256_file(item).encode("ascii"))
    return h.hexdigest()


def ensure_assets_manifest(root: Path = ROOT) -> Path:
    manifest_path = root / "manifests" / "assets_manifest.json"
    if not manifest_path.exists():
        payload = {
            "schema_version": "x5_f0_assets_manifest_v1",
            "status": "MINIMAL_IDENTITY_BINDING",
            "note": "Paths are attributes, not identities. Each registered resource gets a UUID asset_id.",
            "allowed_types": ASSET_TYPES,
            "assets": [],
            "created_at": utc_now(),
            "updated_at": utc_now(),
        }
        write_json(manifest_path, payload)
    return manifest_path


def register_asset(
    *,
    root: Path = ROOT,
    asset_type: str,
    path: Path,
    source_script: str,
    source_data: list[str],
    run_id: str,
    status: str,
) -> dict[str, Any]:
    if asset_type not in ASSET_TYPES:
        raise ValueError(f"Unsupported asset type: {asset_type}")
    manifest_path = ensure_assets_manifest(root)
    payload = read_json(manifest_path, default={}) or {}
    assets = payload.setdefault("assets", [])
    entry = {
        "asset_id": str(uuid.uuid4()),
        "type": asset_type,
        "path": rel_path(path, root),
        "source_script": source_script,
        "source_data": source_data,
        "run_id": run_id,
        "sha256": sha256_path(path),
        "created_at": utc_now(),
        "status": status,
    }
    assets.append(entry)
    payload["updated_at"] = utc_now()
    write_json(manifest_path, payload)
    return entry


def add_risk(
    risks: list[dict[str, Any]],
    *,
    severity: str,
    category: str,
    message: str,
    fatal: bool = False,
    path: str | None = None,
    recommendation: str | None = None,
) -> None:
    risks.append(
        {
            "severity": severity.upper(),
            "category": category,
            "message": message,
            "fatal": fatal,
            "path": path,
            "recommendation": recommendation,
            "created_at": utc_now(),
        }
    )


def has_fatal_risk(risks: list[dict[str, Any]]) -> bool:
    return any(bool(item.get("fatal")) for item in risks)


def markdown_table(rows: list[dict[str, Any]], columns: list[tuple[str, str]]) -> str:
    lines = [
        "|" + "|".join(label for _, label in columns) + "|",
        "|" + "|".join("---" for _ in columns) + "|",
    ]
    for row in rows:
        values: list[str] = []
        for key, _label in columns:
            value = row.get(key, "")
            if isinstance(value, (list, tuple)):
                value = ", ".join(str(v) for v in value)
            if value is None:
                value = ""
            values.append(str(value).replace("\n", " "))
        lines.append("|" + "|".join(values) + "|")
    return "\n".join(lines)


def write_risk_summary(
    run_dir: Path,
    risks: list[dict[str, Any]],
    *,
    source_provenance: dict[str, Any] | None = None,
) -> None:
    fatal_count = sum(1 for item in risks if item.get("fatal"))
    warning_count = sum(1 for item in risks if item.get("severity") == "WARNING")
    payload = {
        "schema_version": "x5_f0_risk_summary_v1",
        "generated_at": utc_now(),
        "fatal_count": fatal_count,
        "warning_count": warning_count,
        "risks": risks,
        "final_submit_created": False,
    }
    if source_provenance is not None:
        payload["source_provenance"] = source_provenance
    write_json(run_dir / "risk_summary.json", payload)
    rows = [
        {
            "severity": item.get("severity"),
            "fatal": item.get("fatal"),
            "category": item.get("category"),
            "message": item.get("message"),
            "path": item.get("path") or "",
        }
        for item in risks
    ]
    body = markdown_table(
        rows,
        [
            ("severity", "Severity"),
            ("fatal", "Fatal"),
            ("category", "Category"),
            ("message", "Message"),
            ("path", "Path"),
        ],
    )
    md = f"""# X5-F0 Risk Summary

Status: `{'FATAL' if fatal_count else 'PASS_WITH_RISK'}`

- generated_at: `{payload['generated_at']}`
- fatal_count: `{fatal_count}`
- warning_count: `{warning_count}`
- final_submit_created: `false`

## Risks

{body}
"""
    (run_dir / "risk_summary.md").write_text(md, encoding="utf-8")


def inventory_source_data(case_dir: Path) -> list[str]:
    raw_dir = case_dir / "data" / "raw"
    if not raw_dir.exists():
        return []
    return [rel_path(p) for p in sorted(raw_dir.rglob("*")) if p.is_file()]


def inventory_files(path: Path, patterns: tuple[str, ...] = ("*",)) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    files: list[Path] = []
    for pattern in patterns:
        files.extend(p for p in path.rglob(pattern) if p.is_file())
    for item in sorted(set(files)):
        rows.append(
            {
                "path": item.as_posix(),
                "size": item.stat().st_size,
                "sha256": sha256_file(item),
            }
        )
    return rows


def detect_current_case(root: Path = ROOT) -> Path:
    return root / "cases" / "active_competition" / "current_case"


def load_case_config(case_dir: Path) -> dict[str, Any]:
    config = read_json(case_dir / "case_config.json", default=None)
    if isinstance(config, dict):
        return config
    return {
        "case_id": case_dir.name,
        "case_name": "active current case fallback",
        "case_config_missing": True,
        "active_case": "active_competition/current_case" in case_dir.as_posix(),
    }


def find_paper_pdf(case_dir: Path) -> Path | None:
    canonical_pdf = case_dir / "paper" / "main.pdf"
    return canonical_pdf if canonical_pdf.is_file() else None


def _pdf_page_count(pdf_path: Path) -> tuple[bool, int | None, str | None]:
    if not pdf_path.exists():
        return False, None, "missing"
    try:
        try:
            from pypdf import PdfReader  # type: ignore
        except Exception:
            from PyPDF2 import PdfReader  # type: ignore
        reader = PdfReader(str(pdf_path))
        return True, len(reader.pages), None
    except Exception as exc:
        try:
            header = pdf_path.read_bytes()[:5]
            if header == b"%PDF-":
                return True, None, f"opened_by_header_only: {exc}"
        except OSError:
            pass
        return False, None, str(exc)


def generate_pdf_checklist(case_dir: Path, run_dir: Path, *, strict: bool = False) -> dict[str, Any]:
    pdf_path = find_paper_pdf(case_dir)
    tex_files = list((case_dir / "paper").rglob("*.tex")) if (case_dir / "paper").exists() else []
    log_files = list((case_dir / "paper").rglob("*.log")) if (case_dir / "paper").exists() else []
    text = "\n".join(
        f.read_text(encoding="utf-8", errors="replace")[:1_000_000] for f in tex_files + log_files if f.is_file()
    )
    opened = False
    pages: int | None = None
    open_error = "pdf not found"
    if pdf_path:
        opened, pages, open_error = _pdf_page_count(pdf_path)

    checks = [
        {
            "name": "pdf_exists",
            "status": "pass" if pdf_path else ("fail" if strict else "warning"),
            "detail": rel_path(pdf_path) if pdf_path else "paper/main.pdf not found",
        },
        {
            "name": "pdf_can_open",
            "status": "pass" if opened else ("fail" if strict else "warning"),
            "detail": open_error or "ok",
        },
        {"name": "page_count", "status": "info", "detail": pages if pages is not None else "unknown"},
        {
            "name": "todo",
            "status": "warning" if "TODO" in text.upper() else "pass",
            "detail": "TODO found in TeX/log" if "TODO" in text.upper() else "not found",
        },
        {
            "name": "placeholder",
            "status": "warning" if "PLACEHOLDER" in text.upper() else "pass",
            "detail": "placeholder found" if "PLACEHOLDER" in text.upper() else "not found",
        },
        {
            "name": "overfull_underfull",
            "status": "warning" if ("Overfull" in text or "Underfull" in text) else "pass",
            "detail": "TeX overfull/underfull warning found" if ("Overfull" in text or "Underfull" in text) else "not found",
        },
        {
            "name": "missing_images",
            "status": "warning" if ("not found" in text.lower() and "includegraphics" in text.lower()) else "pass",
            "detail": "possible missing image in TeX/log" if ("not found" in text.lower() and "includegraphics" in text.lower()) else "not found",
        },
        {
            "name": "figure_caption_split_alarm",
            "status": "warning",
            "detail": "manual visual review required; no automatic FloatBarrier or [H] rewrite is allowed",
        },
        {
            "name": "table_page_break_alarm",
            "status": "warning",
            "detail": "manual visual review required",
        },
        {
            "name": "black_background_alarm",
            "status": "warning",
            "detail": "manual visual review required",
        },
        {
            "name": "large_blank_alarm",
            "status": "warning",
            "detail": "manual visual review required",
        },
    ]
    payload = {
        "schema_version": "x5_f0_pdf_checklist_v1",
        "generated_at": utc_now(),
        "pdf_path": rel_path(pdf_path) if pdf_path else None,
        "strict": strict,
        "auto_layout_fix_applied": False,
        "floatbarrier_inserted": False,
        "latex_modified": False,
        "checks": checks,
    }
    write_json(run_dir / "pdf_checklist.json", payload)
    md_rows = [{"check": c["name"], "status": c["status"], "detail": c["detail"]} for c in checks]
    md = f"""# X5-F0 PDF Checklist

Status: `ALARM_ONLY`

This checklist does not rewrite LaTeX, insert FloatBarrier, force `[H]`, or silently change layout.

{markdown_table(md_rows, [('check', 'Check'), ('status', 'Status'), ('detail', 'Detail')])}
"""
    (run_dir / "pdf_checklist.md").write_text(md, encoding="utf-8")
    return payload


def _bad_number(value: Any) -> bool:
    if isinstance(value, float):
        return math.isnan(value) or math.isinf(value)
    return False


def scan_bad_values(value: Any, path: str = "$") -> list[dict[str, str]]:
    findings: list[dict[str, str]] = []
    if value is None:
        return findings
    if _bad_number(value):
        findings.append({"path": path, "issue": "NaN_or_Inf"})
    elif isinstance(value, dict):
        if not value:
            findings.append({"path": path, "issue": "empty_object"})
        for key, child in value.items():
            findings.extend(scan_bad_values(child, f"{path}.{key}"))
    elif isinstance(value, list):
        if not value:
            findings.append({"path": path, "issue": "empty_array"})
        for idx, child in enumerate(value):
            findings.extend(scan_bad_values(child, f"{path}[{idx}]"))
    return findings


def is_empty_required_value(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, (list, dict)):
        return len(value) == 0
    return False


def validate_result_summary_file(path: Path) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    if not path.exists():
        return [{"file_path": rel_path(path), "json_path": "$", "issue": "json_missing"}]
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        return [
            {
                "file_path": rel_path(path),
                "json_path": "$",
                "issue": "json_parse_failed",
                "detail": str(exc),
            }
        ]
    if not isinstance(payload, dict):
        return [{"file_path": rel_path(path), "json_path": "$", "issue": "top_level_not_object"}]
    if not payload:
        return [{"file_path": rel_path(path), "json_path": "$", "issue": "empty_top_level_object"}]

    required_fields = list(RESULT_SUMMARY_REQUIRED_FIELDS)
    if payload.get("schema_version") == "x5_f0_result_summary_v1":
        required_fields.extend(["data_health_status", "final_submit_created"])
    if payload.get("schema_version") == "x5_f1_math_core_result_summary_v1":
        required_fields.extend(
            [
                "created_at",
                "model_id",
                "model_family",
                "success",
                "status",
                "metrics",
                "primary_results",
                "risk_items",
                "fatal_count",
                "warning_count",
                "limitations",
                "final_submit_created",
            ]
        )
    for field in required_fields:
        if field not in payload:
            findings.append({"file_path": rel_path(path), "json_path": f"$.{field}", "issue": "required_field_missing"})
        elif (
            payload.get("schema_version") == "x5_f1_math_core_result_summary_v1"
            and field == "risk_items"
            and payload.get("success") is True
        ):
            continue
        elif is_empty_required_value(payload.get(field)):
            findings.append({"file_path": rel_path(path), "json_path": f"$.{field}", "issue": "required_field_empty"})

    if payload.get("schema_version") == "x5_f1_math_core_result_summary_v1":
        if payload.get("success") is False:
            if is_empty_required_value(payload.get("failure_reason")):
                findings.append({"file_path": rel_path(path), "json_path": "$.failure_reason", "issue": "failure_reason_required"})
            if not isinstance(payload.get("risk_items"), list) or not payload.get("risk_items"):
                findings.append({"file_path": rel_path(path), "json_path": "$.risk_items", "issue": "risk_items_required_when_failed"})
        try:
            fatal_count = int(payload.get("fatal_count", 0))
        except (TypeError, ValueError):
            fatal_count = 1
            findings.append({"file_path": rel_path(path), "json_path": "$.fatal_count", "issue": "fatal_count_invalid"})
        if fatal_count > 0:
            findings.append({"file_path": rel_path(path), "json_path": "$.fatal_count", "issue": "math_core_fatal_count_positive"})

    for item in scan_bad_values(payload):
        if item["issue"] == "NaN_or_Inf":
            findings.append({"file_path": rel_path(path), "json_path": item.get("path"), "issue": "NaN_or_Inf"})
    return findings


def scan_structured_result_files(output_dir: Path) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    if not output_dir.exists():
        return findings
    for path in sorted(output_dir.rglob("*")):
        if not path.is_file():
            continue
        if "validation" in path.relative_to(output_dir).parts or "quality" in path.relative_to(output_dir).parts:
            continue
        suffix = path.suffix.lower()
        try:
            if suffix == ".json":
                if path.name == "result_summary.json":
                    findings.extend(validate_result_summary_file(path))
                    continue
                payload = read_json(path, default=None)
                if payload is None:
                    continue
                for item in scan_bad_values(payload):
                    if path.name == "result_summary.json" and item["issue"] in {"empty_array", "empty_object"}:
                        continue
                    findings.append({"file_path": rel_path(path), "json_path": item.get("path"), "issue": item.get("issue")})
            elif suffix in {".csv", ".tsv"}:
                sep = "\t" if suffix == ".tsv" else ","
                with path.open("r", encoding="utf-8-sig", errors="replace", newline="") as f:
                    reader = csv.reader(f, delimiter=sep)
                    rows = list(reader)
                if len(rows) <= 1:
                    findings.append({"file_path": rel_path(path), "issue": "empty_table"})
                for row_idx, row in enumerate(rows[1:], start=2):
                    for col_idx, cell in enumerate(row, start=1):
                        text = str(cell).strip().lower()
                        if text in {"nan", "inf", "+inf", "-inf", "infinity", "-infinity"}:
                            findings.append(
                                {"file_path": rel_path(path), "issue": "NaN_or_Inf", "cell": f"R{row_idx}C{col_idx}"}
                            )
        except Exception as exc:
            findings.append({"file_path": rel_path(path), "issue": "scan_error", "detail": str(exc)})
    return findings


def safe_promote(run_output_dir: Path, case_output_dir: Path, backup_dir: Path) -> list[str]:
    promoted: list[str] = []
    if not run_output_dir.exists():
        return promoted
    backup_dir.mkdir(parents=True, exist_ok=True)
    case_output_dir.mkdir(parents=True, exist_ok=True)
    for src in sorted(p for p in run_output_dir.rglob("*") if p.is_file()):
        rel = src.relative_to(run_output_dir)
        dst = case_output_dir / rel
        if dst.exists():
            backup_target = backup_dir / rel
            backup_target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(dst, backup_target)
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        promoted.append(rel.as_posix())
    return promoted


def run_subprocess_with_timeout(args: list[str], timeout_seconds: int, cwd: Path, log_path: Path) -> dict[str, Any]:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    started_at = utc_now()
    try:
        proc = subprocess.Popen(
            args,
            cwd=str(cwd),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        try:
            stdout, stderr = proc.communicate(timeout=timeout_seconds)
            timed_out = False
        except subprocess.TimeoutExpired:
            proc.kill()
            stdout, stderr = proc.communicate()
            timed_out = True
        log_path.write_text(
            "\n".join(
                [
                    f"command: {' '.join(args)}",
                    f"started_at: {started_at}",
                    f"finished_at: {utc_now()}",
                    f"timeout_seconds: {timeout_seconds}",
                    f"timed_out: {timed_out}",
                    f"returncode: {proc.returncode}",
                    "",
                    "[stdout]",
                    stdout or "",
                    "",
                    "[stderr]",
                    stderr or "",
                ]
            ),
            encoding="utf-8",
        )
        return {
            "command": args,
            "returncode": proc.returncode,
            "timed_out": timed_out,
            "log_path": rel_path(log_path),
        }
    except Exception as exc:
        log_path.write_text(f"command failed before start: {exc}\n", encoding="utf-8")
        return {"command": args, "returncode": 1, "timed_out": False, "error": str(exc), "log_path": rel_path(log_path)}


def python_executable() -> str:
    return sys.executable or "python"


WRITE_REGISTRY_INITIAL_COMPLETE = False


@dataclass
class WriteRegistry:
    """Diagnostic-only written-path registry primitive.

    This class is intentionally not wired into any real execution path. It
    does not establish authoritative provenance, does not affect promotion,
    and must never set: source_worktree_clean_after, source_provenance_valid,
    safe_promotion_allowed, authorization_bindings_valid, final_freeze_ready,
    final_submit_allowed. Those remain out of scope for this primitive.
    """

    root: Path
    required_tokens: frozenset[str]
    _written_repo_paths: set[str] = field(default_factory=set, init=False)
    _expected_post_snapshot_paths: set[str] = field(default_factory=set, init=False)
    _external_written_paths: set[str] = field(default_factory=set, init=False)
    _registry_errors: list[str] = field(default_factory=list, init=False)
    _completed_tokens: set[str] = field(default_factory=set, init=False)
    _ambiguous_write_detected: bool = field(default=False, init=False)

    @property
    def written_repo_paths(self) -> frozenset[str]:
        return frozenset(self._written_repo_paths)

    @property
    def expected_post_snapshot_paths(self) -> frozenset[str]:
        return frozenset(self._expected_post_snapshot_paths)

    @property
    def external_written_paths(self) -> frozenset[str]:
        return frozenset(self._external_written_paths)

    @property
    def registry_errors(self) -> tuple[str, ...]:
        return tuple(self._registry_errors)

    @property
    def completed_tokens(self) -> frozenset[str]:
        return frozenset(self._completed_tokens)

    @property
    def ambiguous_write_detected(self) -> bool:
        return self._ambiguous_write_detected

    def record_write(self, path: Path | os.PathLike[str], *, token: str) -> None:
        """Register one successful write. The only path-registration entry point.

        This is a post-write registration call: the caller invokes it only
        after a real write has already succeeded, so the path is expected to
        exist as a regular file at call time. No filename-character
        heuristic is used anywhere in this method — concrete filenames such
        as "report[1].json" or one literally containing "*" or "?" are
        accepted or rejected solely on resolve()/existence/file-type, never
        on the characters in the name. Symlinks are followed by
        Path.resolve()/is_file() (the pathlib default): a symlink whose
        final target is a regular file inside root registers as that target
        path, matching normal filesystem write semantics.
        """

        path = Path(path)

        if token not in self.required_tokens:
            self._registry_errors.append(f"unrecognized token: {token}")
            return

        raw = str(path)
        try:
            resolved = path.resolve()
        except (OSError, ValueError) as exc:
            self._registry_errors.append(f"path resolution failed for {raw}: {exc}")
            return

        if resolved.is_dir():
            self._ambiguous_write_detected = True
            self._registry_errors.append(f"directory-level write rejected: {raw}")
            return

        if not resolved.is_file():
            self._registry_errors.append(f"path does not exist as a regular file: {raw}")
            return

        resolved_root = self.root.resolve()
        try:
            normalized = resolved.relative_to(resolved_root).as_posix()
        except ValueError:
            self._external_written_paths.add(resolved.as_posix())
            self._completed_tokens.add(token)
            return

        self._written_repo_paths.add(normalized)
        self._completed_tokens.add(token)

    def note_expected_post_snapshot_path(self, path: Path) -> None:
        """Record a path expected to appear in a later snapshot. Not consumed here."""

        resolved_root = self.root.resolve()
        try:
            resolved = path.resolve()
            normalized = resolved.relative_to(resolved_root).as_posix()
        except (OSError, ValueError):
            normalized = str(path)
        self._expected_post_snapshot_paths.add(normalized)

    def diagnostic_completeness(self) -> bool:
        """The sole completeness predicate. Purely derived; accepts no overrides.

        Requires exact token-set equality, not a superset relationship: a
        completed token outside required_tokens can never reach
        _completed_tokens (record_write rejects it into registry_errors), so
        this equality check also acts as a structural invariant guard.
        """

        return (
            self.required_tokens == self._completed_tokens
            and not self._registry_errors
            and not self._ambiguous_write_detected
        )
