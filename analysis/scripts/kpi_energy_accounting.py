"""赛题综合能效 ≥6% 重新核算 (读取已完成的 A6 校准连续回测账本, 不重跑回测)。

公式 (任务书 §268/274):
  E_kWh = Σ P_kW·Δt_h; V_m3 = Σ Q·Δt_min; SEC = ΣE/ΣV;
  同等供气量下节电率 = (E_base - E_opt)/E_base; "综合能效提升" 指同供气任务的能耗或 SEC 改善。
输出: analysis/reports/b/kpi_energy_accounting.json
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
LED = ROOT / "analysis/reports/b/a6_b5_continuous_cal_ledger.csv"
OUT = ROOT / "analysis/reports/b/kpi_energy_accounting.json"
STATION = ROOT / "data/processed/station_timeseries.parquet"
STRAT = ("b1_rule", "b3_b4", "myopic", "multi_step", "v2", "cand")
STEP = 15
VAL = ("2026-08-01", "2026-08-20 23:59:00")


def _blk(g, dt_h):
    out = {}
    for s in STRAT:
        E = float(g[f"{s}_power"].fillna(0).sum()) * dt_h
        V = float(g[f"{s}_flow"].fillna(0).sum()) * STEP
        out[s] = {"energy_kwh": round(E, 2), "supply_m3": round(V, 1),
                  "sec_kwh_per_m3": round(E / V, 6) if V else None}
    b1e, b1v = out["b1_rule"]["energy_kwh"], out["b1_rule"]["supply_m3"]
    b1sec = out["b1_rule"]["sec_kwh_per_m3"]
    for s in STRAT:
        out[s]["energy_saving_vs_b1_pct"] = round((b1e - out[s]["energy_kwh"]) / b1e * 100, 4) if b1e else None
        out[s]["sec_reduction_vs_b1_pct"] = round((b1sec - out[s]["sec_kwh_per_m3"]) / b1sec * 100, 4) if b1sec and out[s]["sec_kwh_per_m3"] else None
        out[s]["supply_diff_vs_b1_m3"] = round(out[s]["supply_m3"] - b1v, 1)
    return out


def main():
    if not LED.exists():
        raise SystemExit(f"缺少回测账本: {LED}")
    df = pd.read_csv(LED)
    df = df[df["window"] == "valid"].reset_index(drop=True)
    dt_h = STEP / 60.0
    rep = {"formula": "E=ΣP·Δt_h; V=ΣQ·Δt_min; SEC=E/V; 节电率=(E_b1-E_opt)/E_b1 (任务书 §268/274)",
           "valid_window": VAL, "n_steps": int(len(df))}

    # 全窗口(可执行步)
    ok_all = df[[f"{s}_status" for s in STRAT]].eq("success").all(axis=1)
    for s in STRAT:
        ok_all = ok_all & df[f"{s}_exec"]
    full = df[ok_all]
    rep["full_exec_steps"] = int(len(full))
    rep["full_exec"] = _blk(full, dt_h)

    # 共同可执行 (各策略各自 exec 且成功) -> 用 b1 & b3 & myopic 共同 (与旧口径一致)
    common = full.copy()
    rep["common_steps"] = int(len(common))
    rep["common"] = _blk(common, dt_h)

    # 服务等价: 各策略均满足实测需求
    svc = common.copy()
    for s in STRAT:
        svc = svc[svc[f"{s}_flow"] >= svc["obs_flow"] - 0.05]
    rep["service_equiv_steps"] = int(len(svc))
    rep["service_equiv"] = _blk(svc, dt_h)

    # 历史 SCADA 实测能效 (同时段)
    st = pd.read_parquet(STATION, columns=["time", "total_flow_m3_min", "total_power_kw"])
    st["time"] = pd.to_datetime(st["time"])
    v = st[(st["time"] >= VAL[0]) & (st["time"] <= VAL[1])]
    v = v[v["total_flow_m3_min"].notna() & v["total_power_kw"].notna() & (v["total_power_kw"] > 0)]
    E_obs = float(v["total_power_kw"].sum()) / 60.0        # 1-min 步长
    V_obs = float(v["total_flow_m3_min"].sum())
    rep["historical_scada"] = {"n_minutes": int(len(v)), "energy_kwh": round(E_obs, 2),
                               "supply_m3": round(V_obs, 1), "sec_kwh_per_m3": round(E_obs / V_obs, 6) if V_obs else None}
    b1 = rep["full_exec"]["b1_rule"]
    rep["historical_vs_b1_sim"] = {
        "sec_obs": rep["historical_scada"]["sec_kwh_per_m3"], "sec_b1_sim": b1["sec_kwh_per_m3"],
        "note": "历史为真实运行(含人工调度/停机/扰动), 与仿真基线口径不同, 仅对照"}

    # 5.46% 定位
    rep["what_was_5_46"] = ("此前的 5.46% = 验证段**服务等价步**上 B3+B4 相对 B1 的**电量降幅**"
                            "(各策略均满足实测需求的子集); 见 service_equiv.b3_b4.energy_saving_vs_b1_pct")
    OUT.write_text(json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(rep, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
