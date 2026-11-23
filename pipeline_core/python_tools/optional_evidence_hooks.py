"""P2-4B preflight and executor for optional sensitivity and tournament hooks.

Only case-local adapters explicitly enabled in ``case_config.json`` are
loaded.  The module delegates all mathematics to the existing math-core
implementations and their report writers; it never creates ``final_submit/``.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
from typing import Any

from math_core.comparison.model_tournament import run_model_tournament, write_tournament_report
from math_core.sensitivity.perturbation_matrix import (
    run_perturbation_matrix,
    write_sensitivity_report,
)


VALID_TIERS = frozenset({"training", "test", "homework", "selection", "formal"})
FORMAL_TIERS = frozenset({"selection", "formal"})
HOOK_NAMES = ("sensitivity", "tournament")

DEFAULT_HOOKS = {
    "sensitivity": {"output_dir": "output/sensitivity"},
    "tournament": {"output_dir": "output/optimizer_arena"},
}


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


def _normalize_tier(value: Any) -> tuple[str, bool]:
    if value is None or not str(value).strip():
        return "training", True
    tier = str(value).strip().lower()
    return tier, tier in VALID_TIERS


def _adapter_state(adapter: Any) -> tuple[str, str]:
    """Return ``(state, normalized_value)`` without loading the adapter."""

    if not isinstance(adapter, str) or not adapter.strip():
        return "missing", ""
    normalized = adapter.strip()
    if Path(normalized).is_absolute():
        return "unsafe", normalized
    path_text, separator, _function_name = normalized.rpartition(":")
    if not separator:
        path_text = normalized
    path_text = path_text.strip()
    if not path_text:
        return "missing", normalized
    candidate = Path(path_text)
    if candidate.is_absolute() or ".." in candidate.parts:
        return "unsafe", normalized
    return "safe", normalized


def _adapter_parts(adapter: str) -> tuple[str, str] | None:
    path_text, separator, function_name = adapter.rpartition(":")
    if not separator or not path_text.strip() or not function_name.strip():
        return None
    return path_text.strip(), function_name.strip()


def _case_relative_path(case_dir: Path, value: str) -> Path | None:
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


def _load_adapter(case_dir: Path, adapter: str) -> tuple[Any | None, str | None]:
    """Load a case-local ``path.py:function`` adapter without package imports."""

    parts = _adapter_parts(adapter)
    if parts is None:
        return None, "adapter must use path/to/file.py:build_config"
    path_text, function_name = parts
    adapter_path = _case_relative_path(case_dir, path_text)
    if adapter_path is None or adapter_path.suffix.lower() != ".py" or not adapter_path.is_file():
        return None, "adapter file is missing or outside case_dir"

    module_name = "optional_evidence_adapter_" + hashlib.sha256(
        str(adapter_path).encode("utf-8")
    ).hexdigest()[:16]
    try:
        spec = importlib.util.spec_from_file_location(module_name, adapter_path)
        if spec is None or spec.loader is None:
            return None, "adapter module spec could not be created"
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    except Exception as exc:
        return None, f"adapter import failed: {exc}"

    build_config = getattr(module, function_name, None)
    if not callable(build_config):
        return None, f"adapter function '{function_name}' is missing or not callable"
    return build_config, None


def _call_adapter(build_config: Any, case_dir: Path, output_dir: Path) -> tuple[dict[str, Any] | None, str | None]:
    try:
        payload = build_config(case_dir=case_dir, output_dir=output_dir)
    except TypeError:
        try:
            payload = build_config(case_dir, output_dir)
        except Exception as exc:
            return None, f"adapter build_config failed: {exc}"
    except Exception as exc:
        return None, f"adapter build_config failed: {exc}"
    if not isinstance(payload, dict):
        return None, "adapter build_config must return a dict"
    return payload, None


def _hook_view(config: dict[str, Any], name: str) -> tuple[dict[str, Any], dict[str, Any]]:
    raw_hook = config.get(f"{name}_hook")
    hook = raw_hook if isinstance(raw_hook, dict) else {}
    adapter_state, adapter = _adapter_state(hook.get("adapter", ""))
    default_output_dir = DEFAULT_HOOKS[name]["output_dir"]
    output_dir = hook.get("output_dir", default_output_dir)
    if not isinstance(output_dir, str):
        output_dir = default_output_dir
    view = {
        "required": config.get(f"{name}_required") is True,
        "enabled": hook.get("enabled") is True,
        "executed": False,
        "adapter": adapter,
        "output_dir": output_dir,
        "evidence_files": [],
    }
    return view, {"adapter_state": adapter_state, "hook_config_present": isinstance(raw_hook, dict)}


def _append_check(checks: list[dict[str, Any]], name: str, status: str, message: str) -> None:
    checks.append({"name": name, "status": status, "message": message})


def _execute_hook(
    *,
    name: str,
    case_dir: Path,
    output_root: Path,
    view: dict[str, Any],
) -> tuple[list[str] | None, list[str], str | None]:
    """Run one math-core hook and return evidence, manual items, or an error."""

    output_dir = _case_relative_path(output_root, str(view["output_dir"]))
    if output_dir is None:
        return None, [], "configured output_dir is unsafe"
    build_config, load_error = _load_adapter(case_dir, str(view["adapter"]))
    if load_error:
        return None, [], f"LOAD:{load_error}"
    assert build_config is not None

    adapter_payload, adapter_error = _call_adapter(build_config, case_dir, output_dir)
    if adapter_error:
        return None, [], adapter_error
    assert adapter_payload is not None
    kwargs = adapter_payload.get("kwargs")
    if not isinstance(kwargs, dict):
        return None, [], "adapter payload requires a kwargs object"
    call_kwargs = dict(kwargs)
    call_kwargs.setdefault("run_id", f"optional_evidence_{name}_{case_dir.name}")

    try:
        if name == "sensitivity":
            report = run_perturbation_matrix(**call_kwargs)
            report_manual_items = list(report.get("manual_review_required") or [])
            if not isinstance(report, dict) or report.get("success") is not True:
                return None, report_manual_items, "math-core execution returned success=false"
            writer_paths = write_sensitivity_report(report, output_dir)
        else:
            report = run_model_tournament(**call_kwargs)
            report_manual_items = list(report.get("manual_review_items") or [])
            if not isinstance(report, dict) or report.get("success") is not True:
                return None, report_manual_items, "math-core execution returned success=false"
            writer_paths = write_tournament_report(report, output_dir)
    except Exception as exc:
        return None, [], f"math-core execution failed: {exc}"

    expected_outputs = adapter_payload.get("expected_outputs")
    if expected_outputs is None:
        expected_outputs = [path.name for path in writer_paths]
    if not isinstance(expected_outputs, list) or not all(
        isinstance(item, str) and item.strip() for item in expected_outputs
    ):
        return None, report_manual_items, "expected_outputs must be a list of non-empty paths"

    evidence_files: list[str] = []
    for item in expected_outputs:
        expected_path = _case_relative_path(output_dir, item)
        if expected_path is None or not expected_path.is_file() or expected_path.stat().st_size <= 0:
            return None, report_manual_items, f"EVIDENCE:{item}"
        evidence_files.append(str(expected_path))
    return evidence_files, report_manual_items, None


def _evaluate_hook(
    *,
    name: str,
    case_dir: Path,
    output_root: Path,
    tier: str,
    view: dict[str, Any],
    metadata: dict[str, Any],
    config: dict[str, Any],
    checks: list[dict[str, Any]],
    blockers: list[str],
    warnings: list[str],
    manual_review_items: list[str],
) -> None:
    upper = name.upper()
    required = view["required"]
    enabled = view["enabled"]

    if not required:
        if tier in FORMAL_TIERS and not str(config.get(f"{name}_skip_reason") or "").strip():
            blockers.append("FORMAL_SKIP_REASON_MISSING")
            _append_check(checks, name, "FAIL", "FORMAL_SKIP_REASON_MISSING")
            return
        elif not enabled:
            _append_check(checks, name, "SKIPPED", "not required and hook disabled")
            return
    elif not enabled:
        code = f"{upper}_HOOK_DISABLED"
        blockers.append(code)
        _append_check(checks, name, "FAIL", code)
        return

    adapter_state = metadata["adapter_state"]
    if adapter_state == "missing":
        code = f"{upper}_HOOK_ADAPTER_MISSING"
        blockers.append(code)
        _append_check(checks, name, "FAIL", code)
        return
    if adapter_state == "unsafe":
        code = f"{upper}_HOOK_ADAPTER_UNSAFE"
        blockers.append(code)
        _append_check(checks, name, "FAIL", code)
        return

    evidence_files, report_manual_items, execution_error = _execute_hook(
        name=name,
        case_dir=case_dir,
        output_root=output_root,
        view=view,
    )
    if execution_error:
        if execution_error.startswith("LOAD:"):
            code = f"{upper}_HOOK_LOAD_FAILED"
        elif execution_error.startswith("EVIDENCE:"):
            code = f"{upper}_REQUIRED_EVIDENCE_MISSING"
        else:
            code = f"{upper}_HOOK_EXECUTION_FAILED"
        blockers.append(code)
        _append_check(checks, name, "FAIL", code)
        return

    assert evidence_files is not None
    view["executed"] = True
    view["evidence_files"] = evidence_files
    _append_check(checks, name, "PASS", "math-core hook executed and evidence verified")
    if not required:
        warnings.append("OPTIONAL_EVIDENCE_HOOK_MANUAL_REVIEW_REQUIRED")
        manual_review_items.append(
            f"{name}_hook was optional; review generated evidence before using it in a paper."
        )
    if report_manual_items:
        warnings.append("OPTIONAL_EVIDENCE_HOOK_MANUAL_REVIEW_REQUIRED")
        manual_review_items.extend(str(item) for item in report_manual_items)


def check_optional_evidence_hooks(
    case_dir: str | Path,
    *,
    which: str = "all",
    output_root: str | Path | None = None,
) -> dict[str, Any]:
    """Validate and, only when explicitly configured, execute optional hooks."""

    if which not in {"all", *HOOK_NAMES}:
        raise ValueError("which must be all, sensitivity, or tournament")

    case_dir = Path(case_dir)
    output_root = Path(output_root) if output_root is not None else case_dir
    config, config_error = _read_json(case_dir / "case_config.json")
    checks: list[dict[str, Any]] = []
    blockers: list[str] = []
    warnings: list[str] = []
    manual_review_items: list[str] = []

    if config_error:
        blockers.append("OPTIONAL_EVIDENCE_CONFIG_UNREADABLE")
        config = {}
        _append_check(checks, "case_config", "FAIL", "OPTIONAL_EVIDENCE_CONFIG_UNREADABLE")
    assert config is not None

    tier, tier_known = _normalize_tier(config.get("submission_tier"))
    if not tier_known:
        warnings.append("UNKNOWN_SUBMISSION_TIER")
        manual_review_items.append(f"UNKNOWN_SUBMISSION_TIER: '{tier}' requires manual review.")
        _append_check(checks, "submission_tier", "MANUAL_REVIEW", "UNKNOWN_SUBMISSION_TIER")

    views: dict[str, dict[str, Any]] = {}
    for name in HOOK_NAMES:
        view, metadata = _hook_view(config, name)
        views[name] = view
        if which in {"all", name}:
            _evaluate_hook(
                name=name,
                case_dir=case_dir,
                output_root=output_root,
                tier=tier,
                view=view,
                metadata=metadata,
                config=config,
                checks=checks,
                blockers=blockers,
                warnings=warnings,
                manual_review_items=manual_review_items,
            )

    unique_blockers = list(dict.fromkeys(blockers))
    unique_warnings = list(dict.fromkeys(warnings))
    if unique_blockers:
        status = "BLOCKED"
    elif manual_review_items:
        status = "MANUAL_REVIEW_REQUIRED"
    elif any(view["executed"] for view in views.values()):
        status = "PASS"
    else:
        status = "SKIPPED"

    return {
        "status": status,
        "checks": checks,
        "blockers": unique_blockers,
        "warnings": unique_warnings,
        "manual_review_items": manual_review_items,
        "submission_tier": tier,
        "sensitivity": views["sensitivity"],
        "tournament": views["tournament"],
        "final_submit_created": False,
        "limitations": [
            "Only explicitly enabled or required hooks load a case-local adapter.",
            "Math-core reports are evidence, not final model or submission decisions.",
            "This preflight never creates final_submit.",
        ],
    }


def write_optional_evidence_hooks_report(report: dict[str, Any], case_dir: str | Path) -> tuple[Path, Path]:
    """Write an explicit report only when the CLI caller opts in."""

    output_dir = Path(case_dir) / "output" / "validation"
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "optional_evidence_hooks_report.json"
    markdown_path = output_dir / "optional_evidence_hooks_report.md"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    lines = [
        "# Optional Evidence Hooks Report",
        "",
        f"- status: `{report['status']}`",
        f"- submission_tier: `{report['submission_tier']}`",
        "- final_submit_created: `false`",
        "",
        "## Blockers",
        "",
    ]
    lines.extend(f"- `{item}`" for item in report["blockers"]) if report["blockers"] else lines.append("- None")
    markdown_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return json_path, markdown_path


def main() -> int:
    parser = argparse.ArgumentParser(description="P2-4B optional evidence hook executor")
    parser.add_argument("--case-dir", required=True)
    parser.add_argument("--which", choices=("all", "sensitivity", "tournament"), default="all")
    parser.add_argument("--write-report", action="store_true")
    args = parser.parse_args()

    report = check_optional_evidence_hooks(args.case_dir, which=args.which)
    if args.write_report:
        write_optional_evidence_hooks_report(report, args.case_dir)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] in {"PASS", "SKIPPED", "MANUAL_REVIEW_REQUIRED"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
