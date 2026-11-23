"""2026 C 题正式 Python 入口。

功能：验证正式 authorization，区分 synthetic/formal 模式，并把输入交给现有
Q1 builder 与 ``solver.run_lexicographic_q1``。本文件不复制模型或验证逻辑。
默认行为：没有显式 mode 和 authorization 时由 argparse fail-closed；导入不求解。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Sequence


CASE_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = next(
    (parent for parent in Path(__file__).resolve().parents if (parent / "pipeline_core").is_dir()),
    None,
)
if REPO_ROOT is None:
    raise RuntimeError("REPOSITORY_ROOT_NOT_FOUND")
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from pipeline_core.python_tools.human_gate import verify_authorized_stage_transition_for_case  # noqa: E402
from pipeline_core.python_tools.model_authority import (  # noqa: E402
    verify_model_authority,
    verify_post_acceptance_revision_reauthorization,
)

from formal_q1_provider import load_formal_q1_inputs  # noqa: E402
from q1_model_solver import build_q1_model  # noqa: E402
from result1_writer import write_result1_from_q1  # noqa: E402
from solver import LinearProgramBackend, MathCoreLinearProgramBackend, run_lexicographic_q1  # noqa: E402
from synthetic_fixtures import build_synthetic_suite  # noqa: E402


CANONICAL_ENTRYPOINT = "code/python/run_pipeline.py"
SUPPORTED_MODES = ("synthetic", "formal")
FORMAL_ATTACHMENT1 = CASE_ROOT / "data" / "raw" / "附件1.xlsx"
FORMAL_RESULT1_TEMPLATE = CASE_ROOT / "data" / "raw" / "result1.xlsx"
FORMAL_RESULT1_OUTPUT = CASE_ROOT / "output" / "tables" / "result1.xlsx"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Authorized 2026 C solver execution entrypoint")
    parser.add_argument("--mode", required=True, choices=SUPPORTED_MODES)
    parser.add_argument("--authorization", required=True, help="transition_case_v1 authorization path")
    parser.add_argument("--fixture", help="synthetic fixture name; required for synthetic mode")
    return parser


def verify_execution_authorization(authorization_path: Path) -> dict[str, object]:
    config = json.loads((CASE_ROOT / "case_config.json").read_text(encoding="utf-8-sig"))
    current_stage = str(config.get("current_stage") or "")
    requested_stage = str(config.get("next_allowed_stage") or "")
    revision_reauth = verify_post_acceptance_revision_reauthorization(CASE_ROOT, authorization_path)
    if revision_reauth.get("status") == "PASS":
        authority_check = verify_model_authority(CASE_ROOT)
        if authority_check.get("status") != "PASS":
            return {"status": "FAIL", "reason": authority_check.get("reason", "MODEL_AUTHORIZATION_INVALIDATED")}
        return {
            "status": "PASS",
            "reason": "POST_ACCEPTANCE_MODEL_REVISION_REAUTHORIZATION_VERIFIED",
            "revision_reauthorization": revision_reauth,
        }
    stage_check = verify_authorized_stage_transition_for_case(
        authorization_path,
        case_path=CASE_ROOT,
        current_stage=current_stage,
        requested_stage=requested_stage,
    )
    if stage_check.get("status") != "PASS":
        return {
            "status": "FAIL",
            "reason": stage_check.get("reason", "STAGE_AUTHORIZATION_INVALID"),
        }
    authority_check = verify_model_authority(
        CASE_ROOT,
        authorization_path=authorization_path,
        entrypoint=CANONICAL_ENTRYPOINT,
    )
    if authority_check.get("status") != "PASS":
        return {
            "status": "FAIL",
            "reason": authority_check.get("reason", "MODEL_AUTHORIZATION_INVALIDATED"),
        }
    return {"status": "PASS", "reason": "EXECUTION_AUTHORIZATION_VERIFIED"}


def _execute_synthetic(
    fixture_name: str | None,
    *,
    backend: LinearProgramBackend,
) -> dict[str, object]:
    if not fixture_name:
        return {"status": "FAIL", "reason": "SYNTHETIC_FIXTURE_REQUIRED"}
    suite = build_synthetic_suite()
    if fixture_name not in suite:
        return {"status": "FAIL", "reason": "SYNTHETIC_FIXTURE_UNKNOWN"}
    fixture = suite[fixture_name]
    if len(fixture.inputs.load_kwh) != 144 or fixture.terminal_energy_kwh != 6_000.0:
        return {"status": "FAIL", "reason": "SYNTHETIC_FIXTURE_NOT_Q1_COMPATIBLE"}
    model = build_q1_model(
        fixture.inputs.load_kwh,
        fixture.inputs.pv_available_kwh,
        fixture.inputs.price_per_kwh,
    )
    return run_lexicographic_q1(
        model,
        backend=backend,
        run_id=f"synthetic_{fixture_name.lower()}",
        case_dir=CASE_ROOT,
    )


def _execute_formal(*, backend: LinearProgramBackend) -> dict[str, object]:
    """Run the authorized Q1 formal chain; callers must validate authorization first."""

    try:
        formal = load_formal_q1_inputs(FORMAL_ATTACHMENT1)
        model = build_q1_model(
            formal.physical_inputs.load_kwh,
            formal.physical_inputs.pv_available_kwh,
            formal.physical_inputs.price_per_kwh,
        )
    except (FileNotFoundError, OSError, TypeError, ValueError) as exc:
        return {"status": "FAIL", "reason": str(exc) or "FORMAL_INPUT_PROVIDER_FAILED"}

    result = run_lexicographic_q1(
        model,
        backend=backend,
        run_id="formal_q1",
        case_dir=CASE_ROOT,
    )
    if result.get("status") != "PASS":
        return result
    try:
        write_record = write_result1_from_q1(
            FORMAL_RESULT1_TEMPLATE,
            FORMAL_RESULT1_OUTPUT,
            result,
            initial_energy_kwh=model.initial_energy_kwh,
        )
    except (FileExistsError, FileNotFoundError, OSError, TypeError, ValueError) as exc:
        failed = dict(result)
        failed.update({"status": "FAIL", "reason": str(exc) or "RESULT1_WRITE_FAILED"})
        return failed
    completed = dict(result)
    completed["formal_input"] = {
        "source_sha256": formal.source_sha256,
        "sheet_name": formal.sheet_name,
        "data_positions": formal.data_positions,
        "mapping": "POSITION_BASED",
    }
    completed["result1_output"] = write_record
    return completed


def dispatch_execution(
    *,
    mode: str,
    fixture_name: str | None,
    backend: LinearProgramBackend,
) -> dict[str, object]:
    if mode == "synthetic":
        return _execute_synthetic(fixture_name, backend=backend)
    if mode == "formal":
        return _execute_formal(backend=backend)
    return {"status": "FAIL", "reason": "EXECUTION_MODE_NOT_ALLOWED"}


def main(
    argv: Sequence[str] | None = None,
) -> int:
    args = build_parser().parse_args(argv)
    authorization = verify_execution_authorization(Path(args.authorization))
    if authorization.get("status") != "PASS":
        print(json.dumps(authorization, ensure_ascii=False))
        return 2
    result = dispatch_execution(
        mode=args.mode,
        fixture_name=args.fixture,
        backend=MathCoreLinearProgramBackend(),
    )
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result.get("status") == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
