"""Registry entrypoint for X5-F1 minimal math core."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any, Callable

from .base import make_result_summary
from .forecasting import moving_average
from .graph import shortest_path
from .optimization import linear_program, nonlinear_minimize
from .statistics import linear_regression
from .competition_2023d_q3 import MODEL_SPEC as q3_model_spec, run as run_q3
from .validators import build_risk_item


Runner = Callable[[dict[str, Any]], dict[str, Any]]


_MODELS = {
    "optimization.linear_program": (linear_program.MODEL_SPEC, linear_program.run),
    "optimization.nonlinear_minimize": (nonlinear_minimize.MODEL_SPEC, nonlinear_minimize.run),
    "statistics.linear_regression": (linear_regression.MODEL_SPEC, linear_regression.run),
    "forecasting.moving_average": (moving_average.MODEL_SPEC, moving_average.run),
    "graph.shortest_path": (shortest_path.MODEL_SPEC, shortest_path.run),
    "competition.2023d_q3": (q3_model_spec, run_q3),
}


def list_models() -> list[dict[str, Any]]:
    return [deepcopy(spec) for spec, _runner in _MODELS.values()]


def get_model_spec(model_type: str) -> dict[str, Any] | None:
    entry = _MODELS.get(model_type)
    if not entry:
        return None
    return deepcopy(entry[0])


def _load_input_json(config: dict[str, Any], case_dir: Path | None) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    input_path = config.get("input_json")
    if not input_path:
        return config, []
    if not case_dir:
        return config, [
            build_risk_item(
                severity="fatal",
                category="registry",
                message="case_dir is required for input_json",
            )
        ]
    path = case_dir / str(input_path)
    if not path.exists():
        return config, [
            build_risk_item(
                severity="fatal",
                category="registry",
                message=f"input_json missing: {input_path}",
                path=str(input_path),
            )
        ]
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        return config, [
            build_risk_item(
                severity="fatal",
                category="registry",
                message=f"input_json parse failed: {exc}",
                path=str(input_path),
            )
        ]
    merged = dict(config)
    if isinstance(payload, dict):
        merged.update(payload)
    return merged, []


def run_model(config: dict[str, Any], *, run_id: str, case_dir: Path | None = None) -> dict[str, Any]:
    model_type = str(config.get("model_type") or "").strip()
    case_id = case_dir.name if case_dir else None
    if not model_type or model_type not in _MODELS:
        return make_result_summary(
            run_id=run_id,
            case_id=case_id,
            model_id=model_type or "unregistered",
            model_family="unregistered",
            success=False,
            status="unregistered_model_type",
            metrics={},
            primary_results={},
            risk_items=[
                build_risk_item(
                    severity="fatal",
                    category="registry",
                    message=f"unregistered model_type: {model_type or '<missing>'}",
                    recommendation="Use one of the registered model_type values.",
                )
            ],
            limitations=[],
            failure_reason="unregistered_model_type",
        )

    merged_config, load_risks = _load_input_json(config, case_dir)
    if load_risks:
        spec = _MODELS[model_type][0]
        return make_result_summary(
            run_id=run_id,
            case_id=case_id,
            model_id=spec["model_id"],
            model_family=spec["model_family"],
            success=False,
            status="input_invalid",
            metrics={},
            primary_results={},
            risk_items=load_risks,
            limitations=spec.get("limitations", []),
            failure_reason="input_invalid",
        )

    _spec, runner = _MODELS[model_type]
    return runner(merged_config, run_id=run_id, case_dir=case_dir)
