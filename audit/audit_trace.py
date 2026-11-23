"""Append-only audit trace helper for X5-F4.1."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from x5_surgical_utils import rel_path, utc_now, write_json


def append_audit_trace(trace_path: Path, event: str, payload: dict[str, Any]) -> dict[str, Any]:
    entries: list[dict[str, Any]] = []
    if trace_path.exists():
        try:
            raw = json.loads(trace_path.read_text(encoding="utf-8-sig"))
            if isinstance(raw.get("entries"), list):
                entries = raw["entries"]
        except json.JSONDecodeError:
            entries = []
    entry = {
        "timestamp": utc_now(),
        "event": event,
        "payload": payload,
    }
    entries.append(entry)
    trace = {
        "schema_version": "x5_f4_1_audit_trace_v1",
        "trace_path": rel_path(trace_path),
        "entry_count": len(entries),
        "entries": entries,
    }
    trace_path.parent.mkdir(parents=True, exist_ok=True)
    write_json(trace_path, trace)
    return trace
