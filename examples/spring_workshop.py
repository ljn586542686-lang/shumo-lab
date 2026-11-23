"""Local post-contest exercise using the competition's case and transition APIs."""
import json
from pathlib import Path
from examples.spring_steps import solve, verify, write_evidence, render_training_draft

from pipeline_core.python_tools.case_initializer import initialize_case
from pipeline_core.python_tools.data_audit import audit_data
from pipeline_core.python_tools.transition_case import (
    authorize_transition, write_transition_authorization, build_completion_receipt,
    write_completion_receipt, commit_transition,
)
from pipeline_core.model_route.model_route_score_writer import write_model_route_score_xlsx
from pipeline_core.python_tools.run_pipeline import run_stage

ROOT = Path(__file__).resolve().parents[1]
CASE = ROOT / "cases" / "synthetic_spring"


def require(result):
    if result.get("status") != "PASS":
        raise SystemExit("等待复核：" + str(result.get("reason") or result.get("status")))
    return result


def prepare(target, executor):
    auth = require(authorize_transition(CASE, requested_stage=target, executor=executor))
    return write_transition_authorization(CASE, auth)


def finish(auth_path, artifacts):
    receipt = require(build_completion_receipt(CASE, authorization_path=auth_path,
                      artifacts=artifacts, executor_status="PASS"))
    receipt_path = write_completion_receipt(CASE, receipt)
    require(commit_transition(CASE, authorization_path=auth_path, completion_receipt_path=receipt_path))


def main():
    if not CASE.exists():
        initialize_case(ROOT, CASE.name)
        (CASE / "data/raw/spring.csv").write_text("mm,N,role\n1,2,fit\n2,4,fit\n3,6,fit\n4,8,check\n", encoding="utf8")
        (CASE / "problem_contract.md").write_text(
            "# 合成弹簧标定\n拟合力与位移。只用 1、2、3 mm 训练，4 mm 与 8 N 单独留作检验。\n", encoding="utf8")
        (CASE / "problem_analysis.md").write_text(
            "# 问题分析\n输入为位移（mm）和力（N）。仅前三组参与拟合，目标为最小化力的残差平方和。比较带截距与过原点两种线性关系，报告斜率、截距、留出点误差。零点偏移未知，单个留出点只能检验本次外推。\n", encoding="utf8")
        (CASE / "problem").mkdir(exist_ok=True)
        (CASE / "problem/problem_contract.md").write_bytes((CASE / "problem_contract.md").read_bytes())
    stage = json.loads((CASE / "case_config.json").read_text(encoding="utf-8-sig"))["current_stage"]
    if stage == "CASE_INTAKE":
        print("数据盘点", audit_data(CASE)["status"])
        auth = prepare("PROBLEM_UNDERSTANDING", "example:intake")
        finish(auth, ["problem/problem_contract.md", "problem_analysis.md"])
        stage = "PROBLEM_UNDERSTANDING"
    if stage == "PROBLEM_UNDERSTANDING":
        auth = prepare("MODEL_ROUTE_GENERATION", "run_pipeline:model")
        routes = [("R1：带截距直线", 2, "拟合 F=kx+b，用最小二乘同时估计刚度 k 与零点偏移 b；可以检验截距，三个点对偏移的约束有限"), ("R2：过原点直线", 1, "约束 b=0，以 k=ΣxF/Σx² 最小化平方误差；少一个参数，须另证零点可信，否则刚度估计可能偏移")]
        # Prewritten discussion for this synthetic example. A human selects the route.
        text = "# 模型路线\n" + "\n".join(f"\n## 路线 {name}\n\n{reason}。\n" for name, _, reason in routes)
        scores = CASE / "model_route_score.xlsx"
        if not scores.exists():
            (CASE / "model_routes.md").write_text(text, encoding="utf8")
            write_model_route_score_xlsx(scores, [{"name":"零点偏移可辨识","direction":"higher","weight":1}],
                [{"route_name":name,"scores":{"零点偏移可辨识":points},"comparison_reasoning":reason} for name,points,reason in routes])
        report = run_stage(CASE, "model", transition_authorization_path=auth)
        print("路线执行器", report["status"])
        outputs = report.get("model_route_stage", {}).get("outputs", {})
        if outputs.get("status") not in {"AWAITING_GATE2","READY_FOR_MODEL_IMPLEMENTATION"}:
            raise SystemExit(str(report.get("reason") or report.get("error") or outputs))
        finish(auth, ["routing/problem_type_routing_result.json","model_routes.md","handoff/MODEL_ROUTE_GENERATION_HANDOFF.md"])
        stage = "MODEL_ROUTE_GENERATION"
    if stage == "MODEL_ROUTE_GENERATION":
        auth = prepare("MODEL_IMPLEMENTATION","run_pipeline:model")
        result = run_stage(CASE, "model", transition_authorization_path=auth)
        if (result.get("model_building_handoff") or {}).get("status") != "PASS":
            raise SystemExit("请检查模型路线批准记录、建模边界和知识卡正文")
        finish(auth, ["handoff/MODEL_BUILDING_HANDOFF.md"])
        stage = "MODEL_IMPLEMENTATION"
    if stage == "MODEL_IMPLEMENTATION":
        auth = prepare("SOLVER_EXECUTION", "example:fit")
        solve(CASE)
        finish(auth, ["output/result_summary.json","output/result_summary.txt"])
        stage = "SOLVER_EXECUTION"
    reports = ["output/validation/output_contract_report.md","output/validation/evidence_trace_report.md"]
    if stage == "SOLVER_EXECUTION":
        auth = prepare("RESULT_VALIDATION","example:verify")
        checked = write_evidence(CASE)
        (CASE/reports[0]).write_text("# 教学数值复核\n"+checked+"\n",encoding="utf8")
        (CASE/reports[1]).write_text("# 证据路径\n输入 data/raw/spring.csv，模型与哈希 output/result_summary.json。\n"+checked+"\n",encoding="utf8")
        finish(auth,[*reports,"output/figures/calibration.png","output/tables/calibration.csv","evidence_map.md"])
        stage="RESULT_VALIDATION"
    if stage == "RESULT_VALIDATION":
        auth = prepare("RESULT_ACCEPTANCE","example:acceptance-packet")
        print(verify(CASE))
        finish(auth,["output/result_summary.json",*reports])
        stage="RESULT_ACCEPTANCE"
    if stage == "RESULT_ACCEPTANCE":
        render_training_draft(CASE)



if __name__ == "__main__":
    main()
