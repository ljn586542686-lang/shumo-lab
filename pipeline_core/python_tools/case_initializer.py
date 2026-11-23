"""Initialize a new case from cases/case_template."""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path


def initialize_case(root: Path, case_id: str) -> Path:
    template = root / "cases" / "case_template"
    target = root / "cases" / case_id
    if not template.exists():
        raise FileNotFoundError("cases/case_template does not exist.")
    if target.exists():
        raise FileExistsError(f"Target case already exists: {target}")
    shutil.copytree(template, target)
    config_path = target / "case_config.json"
    config = json.loads(config_path.read_text(encoding="utf-8-sig"))
    config.update(
        {
            "case_id": case_id,
            "case_name": case_id,
            "status": "CASE_INTAKE",
            "current_stage": "CASE_INTAKE",
            "previous_stage": None,
            "next_allowed_stage": "PROBLEM_UNDERSTANDING",
            "state_authority": "case_config.json",
            "transition_authority": "transition_case_v1",
            "state_revision": 0,
            "current_handoff_path": "CURRENT_HANDOFF.md",
        }
    )
    config_path.write_text(
        json.dumps(config, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    handoff_path = target / "CURRENT_HANDOFF.md"
    handoff = handoff_path.read_text(encoding="utf-8-sig")
    handoff_path.write_text(
        handoff.replace("`CASE_ID`: `case_template`", f"`CASE_ID`: `{case_id}`"),
        encoding="utf-8",
        newline="\n",
    )
    return target


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("case_id")
    parser.add_argument("--root", default=".")
    args = parser.parse_args()
    target = initialize_case(Path(args.root).resolve(), args.case_id)
    print(target)


if __name__ == "__main__":
    main()
