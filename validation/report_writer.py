"""Markdown writers for X5-F2 validation reports."""

from __future__ import annotations

from typing import Any


def _table(rows: list[dict[str, Any]], columns: list[tuple[str, str]]) -> str:
    lines = [
        "|" + "|".join(label for _key, label in columns) + "|",
        "|" + "|".join("---" for _key, _label in columns) + "|",
    ]
    for row in rows:
        cells: list[str] = []
        for key, _label in columns:
            value = row.get(key, "")
            if isinstance(value, (list, tuple)):
                value = ", ".join(str(item) for item in value)
            if value is None:
                value = ""
            cells.append(str(value).replace("\n", " "))
        lines.append("|" + "|".join(cells) + "|")
    return "\n".join(lines)


def validation_report_markdown(report: dict[str, Any]) -> str:
    md = f"""# X5-F2 Model-Aware Validation Report

- run_id: `{report.get('run_id')}`
- model_id: `{report.get('model_id')}`
- model_family: `{report.get('model_family')}`
- validation_status: `{report.get('validation_status')}`
- fatal_count: `{report.get('fatal_count')}`
- warning_count: `{report.get('warning_count')}`

## Hard Assertions

{_table(report.get('hard_assertions', []), [('name', 'Assertion'), ('status', 'Status'), ('severity', 'Severity'), ('message', 'Message')])}

## Soft Warnings

{_table(report.get('soft_warnings', []), [('name', 'Warning'), ('severity', 'Severity'), ('message', 'Message')])}

## Human Review Items

{_table(report.get('human_review_items', []), [('name', 'Item'), ('severity', 'Severity'), ('message', 'Message')])}
"""
    return md


def comparison_report_markdown(report: dict[str, Any]) -> str:
    md = f"""# X5-F2 Model Comparison

- comparison_status: `{report.get('comparison_status')}`
- family_mode: `{report.get('family_mode')}`
- based_on_metric: `{report.get('based_on_metric') or 'none'}`
- best_candidate: `{report.get('best_candidate') or 'none'}`

## Candidates

{_table(report.get('candidates', []), [('candidate_id', 'Candidate'), ('model_id', 'Model'), ('model_family', 'Family'), ('success', 'Success'), ('metric_value', 'Metric')])}

## Notes

{chr(10).join('- ' + str(item) for item in report.get('notes', [])) if report.get('notes') else 'None'}
"""
    return md
