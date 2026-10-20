"""B1 人工启发式基线审计 (代码 + 数据依据; 不改 B1)。

输出: analysis/reports/b/b1_audit.json
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
LED = ROOT / "analysis/reports/b/a6_b5_continuous_cal_ledger.csv"
DEV = ROOT / "data/processed/device_timeseries.parquet"
STN = ROOT / "data/processed/station_timeseries.parquet"
OUT = ROOT / "analysis/reports/b/b1_audit.json"
STEP, RATED = 15, 268.9
VAL = ("2026-08-01", "2026-08-20 23:59:00")


def _hist_15min():
    st = pd.read_parquet(STN, columns=["time", "total_flow_m3_min", "total_power_kw"])
    de = pd.read_parquet(DEV, columns=["time", "device_id", "running", "flow_m3_min", "power_kw", "load_ratio", "quality_valid"])
    st["time"] = pd.to_datetime(st["time"]); de["time"] = pd.to_datetime(de["time"])
    st = st[(st.time >= VAL[0]) & (st.time <= VAL[1])].copy()
    de = de[(de.time >= VAL[0]) & (de.time <= VAL[1])].copy()
    st["bucket"] = st["time"].dt.floor("15min")
    de["bucket"] = de["time"].dt.floor("15min")
    st15 = st.groupby("bucket").agg(flow=("total_flow_m3_min", "mean"), power=("total_power_kw", "mean")).reset_index()
    de15 = de[de.running & de.quality_valid].groupby("bucket").agg(
        n=("device_id", "nunique"), lr=("load_ratio", "mean")).reset_index()
    return st15, de15, st, de


def main():
    df = pd.read_csv(LED); df = df[df["window"] == "valid"].reset_index(drop=True)
    df["t"] = pd.to_datetime(df["t"]); df["bucket"] = df["t"].dt.floor("15min")
    st15, de15, st, de = _hist_15min()

    b1 = df[df["b1_rule_exec"]].copy()
    dt_h = STEP / 60.0
    rep = {"code_evidence": {
        "demand_basis": "configs/baseline.yaml demand.basis = upper_bound (B1 用 forecast.demand_value(upper_bound))",
        "reserve": "need=required+extra5% 仅用于**容量/启停判定**(是否开机); 供气分配目标=required(upper_bound)",
        "allocation": "基载机(4#)=required-min(5#), 调节机(5#)吸收余量 -> 双机时 5# 常压到最小 -> 低负荷来源",
        "power_model": "power_estimate.mode=none -> B1 自身不产功率; 实验中用 B2 模型按其流量估算",
        "no_optimization": "无功率搜索/无多步预测; 单步规则(容量驱动+压力迟滞+启停锁定+切换预算)"},
        "protocol": {"window": "valid", "steps": int(len(b1))}}

    # B1 vs Σupper
    rep["b1_vs_upper"] = {"b1_supply_m3": round(float(b1["b1_rule_flow"].sum()) * STEP, 1),
                          "sum_ub15_m3": round(float(b1["ub15"].sum()) * STEP, 1),
                          "b1_over_ub_pct": round((float(b1["b1_rule_flow"].sum()) - float(b1["ub15"].sum())) / float(b1["ub15"].sum()) * 100, 3)}
    # B1 vs historical (aligned 15-min)
    merged = b1.merge(st15, on="bucket", how="inner")
    rep["b1_vs_historical"] = {
        "steps": int(len(merged)),
        "b1_supply_m3": round(float(merged["b1_rule_flow"].sum()) * STEP, 1),
        "hist_supply_m3": round(float(merged["flow"].sum()) * STEP, 1),
        "b1_over_hist_pct": round((float(merged["b1_rule_flow"].sum()) - float(merged["flow"].sum())) / float(merged["flow"].sum()) * 100, 3),
        "b1_energy_kwh_est": round(float(merged["b1_rule_power"].sum()) * dt_h, 1),
        "hist_energy_kwh": round(float(merged["power"].sum()) * dt_h, 1),
        "b1_sec": round(float(merged["b1_rule_power"].sum()) * dt_h / (float(merged["b1_rule_flow"].sum()) * STEP), 6),
        "hist_sec": round(float(merged["power"].sum()) * dt_h / (float(merged["flow"].sum()) * STEP), 6),
        "b1_mean_n": round(float(((merged["b1_rule_f4"] > 0).astype(int) + (merged["b1_rule_f5"] > 0).astype(int)).mean()), 3),
        "hist_mean_n": round(float(de15.set_index("bucket")["n"].reindex(merged["bucket"]).fillna(0).mean()), 3) if len(de15) else None}
    # 逐机负荷分配 (B1)
    rep["b1_device_share"] = {"4#": round(float(b1["b1_rule_f4"].sum()) / float(b1["b1_rule_flow"].sum()), 4),
                              "5#": round(float(b1["b1_rule_f5"].sum()) / float(b1["b1_rule_flow"].sum()), 4),
                              "5#_at_min_frac": round(float(((b1["b1_rule_f5"] > 0) & (b1["b1_rule_f5"] <= 120.5)).mean()), 4)}
    # 历史逐机负荷
    dl = de[de.running & de.quality_valid]
    rep["hist_device_load_ratio_mean"] = {k: round(float(v), 4) for k, v in dl.groupby("device_id")["load_ratio"].mean().items()}
    rep["hist_running_share"] = {k: round(float(v), 4) for k, v in de.groupby("device_id")["running"].mean().items()}
    OUT.write_text(json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(rep, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
