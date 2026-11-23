"""Initialize a new case from cases/case_template."""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

if __package__:
    from .case_paths import parse_case_relative_path, resolve_case_relative_path
else:  # Also support running this file directly.
    from case_paths import parse_case_relative_path, resolve_case_relative_path


def initialize_case(root: Path, case_id: str) -> Path:
    cases_dir = root / "cases"
    template = cases_dir / "case_template"
    relative = parse_case_relative_path(case_id)
    if relative is None or not relative.parts:
        raise ValueError("case_id must name a directory within cases/")
    resolved = resolve_case_relative_path(cases_dir, case_id)
    if resolved is None or not cases_dir.resolve().is_relative_to(root.resolve()):
        raise ValueError("case_id must name a directory within cases/")
    target = cases_dir / relative
    if not template.is_dir():
        raise FileNotFoundError("cases/case_template does not exist.")
    if not template.resolve().is_relative_to(cases_dir.resolve()):
        raise ValueError("The case template must be inside cases/")
    if target.exists() or target.is_symlink():
        raise FileExistsError(f"Target case already exists: {target}")
    if resolved.is_relative_to(template.resolve()):
        raise ValueError("A new case cannot be created inside cases/case_template")
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
