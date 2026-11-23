"""Public regression inputs for the extracted storage components."""
import sys
from datetime import date, timedelta
from pathlib import Path

CODE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CODE))
from corrected_forecast import corrected_load_forecast
from q3_model_solver import apply_future_plan_update
from q3_daily import _validate_daily_ledger_record
import pytest


def test_prediction_sees_prior_days_only():
    start = date(2025, 1, 1)
    history = {start + timedelta(days=n): (10.0,) * 144 for n in range(45)}
    target = start + timedelta(days=45)
    clean, sources = corrected_load_forecast(target, history, 3)
    future = {**history, target + timedelta(days=1): (1e6,) * 144}
    assert corrected_load_forecast(target, future, 3) == (clean, sources)
    assert len(sources) == 3 and max(sources) < target and clean == (10.0,) * 144


def test_rolling_update_keeps_past_and_rejects_incomplete_ledger():
    result = apply_future_plan_update([1] * 144, [2] * 108, release_hour=6)
    assert result == (1,) * 36 + (2,) * 108
    with pytest.raises((ValueError, RuntimeError, KeyError)):
        _validate_daily_ledger_record({})


def test_q4_resume_order_and_missing_authorization(tmp_path):
    from q4_scenarios import aggregate, read_resume, run_persisted
    ledger = tmp_path / "days.jsonl"
    assert read_resume(ledger) == []
    with pytest.raises(ValueError, match="Q4_ANNUAL_PERIOD_INCOMPLETE"):
        aggregate("Q4-2", [])
    ledger.write_text('{"date":"2025-02-02"}\n', encoding="utf8")
    with pytest.raises(RuntimeError, match="Q4_RESUME_PREFIX_INVALID"):
        read_resume(ledger)
    with pytest.raises(RuntimeError, match="Q4_STAGE_AUTHORIZATION_REQUIRED"):
        run_persisted(ledger, "Q4-2", None, {}, initial_soc=6000, predictor=None,
                      authorization_path=tmp_path / "missing.json")
