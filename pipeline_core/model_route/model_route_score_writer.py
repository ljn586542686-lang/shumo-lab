"""Write an executor-authored model-route score workbook.

The helper serializes score data supplied by the caller.  It never derives
scores, weights, directions, or reasoning from ``model_routes.md``.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from math import isclose, isfinite
from numbers import Real
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape
import zipfile


SCORE_FILE_NAME = "model_route_score.xlsx"


def _xml_text(value: str) -> str:
    return escape(value)


def _xml_attribute(value: str) -> str:
    return escape(value, {'"': "&quot;"})


def _inline_string_cell(reference: str, value: str) -> str:
    return (
        f'<c r="{_xml_attribute(reference)}" t="inlineStr">'
        f'<is><t xml:space="preserve">{_xml_text(value)}</t></is></c>'
    )


def _number_text(value: Real) -> str:
    return format(float(value), ".15g")


def _number_cell(reference: str, value: Real, formula: str | None = None) -> str:
    formula_xml = f"<f>{_xml_text(formula)}</f>" if formula is not None else ""
    return (
        f'<c r="{_xml_attribute(reference)}">'
        f"{formula_xml}<v>{_number_text(value)}</v></c>"
    )


def _worksheet_xml(rows: Sequence[Sequence[str]]) -> str:
    row_xml: list[str] = []
    for row_number, cells in enumerate(rows, start=1):
        row_xml.append(f'<row r="{row_number}">{"".join(cells)}</row>')
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:xml="http://www.w3.org/XML/1998/namespace">'
        f'<sheetData>{"".join(row_xml)}</sheetData>'
        "</worksheet>"
    )


def _fixed_zip_info(name: str) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
    info.compress_type = zipfile.ZIP_DEFLATED
    info.create_system = 0
    info.external_attr = 0
    return info


def _required_text(record: Mapping[str, Any], key: str, error: str) -> str:
    value = record.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(error)
    return value


def _validate_criteria(criteria: Sequence[Mapping[str, Any]]) -> tuple[list[dict[str, Any]], float]:
    normalized: list[dict[str, Any]] = []
    names: set[str] = set()
    total_weight = 0.0
    for criterion in criteria:
        if not isinstance(criterion, Mapping):
            raise ValueError("MODEL_ROUTE_SCORE_CRITERION_INVALID")
        name = _required_text(criterion, "name", "MODEL_ROUTE_SCORE_CRITERION_NAME_INVALID")
        if name in names:
            raise ValueError("MODEL_ROUTE_SCORE_CRITERION_DUPLICATE")
        direction = _required_text(
            criterion,
            "direction",
            "MODEL_ROUTE_SCORE_CRITERION_DIRECTION_REQUIRED",
        )
        weight = criterion.get("weight")
        if isinstance(weight, bool) or not isinstance(weight, Real):
            raise ValueError("MODEL_ROUTE_SCORE_CRITERION_WEIGHT_INVALID")
        weight_value = float(weight)
        if not isfinite(weight_value) or weight_value < 0:
            raise ValueError("MODEL_ROUTE_SCORE_CRITERION_WEIGHT_INVALID")
        names.add(name)
        total_weight += weight_value
        normalized.append({"name": name, "direction": direction, "weight": weight_value})
    if not normalized:
        raise ValueError("MODEL_ROUTE_SCORE_CRITERIA_REQUIRED")
    if not isfinite(total_weight) or total_weight <= 0:
        raise ValueError("MODEL_ROUTE_SCORE_CRITERION_WEIGHT_TOTAL_INVALID")
    return normalized, total_weight


def _validate_routes(
    routes: Sequence[Mapping[str, Any]],
    criteria: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    criterion_names = [criterion["name"] for criterion in criteria]
    criterion_name_set = set(criterion_names)
    normalized: list[dict[str, Any]] = []
    route_names: set[str] = set()
    for route in routes:
        if not isinstance(route, Mapping):
            raise ValueError("MODEL_ROUTE_SCORE_ROUTE_INVALID")
        route_name = _required_text(route, "route_name", "MODEL_ROUTE_SCORE_ROUTE_NAME_INVALID")
        if route_name in route_names:
            raise ValueError("MODEL_ROUTE_SCORE_ROUTE_NAME_DUPLICATE")
        scores = route.get("scores")
        if not isinstance(scores, Mapping):
            raise ValueError("MODEL_ROUTE_SCORE_SCORES_REQUIRED")
        score_keys = list(scores.keys())
        if any(not isinstance(key, str) for key in score_keys):
            raise ValueError("MODEL_ROUTE_SCORE_DIMENSION_INVALID")
        if set(score_keys) != criterion_name_set:
            raise ValueError("MODEL_ROUTE_SCORE_DIMENSION_MISMATCH")
        numeric_scores: dict[str, float] = {}
        for criterion_name in criterion_names:
            score = scores[criterion_name]
            if isinstance(score, bool) or not isinstance(score, Real):
                raise ValueError("MODEL_ROUTE_SCORE_VALUE_INVALID")
            score_value = float(score)
            if not isfinite(score_value):
                raise ValueError("MODEL_ROUTE_SCORE_VALUE_INVALID")
            numeric_scores[criterion_name] = score_value
        reasoning = route.get("comparison_reasoning")
        if not isinstance(reasoning, str) or not reasoning.strip():
            raise ValueError("MODEL_ROUTE_SCORE_COMPARISON_REASONING_REQUIRED")
        reported_total = route.get("reported_total")
        if reported_total is not None:
            if isinstance(reported_total, bool) or not isinstance(reported_total, Real):
                raise ValueError("MODEL_ROUTE_SCORE_REPORTED_TOTAL_INVALID")
            reported_total_value = float(reported_total)
            if not isfinite(reported_total_value):
                raise ValueError("MODEL_ROUTE_SCORE_REPORTED_TOTAL_INVALID")
            contribution_total = sum(numeric_scores.values())
            if not isclose(reported_total_value, contribution_total, rel_tol=0.0, abs_tol=1e-9):
                raise ValueError("MODEL_ROUTE_SCORE_REPORTED_TOTAL_MISMATCH")
        route_names.add(route_name)
        normalized.append(
            {
                "route_name": route_name,
                "scores": numeric_scores,
                "comparison_reasoning": reasoning,
                "reported_total": reported_total_value if reported_total is not None else None,
            }
        )
    if not normalized:
        raise ValueError("MODEL_ROUTE_RECORDS_REQUIRED")
    return normalized


def _contribution_total(route: Mapping[str, Any], criteria: Sequence[Mapping[str, Any]]) -> float:
    """Return the supplied criterion contribution points without reweighting."""
    return sum(route["scores"][criterion["name"]] for criterion in criteria)


def _column_name(index: int) -> str:
    result = ""
    while index:
        index, remainder = divmod(index - 1, 26)
        result = chr(65 + remainder) + result
    return result


def _build_route_scores_sheet(
    criteria: Sequence[Mapping[str, Any]],
    routes: Sequence[Mapping[str, Any]],
) -> str:
    headers = ["Route Name"] + [criterion["name"] for criterion in criteria]
    headers += ["Total Score", "Comparison Reasoning"]
    rows: list[list[str]] = [
        [_inline_string_cell(f"{_column_name(index)}1", value) for index, value in enumerate(headers, 1)]
    ]
    total_column = 2 + len(criteria)
    reasoning_column = total_column + 1
    last_score_column = 1 + len(criteria)
    for row_number, route in enumerate(routes, start=2):
        cells = [_inline_string_cell(f"A{row_number}", route["route_name"])]
        for index, criterion in enumerate(criteria, start=2):
            cells.append(
                _number_cell(
                    f"{_column_name(index)}{row_number}",
                    route["scores"][criterion["name"]],
                )
            )
        total_formula = f"SUM(B{row_number}:{_column_name(last_score_column)}{row_number})"
        cells.append(
            _number_cell(
                f"{_column_name(total_column)}{row_number}",
                _contribution_total(route, criteria),
                total_formula,
            )
        )
        cells.append(
            _inline_string_cell(
                f"{_column_name(reasoning_column)}{row_number}",
                route["comparison_reasoning"],
            )
        )
        rows.append(cells)
    return _worksheet_xml(rows)


def _build_scoring_rules_sheet(criteria: Sequence[Mapping[str, Any]]) -> str:
    rows: list[list[str]] = [
        [
            _inline_string_cell("A1", "Criterion"),
            _inline_string_cell("B1", "Direction"),
            _inline_string_cell("C1", "Weight"),
        ]
    ]
    for row_number, criterion in enumerate(criteria, start=2):
        rows.append(
            [
                _inline_string_cell(f"A{row_number}", criterion["name"]),
                _inline_string_cell(f"B{row_number}", criterion["direction"]),
                _number_cell(f"C{row_number}", criterion["weight"]),
            ]
        )
    return _worksheet_xml(rows)


def write_model_route_score_xlsx(
    output_path: str | Path,
    criteria: Sequence[Mapping[str, Any]],
    routes: Sequence[Mapping[str, Any]],
) -> Path:
    """Serialize explicit executor scoring data to the formal XLSX artifact.

    ``criteria`` entries require ``name``, ``direction``, and ``weight``.
    ``routes`` entries require ``route_name``, a complete ``scores`` mapping,
    and non-empty ``comparison_reasoning``.  Scores are executor-provided
    contribution points, so Total Score is the deterministic direct sum
    ``sum(score)``; criterion weights are recorded metadata and are not applied
    a second time.  An optional ``reported_total`` is checked against that sum.
    No score data is synthesized.
    """

    path = Path(output_path)
    if path.name != SCORE_FILE_NAME:
        raise ValueError("MODEL_ROUTE_SCORE_OUTPUT_FILENAME_INVALID")
    if not path.parent.is_dir():
        raise ValueError("MODEL_ROUTE_SCORE_OUTPUT_DIRECTORY_MISSING")
    if isinstance(criteria, (str, bytes)) or not isinstance(criteria, Sequence):
        raise ValueError("MODEL_ROUTE_SCORE_CRITERIA_REQUIRED")
    if isinstance(routes, (str, bytes)) or not isinstance(routes, Sequence):
        raise ValueError("MODEL_ROUTE_RECORDS_REQUIRED")

    normalized_criteria, _ = _validate_criteria(criteria)
    normalized_routes = _validate_routes(routes, normalized_criteria)
    content_types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/xl/workbook.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
        '<Override PartName="/xl/worksheets/sheet1.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        '<Override PartName="/xl/worksheets/sheet2.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        "</Types>"
    )
    root_relationships = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
        'Target="xl/workbook.xml"/>'
        "</Relationships>"
    )
    workbook = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        '<sheets><sheet name="Route Scores" sheetId="1" r:id="rId1"/>'
        '<sheet name="Scoring Rules" sheetId="2" r:id="rId2"/></sheets>'
        "</workbook>"
    )
    workbook_relationships = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
        'Target="worksheets/sheet1.xml"/>'
        '<Relationship Id="rId2" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
        'Target="worksheets/sheet2.xml"/>'
        "</Relationships>"
    )
    route_scores = _build_route_scores_sheet(
        normalized_criteria,
        normalized_routes,
    )
    scoring_rules = _build_scoring_rules_sheet(normalized_criteria)
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, content in (
            ("[Content_Types].xml", content_types),
            ("_rels/.rels", root_relationships),
            ("xl/workbook.xml", workbook),
            ("xl/_rels/workbook.xml.rels", workbook_relationships),
            ("xl/worksheets/sheet1.xml", route_scores),
            ("xl/worksheets/sheet2.xml", scoring_rules),
        ):
            archive.writestr(_fixed_zip_info(name), content.encode("utf-8"))
    return path
