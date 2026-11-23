"""Synthetic decisions exist only in the temporary directory owned by pytest."""
import json
import shutil
import pytest
from examples import spring_workshop as workshop
from pipeline_core.tests.test_human_gate_decision_semantics import GATE_FILES, write_gate_case


@pytest.mark.parametrize("selected", ["R1：带截距直线", "R2：过原点直线"])
def test_supervised_calibration(tmp_path, monkeypatch, selected):
    root = tmp_path / "public"
    shutil.copytree(workshop.ROOT / "cases/case_template", root / "cases/case_template")
    case = root / "cases/synthetic_spring"
    monkeypatch.setattr(workshop, "ROOT", root)
    monkeypatch.setattr(workshop, "CASE", case)
    for gate in (1, 2, 3):
        with pytest.raises(SystemExit, match="等待复核"):
            workshop.main()
        assert not (case / "learning_draft/tex/main.tex").exists()
        fixture = write_gate_case(tmp_path / f"fixture{gate}", gate, 0)
        decision = (fixture / "human_review" / GATE_FILES[gate]).read_text(encoding="utf8")
        if gate == 2:
            decision = decision.replace("选择路线：", "选择路线：" + selected)
            decision = decision.replace("选择理由：", "选择理由：测试用合成决策。")
            decision += "\n## 批准后的正式建模/求解前强制 fail-closed 条件\n- 保留独立检验点。\n"
        (case / "human_review" / GATE_FILES[gate]).write_text(decision, encoding="utf8")

    result = case / "output/result_summary.json"
    good = result.read_bytes()
    result.unlink()
    with pytest.raises(SystemExit):
        workshop.main()
    assert not (case / "learning_draft/tex/main.tex").exists()
    result.write_bytes(good)
    workshop.main()
    assert (case / "learning_draft/tex/main.tex").is_file()
    assert (case / "output/figures/calibration.png").is_file()
    assert (case / "output/tables/calibration.csv").read_text().splitlines()[-1].startswith("holdout,4.0,8.0,")
    assert json.loads((case / "case_config.json").read_text(encoding="utf8"))["current_stage"] == "RESULT_ACCEPTANCE"
    assert not (case / "final_submit").exists()
