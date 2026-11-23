"""Synthetic calibration exercise; the TeX output is a teaching draft."""
import csv
import hashlib
import json
import math
from pathlib import Path

from math_core import run_model
from pipeline_core.model_route.model_route_stage import validate_model_route_outputs
from pipeline_core.paper_template.paper_renderer import render_paper
from pipeline_core.python_tools.human_gate import evaluate_human_gate
from pipeline_core.python_tools.result_summary_validator import validate_result_summary


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path, payload):
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf8")


def inputs(case):
    with (case / "data/raw/spring.csv").open(encoding="utf8", newline="") as stream:
        rows = list(csv.DictReader(stream))
    train = [(float(r["mm"]), float(r["N"])) for r in rows if r["role"] == "fit"]
    check = [(float(r["mm"]), float(r["N"])) for r in rows if r["role"] == "check"]
    if len(train) < 3 or len(set(x for x,y in train)) < 2 or len(check) != 1 or len(rows) != len(train)+1:
        raise SystemExit("训练或留出数据缺失")
    if not all(math.isfinite(v) for pair in train+check for v in pair):
        raise SystemExit("输入不是有限数")
    return train, check[0]


def solve(case):
    route = validate_model_route_outputs(case)
    if route["status"] != "READY_FOR_MODEL_IMPLEMENTATION":
        raise SystemExit("模型选择尚未确认")
    selected = route["resolved_selected_route"]
    handoff = case / "handoff/MODEL_BUILDING_HANDOFF.md"
    if f"`selected_model_route`: `{selected}`" not in handoff.read_text(encoding="utf8"):
        raise SystemExit("批准路线与建模交接不一致")
    train, _ = inputs(case)
    if selected.startswith("R1"):
        run = run_model({"model_type":"statistics.linear_regression","X":[[x] for x,y in train],
                         "y":[y for x,y in train]}, run_id="synthetic_spring")
        if not run["success"]:
            raise SystemExit(str(run["failure_reason"]))
        k, = run["primary_results"]["beta"]
        b = run["primary_results"]["intercept"]
    elif selected.startswith("R2"):
        k = sum(x*y for x,y in train) / sum(x*x for x,y in train)
        b = 0.0
    else:
        raise SystemExit("此教学示例只实现工作表中的两种路线")
    result = {"case_id":case.name, "success":True, "status":"ok","selected_route":selected,
              "k_N_per_mm":k,"b_N":b,"data_sha256":digest(case/"data/raw/spring.csv"),
              "handoff_sha256":digest(handoff)}
    write_json(case/"output/result_summary.json",result)
    (case/"output/result_summary.txt").write_text(f"{selected}\nk={k:.8g} N/mm, b={b:.8g} N\n",encoding="utf8")


def verify(case):
    if validate_result_summary(case)["status"] != "ok":
        raise SystemExit("结果文件不全")
    r=json.loads((case/"output/result_summary.json").read_text(encoding="utf8"))
    if r["data_sha256"] != digest(case/"data/raw/spring.csv") or r["handoff_sha256"] != digest(case/"handoff/MODEL_BUILDING_HANDOFF.md"):
        raise SystemExit("数据或交接文件已经改变")
    route = validate_model_route_outputs(case)
    if route["status"] != "READY_FOR_MODEL_IMPLEMENTATION" or route["resolved_selected_route"] != r["selected_route"]:
        raise SystemExit("求解结果与当前人工选择不一致")
    train,(x,ref)=inputs(case)
    pred=r["k_N_per_mm"]*x+r["b_N"]
    # Independent closed-form check for this one-dimensional synthetic problem.
    mx=sum(v for v,_ in train)/len(train) if r["selected_route"].startswith("R1") else 0.0
    my=sum(v for _,v in train)/len(train) if r["selected_route"].startswith("R1") else 0.0
    k=sum((a-mx)*(b-my) for a,b in train)/sum((a-mx)**2 for a,b in train)
    independent=my+k*(x-mx)
    if not (abs(pred-ref)<=1e-9 and abs(pred-independent)<=1e-9):
        raise SystemExit("留出或独立计算复核失败")
    return f"{x:g} mm 的预测力为 {pred:.8g} N，留出参考为 {ref:g} N，绝对误差为 {abs(pred-ref):.3g} N。"


def write_evidence(case):
    from matplotlib.figure import Figure

    checked = verify(case)
    train, (x, reference) = inputs(case)
    result = json.loads((case / "output/result_summary.json").read_text(encoding="utf8"))
    k, b = result["k_N_per_mm"], result["b_N"]
    table = case / "output/tables/calibration.csv"
    table.parent.mkdir(exist_ok=True)
    with table.open("w", newline="", encoding="utf8") as stream:
        writer = csv.writer(stream)
        writer.writerow(["role", "mm", "reference_N", "predicted_N", "error_N"])
        for role, (distance, force) in [*(("fit", row) for row in train), ("holdout", (x, reference))]:
            pred = k * distance + b
            writer.writerow([role, distance, force, pred, pred - force])
    fig = Figure(figsize=(5, 3.5), layout="constrained")
    ax = fig.subplots()
    ax.scatter(*zip(*train), label="Training readings")
    ax.scatter([x], [reference], marker="s", facecolors="none", edgecolors="black", label="Holdout reading")
    domain = sorted([a for a, _ in train] + [x])
    ax.plot(domain, [k*a+b for a in domain], label="Fitted line")
    ax.set(xlabel="Displacement (mm)", ylabel="Force (N)", title="Synthetic spring calibration")
    ax.legend()
    chart = case / "output/figures/calibration.png"
    chart.parent.mkdir(exist_ok=True)
    fig.savefig(chart)
    (case / "evidence_map.md").write_text(
        "# 标定证据\n" + checked + "\n\n" +
        "\n".join(f"- `{p.relative_to(case).as_posix()}` SHA-256 `{digest(p)}`" for p in
                  (case / "data/raw/spring.csv", case / "output/result_summary.json", table, chart)) +
        "\n\n局限：仅一个留出点，尚无重复测量或刚度对温度的灵敏度证据。\n", encoding="utf8")
    return checked


def render_training_draft(case):
    review=evaluate_human_gate(case,"GATE3")
    if review["gate_status"] != "PASS":
        raise SystemExit("等待复核：GATE3")
    line=verify(case)
    evidence = case / "evidence_map.md"
    recorded = evidence.read_text(encoding="utf8") if evidence.is_file() else ""
    for relative in ("data/raw/spring.csv", "output/result_summary.json", "output/tables/calibration.csv", "output/figures/calibration.png"):
        artifact = case / relative
        if not artifact.is_file() or f"`{relative}` SHA-256 `{digest(artifact)}`" not in recorded:
            raise SystemExit("证据缺失或内容已变更：" + relative)
    work=case/"learning_draft"
    content=work/"content"
    content.mkdir(parents=True,exist_ok=True)
    values={"title":"弹簧标定教学记录","authors":"合成示例","abstract":line,
            "keywords":"标定；留出验证","problem_restatement":"用三个力与位移读数拟合，预测第四个点。",
            "model_assumptions":"测量区间内力与位移呈线性关系。","notation":"位移单位为 mm，力单位为 N。",
            "references":"本练习仅使用合成观测。","appendix":"输入见案例中的标定表。"}
    files={}
    for name,value in values.items():
        files[name]=name+".tex"
        (content/files[name]).write_text(value+"\n",encoding="utf8")
    r=json.loads((case/"output/result_summary.json").read_text(encoding="utf8"))
    for name,obj in {
        "question_content_q1":{"intro":"按人工确认的标定路线计算。","sections":[{"title":"标定及检查",
            "content":f"采用{r['selected_route']}。拟合斜率为 {r['k_N_per_mm']:.6g} N/mm。"+line}],"closing_optional":""},
        "model_evaluation":{"strengths":"留出点独立于拟合。","weaknesses":"仅检查一个外推点。","improvements":"增加零点与不同位移的实测读数。"}
    }.items():
        files[name]=name+".json";write_json(content/files[name],obj)
    manifest=work/"manifest.json"
    write_json(manifest,{"manifest_mode":"LEGACY_COMPATIBILITY","question_count":1,"content_root":"content","content":files})
    render_paper(manifest,work/"tex")
    print("教学稿", (work/"tex/main.tex").relative_to(case))
