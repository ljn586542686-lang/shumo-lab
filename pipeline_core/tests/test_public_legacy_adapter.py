from math_core import get_model_spec
from math_core.competition_2023d_q3 import CASE_ID, build_q3_execution_request


def test_missing_private_inputs_block_adapter(tmp_path):
    assert get_model_spec("competition.2023d_q3")["model_id"] == "competition.2023d_q3"
    case = tmp_path / CASE_ID
    case.mkdir()
    check = build_q3_execution_request({"execution_mode": "stochastic_q3"}, case)
    assert check["adapter_status"] == "BLOCKED"
    assert any(r["field"] == "required_case_artifacts" for r in check["risks"])
