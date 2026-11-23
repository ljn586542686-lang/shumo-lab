"""Load MathModeling2026 case configuration."""

from __future__ import annotations

import json
from pathlib import Path


def load_case_config(case_dir: str | Path) -> dict:
    path = Path(case_dir) / "case_config.json"
    if not path.exists():
        raise FileNotFoundError(f"Missing case_config.json: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def get_active_case(root: str | Path) -> Path:
    root = Path(root)
    candidates = sorted((root / "cases").glob("case_*/case_config.json"))
    active = []
    for config_path in candidates:
        try:
            config = json.loads(config_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        if config.get("active_case") is True:
            active.append(config_path.parent)
    if len(active) != 1:
        raise RuntimeError(f"Expected exactly one active case, found {len(active)}.")
    return active[0]
