"""Compile declared paper figures into reproducible MATLAB scripts.

Formal figures use MATLAB by default. Python may still prepare data, but it is
not accepted as the rendering backend for a declared formal figure unless the
case explicitly enters a human-reviewed exception path.

This module owns figure-spec validation, deterministic script generation and
the fail-closed MATLAB execution receipt used by the paper pipeline.  A figure
spec is only a declaration: result approval is resolved from the current
result-acceptance contract and live Gate 3 evidence at execution time.
"""

from __future__ import annotations

import binascii
import json
import hashlib
import math
import os
import re
import shutil
import struct
import subprocess
import tempfile
import zlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from pipeline_core.quality_gates.result_acceptance_contract import (
    ResultAcceptanceContractError,
    validate_result_acceptance_contract,
)


FORMAL_FIGURE_BACKEND = "MATLAB"
FIGURE_SPEC_VERSION = "matlab_paper_figure_spec_v2"
FIGURE_EXECUTION_RECEIPT_VERSION = "matlab_figure_execution_receipt_v2"
PUBLICATION_STYLE_PROFILE = "COMPETITION_PUBLICATION_STYLE_V1"

SUPPORTED_FIGURE_TYPES = frozenset(
    {
        "line",
        "multi_line",
        "scatter",
        "bar",
        "grouped_bar",
        "error_bar",
        "uncertainty_band",
        "heatmap",
        "contour",
        "filled_contour",
        "surface_3d",
        "trajectory_2d",
        "trajectory_3d",
        "geometric_schematic",
        "interval_timeline",
    }
)

DEFERRED_FIGURE_TYPES = frozenset(
    {"stacked_bar", "radar", "polar", "histogram", "boxplot", "mesh_3d"}
)

SIZE_PROFILES: dict[str, tuple[float, float]] = {
    "standard": (16.0, 10.0),
    "wide": (18.0, 9.5),
    "square": (12.0, 12.0),
}

PUBLICATION_COLORS = (
    (0.0000, 0.4470, 0.7410),
    (0.8500, 0.3250, 0.0980),
    (0.4660, 0.6740, 0.1880),
    (0.4940, 0.1840, 0.5560),
)

SOURCE_BINDING_TYPES = frozenset({"result_card"})
SOURCE_SHA256_PATTERN = re.compile(r"^[0-9a-fA-F]{64}$")
FIGURE_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
OUTPUT_FILE_EXTENSIONS = frozenset({".png", ".pdf"})
FIGURE_PURPOSES = frozenset(
    {
        "model_explanation",
        "parameter_sensitivity",
        "result_comparison",
        "validation",
        "decision_support",
    }
)
FIGURE_MIN_DPI = 300.0
FIGURE_MIN_DIMENSION = 100
FIGURE_MAX_DIMENSION = 10000
PDF_MIN_PAGE_POINTS = 36.0
PDF_MAX_PAGE_POINTS = 2000.0


class MatlabFigureSpecError(ValueError):
    pass


class FigureAssetQualityError(ValueError):
    """Raised when a generated figure asset fails deterministic visual QA."""

    pass


class FigurePaperBindingError(ValueError):
    """Raised when figure metadata has no usable paper purpose binding."""

    pass


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise MatlabFigureSpecError(f"MATLAB_FIGURE_REQUIRED_TEXT: {field}")
    return value.strip()


def validate_figure_paper_binding(figure: Mapping[str, Any]) -> dict[str, Any]:
    """Validate existing figure metadata without judging the referenced claim."""

    figure_id = str(figure.get("figure_id") or "figure")
    purpose = figure.get("figure_purpose")
    if not isinstance(purpose, str) or not purpose.strip():
        raise FigurePaperBindingError(f"FIGURE_PURPOSE_REQUIRED: {figure_id}")
    if purpose.strip() not in FIGURE_PURPOSES:
        raise FigurePaperBindingError(f"FIGURE_PURPOSE_INVALID: {figure_id}")
    paper_binding = figure.get("paper_binding")
    if not isinstance(paper_binding, Mapping):
        raise FigurePaperBindingError(f"FIGURE_PAPER_BINDING_REQUIRED: {figure_id}")
    fields: dict[str, str] = {}
    for field in ("section", "supported_claim", "conclusion_reference"):
        value = paper_binding.get(field)
        if not isinstance(value, str) or not value.strip():
            raise FigurePaperBindingError(f"FIGURE_PAPER_BINDING_{field.upper()}_REQUIRED: {figure_id}")
        fields[field] = value.strip()
    first_discussion = figure.get("first_discussion")
    if not isinstance(first_discussion, str) or not first_discussion.strip():
        raise FigurePaperBindingError(f"FIGURE_FIRST_DISCUSSION_REQUIRED: {figure_id}")
    if fields["section"] != first_discussion.strip():
        raise FigurePaperBindingError(f"FIGURE_PAPER_BINDING_SECTION_MISMATCH: {figure_id}")
    return {
        "figure_purpose": purpose.strip(),
        "paper_binding": fields,
        "first_discussion": first_discussion.strip(),
    }


def _canonical_json_sha256(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def canonical_record_sha256(value: Mapping[str, Any]) -> str:
    """Return the stable SHA-256 used to bind contracts, cards and specs."""

    if not isinstance(value, Mapping):
        raise MatlabFigureSpecError("MATLAB_FIGURE_CANONICAL_RECORD_REQUIRED")
    return _canonical_json_sha256(dict(value))


def bind_gate_evidence(case_root: str | Path, evaluation: Mapping[str, Any]) -> dict[str, Any]:
    """Bind a live human-gate evaluation to its case-local evidence file."""

    if not isinstance(evaluation, Mapping):
        raise MatlabFigureSpecError("MATLAB_FIGURE_GATE3_EVALUATION_REQUIRED")
    root = Path(case_root).resolve()
    relative = evaluation.get("path")
    if not isinstance(relative, str) or not relative.strip():
        raise MatlabFigureSpecError("MATLAB_FIGURE_GATE3_EVIDENCE_UNBOUND")
    evidence = (root / relative).resolve()
    try:
        evidence.relative_to(root)
    except ValueError as exc:
        raise MatlabFigureSpecError("MATLAB_FIGURE_GATE3_EVIDENCE_ESCAPES_CASE") from exc
    if not evidence.is_file():
        raise MatlabFigureSpecError("MATLAB_FIGURE_GATE3_EVIDENCE_MISSING")
    normalized = dict(evaluation)
    normalized.pop("evidence_sha256", None)
    normalized["gate_file_sha256"] = _sha256_file(evidence)
    normalized["evidence_sha256"] = _canonical_json_sha256(normalized)
    return normalized


def _number_list(value: Any, field: str) -> list[float]:
    if not isinstance(value, list) or not value or any(
        isinstance(item, bool) or not isinstance(item, (int, float)) for item in value
    ):
        raise MatlabFigureSpecError(f"MATLAB_FIGURE_NUMERIC_SERIES_REQUIRED: {field}")
    try:
        normalized = [float(item) for item in value]
    except (OverflowError, ValueError) as exc:
        raise MatlabFigureSpecError(f"MATLAB_FIGURE_FINITE_VALUES_REQUIRED: {field}") from exc
    if any(not math.isfinite(item) for item in normalized):
        raise MatlabFigureSpecError(f"MATLAB_FIGURE_FINITE_VALUES_REQUIRED: {field}")
    return normalized


def _categories(value: Any, field: str) -> list[str]:
    if not isinstance(value, list) or not value or any(not isinstance(item, str) or not item.strip() for item in value):
        raise MatlabFigureSpecError(f"MATLAB_FIGURE_CATEGORIES_REQUIRED: {field}")
    return [item.strip() for item in value]


def _number_matrix(value: Any, field: str) -> list[list[float]]:
    if not isinstance(value, list) or not value or any(not isinstance(row, list) or not row for row in value):
        raise MatlabFigureSpecError(f"MATLAB_FIGURE_NUMERIC_MATRIX_REQUIRED: {field}")
    matrix: list[list[float]] = []
    width: int | None = None
    for row_index, row in enumerate(value, 1):
        if any(isinstance(item, bool) or not isinstance(item, (int, float)) for item in row):
            raise MatlabFigureSpecError(f"MATLAB_FIGURE_NUMERIC_MATRIX_REQUIRED: {field}[{row_index}]")
        try:
            normalized = [float(item) for item in row]
        except (OverflowError, ValueError) as exc:
            raise MatlabFigureSpecError(f"MATLAB_FIGURE_FINITE_VALUES_REQUIRED: {field}") from exc
        if any(not math.isfinite(item) for item in normalized):
            raise MatlabFigureSpecError(f"MATLAB_FIGURE_FINITE_VALUES_REQUIRED: {field}")
        if width is None:
            width = len(normalized)
        elif len(normalized) != width:
            raise MatlabFigureSpecError(f"MATLAB_FIGURE_MATRIX_RECTANGULAR_REQUIRED: {field}")
        matrix.append(normalized)
    return matrix


def _equal_lengths(values: dict[str, list[float]], label: str) -> None:
    if len({len(items) for items in values.values()}) != 1:
        raise MatlabFigureSpecError(f"MATLAB_FIGURE_SERIES_LENGTH_MISMATCH: {label}")


def _source_binding(value: Any) -> dict[str, Any]:
    """Validate a declared result-card identity; approval is resolved later."""

    if not isinstance(value, dict):
        raise MatlabFigureSpecError("MATLAB_FIGURE_SOURCE_BINDING_STRUCTURED_REQUIRED")
    source_id = _text(value.get("source_id"), "source_binding.source_id")
    source_type = _text(value.get("source_type"), "source_binding.source_type")
    if source_type not in SOURCE_BINDING_TYPES:
        raise MatlabFigureSpecError(f"MATLAB_FIGURE_SOURCE_TYPE_UNSUPPORTED: {source_type}")
    sha256 = _text(value.get("sha256"), "source_binding.sha256").lower()
    if SOURCE_SHA256_PATTERN.fullmatch(sha256) is None:
        raise MatlabFigureSpecError("MATLAB_FIGURE_SOURCE_SHA256_INVALID")
    return {
        "source_id": source_id,
        "source_type": source_type,
        "sha256": sha256,
    }


def _output_file(value: Any) -> str:
    output = _text(value, "output_file")
    if "\x00" in output:
        raise MatlabFigureSpecError("MATLAB_FIGURE_OUTPUT_PATH_INVALID")
    normalized = Path(output.replace("\\", "/"))
    if normalized.is_absolute() or re.match(r"^[A-Za-z]:", output):
        raise MatlabFigureSpecError("MATLAB_FIGURE_OUTPUT_PATH_ABSOLUTE")
    if normalized.suffix.lower() not in OUTPUT_FILE_EXTENSIONS:
        raise MatlabFigureSpecError("MATLAB_FIGURE_OUTPUT_EXTENSION_UNSUPPORTED")
    if any(part == ".." for part in re.split(r"[\\/]+", output)):
        raise MatlabFigureSpecError("MATLAB_FIGURE_OUTPUT_PATH_TRAVERSAL")
    return output


def _approved_result_binding(
    source_binding: Mapping[str, Any],
    result_acceptance_contract: Mapping[str, Any] | None,
    gate3_evaluation: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Resolve one declared source against the accepted result registry and Gate 3."""

    if not isinstance(result_acceptance_contract, Mapping):
        raise MatlabFigureSpecError("MATLAB_FIGURE_RESULT_ACCEPTANCE_CONTRACT_REQUIRED")
    contract = dict(result_acceptance_contract)
    try:
        report = validate_result_acceptance_contract(contract)
    except ResultAcceptanceContractError as exc:
        raise MatlabFigureSpecError("MATLAB_FIGURE_RESULT_ACCEPTANCE_CONTRACT_INVALID") from exc
    if report.get("status") != "PASS":
        raise MatlabFigureSpecError("MATLAB_FIGURE_RESULT_ACCEPTANCE_CONTRACT_INVALID")
    if (
        not isinstance(gate3_evaluation, Mapping)
        or gate3_evaluation.get("gate_id") != "GATE3"
        or gate3_evaluation.get("gate_status") != "PASS"
    ):
        raise MatlabFigureSpecError("MATLAB_FIGURE_GATE3_NOT_APPROVED")
    evidence_sha256 = str(gate3_evaluation.get("evidence_sha256") or "").lower()
    if SOURCE_SHA256_PATTERN.fullmatch(evidence_sha256) is None:
        raise MatlabFigureSpecError("MATLAB_FIGURE_GATE3_EVIDENCE_UNBOUND")
    result_id = str(source_binding["source_id"])
    cards = [
        card
        for card in contract.get("result_cards", [])
        if isinstance(card, dict) and card.get("result_id") == result_id
    ]
    if len(cards) != 1:
        raise MatlabFigureSpecError("MATLAB_FIGURE_RESULT_CARD_NOT_FOUND")
    card = cards[0]
    if card.get("approved") is not True:
        raise MatlabFigureSpecError("MATLAB_FIGURE_RESULT_CARD_NOT_APPROVED")
    result_sha256 = _canonical_json_sha256(card)
    if result_sha256 != str(source_binding["sha256"]).lower():
        raise MatlabFigureSpecError("MATLAB_FIGURE_RESULT_CARD_HASH_MISMATCH")
    return {
        "result_id": result_id,
        "result_type": "result_card",
        "result_sha256": result_sha256,
        "approved": True,
        "source_contract": "result_acceptance_contract",
        "result_acceptance_contract_sha256": _canonical_json_sha256(contract),
        "gate3_evidence_sha256": evidence_sha256,
    }


def _require_nonnegative(values: list[float], field: str) -> None:
    if any(value < 0 for value in values):
        raise MatlabFigureSpecError(f"MATLAB_FIGURE_NONNEGATIVE_REQUIRED: {field}")


def _require_meaningful_variation(values: list[float], field: str) -> None:
    if len(set(values)) < 2:
        raise MatlabFigureSpecError(f"MATLAB_FIGURE_3D_VARIATION_REQUIRED: {field}")


def _validate_series(raw_series: Any, *, minimum: int = 1) -> list[dict[str, Any]]:
    if not isinstance(raw_series, list) or len(raw_series) < minimum:
        raise MatlabFigureSpecError("MATLAB_FIGURE_SERIES_REQUIRED")
    normalized: list[dict[str, Any]] = []
    for index, raw in enumerate(raw_series, 1):
        if not isinstance(raw, dict):
            raise MatlabFigureSpecError(f"MATLAB_FIGURE_SERIES_INVALID: {index}")
        x = _number_list(raw.get("x"), f"series[{index}].x")
        y = _number_list(raw.get("y"), f"series[{index}].y")
        _equal_lengths({"x": x, "y": y}, str(index))
        normalized.append({"name": _text(raw.get("name"), f"series[{index}].name"), "x": x, "y": y})
    return normalized


def _validate_grid(spec: dict[str, Any], *, require_z_label: bool = False) -> dict[str, Any]:
    x = _number_list(spec.get("x"), "x")
    y = _number_list(spec.get("y"), "y")
    z = _number_matrix(spec.get("z"), "z")
    if len(z) != len(y) or len(z[0]) != len(x):
        raise MatlabFigureSpecError("MATLAB_FIGURE_MATRIX_DIMENSIONS_MISMATCH")
    if require_z_label:
        _text(spec.get("z_label"), "z_label")
    return {"x": x, "y": y, "z": z}


def _validate_xy(spec: dict[str, Any], *, label: str) -> dict[str, Any]:
    x = _number_list(spec.get("x"), "x")
    y = _number_list(spec.get("y"), "y")
    _equal_lengths({"x": x, "y": y}, label)
    return {"x": x, "y": y}


def _finite_number(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise MatlabFigureSpecError(f"MATLAB_FIGURE_FINITE_NUMBER_REQUIRED: {field}")
    number = float(value)
    if not math.isfinite(number):
        raise MatlabFigureSpecError(f"MATLAB_FIGURE_FINITE_NUMBER_REQUIRED: {field}")
    return number


def _validate_geometric_schematic(spec: dict[str, Any]) -> dict[str, Any]:
    """Validate object-and-relation diagrams used to introduce a model."""

    raw_panels = spec.get("panels")
    if not isinstance(raw_panels, list) or not raw_panels or len(raw_panels) > 4:
        raise MatlabFigureSpecError("MATLAB_FIGURE_SCHEMATIC_PANELS_REQUIRED")
    panels: list[dict[str, Any]] = []
    for panel_index, raw_panel in enumerate(raw_panels, 1):
        if not isinstance(raw_panel, dict):
            raise MatlabFigureSpecError(f"MATLAB_FIGURE_SCHEMATIC_PANEL_INVALID: {panel_index}")
        panel_title = _text(raw_panel.get("title"), f"panels[{panel_index}].title")
        raw_objects = raw_panel.get("objects")
        if not isinstance(raw_objects, list) or len(raw_objects) < 2:
            raise MatlabFigureSpecError(f"MATLAB_FIGURE_SCHEMATIC_OBJECTS_REQUIRED: {panel_index}")
        objects: list[dict[str, Any]] = []
        coordinates: dict[str, tuple[float, float]] = {}
        for object_index, raw_object in enumerate(raw_objects, 1):
            if not isinstance(raw_object, dict):
                raise MatlabFigureSpecError(f"MATLAB_FIGURE_SCHEMATIC_OBJECT_INVALID: {panel_index}.{object_index}")
            object_id = _text(raw_object.get("object_id"), f"panels[{panel_index}].objects[{object_index}].object_id")
            if FIGURE_ID_PATTERN.fullmatch(object_id) is None or object_id in coordinates:
                raise MatlabFigureSpecError(f"MATLAB_FIGURE_SCHEMATIC_OBJECT_ID_INVALID: {panel_index}.{object_id}")
            x = _finite_number(raw_object.get("x"), f"{object_id}.x")
            y = _finite_number(raw_object.get("y"), f"{object_id}.y")
            marker_kind = str(raw_object.get("marker_kind", "generic")).strip().lower()
            if marker_kind not in {"anchor", "receiver", "unknown", "target", "generic"}:
                raise MatlabFigureSpecError(f"MATLAB_FIGURE_SCHEMATIC_MARKER_INVALID: {object_id}")
            label_offset = raw_object.get("label_offset", [0.0, 0.0])
            offset = _number_list(label_offset, f"{object_id}.label_offset")
            if len(offset) != 2:
                raise MatlabFigureSpecError(f"MATLAB_FIGURE_SCHEMATIC_LABEL_OFFSET_INVALID: {object_id}")
            objects.append({
                "object_id": object_id,
                "label": _text(raw_object.get("label"), f"{object_id}.label"),
                "x": x,
                "y": y,
                "marker_kind": marker_kind,
                "label_offset": offset,
            })
            coordinates[object_id] = (x, y)
        raw_relations = raw_panel.get("relations")
        if not isinstance(raw_relations, list) or not raw_relations:
            raise MatlabFigureSpecError(f"MATLAB_FIGURE_SCHEMATIC_RELATIONS_REQUIRED: {panel_index}")
        relations: list[dict[str, Any]] = []
        for relation_index, raw_relation in enumerate(raw_relations, 1):
            if not isinstance(raw_relation, dict):
                raise MatlabFigureSpecError(f"MATLAB_FIGURE_SCHEMATIC_RELATION_INVALID: {panel_index}.{relation_index}")
            kind = str(raw_relation.get("kind") or "").strip().lower()
            if kind not in {"segment", "dashed_segment", "arrow", "circle", "arc"}:
                raise MatlabFigureSpecError(f"MATLAB_FIGURE_SCHEMATIC_RELATION_KIND_INVALID: {panel_index}.{relation_index}")
            relation: dict[str, Any] = {"kind": kind, "label": str(raw_relation.get("label") or "").strip()}
            if kind in {"segment", "dashed_segment", "arrow"}:
                source = _text(raw_relation.get("from"), f"relations[{relation_index}].from")
                target = _text(raw_relation.get("to"), f"relations[{relation_index}].to")
                if source not in coordinates or target not in coordinates or source == target:
                    raise MatlabFigureSpecError(f"MATLAB_FIGURE_SCHEMATIC_RELATION_ENDPOINT_INVALID: {panel_index}.{relation_index}")
                relation.update({"from": source, "to": target})
            else:
                center = _text(raw_relation.get("center"), f"relations[{relation_index}].center")
                if center not in coordinates:
                    raise MatlabFigureSpecError(f"MATLAB_FIGURE_SCHEMATIC_RELATION_CENTER_INVALID: {panel_index}.{relation_index}")
                radius = _finite_number(raw_relation.get("radius"), f"relations[{relation_index}].radius")
                if radius <= 0:
                    raise MatlabFigureSpecError(f"MATLAB_FIGURE_SCHEMATIC_RADIUS_INVALID: {panel_index}.{relation_index}")
                relation.update({"center": center, "radius": radius})
                if kind == "arc":
                    relation["start_deg"] = _finite_number(raw_relation.get("start_deg"), f"relations[{relation_index}].start_deg")
                    relation["end_deg"] = _finite_number(raw_relation.get("end_deg"), f"relations[{relation_index}].end_deg")
                    if relation["start_deg"] == relation["end_deg"]:
                        raise MatlabFigureSpecError(f"MATLAB_FIGURE_SCHEMATIC_ARC_INVALID: {panel_index}.{relation_index}")
            relations.append(relation)
        show_axes = raw_panel.get("show_axes", False)
        if not isinstance(show_axes, bool):
            raise MatlabFigureSpecError(f"MATLAB_FIGURE_SCHEMATIC_SHOW_AXES_INVALID: {panel_index}")
        panel: dict[str, Any] = {
            "title": panel_title,
            "objects": objects,
            "relations": relations,
            "show_axes": show_axes,
        }
        for limit_name in ("x_limits", "y_limits"):
            if limit_name in raw_panel:
                limits = _number_list(raw_panel[limit_name], f"panels[{panel_index}].{limit_name}")
                if len(limits) != 2 or limits[0] >= limits[1]:
                    raise MatlabFigureSpecError(f"MATLAB_FIGURE_SCHEMATIC_LIMITS_INVALID: {panel_index}.{limit_name}")
                panel[limit_name] = limits
        panels.append(panel)
    return {"panels": panels}


def _validate_interval_timeline(spec: dict[str, Any]) -> dict[str, Any]:
    """Validate a resource/task timeline without admitting generic flow boxes."""

    raw_lanes = spec.get("lanes")
    if not isinstance(raw_lanes, list) or not raw_lanes:
        raise MatlabFigureSpecError("MATLAB_FIGURE_TIMELINE_LANES_REQUIRED")
    lanes: list[dict[str, Any]] = []
    seen_lane_ids: set[str] = set()
    interval_count = 0
    for lane_index, raw_lane in enumerate(raw_lanes, 1):
        if not isinstance(raw_lane, dict):
            raise MatlabFigureSpecError(f"MATLAB_FIGURE_TIMELINE_LANE_INVALID: {lane_index}")
        lane_id = _text(raw_lane.get("lane_id"), f"lanes[{lane_index}].lane_id")
        if FIGURE_ID_PATTERN.fullmatch(lane_id) is None or lane_id in seen_lane_ids:
            raise MatlabFigureSpecError(f"MATLAB_FIGURE_TIMELINE_LANE_ID_INVALID: {lane_id}")
        seen_lane_ids.add(lane_id)
        raw_intervals = raw_lane.get("intervals")
        if not isinstance(raw_intervals, list) or not raw_intervals:
            raise MatlabFigureSpecError(f"MATLAB_FIGURE_TIMELINE_INTERVALS_REQUIRED: {lane_id}")
        intervals: list[dict[str, Any]] = []
        for interval_index, raw_interval in enumerate(raw_intervals, 1):
            if not isinstance(raw_interval, dict):
                raise MatlabFigureSpecError(f"MATLAB_FIGURE_TIMELINE_INTERVAL_INVALID: {lane_id}.{interval_index}")
            start = _finite_number(raw_interval.get("start"), f"{lane_id}.{interval_index}.start")
            end = _finite_number(raw_interval.get("end"), f"{lane_id}.{interval_index}.end")
            if start >= end:
                raise MatlabFigureSpecError(f"MATLAB_FIGURE_TIMELINE_INTERVAL_ORDER_INVALID: {lane_id}.{interval_index}")
            intervals.append({
                "start": start,
                "end": end,
                "label": _text(raw_interval.get("label"), f"{lane_id}.{interval_index}.label"),
                "category": str(raw_interval.get("category") or "default").strip(),
            })
            interval_count += 1
        lanes.append({
            "lane_id": lane_id,
            "label": _text(raw_lane.get("label"), f"lanes[{lane_index}].label"),
            "intervals": intervals,
        })
    if interval_count < 2:
        raise MatlabFigureSpecError("MATLAB_FIGURE_TIMELINE_MULTIPLE_INTERVALS_REQUIRED")
    return {"lanes": lanes}


def validate_figure_spec(spec: dict[str, Any]) -> dict[str, Any]:
    """Validate and normalize a figure spec without changing its data."""

    if not isinstance(spec, dict):
        raise MatlabFigureSpecError("MATLAB_FIGURE_SPEC_INVALID")
    figure_id = _text(spec.get("figure_id"), "figure_id")
    if FIGURE_ID_PATTERN.fullmatch(figure_id) is None:
        raise MatlabFigureSpecError("MATLAB_FIGURE_ID_INVALID")
    if spec.get("backend", FORMAL_FIGURE_BACKEND) != FORMAL_FIGURE_BACKEND:
        raise MatlabFigureSpecError("FORMAL_FIGURE_BACKEND_MUST_BE_MATLAB")
    for field in ("title", "x_label", "y_label"):
        _text(spec.get(field), field)
    source_binding = _source_binding(spec.get("source_binding"))
    output_file = _output_file(spec.get("output_file"))

    # Specs written before F1 remain valid as line figures. New typed figures
    # must provide figure_type and purpose together.
    legacy_type_spec = "figure_type" not in spec and "purpose" not in spec
    figure_type = spec.get("figure_type", "line")
    if not isinstance(figure_type, str) or figure_type not in SUPPORTED_FIGURE_TYPES:
        raise MatlabFigureSpecError(f"MATLAB_FIGURE_TYPE_UNSUPPORTED: {figure_type}")
    purpose = "trend" if legacy_type_spec else _text(spec.get("purpose"), "purpose")

    size_profile = spec.get("size_profile", "standard")
    if size_profile not in SIZE_PROFILES:
        raise MatlabFigureSpecError(f"MATLAB_FIGURE_SIZE_PROFILE_UNSUPPORTED: {size_profile}")
    show_marker = spec.get("show_marker", False)
    if not isinstance(show_marker, bool):
        raise MatlabFigureSpecError("MATLAB_FIGURE_SHOW_MARKER_INVALID")
    show_title = spec.get("show_title", False)
    if not isinstance(show_title, bool):
        raise MatlabFigureSpecError("MATLAB_FIGURE_SHOW_TITLE_INVALID")
    show_legend = spec.get("show_legend")
    if show_legend is not None and not isinstance(show_legend, bool):
        raise MatlabFigureSpecError("MATLAB_FIGURE_SHOW_LEGEND_INVALID")
    marker_size = spec.get("marker_size", 5)
    if isinstance(marker_size, bool) or not isinstance(marker_size, (int, float)) or marker_size <= 0:
        raise MatlabFigureSpecError("MATLAB_FIGURE_MARKER_SIZE_INVALID")
    dpi = spec.get("dpi", 400)
    if not isinstance(dpi, int) or isinstance(dpi, bool) or dpi < 300:
        raise MatlabFigureSpecError("MATLAB_FIGURE_DPI_TOO_LOW")

    normalized: dict[str, Any] = {
        "figure_id": figure_id,
        "backend": FORMAL_FIGURE_BACKEND,
        "figure_type": figure_type,
        "purpose": purpose,
        "title": str(spec["title"]),
        "x_label": str(spec["x_label"]),
        "y_label": str(spec["y_label"]),
        "source_binding": source_binding,
        "output_file": output_file,
        "dpi": dpi,
        "size_profile": size_profile,
        "show_marker": show_marker,
        "show_title": show_title,
        "show_legend": show_legend,
        "marker_size": float(marker_size),
    }

    if figure_type in {"line", "multi_line", "scatter"}:
        normalized["series"] = _validate_series(spec.get("series"), minimum=1)
        if figure_type == "line" and len(normalized["series"]) != 1:
            raise MatlabFigureSpecError("MATLAB_FIGURE_LINE_REQUIRES_ONE_SERIES")
    elif figure_type == "bar":
        categories = _categories(spec.get("categories"), "categories")
        values = _number_list(spec.get("values"), "values")
        if len(categories) != len(values):
            raise MatlabFigureSpecError("MATLAB_FIGURE_SERIES_LENGTH_MISMATCH: bar")
        normalized.update({"categories": categories, "values": values, "series_name": str(spec.get("series_name", "Value"))})
    elif figure_type == "grouped_bar":
        categories = _categories(spec.get("categories"), "categories")
        raw_series = spec.get("series")
        if not isinstance(raw_series, list) or not raw_series:
            raise MatlabFigureSpecError("MATLAB_FIGURE_SERIES_REQUIRED")
        series: list[dict[str, Any]] = []
        for index, raw in enumerate(raw_series, 1):
            if not isinstance(raw, dict):
                raise MatlabFigureSpecError(f"MATLAB_FIGURE_SERIES_INVALID: {index}")
            values = _number_list(raw.get("values"), f"series[{index}].values")
            if len(values) != len(categories):
                raise MatlabFigureSpecError(f"MATLAB_FIGURE_SERIES_LENGTH_MISMATCH: {index}")
            series.append({"name": _text(raw.get("name"), f"series[{index}].name"), "values": values})
        normalized.update({"categories": categories, "series": series})
    elif figure_type == "error_bar":
        if "categories" in spec:
            categories = _categories(spec.get("categories"), "categories")
            y = _number_list(spec.get("y"), "y")
            x = [float(index) for index in range(1, len(categories) + 1)]
            normalized["categories"] = categories
            if len(categories) != len(y):
                raise MatlabFigureSpecError("MATLAB_FIGURE_SERIES_LENGTH_MISMATCH: error_bar")
        else:
            x = _number_list(spec.get("x"), "x")
            y = _number_list(spec.get("y"), "y")
        _equal_lengths({"x": x, "y": y}, "error_bar")
        if "error" in spec:
            error = _number_list(spec.get("error"), "error")
            _equal_lengths({"y": y, "error": error}, "error_bar")
            _require_nonnegative(error, "error")
            normalized["error"] = error
        else:
            lower = _number_list(spec.get("lower_error"), "lower_error")
            upper = _number_list(spec.get("upper_error"), "upper_error")
            _equal_lengths({"y": y, "lower_error": lower, "upper_error": upper}, "error_bar")
            _require_nonnegative(lower, "lower_error")
            _require_nonnegative(upper, "upper_error")
            normalized.update({"lower_error": lower, "upper_error": upper})
        normalized.update({"x": x, "y": y})
    elif figure_type == "uncertainty_band":
        x = _number_list(spec.get("x"), "x")
        center = _number_list(spec.get("center"), "center")
        lower = _number_list(spec.get("lower"), "lower")
        upper = _number_list(spec.get("upper"), "upper")
        _equal_lengths({"x": x, "center": center, "lower": lower, "upper": upper}, "uncertainty_band")
        if any(not (lo <= mid <= hi) for lo, mid, hi in zip(lower, center, upper)):
            raise MatlabFigureSpecError("MATLAB_FIGURE_UNCERTAINTY_ORDER_INVALID")
        _require_nonnegative([mid - lo for mid, lo in zip(center, lower)], "lower_error")
        _require_nonnegative([hi - mid for hi, mid in zip(upper, center)], "upper_error")
        normalized.update({"x": x, "center": center, "lower": lower, "upper": upper})
    elif figure_type in {"heatmap", "contour", "filled_contour"}:
        normalized.update(_validate_grid(spec))
        normalized["colorbar_label"] = str(spec.get("colorbar_label", ""))
    elif figure_type == "surface_3d":
        normalized.update(_validate_grid(spec, require_z_label=True))
        _require_meaningful_variation([item for row in normalized["z"] for item in row], "z")
        normalized["z_label"] = _text(spec.get("z_label"), "z_label")
        normalized["colorbar_label"] = str(spec.get("colorbar_label", ""))
    elif figure_type == "trajectory_2d":
        normalized.update(_validate_xy(spec, label="trajectory_2d"))
    elif figure_type == "trajectory_3d":
        x = _number_list(spec.get("x"), "x")
        y = _number_list(spec.get("y"), "y")
        z = _number_list(spec.get("z"), "z")
        _equal_lengths({"x": x, "y": y, "z": z}, "trajectory_3d")
        _require_meaningful_variation(z, "z")
        normalized.update({"x": x, "y": y, "z": z, "z_label": _text(spec.get("z_label"), "z_label")})
    elif figure_type == "geometric_schematic":
        normalized.update(_validate_geometric_schematic(spec))
    elif figure_type == "interval_timeline":
        normalized.update(_validate_interval_timeline(spec))
    if "publication_layout" in spec:
        from pipeline_core.paper_quality.matlab_publication_layout import normalize_layout, PublicationLayoutError
        try:
            normalized["publication_layout"] = normalize_layout(spec["publication_layout"], normalized)
        except PublicationLayoutError as exc:
            raise MatlabFigureSpecError(str(exc)) from exc
    return normalized


def _matlab_vector(values: list[float]) -> str:
    return "[" + " ".join(f"{value:.15g}" for value in values) + "]"


def _matlab_matrix(values: list[list[float]]) -> str:
    return "[" + "; ".join(" ".join(f"{item:.15g}" for item in row) for row in values) + "]"


def _quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _png_empty_check(raw_pixels: bytes, *, channels: int, bit_depth: int) -> None:
    if not raw_pixels or len(set(raw_pixels)) <= 1:
        raise FigureAssetQualityError("FIGURE_ASSET_EMPTY")
    if bit_depth in {8, 16} and channels in {2, 4}:
        bytes_per_sample = bit_depth // 8
        pixel_stride = channels * bytes_per_sample
        alpha_offset = (channels - 1) * bytes_per_sample
        alpha = bytearray()
        for offset in range(alpha_offset, len(raw_pixels), pixel_stride):
            alpha.extend(raw_pixels[offset : offset + bytes_per_sample])
        if alpha and not any(alpha):
            raise FigureAssetQualityError("FIGURE_ASSET_EMPTY")


def _validate_png_asset(path: Path, *, minimum_dpi: float) -> dict[str, Any]:
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise FigureAssetQualityError("FIGURE_ASSET_UNREADABLE") from exc
    signature = b"\x89PNG\r\n\x1a\n"
    if not data.startswith(signature):
        raise FigureAssetQualityError("FIGURE_ASSET_FORMAT_INVALID")
    position = len(signature)
    ihdr: tuple[int, int, int, int, int, int, int] | None = None
    idat = bytearray()
    ppu: tuple[int, int, int] | None = None
    saw_iend = False
    try:
        while position < len(data):
            if position + 12 > len(data):
                raise FigureAssetQualityError("FIGURE_ASSET_UNREADABLE")
            length = struct.unpack(">I", data[position : position + 4])[0]
            chunk_start = position + 4
            chunk_end = chunk_start + 4 + length + 4
            if chunk_end > len(data):
                raise FigureAssetQualityError("FIGURE_ASSET_UNREADABLE")
            chunk_type = data[chunk_start : chunk_start + 4]
            chunk_data = data[chunk_start + 4 : chunk_start + 4 + length]
            expected_crc = struct.unpack(">I", data[chunk_start + 4 + length : chunk_end])[0]
            if binascii.crc32(chunk_type + chunk_data) & 0xFFFFFFFF != expected_crc:
                raise FigureAssetQualityError("FIGURE_ASSET_UNREADABLE")
            if chunk_type == b"IHDR":
                if length != 13 or ihdr is not None:
                    raise FigureAssetQualityError("FIGURE_ASSET_UNREADABLE")
                ihdr = struct.unpack(">IIBBBBB", chunk_data)
            elif chunk_type == b"pHYs" and length == 9:
                ppu = struct.unpack(">IIB", chunk_data)
            elif chunk_type == b"IDAT":
                idat.extend(chunk_data)
            elif chunk_type == b"IEND":
                saw_iend = True
                break
            position = chunk_end
    except (struct.error, ValueError) as exc:
        raise FigureAssetQualityError("FIGURE_ASSET_UNREADABLE") from exc
    if ihdr is None or not saw_iend or not idat:
        raise FigureAssetQualityError("FIGURE_ASSET_UNREADABLE")
    width, height, bit_depth, color_type, compression, filter_method, interlace = ihdr
    channel_counts = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}
    if (
        width < FIGURE_MIN_DIMENSION
        or height < FIGURE_MIN_DIMENSION
        or width > FIGURE_MAX_DIMENSION
        or height > FIGURE_MAX_DIMENSION
    ):
        raise FigureAssetQualityError("FIGURE_ASSET_DIMENSIONS_INVALID")
    if color_type not in channel_counts or compression != 0 or filter_method != 0 or interlace != 0:
        raise FigureAssetQualityError("FIGURE_ASSET_FORMAT_INVALID")
    if bit_depth not in {1, 2, 4, 8, 16}:
        raise FigureAssetQualityError("FIGURE_ASSET_FORMAT_INVALID")
    if color_type in {2, 4} and bit_depth not in {8, 16}:
        raise FigureAssetQualityError("FIGURE_ASSET_FORMAT_INVALID")
    channels = channel_counts[color_type]
    row_bytes = (width * channels * bit_depth + 7) // 8
    bytes_per_pixel = max(1, (channels * bit_depth + 7) // 8)
    try:
        inflated = zlib.decompress(bytes(idat))
    except zlib.error as exc:
        raise FigureAssetQualityError("FIGURE_ASSET_UNREADABLE") from exc
    expected_size = height * (row_bytes + 1)
    if len(inflated) != expected_size:
        raise FigureAssetQualityError("FIGURE_ASSET_UNREADABLE")
    rows: list[bytes] = []
    offset = 0
    previous = bytearray(row_bytes)
    for _ in range(height):
        filter_type = inflated[offset]
        filtered = inflated[offset + 1 : offset + 1 + row_bytes]
        offset += row_bytes + 1
        row = bytearray(row_bytes)
        for index, value in enumerate(filtered):
            left = row[index - bytes_per_pixel] if index >= bytes_per_pixel else 0
            up = previous[index]
            upper_left = previous[index - bytes_per_pixel] if index >= bytes_per_pixel else 0
            if filter_type == 0:
                predictor = 0
            elif filter_type == 1:
                predictor = left
            elif filter_type == 2:
                predictor = up
            elif filter_type == 3:
                predictor = (left + up) // 2
            elif filter_type == 4:
                estimate = left + up - upper_left
                distances = (abs(estimate - left), abs(estimate - up), abs(estimate - upper_left))
                predictor = (left, up, upper_left)[distances.index(min(distances))]
            else:
                raise FigureAssetQualityError("FIGURE_ASSET_UNREADABLE")
            row[index] = (value + predictor) & 0xFF
        rows.append(bytes(row))
        previous = row
    _png_empty_check(b"".join(rows), channels=channels, bit_depth=bit_depth)
    if ppu is None or ppu[2] != 1 or ppu[0] <= 0 or ppu[1] <= 0:
        raise FigureAssetQualityError("FIGURE_ASSET_DPI_MISSING")
    dpi_x = ppu[0] * 0.0254
    dpi_y = ppu[1] * 0.0254
    if dpi_x < minimum_dpi or dpi_y < minimum_dpi:
        raise FigureAssetQualityError("FIGURE_ASSET_DPI_TOO_LOW")
    return {
        "status": "PASS",
        "format": "png",
        "size_bytes": path.stat().st_size,
        "width": width,
        "height": height,
        "dpi_x": round(dpi_x, 3),
        "dpi_y": round(dpi_y, 3),
        "empty_check": "PASS",
    }


def _validate_pdf_asset(path: Path) -> dict[str, Any]:
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise FigureAssetQualityError("FIGURE_ASSET_UNREADABLE") from exc
    if not data.startswith(b"%PDF-") or b"%%EOF" not in data[-4096:]:
        raise FigureAssetQualityError("FIGURE_ASSET_FORMAT_INVALID")
    box_match = re.search(
        rb"/MediaBox\s*\[\s*(-?[0-9]+(?:\.[0-9]+)?)\s+(-?[0-9]+(?:\.[0-9]+)?)\s+(-?[0-9]+(?:\.[0-9]+)?)\s+(-?[0-9]+(?:\.[0-9]+)?)\s*\]",
        data,
    )
    width: float | None = None
    height: float | None = None
    if box_match is not None:
        left, bottom, right, top = (float(value) for value in box_match.groups())
        width = right - left
        height = top - bottom
    renderer = shutil.which("pdftoppm")
    if renderer is None:
        raise FigureAssetQualityError("FIGURE_PDF_RENDERER_UNAVAILABLE")
    with tempfile.TemporaryDirectory(prefix="figure_pdf_qa_") as directory:
        prefix = Path(directory) / "rendered"
        try:
            completed = subprocess.run(
                [renderer, "-png", "-singlefile", "-r", "72", str(path), str(prefix)],
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise FigureAssetQualityError("FIGURE_PDF_RENDER_FAILED") from exc
        rendered = prefix.with_suffix(".png")
        if completed.returncode != 0 or not rendered.is_file():
            raise FigureAssetQualityError("FIGURE_PDF_RENDER_FAILED")
        try:
            rendered_qa = _validate_png_asset(rendered, minimum_dpi=70.0)
        except FigureAssetQualityError as exc:
            raise FigureAssetQualityError(f"FIGURE_PDF_RENDERED_QA_FAILED: {exc}") from exc
    if width is None or height is None:
        width = float(rendered_qa["width"])
        height = float(rendered_qa["height"])
    if not (PDF_MIN_PAGE_POINTS <= width <= PDF_MAX_PAGE_POINTS and PDF_MIN_PAGE_POINTS <= height <= PDF_MAX_PAGE_POINTS):
        raise FigureAssetQualityError("FIGURE_ASSET_DIMENSIONS_INVALID")
    return {
        "status": "PASS",
        "format": "pdf",
        "size_bytes": path.stat().st_size,
        "width_points": width,
        "height_points": height,
        "dpi_x": None,
        "dpi_y": None,
        "empty_check": "PASS",
        "rendered_page_check": "PASS",
        "rendered_width": rendered_qa["width"],
        "rendered_height": rendered_qa["height"],
    }


def validate_figure_asset(
    path: str | Path,
    *,
    asset_format: str | None = None,
    minimum_dpi: float = FIGURE_MIN_DPI,
) -> dict[str, Any]:
    """Run deterministic readability, format, dimension, DPI and empty checks."""

    asset = Path(path)
    if not asset.is_file() or asset.stat().st_size <= 0:
        raise FigureAssetQualityError("FIGURE_ASSET_MISSING")
    expected_format = (asset_format or asset.suffix.lstrip(".")).lower().strip()
    if expected_format not in {"png", "pdf"} or asset.suffix.lower() != f".{expected_format}":
        raise FigureAssetQualityError("FIGURE_ASSET_FORMAT_INVALID")
    if expected_format == "png":
        return _validate_png_asset(asset, minimum_dpi=max(float(minimum_dpi), FIGURE_MIN_DPI))
    return _validate_pdf_asset(asset)


def _matlab_cellstr(values: list[str]) -> str:
    return "{" + ", ".join(_quote(value) for value in values) + "}"


def _color_literal(color: tuple[float, float, float]) -> str:
    return "[" + " ".join(f"{item:.4f}" for item in color) + "]"


def _append_labels(lines: list[str], figure: dict[str, Any], *, z_label: bool = False) -> None:
    lines.append(f"xlabel(ax,{_quote(figure['x_label'])},'Color','k');")
    lines.append(f"ylabel(ax,{_quote(figure['y_label'])},'Color','k');")
    if z_label:
        lines.append(f"zlabel(ax,{_quote(figure['z_label'])},'Color','k');")
    if figure["show_title"]:
        lines.append(f"title(ax,{_quote(figure['title'])},'FontWeight','bold','Color','k');")


def _append_legend(lines: list[str], figure: dict[str, Any], count: int) -> None:
    show_legend = figure["show_legend"] if figure["show_legend"] is not None else count > 1
    if show_legend:
        lines.append("legend(ax,'Location','best','Box','off','TextColor','k','Color','w');")


def _append_colorbar(lines: list[str], figure: dict[str, Any]) -> None:
    lines.append("cb = colorbar(ax);")
    if figure["colorbar_label"]:
        lines.append(f"ylabel(cb,{_quote(figure['colorbar_label'])},'Color','k');")


def _append_export(lines: list[str], figure: dict[str, Any]) -> None:
    output = figure["output_file"].replace("\\", "/")
    lines.extend(
        [
            "scriptFolder = fileparts(mfilename('fullpath'));",
            "if isempty(scriptFolder), scriptFolder = pwd; end",
            f"outputFile = fullfile(scriptFolder,{_quote(output)});",
            "outputFolder = fileparts(outputFile);",
            "if ~exist(outputFolder,'dir'), mkdir(outputFolder); end",
        ]
    )
    if output.lower().endswith(".pdf"):
        lines.append("exportgraphics(fig,outputFile,'ContentType','vector');")
    else:
        lines.append(f"exportgraphics(fig,outputFile,'Resolution',{figure['dpi']});")


def compile_matlab_figure_script(spec: dict[str, Any], script_path: str | Path) -> dict[str, Any]:
    """Write one deterministic MATLAB script; execution remains separately authorised."""

    figure = validate_figure_spec(spec)
    target = Path(script_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    width, height = SIZE_PROFILES[figure["size_profile"]]
    figure_type = figure["figure_type"]
    lines = [
        f"% Generated from {FIGURE_SPEC_VERSION}; do not hand-edit values.",
        f"% Publication style: {PUBLICATION_STYLE_PROFILE}",
        "set(groot,'defaultFigureColor','w');",
        f"fig = figure('Color','w','Units','centimeters','Position',[2 2 {width:g} {height:g}]);",
        "ax = axes(fig,'Color','w','XColor','k','YColor','k','ZColor','k'); hold(ax,'on'); box(ax,'on');",
        "set(ax,'FontName','Microsoft YaHei','FontSize',10,'LineWidth',0.9,'GridAlpha',0.16,'GridColor',[0.55 0.55 0.55]);",
        "colororder(ax,[0.0000 0.4470 0.7410; 0.8500 0.3250 0.0980; 0.4660 0.6740 0.1880; 0.4940 0.1840 0.5560]);",
    ]

    if "publication_layout" in figure:
        from pipeline_core.paper_quality.matlab_publication_layout import compile_layout
        lines.extend(compile_layout(figure))
    elif figure_type in {"line", "multi_line"}:
        lines.append("grid(ax,'on');")
        line_style = "'-o'" if figure["show_marker"] else "'-'"
        for item in figure["series"]:
            marker_options = f",'MarkerSize',{figure['marker_size']:g},'MarkerFaceColor','w'" if figure["show_marker"] else ""
            lines.append(
                f"plot(ax,{_matlab_vector(item['x'])},{_matlab_vector(item['y'])},{line_style},'DisplayName',{_quote(item['name'])},'LineWidth',1.8{marker_options});"
            )
        _append_labels(lines, figure)
        _append_legend(lines, figure, len(figure["series"]))
    elif figure_type == "scatter":
        lines.append("grid(ax,'on');")
        for item in figure["series"]:
            lines.append(
                f"scatter(ax,{_matlab_vector(item['x'])},{_matlab_vector(item['y'])},{figure['marker_size']:g},'filled','DisplayName',{_quote(item['name'])});"
            )
        _append_labels(lines, figure)
        _append_legend(lines, figure, len(figure["series"]))
    elif figure_type == "bar":
        lines.append(f"bar(ax,categorical({_matlab_cellstr(figure['categories'])}),{_matlab_vector(figure['values'])},'DisplayName',{_quote(figure['series_name'])});")
        lines.append("grid(ax,'off');")
        _append_labels(lines, figure)
        _append_legend(lines, figure, 1)
    elif figure_type == "grouped_bar":
        matrix = [[item["values"][row] for item in figure["series"]] for row in range(len(figure["categories"]))]
        lines.append(f"barHandles = bar(ax,categorical({_matlab_cellstr(figure['categories'])}),{_matlab_matrix(matrix)});")
        for index, item in enumerate(figure["series"], 1):
            lines.append(f"set(barHandles({index}),'DisplayName',{_quote(item['name'])});")
        lines.append("grid(ax,'off');")
        _append_labels(lines, figure)
        _append_legend(lines, figure, len(figure["series"]))
    elif figure_type == "error_bar":
        if "error" in figure:
            lines.append(f"errorbar(ax,{_matlab_vector(figure['x'])},{_matlab_vector(figure['y'])},{_matlab_vector(figure['error'])},'o','LineStyle','none','LineWidth',1.2);")
        else:
            lines.append(f"errorbar(ax,{_matlab_vector(figure['x'])},{_matlab_vector(figure['y'])},{_matlab_vector(figure['lower_error'])},{_matlab_vector(figure['upper_error'])},'o','LineStyle','none','LineWidth',1.2);")
        if "categories" in figure:
            lines.append(f"set(ax,'XTick',1:{len(figure['categories'])},'XTickLabel',{_matlab_cellstr(figure['categories'])});")
        lines.append("grid(ax,'off');")
        _append_labels(lines, figure)
    elif figure_type == "uncertainty_band":
        color = _color_literal(PUBLICATION_COLORS[0])
        x = _matlab_vector(figure["x"])
        upper = _matlab_vector(figure["upper"])
        lower = _matlab_vector(figure["lower"])
        lines.extend(
            [
                f"fill(ax,[{x[1:-1]} fliplr({x})],[{upper[1:-1]} fliplr({lower})],{color},'FaceAlpha',0.20,'EdgeColor','none','HandleVisibility','off');",
                f"plot(ax,{x},{_matlab_vector(figure['center'])},'-','LineWidth',1.8,'Color',{color});",
                "grid(ax,'on');",
            ]
        )
        _append_labels(lines, figure)
    elif figure_type in {"heatmap", "contour", "filled_contour"}:
        lines.append(f"xValues = {_matlab_vector(figure['x'])}; yValues = {_matlab_vector(figure['y'])}; zValues = {_matlab_matrix(figure['z'])};")
        if figure_type == "heatmap":
            lines.extend(["imagesc(ax,xValues,yValues,zValues);", "set(ax,'YDir','normal');"])
        elif figure_type == "contour":
            lines.append("contour(ax,xValues,yValues,zValues,12,'LineWidth',1.0);")
        else:
            lines.append("contourf(ax,xValues,yValues,zValues,12,'LineStyle','none');")
        lines.append("grid(ax,'off');")
        _append_colorbar(lines, figure)
        _append_labels(lines, figure)
    elif figure_type == "surface_3d":
        lines.extend(
            [
                f"xValues = {_matlab_vector(figure['x'])}; yValues = {_matlab_vector(figure['y'])}; zValues = {_matlab_matrix(figure['z'])};",
                "[X,Y] = meshgrid(xValues,yValues);",
                "surf(ax,X,Y,zValues,'EdgeColor','none');",
                "shading(ax,'flat');",
                "view(ax,[-35 25]);",
            ]
        )
        _append_colorbar(lines, figure)
        _append_labels(lines, figure, z_label=True)
    elif figure_type == "trajectory_2d":
        lines.extend(
            [
                f"plot(ax,{_matlab_vector(figure['x'])},{_matlab_vector(figure['y'])},'-','LineWidth',1.8);",
                f"scatter(ax,{_matlab_vector([figure['x'][0], figure['x'][-1]])},{_matlab_vector([figure['y'][0], figure['y'][-1]])},{figure['marker_size']:g},'filled');",
                "grid(ax,'on');",
            ]
        )
        _append_labels(lines, figure)
    elif figure_type == "trajectory_3d":
        lines.extend(
            [
                f"plot3(ax,{_matlab_vector(figure['x'])},{_matlab_vector(figure['y'])},{_matlab_vector(figure['z'])},'-','LineWidth',1.8);",
                f"scatter3(ax,{_matlab_vector([figure['x'][0], figure['x'][-1]])},{_matlab_vector([figure['y'][0], figure['y'][-1]])},{_matlab_vector([figure['z'][0], figure['z'][-1]])},{figure['marker_size']:g},'filled');",
                "grid(ax,'on');",
                "view(ax,[-35 25]);",
            ]
        )
        _append_labels(lines, figure, z_label=True)
    elif figure_type == "geometric_schematic":
        panel_count = len(figure["panels"])
        columns = 1 if panel_count == 1 else 2
        rows = math.ceil(panel_count / columns)
        lines.extend(
            [
                "delete(ax);",
                f"layout = tiledlayout(fig,{rows},{columns},'TileSpacing','compact','Padding','compact');",
            ]
        )
        marker_styles = {
            "anchor": ("s", "k", "k"),
            "receiver": ("d", "[0.0000 0.4470 0.7410]", "w"),
            "unknown": ("o", "[0.8500 0.3250 0.0980]", "w"),
            "target": ("p", "[0.4660 0.6740 0.1880]", "[0.4660 0.6740 0.1880]"),
            "generic": ("o", "k", "w"),
        }
        for panel_index, panel in enumerate(figure["panels"], 1):
            coordinates = {
                item["object_id"]: (item["x"], item["y"])
                for item in panel["objects"]
            }
            lines.extend(
                [
                    f"ax = nexttile(layout,{panel_index}); hold(ax,'on'); box(ax,'off');",
                    "set(ax,'Color','w','XColor','k','YColor','k','FontName','Microsoft YaHei','FontSize',10,'LineWidth',0.9);",
                ]
            )
            extent_x = [item["x"] for item in panel["objects"]]
            extent_y = [item["y"] for item in panel["objects"]]
            for relation_index, relation in enumerate(panel["relations"], 1):
                label = relation["label"]
                if relation["kind"] in {"segment", "dashed_segment", "arrow"}:
                    x1, y1 = coordinates[relation["from"]]
                    x2, y2 = coordinates[relation["to"]]
                    if relation["kind"] == "arrow":
                        lines.append(
                            f"quiver(ax,{x1:.15g},{y1:.15g},{x2-x1:.15g},{y2-y1:.15g},0,'Color',[0.25 0.25 0.25],'LineWidth',1.2,'MaxHeadSize',0.18);"
                        )
                    else:
                        line_style = "--" if relation["kind"] == "dashed_segment" else "-"
                        lines.append(
                            f"plot(ax,[{x1:.15g} {x2:.15g}],[{y1:.15g} {y2:.15g}],'{line_style}','Color',[0.25 0.25 0.25],'LineWidth',1.2);"
                        )
                    if label:
                        lines.append(
                            f"text(ax,{(x1+x2)/2:.15g},{(y1+y2)/2:.15g},{_quote(label)},'FontName','Microsoft YaHei','FontSize',9,'BackgroundColor','w','Margin',1,'HorizontalAlignment','center');"
                        )
                else:
                    cx, cy = coordinates[relation["center"]]
                    radius = relation["radius"]
                    if relation["kind"] == "circle":
                        start_deg, end_deg = 0.0, 360.0
                    else:
                        start_deg, end_deg = relation["start_deg"], relation["end_deg"]
                    theta_name = f"theta_{panel_index}_{relation_index}"
                    lines.extend(
                        [
                            f"{theta_name} = linspace({start_deg:.15g},{end_deg:.15g},181)*pi/180;",
                            f"plot(ax,{cx:.15g}+{radius:.15g}*cos({theta_name}),{cy:.15g}+{radius:.15g}*sin({theta_name}),'-','Color',[0.35 0.35 0.35],'LineWidth',1.1);",
                        ]
                    )
                    extent_x.extend([cx - radius, cx + radius])
                    extent_y.extend([cy - radius, cy + radius])
                    if label:
                        mid_angle = (start_deg + end_deg) / 2.0 * math.pi / 180.0
                        label_x = cx + radius * math.cos(mid_angle)
                        label_y = cy + radius * math.sin(mid_angle)
                        lines.append(
                            f"text(ax,{label_x:.15g},{label_y:.15g},{_quote(label)},'FontName','Microsoft YaHei','FontSize',9,'BackgroundColor','w','Margin',1,'HorizontalAlignment','center');"
                        )
            for item in panel["objects"]:
                marker, edge, face = marker_styles[item["marker_kind"]]
                lines.extend(
                    [
                        f"plot(ax,{item['x']:.15g},{item['y']:.15g},'{marker}','MarkerSize',7,'MarkerEdgeColor',{_quote(edge) if edge == 'k' else edge},'MarkerFaceColor',{_quote(face) if face in {'k','w'} else face},'LineWidth',1.1);",
                        f"text(ax,{item['x']+item['label_offset'][0]:.15g},{item['y']+item['label_offset'][1]:.15g},{_quote(item['label'])},'FontName','Microsoft YaHei','FontSize',9,'VerticalAlignment','bottom','HorizontalAlignment','left');",
                    ]
                )
            x_limits = panel.get("x_limits")
            y_limits = panel.get("y_limits")
            if x_limits is None:
                x_span = max(extent_x) - min(extent_x)
                x_pad = max(0.08 * x_span, 0.5)
                x_limits = [min(extent_x) - x_pad, max(extent_x) + x_pad]
            if y_limits is None:
                y_span = max(extent_y) - min(extent_y)
                y_pad = max(0.08 * y_span, 0.5)
                y_limits = [min(extent_y) - y_pad, max(extent_y) + y_pad]
            lines.extend(
                [
                    f"xlim(ax,{_matlab_vector(x_limits)}); ylim(ax,{_matlab_vector(y_limits)}); axis(ax,'equal');",
                    f"title(ax,{_quote(panel['title'])},'FontName','Microsoft YaHei','FontSize',10,'FontWeight','normal');",
                ]
            )
            if panel["show_axes"]:
                _append_labels(lines, figure)
                lines.append("grid(ax,'on');")
            else:
                lines.append("axis(ax,'off');")
    elif figure_type == "interval_timeline":
        lines.extend(
            [
                "cla(ax); hold(ax,'on'); box(ax,'on'); grid(ax,'on');",
                "set(ax,'YGrid','off','XGrid','on','GridAlpha',0.16);",
            ]
        )
        starts = [interval["start"] for lane in figure["lanes"] for interval in lane["intervals"]]
        ends = [interval["end"] for lane in figure["lanes"] for interval in lane["intervals"]]
        span = max(ends) - min(starts)
        pad = max(0.03 * span, 0.1)
        for lane_index, lane in enumerate(figure["lanes"], 1):
            y_position = len(figure["lanes"]) - lane_index + 1
            for interval_index, interval in enumerate(lane["intervals"], 1):
                color = PUBLICATION_COLORS[(lane_index + interval_index - 2) % len(PUBLICATION_COLORS)]
                color_literal = _color_literal(color)
                width_value = interval["end"] - interval["start"]
                lines.extend(
                    [
                        f"rectangle(ax,'Position',[{interval['start']:.15g} {y_position-0.3:.15g} {width_value:.15g} 0.6],'FaceColor',{color_literal},'EdgeColor',[0.25 0.25 0.25],'LineWidth',0.8);",
                        f"text(ax,{(interval['start']+interval['end'])/2:.15g},{y_position:.15g},{_quote(interval['label'])},'FontName','Microsoft YaHei','FontSize',9,'HorizontalAlignment','center','VerticalAlignment','middle');",
                    ]
                )
        lines.extend(
            [
                f"set(ax,'YTick',1:{len(figure['lanes'])},'YTickLabel',{_matlab_cellstr([lane['label'] for lane in reversed(figure['lanes'])])});",
                f"xlim(ax,{_matlab_vector([min(starts)-pad, max(ends)+pad])}); ylim(ax,[0.5 {len(figure['lanes'])+0.5:.15g}]);",
            ]
        )
        _append_labels(lines, figure)

    _append_export(lines, figure)
    lines.extend(["close(fig);", ""])
    target.write_text("\n".join(lines), encoding="utf-8")
    return {
        "status": "READY_FOR_MATLAB_EXECUTION",
        "backend": FORMAL_FIGURE_BACKEND,
        "figure_id": figure["figure_id"],
        "figure_type": figure["figure_type"],
        "purpose": figure["purpose"],
        "publication_style_profile": PUBLICATION_STYLE_PROFILE,
        "script_path": str(target),
        "output_file": figure["output_file"],
        "source_binding": figure["source_binding"],
        "dpi": figure["dpi"],
        "spec_sha256": _canonical_json_sha256(figure),
        "script_sha256": _sha256_file(target),
    }


def load_figure_spec(path: str | Path) -> dict[str, Any]:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise MatlabFigureSpecError("MATLAB_FIGURE_SPEC_UNREADABLE") from exc
    return validate_figure_spec(payload)


def matlab_batch_command(script_path: str | Path) -> list[str]:
    """Return the audited non-GUI MATLAB batch command."""

    script = Path(script_path).resolve()
    if not script.is_file() or script.suffix.lower() != ".m":
        raise MatlabFigureSpecError("MATLAB_FIGURE_SCRIPT_UNREADABLE")
    escaped = script.as_posix().replace("'", "''")
    return ["matlab", "-batch", f"run('{escaped}')"]


def discover_matlab_executable(
    explicit_path: str | Path | None = None,
    *,
    environment: Mapping[str, str] | None = None,
) -> Path | None:
    """Discover MATLAB without assuming that ``matlab`` is the only command."""

    env = os.environ if environment is None else environment
    if explicit_path is not None:
        candidates = [Path(explicit_path)]
    else:
        configured = env.get("MATLAB_EXECUTABLE") or env.get("MATLAB_BIN")
        matlab_root = env.get("MATLAB_ROOT") or env.get("MATLABROOT")
        if configured:
            candidates = [Path(configured)]
        elif matlab_root:
            root = Path(matlab_root)
            candidates = [root / "bin" / "matlab.exe", root / "bin" / "matlab"]
        else:
            candidates = []
            for install_root in (Path("C:/Program Files/MATLAB"), Path("D:/Program Files/MATLAB")):
                if install_root.is_dir():
                    candidates.extend(sorted(install_root.glob("*/bin/matlab.exe")))
                    candidates.extend(sorted(install_root.glob("*/bin/matlab")))
            on_path = shutil.which("matlab")
            if on_path:
                candidates.append(Path(on_path))
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    return None


def execute_matlab_figure(
    spec: dict[str, Any],
    script_path: str | Path,
    *,
    result_acceptance_contract: Mapping[str, Any] | None = None,
    gate3_evaluation: Mapping[str, Any] | None = None,
    executable: str | Path | None = None,
    matlab_start_directory: str | Path | None = None,
    timeout_seconds: int = 120,
    environment: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Compile and execute one figure, returning a fail-closed receipt."""

    started_at = datetime.now(timezone.utc).isoformat()
    target = Path(script_path).resolve()
    receipt: dict[str, Any] = {
        "schema_version": FIGURE_EXECUTION_RECEIPT_VERSION,
        "runtime_origin": "execute_matlab_figure",
        "created_by_actual_matlab_figure_runtime": False,
        "execution_status": "NOT_STARTED",
        "generation_status": "NOT_GENERATED",
        "started_at": started_at,
        "finished_at": None,
        "executable": None,
        "command": [],
        "return_code": None,
        "stdout": "",
        "stderr": "",
        "generated_files": [],
        "asset_path": None,
        "asset_format": None,
        "asset_size_bytes": 0,
    }

    def finish(**updates: Any) -> dict[str, Any]:
        receipt.update(updates)
        receipt["finished_at"] = datetime.now(timezone.utc).isoformat()
        return receipt

    try:
        compiled = compile_matlab_figure_script(spec, target)
        figure = validate_figure_spec(spec)
    except MatlabFigureSpecError as exc:
        return finish(status="BLOCKED", code="FIGURE_SPEC_INVALID", error=str(exc))

    try:
        result_binding = _approved_result_binding(
            figure["source_binding"], result_acceptance_contract, gate3_evaluation
        )
    except MatlabFigureSpecError as exc:
        return finish(status="BLOCKED", code="FIGURE_RESULT_BINDING_INVALID", error=str(exc))

    start_directory = target.parent
    if matlab_start_directory is not None:
        start_directory = Path(matlab_start_directory).resolve()
        if not start_directory.is_dir():
            return finish(
                status="BLOCKED",
                code="FIGURE_RUNTIME_START_DIRECTORY_INVALID",
                error=str(start_directory),
            )

    receipt.update(
        {
            "script_path": compiled["script_path"],
            "figure_id": figure["figure_id"],
            "figure_type": figure["figure_type"],
            "purpose": figure["purpose"],
            "title": figure["title"],
            "x_label": figure["x_label"],
            "y_label": figure["y_label"],
            "spec_sha256": compiled["spec_sha256"],
            "script_sha256": compiled["script_sha256"],
            "result_acceptance_contract_sha256": result_binding["result_acceptance_contract_sha256"],
            "gate3_evidence_sha256": result_binding["gate3_evidence_sha256"],
            "working_directory": str(start_directory),
        }
    )

    matlab = discover_matlab_executable(executable, environment=environment)
    if matlab is None:
        return finish(status="BLOCKED", code="FIGURE_RUNTIME_UNAVAILABLE", error="MATLAB executable not found")

    command = [str(matlab)]
    if matlab_start_directory is not None:
        command.extend(["-sd", str(start_directory)])
    command.extend(matlab_batch_command(target)[1:])
    receipt.update(
        {
            "executable": str(matlab),
            "command": command,
        }
    )
    process_environment = dict(os.environ)
    if environment is not None:
        process_environment.update(environment)
    try:
        completed = subprocess.run(
            command,
            cwd=start_directory,
            env=process_environment,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        return finish(
            status="FAIL",
            code="FIGURE_GENERATION_FAILED",
            execution_status="FAILED",
            stderr=str(exc),
        )
    except OSError as exc:
        return finish(
            status="BLOCKED",
            code="FIGURE_RUNTIME_UNAVAILABLE",
            error=str(exc),
        )

    receipt.update(
        {
            "execution_status": "SUCCEEDED" if completed.returncode == 0 else "FAILED",
            "return_code": completed.returncode,
            "stdout": completed.stdout or "",
            "stderr": completed.stderr or "",
        }
    )
    if completed.returncode != 0:
        if "File system inconsistency" in (completed.stderr or ""):
            return finish(
                status="BLOCKED",
                code="FIGURE_RUNTIME_FILESYSTEM_INCONSISTENCY",
            )
        return finish(status="FAIL", code="FIGURE_GENERATION_FAILED")

    asset_path = (target.parent / figure["output_file"]).resolve()
    try:
        asset_path.relative_to(target.parent.resolve())
    except ValueError:
        return finish(status="BLOCKED", code="FIGURE_OUTPUT_ESCAPES_SCRIPT_ROOT")
    if not asset_path.is_file():
        return finish(
            status="FAIL",
            code="FIGURE_ASSET_MISSING",
            asset_path=str(asset_path),
        )
    asset_size = asset_path.stat().st_size
    if asset_size <= 0:
        return finish(
            status="FAIL",
            code="FIGURE_ASSET_MISSING",
            asset_path=str(asset_path),
        )
    try:
        visual_qa = validate_figure_asset(
            asset_path,
            asset_format=asset_path.suffix.lower().lstrip("."),
            minimum_dpi=FIGURE_MIN_DPI,
        )
    except FigureAssetQualityError as exc:
        return finish(
            status="FAIL",
            code="FIGURE_VISUAL_QA_FAILED",
            error=str(exc),
            asset_path=str(asset_path),
            asset_size_bytes=asset_size,
            visual_qa={"status": "FAIL", "code": str(exc)},
        )
    finished_at = datetime.now(timezone.utc).isoformat()
    source_binding = {**figure["source_binding"], "approved": True}
    asset_binding = {
        "asset_sha256": _sha256_file(asset_path),
        "generated_at": finished_at,
        "generator_version": FIGURE_SPEC_VERSION,
        "spec_sha256": compiled["spec_sha256"],
        "script_sha256": compiled["script_sha256"],
    }
    receipt.update({"finished_at": finished_at})
    return finish(
        status="PASS",
        execution_status="SUCCEEDED",
        generation_status="GENERATED",
        generated_files=[str(asset_path)],
        asset_path=str(asset_path),
        asset_format=asset_path.suffix.lower().lstrip("."),
        asset_size_bytes=asset_size,
        visual_qa=visual_qa,
        created_by_actual_matlab_figure_runtime=True,
        source_binding=source_binding,
        result_binding=result_binding,
        asset_binding=asset_binding,
    )


def generate_declared_matlab_figures(
    writer_manifest: str | Path,
    case_root: str | Path,
    *,
    timeout_seconds: int = 120,
    executable: str | Path | None = None,
    matlab_start_directory: str | Path | None = None,
    derived_manifest: str | Path | None = None,
) -> dict[str, Any]:
    """Generate all declared formal figures and emit a derived writer manifest.

    The source writer manifest is never mutated.  Scripts, receipts and assets
    are restricted to ``<case>/output/figures`` so Track C can consume them as
    case-local, hash-bound artifacts.
    """

    from pipeline_core.python_tools.human_gate import evaluate_human_gate

    root = Path(case_root).resolve()
    manifest_path = Path(writer_manifest).resolve()
    try:
        manifest_path.relative_to(root)
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (ValueError, OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise MatlabFigureSpecError("MATLAB_FIGURE_WRITER_MANIFEST_INVALID") from exc
    if not isinstance(payload, dict):
        raise MatlabFigureSpecError("MATLAB_FIGURE_WRITER_MANIFEST_INVALID")
    source_root = (manifest_path.parent / str(payload.get("content_root", "."))).resolve()
    try:
        source_root.relative_to(root)
    except ValueError as exc:
        raise MatlabFigureSpecError("MATLAB_FIGURE_CONTENT_ROOT_ESCAPES_CASE") from exc
    policy = payload.get("writing_policy")
    placement = policy.get("figure_placement") if isinstance(policy, dict) else None
    raw_figures = placement.get("figures", []) if isinstance(placement, dict) else []
    if not raw_figures:
        return {
            "status": "NOT_REQUIRED",
            "writer_manifest": str(manifest_path),
            "figure_receipts": [],
            "generated_assets": [],
            "generated_scripts": [],
        }
    if not isinstance(raw_figures, list) or placement.get("formal_figure_backend") != FORMAL_FIGURE_BACKEND:
        raise MatlabFigureSpecError("FORMAL_FIGURE_BACKEND_MUST_BE_MATLAB")
    if any(not isinstance(raw, dict) for raw in raw_figures):
        raise MatlabFigureSpecError("MATLAB_FIGURE_DECLARATION_INVALID")
    if not any(raw.get("formal", True) is True for raw in raw_figures):
        return {
            "status": "NOT_REQUIRED",
            "writer_manifest": str(manifest_path),
            "figure_receipts": [],
            "generated_assets": [],
            "generated_scripts": [],
        }
    result_contract = policy.get("result_acceptance_contract") if isinstance(policy, dict) else None
    gate3 = bind_gate_evidence(root, evaluate_human_gate(root, "GATE3"))
    if gate3.get("gate_status") != "PASS":
        raise MatlabFigureSpecError("MATLAB_FIGURE_GATE3_NOT_APPROVED")

    figure_root = (root / "output" / "figures").resolve()
    figure_root.mkdir(parents=True, exist_ok=True)
    receipts_root = figure_root / "receipts"
    receipts_root.mkdir(parents=True, exist_ok=True)
    derived_payload = json.loads(json.dumps(payload, ensure_ascii=False))
    derived_figures = derived_payload["writing_policy"]["figure_placement"]["figures"]
    receipt_paths: list[str] = []
    asset_paths: list[str] = []
    script_paths: list[str] = []
    generated_figure_ids: list[str] = []
    seen_ids: set[str] = set()
    for index, raw in enumerate(raw_figures):
        if not isinstance(raw, dict) or raw.get("formal", True) is not True:
            continue
        figure_id = _text(raw.get("figure_id"), f"figure_placement.figures[{index}].figure_id")
        if FIGURE_ID_PATTERN.fullmatch(figure_id) is None or figure_id in seen_ids:
            raise MatlabFigureSpecError("MATLAB_FIGURE_ID_INVALID_OR_DUPLICATED")
        seen_ids.add(figure_id)
        spec = raw.get("matlab_figure_spec")
        if not isinstance(spec, dict):
            raise MatlabFigureSpecError(f"MATLAB_FIGURE_SPEC_REQUIRED: {figure_id}")
        normalized = validate_figure_spec(spec)
        if normalized["figure_id"] != figure_id:
            raise MatlabFigureSpecError(f"MATLAB_FIGURE_ID_MISMATCH: {figure_id}")
        script_raw = raw.get("matlab_script_path")
        if not isinstance(script_raw, str) or not script_raw.strip():
            raise MatlabFigureSpecError(f"MATLAB_FIGURE_SCRIPT_REQUIRED: {figure_id}")
        script_path = (root / script_raw).resolve()
        try:
            script_path.relative_to(figure_root)
        except ValueError as exc:
            raise MatlabFigureSpecError(f"MATLAB_FIGURE_SCRIPT_OUTSIDE_OUTPUT: {figure_id}") from exc
        if script_path.suffix.lower() != ".m":
            raise MatlabFigureSpecError(f"MATLAB_FIGURE_SCRIPT_EXTENSION_INVALID: {figure_id}")
        predicted_asset = (script_path.parent / normalized["output_file"]).resolve()
        try:
            predicted_asset.relative_to(figure_root)
        except ValueError as exc:
            raise MatlabFigureSpecError(f"MATLAB_FIGURE_OUTPUT_OUTSIDE_CASE: {figure_id}") from exc
        receipt_raw = raw.get("execution_receipt") or f"output/figures/receipts/{figure_id}.json"
        if not isinstance(receipt_raw, str) or not receipt_raw.strip():
            raise MatlabFigureSpecError(f"MATLAB_FIGURE_RECEIPT_PATH_INVALID: {figure_id}")
        receipt_path = (root / receipt_raw).resolve()
        try:
            receipt_path.relative_to(receipts_root)
        except ValueError as exc:
            raise MatlabFigureSpecError(f"MATLAB_FIGURE_RECEIPT_OUTSIDE_OUTPUT: {figure_id}") from exc
        with tempfile.TemporaryDirectory(prefix=f".{figure_id}_stage_", dir=figure_root) as stage_directory:
            staged_script = Path(stage_directory) / script_path.name
            receipt = execute_matlab_figure(
                spec,
                staged_script,
                result_acceptance_contract=result_contract,
                gate3_evaluation=gate3,
                executable=executable,
                matlab_start_directory=matlab_start_directory,
                timeout_seconds=timeout_seconds,
            )
            if receipt.get("status") != "PASS":
                raise MatlabFigureSpecError(str(receipt.get("code") or "FIGURE_GENERATION_FAILED"))
            staged_asset = Path(str(receipt["asset_path"])).resolve()
            script_path.parent.mkdir(parents=True, exist_ok=True)
            predicted_asset.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(staged_script, script_path)
            shutil.copyfile(staged_asset, predicted_asset)
            if _sha256_file(script_path) != receipt["script_sha256"]:
                raise MatlabFigureSpecError(f"MATLAB_FIGURE_SCRIPT_PROMOTION_HASH_MISMATCH: {figure_id}")
            if _sha256_file(predicted_asset) != receipt["asset_binding"]["asset_sha256"]:
                raise MatlabFigureSpecError(f"MATLAB_FIGURE_ASSET_PROMOTION_HASH_MISMATCH: {figure_id}")
            receipt["script_path"] = str(script_path)
            receipt["asset_path"] = str(predicted_asset)
            receipt["generated_files"] = [str(predicted_asset)]
        receipt_path.parent.mkdir(parents=True, exist_ok=True)
        receipt_path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        receipt_relative = receipt_path.relative_to(root).as_posix()
        receipt_paths.append(receipt_relative)
        script_paths.append(script_path.relative_to(root).as_posix())
        asset_path = Path(str(receipt["asset_path"])).resolve()
        try:
            asset_relative = asset_path.relative_to(root).as_posix()
        except ValueError as exc:
            raise MatlabFigureSpecError(f"MATLAB_FIGURE_ASSET_ESCAPES_CASE: {figure_id}") from exc
        derived_raw = derived_figures[index]
        derived_raw["execution_receipt"] = receipt_relative
        derived_raw["asset_path"] = asset_relative
        derived_raw["source_binding"] = receipt["source_binding"]
        asset_paths.append(asset_relative)
        generated_figure_ids.append(figure_id)

    derived_path = Path(derived_manifest).resolve() if derived_manifest is not None else manifest_path.with_name("writer_manifest_with_figures.json")
    try:
        derived_path.relative_to(root)
    except ValueError as exc:
        raise MatlabFigureSpecError("MATLAB_FIGURE_DERIVED_MANIFEST_ESCAPES_CASE") from exc
    derived_path.parent.mkdir(parents=True, exist_ok=True)
    derived_path.write_text(json.dumps(derived_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {
        "status": "PASS",
        "writer_manifest": str(derived_path),
        "source_writer_manifest": str(manifest_path),
        "figure_receipts": receipt_paths,
        "generated_assets": asset_paths,
        "generated_scripts": script_paths,
        "figure_ids": generated_figure_ids,
        "gate3_evidence_sha256": gate3["evidence_sha256"],
        "result_acceptance_contract_sha256": canonical_record_sha256(result_contract),
    }
