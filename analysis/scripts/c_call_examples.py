"""阶段五: C 接口三模式最小调用样例 + 异常/回退用例 (只读, 不改协议)。

输出: analysis/reports/b/delivery/c_call_examples.json
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from air_station.baseline import pipeline as PIPE
from air_station.baseline import rules as B1
from air_station.optimization import contracts as C
from air_station.optimization import dispatch_api as API
from air_station.optimization.forecast_adapter import load_forecast_bundle

ROOT = Path(__file__).resolve().parents[2]
HB = ROOT / "artifacts/handoff_b"
OUT = ROOT / "analysis/reports/b/delivery/c_call_examples.json"


def main():
    scor = pd.read_parquet(HB / "forecast_scoring_actuals_calibrated.parquet")
    bundle = load_forecast_bundle(HB / "forecast_validation_dispatch_calibrated.parquet",
                                  scoring=scor, scoring_split="valid")
    dev, st = PIPE.load_processed()
    t = next(t for t in sorted(bundle.by_issue) if all(h in bundle.by_issue[t] for h in (15, 30, 45, 60)))
    snap = PIPE.snapshot_from_frames(dev, st, t)
    con = C.DispatchConstraints.from_config(B1.load_config(B1.DEFAULT_CONFIG))
    fr = bundle.forecast_result(t, 15)

    rep = {"scenario": {"as_of": str(t), "note": "valid 段真实快照 + A6 校准预测"},
           "modes": {}, "error_cases": {}}
    for mode in API.MODES:
        out = API.plan_dispatch(mode, snap, fr, [], con)
        rep["modes"][mode] = {
            "status": out.get("status"), "is_fallback": out.get("is_fallback"),
            "execution_permission": out.get("execution_permission"),
            "target_combination": out.get("target_combination"),
            "total_flow_m3_min": out.get("total_flow_m3_min"),
            "estimated_power_kw": out.get("estimated_power_kw"),
            "surge_status": out.get("surge_status"), "safety_blocked": out.get("safety_blocked"),
            "latency_ms": out.get("latency_ms"),
            "device_actions": [{"device_id": a.get("device_id"), "action": a.get("action"),
                                "target_flow_m3_min": a.get("target_flow_m3_min"),
                                "running_after": a.get("running_after")} for a in out.get("device_actions", [])],
        }
    # 错误/回退用例
    e = {}
    try:
        API.plan_dispatch("bad_mode", snap, fr, [], con)
        e["invalid_mode"] = "NO_RAISE"
    except ValueError as ex:
        e["invalid_mode"] = f"ValueError:{ex}"
    try:
        o = API.plan_dispatch("b3", snap, None, [], con)
        e["missing_forecast"] = {"status": o.get("status"), "is_fallback": o.get("is_fallback"),
                                 "permission": o.get("execution_permission")}
    except Exception as ex:
        e["missing_forecast"] = f"{type(ex).__name__}:{ex}"
    o = API.plan_dispatch("balanced", snap, fr, [], con, timeout_ms=-1.0)
    e["timeout"] = {"status": o.get("status"), "is_fallback": o.get("is_fallback"),
                    "reason_codes": o.get("reason_codes"),
                    "permission": o.get("execution_permission")}
    rep["error_cases"] = e
    rep["plc_control_note"] = "所有结果 execution_permission=simulation_candidate_only; 无任何 PLC 写指令路径"
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(rep, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
