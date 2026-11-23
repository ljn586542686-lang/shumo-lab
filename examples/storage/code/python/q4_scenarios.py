"""Contest price scenarios. Supply the upstream state and model approval for annual runs."""
from __future__ import annotations

import json, os, sys, time
from datetime import date, datetime, timedelta
from hashlib import sha256
from math import fsum
from pathlib import Path
from types import SimpleNamespace

from openpyxl import load_workbook

CASE_ROOT = Path(__file__).resolve().parents[2]
from pipeline_core.python_tools.model_authority import verify_model_authority
from formal_q2_provider import load_formal_q2_data
from q2_data_contract import POSITIONS_PER_DAY
from q2_model_solver import build_q2_model
from q2_solver import run_lexicographic_q2

from q2_scientific_validation import validate_q2_day
from model_contract import PhysicalInputs, StorageParameters
from q2_daily import _clean_nonnegative, canonicalize_soc_for_propagation
import q3_daily as q3

START = date(2025, 2, 1)
END = date(2025, 12, 31)
DAYS = (END - START).days + 1
def load_prices(path: Path) -> dict[date, tuple[float, ...]]:
    wb = load_workbook(path, read_only=True, data_only=True)
    try:
        ws = wb[wb.sheetnames[0]]
        out = {}
        for row in ws.iter_rows(min_row=2, values_only=True):
            value = row[0]
            if isinstance(value, datetime): day = value.date()
            elif isinstance(value, date): day = value
            else: day = date.fromisoformat(str(value))
            vals = tuple(float(x) for x in row[1:145])
            if len(vals) != 144: raise RuntimeError("ATTACHMENT4_POSITION_COUNT_INVALID")
            out[day] = vals
        return out
    finally: wb.close()

class PriceData:
    def __init__(self, base, price):
        self._base = base; self.price_per_kwh = tuple(price)
    def __getattr__(self, name): return getattr(self._base, name)

def _history(data, day):
    return {d: (data.load_kw_by_day[d], data.pv_kw_by_day[d]) for d in data.dates if d < day}

def _validation(result, inputs, initial, terminal):
    s = result.get("solution")
    if s is None: raise RuntimeError("Q4_NO_ACCEPTED_PRIMAL")
    return validate_q2_day(planned_kwh=s["Q"], ordinary_used_kwh=s["G"], emergency_kwh=s["H"], charge_kwh=s["c"], discharge_kwh=s["d"], curtailed_pv_kwh=s["r"], energy_path_kwh=s["E"], unused_plan_kwh=s["S"], actual_inputs=inputs, initial_energy_kwh=initial, storage=StorageParameters.working_e_a(), future_leakage_count=0, past_decision_mutation_count=0, terminal_energy_kwh=terminal)

def run_q4_2_day(day, initial, data, predictor, price):
    info = q3.build_information_set(day, initial, _history(data, day))
    load_fc = q3.kw_to_kwh(predictor.forecast_load(day, info)); pv_fc = q3.kw_to_kwh(predictor.forecast_pv(day, info))
    actual_load = q3.kw_to_kwh(data.load_kw_by_day[day]); actual_pv = q3.kw_to_kwh(data.pv_kw_by_day[day]); terminal = 6000.0 if day == END else None
    plan_res = run_lexicographic_q2(build_q2_model(load_fc, pv_fc, price, initial_energy_kwh=initial, terminal_energy_kwh=terminal), run_id=f"q4_2_plan_{day}", case_dir=CASE_ROOT)
    pv = _validation(plan_res, PhysicalInputs.from_sequences(load_fc, pv_fc, price), initial, terminal)
    if plan_res.get("status") != "PASS" or pv.get("status") != "PASS": raise RuntimeError(f"Q4_2_PLAN_FAILED:{day}")
    plan = tuple(_clean_nonnegative(float(x)) for x in plan_res["solution"]["Q"])
    ex = {n: [] for n in ("Q","G","H","c","d","r","E","S")}; soc = float(initial); boundary=[]
    for k in range(144):
        rem_load=(actual_load[k],)+tuple(load_fc[k+1:]); rem_pv=(actual_pv[k],)+tuple(pv_fc[k+1:])
        res=run_lexicographic_q2(build_q2_model(rem_load, rem_pv, price[k:], initial_energy_kwh=soc, planned_purchase_fixed_kwh=plan[k:], terminal_energy_kwh=terminal), run_id=f"q4_2_recourse_{day}_k{k+1:03d}", case_dir=CASE_ROOT)
        if res.get("solution") is None:
            raise RuntimeError(f"Q4_2_RECOURSE_NO_PRIMAL:{day}:k{k+1}:status={res.get('status')}:reason={res.get('reason')}")
        val=_validation(res, PhysicalInputs.from_sequences(rem_load, rem_pv, price[k:]), soc, terminal)
        if res.get("status")!="PASS" or val.get("status")!="PASS":
            raise RuntimeError(f"Q4_2_RECOURSE_FAILED:{day}:k{k+1}:status={res.get('status')}:reason={res.get('reason')}:has_solution={res.get('solution') is not None}")
        sol=res["solution"]; cs, ev=canonicalize_soc_for_propagation(float(sol["E"][0]), lower_bound_kwh=1200, upper_bound_kwh=10800, tolerance=1e-7, day=day, interval_one_based=k+1, source_level="LEVEL3")
        if ev: boundary.append(ev)
        for n in ex: ex[n].append(cs if n=="E" else _clean_nonnegative(float(sol[n][0])))
        soc=cs
    costs={"initial_plan_cost": fsum(price[k]*plan[k] for k in range(144)), "adjustment_cost":0.0, "emergency_cost":fsum(5*price[k]*ex["H"][k] for k in range(144))}
    costs["total_cost"]=costs["initial_plan_cost"]+costs["emergency_cost"]
    fin=validate_q2_day(planned_kwh=ex["Q"],ordinary_used_kwh=ex["G"],emergency_kwh=ex["H"],charge_kwh=ex["c"],discharge_kwh=ex["d"],curtailed_pv_kwh=ex["r"],energy_path_kwh=ex["E"],unused_plan_kwh=ex["S"],actual_inputs=PhysicalInputs.from_sequences(actual_load,actual_pv,price),initial_energy_kwh=initial,storage=StorageParameters.working_e_a(),future_leakage_count=0,past_decision_mutation_count=0,terminal_energy_kwh=terminal)
    if fin.get("status")!="PASS": raise RuntimeError(f"Q4_2_FINAL_VALIDATION_FAILED:{day}")
    return {"date":day.isoformat(),"plan_q0":list(plan),"final_effective_plan":list(plan),"charge":ex["c"],"discharge":ex["d"],"emergency":ex["H"],"soc_path":[initial,*ex["E"]],"soc_start":initial,"soc_end":soc,"initial_plan_cost":costs["initial_plan_cost"],"adjustment_cost":0.0,"emergency_cost":costs["emergency_cost"],"total_cost":costs["total_cost"],"validation":fin,"day_scientific_validation":fin,"solver_fallback_counts":{},"effective_plan_after_06":list(plan),"effective_plan_after_12":list(plan),"effective_plan_after_18":list(plan),"ordinary":ex["G"],"curtailment":ex["r"],"unused_plan":ex["S"],"mode":"Q4-2"}

def run_q4_3_day(day, initial, data, predictor, releases, price):
    proxy=PriceData(data,price)
    rec,soc=q3.execute_day(day=day, initial_soc=initial, previous_final_soc=None, data=proxy, predictor=predictor, releases=releases)
    r=q3._canonical_daily_result(rec); r["mode"]="Q4-3"; r["price"] = list(price); return r

def write_jsonl(path, records):
    path.parent.mkdir(parents=True,exist_ok=True)
    with path.open("w",encoding="utf-8") as f:
        for r in records: f.write(json.dumps(r,ensure_ascii=False,separators=(",",":"))+"\n"); f.flush(); os.fsync(f.fileno())

def read_resume(path):
    if not path.exists(): return []
    rows=[]; seen=set()
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip(): continue
        r=json.loads(line); d=r["date"]
        if d in seen: raise RuntimeError(f"Q4_DUPLICATE_DATE:{d}")
        seen.add(d); rows.append(r)
    expected=[(START+timedelta(days=i)).isoformat() for i in range(len(rows))]
    if [r["date"] for r in rows] != expected: raise RuntimeError("Q4_RESUME_PREFIX_INVALID")
    return rows

def run_persisted(path, mode, data, prices, releases=None, *, initial_soc, predictor, authorization_path):
    from pipeline_core.python_tools.human_gate import verify_authorized_stage_transition_for_case
    stage_check = verify_authorized_stage_transition_for_case(authorization_path, case_path=CASE_ROOT, current_stage="MODEL_IMPLEMENTATION", requested_stage="SOLVER_EXECUTION")
    if stage_check.get("status") != "PASS":
        raise RuntimeError("Q4_STAGE_AUTHORIZATION_REQUIRED")
    model_check = verify_model_authority(CASE_ROOT, authorization_path=authorization_path, entrypoint="code/python/q4_scenarios.py")
    if model_check.get("status") != "PASS":
        raise RuntimeError("Q4_MODEL_AUTHORITY_REQUIRED")
    if mode not in {"Q4-2", "Q4-3"}:
        raise ValueError("Q4_UNKNOWN_MODE")
    rows=read_resume(path)
    soc=float(initial_soc) if not rows else float(rows[-1]["soc_end"])
    path.parent.mkdir(parents=True,exist_ok=True)
    with path.open("a" if rows else "w",encoding="utf-8") as f:
        for i in range(len(rows), DAYS):
            d=START+timedelta(days=i)
            r=run_q4_2_day(d,soc,data,predictor,prices[d]) if mode=="Q4-2" else run_q4_3_day(d,soc,data,predictor,releases,prices[d])
            f.write(json.dumps(r,ensure_ascii=False,separators=(",",":"))+"\n"); f.flush(); os.fsync(f.fileno())
            rows.append(r); soc=float(r["soc_end"])
    return rows

def aggregate(name, records):
    expected_dates = [(START + timedelta(days=i)).isoformat() for i in range(DAYS)]
    if [r.get("date") for r in records] != expected_dates:
        raise ValueError("Q4_ANNUAL_PERIOD_INCOMPLETE")
    if any(r.get("mode") != name or r.get("validation", {}).get("status") != "PASS" for r in records):
        raise ValueError("Q4_DAY_VALIDATION_REQUIRED")
    return {"mode":name,"status":"PASS","scope":{"start":START.isoformat(),"end":END.isoformat(),"days":DAYS},"daily_results":records,"summary":{"plan_cost":fsum(float(r["initial_plan_cost"]) for r in records),"adjustment_cost":fsum(float(r["adjustment_cost"]) for r in records),"emergency_kwh":fsum(fsum(float(x) for x in r["emergency"]) for r in records),"emergency_cost":fsum(float(r["emergency_cost"]) for r in records),"total_cost":fsum(float(r["total_cost"]) for r in records),"planned_kwh":fsum(fsum(float(x) for x in r["plan_q0"]) for r in records),"max_balance_residual":max(float(r["validation"]["max_abs_balance_residual"]) for r in records),"max_soc_equation_residual":max(float(r["validation"]["max_abs_soc_equation_residual"]) for r in records),"max_cost_recompute_delta":max(float(r["validation"].get("cost_recompute_delta",0.0)) for r in records),"simultaneous_cd_count":sum(int(r["validation"]["simultaneous_charge_discharge_count"]) for r in records),"feb1_initial_soc":float(records[0]["soc_start"]),"dec31_final_soc":float(records[-1]["soc_end"])}}

