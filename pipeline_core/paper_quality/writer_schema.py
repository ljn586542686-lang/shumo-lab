"""Small, backward-compatible task-contract helpers for Writer Schema V2.

The renderer still owns the document tree.  This module only resolves task
labels and content keys so a case can use numbered, named, or mixed tasks
without putting case-specific wording into the common writer.
"""

from __future__ import annotations

from typing import Any


WRITER_SCHEMA_VERSION = "WRITER_SCHEMA_V2"
CURRENT_FORMAL_MODE = "CURRENT_FORMAL"
LEGACY_COMPATIBILITY_MODE = "LEGACY_COMPATIBILITY"
MANIFEST_MODE_FIELD = "manifest_mode"
_CHINESE_ORDINALS = "一二三四五六七八九"


class WriterSchemaError(ValueError):
    """Raised when an explicit task contract is malformed."""


def _text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise WriterSchemaError(f"TASK_CONTRACT_REQUIRED_TEXT: {label}")
    return value.strip()


def _legacy_tasks(payload: dict[str, Any]) -> list[dict[str, str]]:
    count = payload.get("question_count")
    if not isinstance(count, int) or not 1 <= count <= len(_CHINESE_ORDINALS):
        raise WriterSchemaError("QUESTION_COUNT_REQUIRED_FOR_LEGACY_TASK_CONTRACT")
    return [
        {
            "task_id": f"q{index}",
            "label": f"问题{_CHINESE_ORDINALS[index - 1]}",
            "analysis_content_key": f"problem_analysis_q{index}",
            "content_key": f"question_content_q{index}",
        }
        for index in range(1, count + 1)
    ]


def _legacy_content_shape(payload: dict[str, Any], content_keys: set[str]) -> bool:
    """Recognize the pre-task-contract writer shape without using case names."""

    count = payload.get("question_count")
    if not isinstance(count, int) or not 1 <= count <= len(_CHINESE_ORDINALS):
        return False
    required = {
        "title",
        "authors",
        "abstract",
        "keywords",
        "problem_restatement",
        "model_assumptions",
        "model_assumptions_note",
        "notation",
        "model_evaluation",
        "references",
        "appendix",
    }
    for index in range(1, count + 1):
        required.update({f"problem_analysis_q{index}", f"question_content_q{index}"})
    return required.issubset(content_keys)


def resolve_manifest_mode(
    payload: dict[str, Any], *, content_keys: set[str] | None = None
) -> str:
    """Resolve a manifest's compatibility mode using positive evidence.

    A current manifest is identified by an explicit mode or an explicit task
    contract.  A historical manifest is accepted only when it carries the
    explicit legacy mode or the complete, case-agnostic pre-V2 fixed shape.
    An arbitrary manifest with missing fields is never silently treated as
    legacy.
    """

    raw_mode = payload.get(MANIFEST_MODE_FIELD)
    if raw_mode is not None:
        mode = _text(raw_mode, MANIFEST_MODE_FIELD)
        if mode not in {CURRENT_FORMAL_MODE, LEGACY_COMPATIBILITY_MODE}:
            raise WriterSchemaError("MANIFEST_MODE_UNSUPPORTED")
        return mode

    if "compatibility_mode" in payload:
        if not isinstance(payload.get("compatibility_mode"), str):
            raise WriterSchemaError("MANIFEST_MODE_UNSUPPORTED")
        compatibility_mode = payload["compatibility_mode"].strip()
        if compatibility_mode == LEGACY_COMPATIBILITY_MODE:
            return LEGACY_COMPATIBILITY_MODE
        if compatibility_mode == CURRENT_FORMAL_MODE:
            return CURRENT_FORMAL_MODE
        raise WriterSchemaError("MANIFEST_MODE_UNSUPPORTED")

    if "task_contract" in payload:
        if isinstance(payload.get("task_contract"), dict):
            return CURRENT_FORMAL_MODE
        raise WriterSchemaError("MANIFEST_MODE_UNSUPPORTED")

    keys = content_keys or set()
    if _legacy_content_shape(payload, keys):
        return LEGACY_COMPATIBILITY_MODE
    raise WriterSchemaError("MANIFEST_MODE_REQUIRED")


def resolve_task_contract(payload: dict[str, Any]) -> dict[str, Any]:
    """Resolve an optional explicit task contract or create the V2 fallback.

    Explicit tasks use ``task_id``, ``label``, ``analysis_content_key`` and
    ``content_key``.  The renderer only relies on these public labels and
    keys; it does not infer problem wording from a case id or model route.
    """

    raw = payload.get("task_contract")
    if raw is None:
        tasks = _legacy_tasks(payload)
        return {
            "version": "task_contract_legacy_v1",
            "migration_mode": "LEGACY_QUESTION_COUNT_FALLBACK",
            "tasks": tasks,
        }
    if not isinstance(raw, dict):
        raise WriterSchemaError("TASK_CONTRACT_INVALID")
    tasks_raw = raw.get("tasks")
    if not isinstance(tasks_raw, list) or not tasks_raw:
        raise WriterSchemaError("TASK_CONTRACT_TASKS_REQUIRED")
    tasks: list[dict[str, str]] = []
    task_ids: set[str] = set()
    labels: set[str] = set()
    content_keys: set[str] = set()
    analysis_keys: set[str] = set()
    for index, item in enumerate(tasks_raw, 1):
        if not isinstance(item, dict):
            raise WriterSchemaError(f"TASK_CONTRACT_TASK_INVALID: {index}")
        task = {
            "task_id": _text(item.get("task_id"), f"tasks[{index}].task_id"),
            "label": _text(item.get("label"), f"tasks[{index}].label"),
            "analysis_content_key": _text(
                item.get("analysis_content_key"), f"tasks[{index}].analysis_content_key"
            ),
            "content_key": _text(item.get("content_key"), f"tasks[{index}].content_key"),
        }
        if task["task_id"] in task_ids or task["label"] in labels:
            raise WriterSchemaError("TASK_CONTRACT_TASK_ID_OR_LABEL_DUPLICATED")
        if task["content_key"] in content_keys or task["analysis_content_key"] in analysis_keys:
            raise WriterSchemaError("TASK_CONTRACT_CONTENT_KEY_DUPLICATED")
        task_ids.add(task["task_id"])
        labels.add(task["label"])
        content_keys.add(task["content_key"])
        analysis_keys.add(task["analysis_content_key"])
        tasks.append(task)
    count = payload.get("question_count")
    if count is not None and count != len(tasks):
        raise WriterSchemaError("TASK_CONTRACT_QUESTION_COUNT_MISMATCH")
    return {
        "version": str(raw.get("version") or "task_contract_v1"),
        "migration_mode": "EXPLICIT_TASK_CONTRACT",
        "tasks": tasks,
    }
