"""B7: 两个典型场景的实际演示数据与运行脚本 (可独立重放)。

场景一(负荷下降节能调度): 在开发段历史中自动选取需求下降最陡的窗口,
  输出 B1 / B3 / bal / LRB-t10 的逐机流量、功率、加载率、低负荷标志与同工况节能。
场景二(设备异常安全重调度): 使用 4#/5# 真实编号的受控 fixtures, 经 dispatch_api +
  assess_surge_risk 输出结构化方案与负例(容量不足 / 高喘振 / 过渡风险)。

数据来源标注: real_history(真实回测) / model_estimate(功率) / proxy(喘振) / simulation。
输出目录: analysis/reports/b/b7/
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from air_station.baseline import rules as B1
from air_station.optimization import b_api, contracts as C
from air_station.optimization.contracts import (
    DeviceAction, DeviceSnapshot, DispatchConstraints, ForecastResult, HealthAssessment,
    PolicyState, StationSnapshot,
)

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "analysis/reports/b/b7"
RATED = 268.9
STRAT = ["b1_rule", "b3_b4", "bal", "lrb_t10"]
LEDGER = ROOT / "analysis/reports/b/multikpi_dev_tol_ledger.csv"
MODEL = str(ROOT / "artifacts/models/power_model.json")
PRESSURE = str(ROOT / "artifacts/models/pressure_model.json")


def _con():
    return DispatchConstraints.from_config(B1.load_config(str(ROOT / "configs/baseline.yaml")))


# ---------------- 场景一: 负荷下降 ----------------
def scenario1(win=60):
    df = pd.read_csv(LEDGER)
    m = df[df[["b1_rule_status", "b3_b4_status", "bal_status", "lrb_t10_status"]].eq("success").all(axis=1)
             & df[["b1_rule_exec", "b3_b4_exec", "bal_exec", "lrb_t10_exec"]].fillna(False).all(axis=1)].reset_index(drop=True)
    obs = m["obs"].to_numpy()
    f4b = m["b1_rule_f4"].to_numpy(); f5b = m["b1_rule_f5"].to_numpy()
    # 选取"负荷下降且 B1 存在大马拉小车"的窗口: drop>20 且非停歇, 最大化 B1 低负荷机步
    best_i, best_score = 0, -1e9
    for i in range(len(obs) - win):
        drop = obs[i] - obs[i + win]
        if drop <= 20.0 or obs[i + win] < 120.0:
            continue
        low = int((((f4b[i:i + win] > 0) & (f4b[i:i + win] / RATED < 0.5)).sum()
                   + ((f5b[i:i + win] > 0) & (f5b[i:i + win] / RATED < 0.5)).sum()))
        score = low * 100.0 + drop
        if score > best_score:
            best_score, best_i = score, i
    g = m.iloc[best_i:best_i + win].copy()
    rows = []
    for _, r in g.iterrows():
        rec = {"t": r["t"], "demand_m3min": round(float(r["obs"]), 1)}
        for s in STRAT:
            f4, f5 = float(r[f"{s}_f4"]), float(r[f"{s}_f5"])
            rec[f"{s}_f4"] = round(f4, 1); rec[f"{s}_f5"] = round(f5, 1)
            rec[f"{s}_flow"] = round(float(r[f"{s}_flow"]), 1)
            rec[f"{s}_power"] = round(float(r[f"{s}_power"]), 1)
            rec[f"{s}_lr4"] = round(f4 / RATED, 3); rec[f"{s}_lr5"] = round(f5 / RATED, 3)
            rec[f"{s}_low4"] = int(0 < f4 / RATED < 0.5); rec[f"{s}_low5"] = int(0 < f5 / RATED < 0.5)
            rec[f"{s}_on4"] = int(f4 > 0); rec[f"{s}_on5"] = int(f5 > 0)
        rows.append(rec)
    led = pd.DataFrame(rows)
    led.to_csv(OUT / "scenario1_load_drop_ledger.csv", index=False)
    # 预测序列 (A6 校准, 该窗口 as_of)
    fc = pd.read_parquet(ROOT / "artifacts/handoff_b/forecast_dev_dispatch_calibrated.parquet")
    ts = set(led["t"])
    fc = fc[fc["as_of"].astype(str).isin(ts) & fc["horizon_minutes"].isin([15, 30, 45, 60])]
    (fc[["as_of", "target_time", "horizon_minutes", "predicted_flow", "lower_bound", "upper_bound"]]
     .sort_values(["as_of", "horizon_minutes"]).to_csv(OUT / "scenario1_forecast.csv", index=False))

    def agg(s):
        E = float(led[f"{s}_power"].sum()) * 0.25
        V = float(led[f"{s}_flow"].sum()) * 15
        low = int((led[f"{s}_low4"] + led[f"{s}_low5"]).sum()) * 15
        return E, V, low
    e1, v1, low1 = agg("b1_rule")
    summary = {"window": {"t0": str(led['t'].iloc[0]), "t1": str(led['t'].iloc[-1]), "steps": len(led),
                          "demand_start": float(led['demand_m3min'].iloc[0]), "demand_end": float(led['demand_m3min'].iloc[-1]),
                          "demand_drop": round(float(led['demand_m3min'].iloc[0] - led['demand_m3min'].iloc[-1]), 1)},
               "data_source": "real_history(开发段) + model_estimate(power)", "strategies": {}}
    for s in STRAT:
        E, V, low = agg(s)
        summary["strategies"][s] = {"energy_kwh": round(E, 1), "supply_m3": round(V, 1),
                                    "sec": round(E / V, 6) if V else None,
                                    "sec_reduction_vs_b1_pct": round((e1 / v1 - E / V) / (e1 / v1) * 100, 3) if V else None,
                                    "low_load_min": low,
                                    "low_reduction_vs_b1_pct": round((low1 - low) / low1 * 100, 3) if low1 else None,
                                    "supply_minus_b1_m3": round(V - v1, 1)}
    (OUT / "scenario1_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


# ---------------- 场景二: 设备异常安全重调度 ----------------
def _snap(pressure=0.60, f4=250.0, f5=240.0, running=(True, True), q4=True, q5=True):
    devs = [DeviceSnapshot("4#", running[0], f4, 1300.0, 0.58, 100.0, 100.0, quality_valid=q4),
            DeviceSnapshot("5#", running[1], f5, 1280.0, 0.60, 100.0, 100.0, quality_valid=q5)]
    return StationSnapshot("2026-09-11T00:00:00", pressure, devs, "simulation_fixture")


def _fc(upper):
    return ForecastResult("2026-09-11T00:00:00", 60, upper - 20, upper - 40, upper, "b7")


def scenario2():
    con = _con()
    out = {"data_source": "controlled fixture (4#/5#); surrogate=proxy", "cases": {}}

    def run_case(name, snap, upper, health=None, boundary="artifacts/models/surge_boundary.json", note=""):
        d = b_api.optimize_dispatch_json(snap, _fc(upper), health or [], con,
                                         power_model_path=MODEL, pressure_model_path=PRESSURE)
        # 喘振代理评估
        from air_station.optimization.service import optimize_dispatch
        cand = optimize_dispatch(snap, _fc(upper), health or [], con,
                                 power_model_path=MODEL, pressure_model_path=PRESSURE)
        sa = b_api.assess_surge_risk(snap, cand, boundary_path=boundary)
        out["cases"][name] = {
            "note": note,
            "status": d["status"], "is_fallback": d.get("is_fallback", False),
            "target_combination": d.get("target_combination"),
            "total_flow_m3_min": d.get("total_flow_m3_min"), "estimated_power_kw": d.get("estimated_power_kw"),
            "required_flow_m3_min": d.get("required_flow_m3_min"),
            "device_actions": [{"device_id": a["device_id"], "action": a["action"],
                                "target_flow_m3_min": a["target_flow_m3_min"], "running_after": a["running_after"]}
                               for a in d.get("device_actions", [])],
            "reason_codes": d.get("reason_codes", []),
            "surge": {"status": sa.status, "safety_blocked": sa.safety_blocked, "counts": sa.counts,
                      "boundary_source": sa.boundary_source, "validation_mode": sa.validation_mode},
            "execution_permission": d.get("execution_permission"),
        }
        return out["cases"][name]

    # 正常
    run_case("normal_redispatch", _snap(), 430.0, note="两台正常, 中等需求")
    # 5# 限载/不可用 -> 重新分配 (需求 250 可由单机 4# 承担)
    run_case("unit5_derate", _snap(f4=250, f5=0, running=(True, False), q5=False), 250.0,
             note="5# 不可用 -> 单机 4# 重分配 (需求 250 <= 单机可用容量)")
    # 负例1: 容量不足 (5# 不可用 + 需求 520)
    run_case("neg_capacity_insufficient", _snap(f4=250, f5=0, running=(True, False), q5=False), 520.0,
             note="5# 不可用且需求 520 > 单机 268.9 -> 不可行")
    # 负例2: 高喘振代理风险 (强制低流量候选)
    cand = C.DispatchResult(C.STATUS_SUCCESS, "2026-09-11T00:00:00", "b7")
    cand.device_actions = [DeviceAction("4#", "adjust", 40.0, 40.0 / RATED, True)]
    sa = b_api.assess_surge_risk(_snap(f4=40.0), cand)
    out["cases"]["neg_high_surge"] = {"note": "候选流量贴近代理边界(低流量) -> high 阻断",
                                      "problem_flow_m3min": 40.0, "surge": {"status": sa.status,
                                      "safety_blocked": sa.safety_blocked, "counts": sa.counts,
                                      "boundary_source": sa.boundary_source}}
    # 负例3: 呼吸/过渡风险 (单机->遵循 transition_feasibility 判定)
    from air_station.optimization import constraints as CONS
    caps = {d: CONS.DeviceCapability(d, True, True, 120.0, 268.9, 1.0, "normal", True, True) for d in ("4#", "5#")}
    ok, steps, rc = CONS.transition_feasibility({"4#", "5#"}, ("4#",), caps, 430.0, con.reserve)
    out["cases"]["neg_transition_gap"] = {"note": "双机->单机但需求 430 > 单机容量 -> 过渡供气不足",
                                          "transition_ok": bool(ok), "reason_codes": rc,
                                          "steps_sample": steps[:3]}
    (OUT / "scenario2_events.json").write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    return out


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    s1 = scenario1()
    s2 = scenario2()
    print("scenario1 demand_drop", s1["window"]["demand_drop"], "steps", s1["window"]["steps"])
    for k, v in s1["strategies"].items():
        print("  ", k, "SECred", v["sec_reduction_vs_b1_pct"], "lowred", v["low_reduction_vs_b1_pct"])
    print("scenario2 cases:", {k: v.get("status", v.get("surge", {}).get("status")) for k, v in s2["cases"].items()})
    print("written", OUT)


if __name__ == "__main__":
    main()
