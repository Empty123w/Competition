"""A6 45min 缺失预测处理的敏感性对照 (仅 valid; multi_step 规划器)。

对比三种时域需求策略:
  - hold_previous : [u15,u30,u30,u60]  (主口径; 45->30)
  - next_available: [u15,u30,u60,u60]  (45->60)
  - three_step    : [u15,u30,u60]      (H=3, 丢弃第4步)
输出: analysis/reports/b/a6_h45_sensitivity.json
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pandas as pd

from air_station.baseline import pipeline as PIPE
from air_station.baseline import rules as B1
from air_station.optimization import constraints as CONS
from air_station.optimization import contracts as C
from air_station.optimization.forecast_adapter import load_forecast_bundle
from air_station.optimization.service import load_optimization_config

ROOT = Path(__file__).resolve().parents[2]
HB = ROOT / "artifacts" / "handoff_b"
OUT = ROOT / "analysis/reports/b/a6_h45_sensitivity.json"
_s = importlib.util.spec_from_file_location("ms", ROOT / "analysis/scripts/multistep_optimization.py")
MS = importlib.util.module_from_spec(_s); _s.loader.exec_module(MS)
STEP, RATED = 15, 268.9


def main():
    base = B1.load_config(B1.DEFAULT_CONFIG)
    con = C.DispatchConstraints.from_config(base)
    oc = load_optimization_config(); model = oc["paths"]["power_model"]
    device_df, station_df = PIPE.load_processed()
    scor = pd.read_parquet(HB / "forecast_scoring_actuals.parquet")
    b = load_forecast_bundle(HB / "forecast_validation_dispatch.parquet", scoring=scor, scoring_split="valid")
    grid = [t for t in sorted(b.by_issue) if t.minute % STEP == 0 and "2026-08-01" <= str(t) <= "2026-08-20 23:40:00" and 15 in b.by_issue[t]]
    init = PIPE.snapshot_from_frames(device_df, station_df, grid[0])
    pol = {"hold_previous": lambda u: [u[15], u[30], u[30], u[60]],
           "next_available": lambda u: [u[15], u[30], u[60], u[60]],
           "three_step": lambda u: [u[15], u[30], u[60]]}
    sim = {k: {d.device_id: {"running": bool(d.running), "flow": d.flow_m3_min} for d in init.devices} for k in pol}
    stt = {k: C.PolicyState() for k in pol}
    rows = []
    for t in grid:
        hist = PIPE.snapshot_from_frames(device_df, station_df, t)
        u = {h: (b.forecast_result(t, h).upper_bound if b.forecast_result(t, h) else None) for h in (15, 30, 60)}
        if any(v is None for v in u.values()):
            continue
        row = {"t": str(t), "obs": b.actual_at_target(t, 15)}
        for k, f in pol.items():
            snap = MS._sim_snapshot(hist, sim[k], str(t))
            caps = CONS.build_capabilities(con, snap, [], stt[k])
            cur_on = {d for d in ("4#", "5#") if sim[k][d]["running"]}
            cur_flows = {d: sim[k][d]["flow"] for d in ("4#", "5#")}
            req = f(u)
            bb = MS.plan(req, caps, con, model, cur_on, stt[k].last_switch_at, str(t), 1600.0)
            row[f"{k}_p"] = None; row[f"{k}_f4"] = row[f"{k}_f5"] = 0.0; row[f"{k}_exec"] = False
            if bb is None:
                continue
            combo = bb["seq"][0]; al = bb["alloc0"]
            acts = MS._actions(combo, al, caps, cur_on, cur_flows)
            trans = CONS.transition_feasibility(cur_on, combo, caps, req[0], con.reserve)[1]
            _, blocked, _ = MS._surge(snap, acts, trans)
            ok = (not blocked) and all(x.get("supply_ok", True) for x in trans)
            row[f"{k}_p"] = al["power_kw"]
            row[f"{k}_f4"] = float(al["flows"].get("4#", 0.0)); row[f"{k}_f5"] = float(al["flows"].get("5#", 0.0))
            row[f"{k}_exec"] = ok
            if ok:
                MS._apply(sim[k], acts, str(t), stt[k])
        rows.append(row)
    df = pd.DataFrame(rows); dt_h = STEP / 60.0
    rep = {"window": "valid 2026-08-01~08-20", "n_steps": int(len(df)), "policies": {}}
    for k in pol:
        g = df[df[f"{k}_exec"]]
        E = float(g[f"{k}_p"].sum()) * dt_h
        bs = float((((g[f"{k}_f4"] > 0) & (g[f"{k}_f4"] / RATED < 0.5)).sum()
                    + ((g[f"{k}_f5"] > 0) & (g[f"{k}_f5"] / RATED < 0.5)).sum()) * STEP)
        rep["policies"][k] = {"exec_steps": int(len(g)), "energy_kwh": round(E, 2),
                              "big_small_minutes": round(bs, 1),
                              "switches": int(((g[f"{k}_f4"].fillna(0) > 0).astype(int).diff().abs() == 1).sum()
                                              + ((g[f"{k}_f5"].fillna(0) > 0).astype(int).diff().abs() == 1).sum())}
    OUT.write_text(json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(rep, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
