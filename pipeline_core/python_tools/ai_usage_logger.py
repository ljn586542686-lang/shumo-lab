"""Append AI usage disclosure entries."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def append_usage(case_dir: str | Path, entry: dict) -> Path:
    path = Path(case_dir) / "ai_disclosure" / "ai_usage_log.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        data = json.loads(path.read_text(encoding="utf-8"))
    else:
        data = {"entries": []}
    entry = dict(entry)
    entry.setdefault("timestamp_utc", datetime.now(timezone.utc).isoformat())
    data.setdefault("entries", []).append(entry)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def build_minimum_disclosure_candidate(case_dir: str | Path) -> Path:
    """Create a review-only minimum disclosure candidate from an existing log.

    The output is deliberately labelled as a candidate: a human still decides
    what must be disclosed under the competition's current rules.
    """

    root = Path(case_dir)
    log_path = root / "ai_disclosure" / "ai_usage_log.json"
    try:
        payload: dict[str, Any] = json.loads(log_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("AI_USAGE_LOG_UNREADABLE") from exc
    entries = payload.get("entries")
    if not isinstance(entries, list):
        raise ValueError("AI_USAGE_LOG_INVALID")
    rows: list[str] = []
    for index, raw in enumerate(entries, 1):
        if not isinstance(raw, dict):
            raise ValueError(f"AI_USAGE_LOG_ENTRY_INVALID: {index}")
        tool = str(raw.get("tool") or raw.get("provider") or "未注明工具").strip()
        purpose = str(raw.get("purpose") or raw.get("activity") or "未注明用途").strip()
        scope = str(raw.get("scope") or raw.get("artifact") or "未注明范围").strip()
        rows.append(f"| {index} | {tool} | {purpose} | {scope} |")
    output = root / "ai_disclosure" / "AI工具使用详情候选.md"
    body = [
        "# AI 工具使用详情（候选材料）",
        "",
        "本文件由使用日志整理，仅供人工核对与删改；不代表自动形成正式披露。",
        "",
        "| 序号 | 工具 | 用途 | 涉及范围 |",
        "|---:|---|---|---|",
        *(rows or ["| — | 暂无记录 | — | — |"]),
        "",
        "人工确认后，方可将其中内容用于正式比赛披露。",
        "",
    ]
    output.write_text("\n".join(body), encoding="utf-8")
    return output
