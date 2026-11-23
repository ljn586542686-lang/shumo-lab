"""Validate the case-local human-readable handoff against machine state.

``case_config.json`` is the machine authority.  ``CURRENT_HANDOFF.md`` may
mirror that state for humans, but it must not silently become a second state
machine.  Cases opt in with ``state_authority=case_config.json``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pipeline_core.python_tools.transition_case import (
    preflight_public_stage,
    validate_authoritative_state,
)


def validate_workflow_state(case_dir: str | Path, config: dict[str, Any]) -> dict[str, Any]:
    return validate_authoritative_state(Path(case_dir), config)


def validate_public_stage(stage: str, state: dict[str, Any]) -> dict[str, Any]:
    """Delegate every public-stage decision to the single transition kernel."""

    return preflight_public_stage(stage, state)
