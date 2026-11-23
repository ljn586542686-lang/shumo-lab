"""Structural stabilization freeze tripwire for the P4 golden-case phase.

This module is intentionally read-only.  It compares protected file names and
registered rule/feature identifiers with a checked-in baseline, then prints a
machine-readable report.  It never runs models, promotes artifacts, or creates
submission directories.
"""

from __future__ import annotations

import argparse
import ast
import fnmatch
import json
import re
import sys
from pathlib import Path, PurePosixPath
from typing import Any


DEFAULT_POLICY_PATH = Path("docs/status/STABILIZATION_FREEZE_POLICY.json")
DEFAULT_BASELINE_PATH = Path("docs/status/STABILIZATION_FREEZE_BASELINE.json")
IGNORED_DIRECTORY_NAMES = {
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "__pycache__",
    "node_modules",
    "venv",
}
NON_PRODUCTION_TOP_LEVEL_DIRS = {"archive", "cases", "work_tmp"}
FEATURE_ID_RE = re.compile(r"^\s*-\s*feature_id:\s*[\"']([^\"']+)[\"']\s*(?:#.*)?$", re.MULTILINE)


def _check(checks: list[dict[str, Any]], name: str, status: str, **details: Any) -> None:
    checks.append({"name": name, "status": status, **details})


def _violate(violations: list[dict[str, Any]], code: str, message: str, **details: Any) -> None:
    violations.append({"code": code, "message": message, **details})


def _warn(warnings: list[dict[str, Any]], code: str, message: str, **details: Any) -> None:
    warnings.append({"code": code, "message": message, **details})


def _load_json_object(
    path: Path,
    label: str,
    checks: list[dict[str, Any]],
    violations: list[dict[str, Any]],
) -> dict[str, Any] | None:
    if not path.is_file():
        _check(checks, label, "FAIL", path=path.as_posix())
        _violate(violations, f"{label.upper()}_MISSING", f"{label} file is missing", path=path.as_posix())
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        _check(checks, label, "FAIL", path=path.as_posix())
        _violate(
            violations,
            f"{label.upper()}_MALFORMED",
            f"{label} file cannot be read as JSON object: {exc}",
            path=path.as_posix(),
        )
        return None
    if not isinstance(payload, dict):
        _check(checks, label, "FAIL", path=path.as_posix())
        _violate(violations, f"{label.upper()}_MALFORMED", f"{label} root must be a JSON object", path=path.as_posix())
        return None
    _check(checks, label, "PASS", path=path.as_posix())
    return payload


def _string_list(value: Any) -> list[str] | None:
    if not isinstance(value, list) or any(not isinstance(item, str) or not item.strip() for item in value):
        return None
    return sorted(set(item.replace("\\", "/").strip() for item in value))


def _repo_files(repo_root: Path) -> list[str]:
    files: list[str] = []
    for path in repo_root.rglob("*"):
        try:
            relative = path.relative_to(repo_root)
        except ValueError:
            continue
        if relative.parts and relative.parts[0] in NON_PRODUCTION_TOP_LEVEL_DIRS:
            continue
        if any(part in IGNORED_DIRECTORY_NAMES for part in relative.parts):
            continue
        try:
            is_file = path.is_file()
        except OSError:
            continue
        if is_file:
            files.append(relative.as_posix())
    return sorted(set(files))


def _is_non_production_path(path: str) -> bool:
    parts = PurePosixPath(path).parts
    return bool(parts and parts[0] in NON_PRODUCTION_TOP_LEVEL_DIRS)


def _matches_protected_name(path: str, patterns: list[str]) -> bool:
    name = PurePosixPath(path).name.lower()
    return any(fnmatch.fnmatch(name, pattern.lower()) for pattern in patterns)


def _extract_python_rule_ids(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
    ids: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        for keyword in node.keywords:
            if keyword.arg == "rule_id" and isinstance(keyword.value, ast.Constant) and isinstance(keyword.value.value, str):
                ids.add(keyword.value.value)
    return sorted(ids)


def _extract_literal_feature_ids(path: Path) -> list[str]:
    return sorted(set(FEATURE_ID_RE.findall(path.read_text(encoding="utf-8-sig"))))


def _extract_registry_ids(repo_root: Path, spec: dict[str, Any]) -> list[str]:
    source_path = spec.get("source_path")
    extractor = spec.get("extractor")
    if not isinstance(source_path, str) or not source_path.strip():
        raise ValueError("registry source_path must be a non-empty string")
    path = repo_root / source_path
    if not path.is_file():
        raise FileNotFoundError(source_path)
    if extractor == "python_ast_rule_id_keywords":
        return _extract_python_rule_ids(path)
    if extractor == "literal_yaml_feature_ids":
        return _extract_literal_feature_ids(path)
    raise ValueError(f"unsupported registry extractor: {extractor!r}")


def evaluate(
    repo_root: str | Path,
    *,
    policy_path: str | Path | None = None,
    baseline_path: str | Path | None = None,
) -> dict[str, Any]:
    root = Path(repo_root).resolve()
    policy_file = root / (Path(policy_path) if policy_path is not None else DEFAULT_POLICY_PATH)
    baseline_file = root / (Path(baseline_path) if baseline_path is not None else DEFAULT_BASELINE_PATH)
    checks: list[dict[str, Any]] = []
    violations: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []

    policy = _load_json_object(policy_file, "policy", checks, violations)
    baseline = _load_json_object(baseline_file, "baseline", checks, violations)

    freeze_active = True
    allowlist: list[str] = []
    if policy is not None:
        raw_freeze_active = policy.get("freeze_active")
        if isinstance(raw_freeze_active, bool):
            freeze_active = raw_freeze_active
            _check(checks, "freeze_active", "PASS", value=freeze_active)
        else:
            _check(checks, "freeze_active", "FAIL", value=raw_freeze_active)
            _violate(
                violations,
                "FREEZE_ACTIVE_UNKNOWN",
                "freeze_active must be a JSON boolean; freeze is treated as active",
                value=raw_freeze_active,
            )
            freeze_active = True
        parsed_allowlist = _string_list(policy.get("allowlist"))
        if parsed_allowlist is None:
            _check(checks, "policy_allowlist", "FAIL")
            _violate(violations, "ALLOWLIST_MALFORMED", "policy allowlist must be a list of non-empty paths")
        else:
            allowlist = parsed_allowlist
            _check(checks, "policy_allowlist", "PASS", count=len(allowlist))

    baseline_allowlist: list[str] = []
    protected_patterns: list[str] = []
    protected_baseline: list[str] = []
    registry_specs: dict[str, Any] = {}
    baseline_ready = baseline is not None
    if baseline is not None:
        parsed_patterns = _string_list(baseline.get("protected_file_name_patterns"))
        parsed_paths = _string_list(baseline.get("protected_files_baseline"))
        parsed_baseline_allowlist = _string_list(baseline.get("allowlist"))
        if parsed_patterns is None or not parsed_patterns:
            _violate(violations, "BASELINE_PATTERNS_MALFORMED", "baseline protected patterns must be a non-empty string list")
            _check(checks, "baseline_patterns", "FAIL")
            baseline_ready = False
        else:
            protected_patterns = parsed_patterns
            _check(checks, "baseline_patterns", "PASS", count=len(protected_patterns))
        if parsed_paths is None:
            _violate(violations, "BASELINE_FILES_MALFORMED", "baseline protected files must be a string list")
            _check(checks, "baseline_files", "FAIL")
            baseline_ready = False
        else:
            protected_baseline = parsed_paths
            _check(checks, "baseline_files", "PASS", count=len(protected_baseline))
        if parsed_baseline_allowlist is None:
            _violate(violations, "BASELINE_ALLOWLIST_MALFORMED", "baseline allowlist must be a string list")
            _check(checks, "baseline_allowlist", "FAIL")
            baseline_ready = False
        else:
            baseline_allowlist = parsed_baseline_allowlist
            _check(checks, "baseline_allowlist", "PASS", count=len(baseline_allowlist))
        raw_registries = baseline.get("registries")
        if not isinstance(raw_registries, dict) or not raw_registries:
            _violate(violations, "BASELINE_REGISTRIES_MALFORMED", "baseline registries must be a non-empty object")
            _check(checks, "baseline_registries", "FAIL")
            baseline_ready = False
        else:
            registry_specs = raw_registries
            _check(checks, "baseline_registries", "PASS", count=len(registry_specs))

    allowlist_added = sorted(set(allowlist) - set(baseline_allowlist))
    allowlist_removed = sorted(set(baseline_allowlist) - set(allowlist))
    if policy is not None and baseline is not None and (allowlist_added or allowlist_removed):
        _violate(
            violations,
            "ALLOWLIST_BASELINE_MISMATCH",
            "policy and baseline allowlists differ",
            added=allowlist_added,
            removed=allowlist_removed,
        )
        _check(checks, "allowlist_equality", "FAIL", added=allowlist_added, removed=allowlist_removed)
    elif policy is not None and baseline is not None:
        _check(checks, "allowlist_equality", "PASS")

    all_files = _repo_files(root)
    current_protected = sorted(path for path in all_files if _matches_protected_name(path, protected_patterns)) if protected_patterns else []
    protected_added = sorted(set(current_protected) - set(protected_baseline) - set(allowlist))
    active_protected_baseline = [
        path for path in protected_baseline if not _is_non_production_path(path)
    ]
    protected_removed = sorted(set(active_protected_baseline) - set(current_protected))
    if baseline_ready and protected_added:
        target = violations if freeze_active else warnings
        add = _violate if freeze_active else _warn
        for path in protected_added:
            add(target, "NEW_PROTECTED_FILE", "new protected file path is outside the frozen baseline", path=path)
        _check(checks, "protected_file_set", "FAIL" if freeze_active else "WARNING", added=protected_added, removed=protected_removed)
    elif baseline_ready:
        _check(checks, "protected_file_set", "PASS", added=[], removed=protected_removed)
    if protected_removed:
        _warn(warnings, "PROTECTED_BASELINE_PATH_MISSING", "protected baseline paths are absent from the current tree", paths=protected_removed)

    registered_current: dict[str, list[str]] = {}
    registered_baseline: dict[str, list[str]] = {}
    registry_diff: dict[str, dict[str, list[str]]] = {}
    if baseline_ready:
        for registry_name, raw_spec in sorted(registry_specs.items()):
            if not isinstance(raw_spec, dict):
                _violate(violations, "REGISTRY_SPEC_MALFORMED", "registry specification must be an object", registry=registry_name)
                continue
            expected = _string_list(raw_spec.get("ids"))
            if expected is None:
                _violate(violations, "REGISTRY_BASELINE_IDS_MALFORMED", "registry baseline ids must be a string list", registry=registry_name)
                continue
            registered_baseline[registry_name] = expected
            try:
                current = _extract_registry_ids(root, raw_spec)
            except (OSError, UnicodeError, SyntaxError, ValueError) as exc:
                _violate(violations, "REGISTRY_READ_FAILED", f"registry cannot be recomputed: {exc}", registry=registry_name)
                _check(checks, f"registry:{registry_name}", "FAIL")
                continue
            registered_current[registry_name] = current
            added = sorted(set(current) - set(expected))
            removed = sorted(set(expected) - set(current))
            registry_diff[registry_name] = {"added": added, "removed": removed}
            if added or removed:
                add = _violate if freeze_active else _warn
                target = violations if freeze_active else warnings
                add(
                    target,
                    "REGISTRY_SET_CHANGED",
                    "registered rule/feature ID set differs from baseline",
                    registry=registry_name,
                    added=added,
                    removed=removed,
                )
                _check(checks, f"registry:{registry_name}", "FAIL" if freeze_active else "WARNING", added=added, removed=removed)
            else:
                _check(checks, f"registry:{registry_name}", "PASS", count=len(current))

    if not freeze_active:
        _warn(warnings, "FREEZE_DISABLED", "structural freeze is disabled; baseline differences are still reported")

    final_submit_present = (root / "final_submit").exists()
    if final_submit_present:
        _violate(violations, "ROOT_FINAL_SUBMIT_PRESENT", "root final_submit exists; tripwire did not create it")
        _check(checks, "root_final_submit", "FAIL")
    else:
        _check(checks, "root_final_submit", "PASS")

    result = {
        "status": "FAIL" if violations else "PASS",
        "freeze_active": freeze_active,
        "checks": checks,
        "violations": violations,
        "warnings": warnings,
        "baseline_path": baseline_file.relative_to(root).as_posix() if baseline_file.is_relative_to(root) else baseline_file.as_posix(),
        "policy_path": policy_file.relative_to(root).as_posix() if policy_file.is_relative_to(root) else policy_file.as_posix(),
        "allowlist": allowlist,
        "allowlist_paths_present": sorted(path for path in allowlist if (root / path).is_file()),
        "allowlist_entries_not_in_baseline": allowlist_added,
        "baseline_allowlist_entries_not_in_policy": allowlist_removed,
        "registered_gate_ids_current": registered_current.get("quality_gate_rule_ids", []),
        "registered_gate_ids_baseline": registered_baseline.get("quality_gate_rule_ids", []),
        "registered_ids_current": registered_current,
        "registered_ids_baseline": registered_baseline,
        "protected_file_violations": protected_added,
        "baseline_diff": {
            "protected_files_added": protected_added,
            "protected_files_removed": protected_removed,
            "registries": registry_diff,
            "allowlist_added": allowlist_added,
            "allowlist_removed": allowlist_removed,
        },
        "final_submit_created": final_submit_present,
    }
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only structural stabilization freeze tripwire")
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--policy-path", default=None)
    parser.add_argument("--baseline-path", default=None)
    args = parser.parse_args(argv)
    result = evaluate(args.repo_root, policy_path=args.policy_path, baseline_path=args.baseline_path)
    json.dump(result, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
