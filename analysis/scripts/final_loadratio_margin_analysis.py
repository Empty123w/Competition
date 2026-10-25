"""加载率约束余量: 误差可估计性分析 (真实 SCADA; 不新增算法)。

问题: joint_nompc 以 |λ4-λ5| <= 0.10 作为**硬约束** (unit_commitment 枚举内 continue),
      实测最大 9.999pp 贴近上限。历史数据能否估计"流量测量/执行/预测误差"以给出可靠余量?

本脚本估计:
  1) 流量记录量化 (最小非零差分/小数位);
  2) 站级总流量 与 两台之和 的残差 (双机运行) -> 仪表/系统不一致量级;
  3) 稳态相邻样本重复性 (近似噪声+控制抖动);
  4) 约束饱和程度 (回测账本中 D>9.5pp 的双机步占比);
  5) 明确列出**不可估计**的误差 (指令-执行跟踪、单一流量计无冗余、模型预测误差与流量的映射)。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
FIN = ROOT / "analysis/reports/b/final"
RATED = 268.9


def _flow_precision(s: pd.Series):
    x = s.dropna().to_numpy(float)
    x = x[np.isfinite(x)]
    u = np.unique(np.round(x, 6))
    dif = np.diff(u)
    dif = dif[dif > 1e-9]
    return {"min_nonzero_step": round(float(dif.min()), 6) if dif.size else None,
            "is_integer": bool(np.allclose(x, np.round(x))),
            "two_decimals": bool(np.allclose(x * 100, np.round(x * 100)))}


def main():
    dev = pd.read_parquet(ROOT / "data/processed/device_timeseries.parquet")
    st = pd.read_parquet(ROOT / "data/processed/station_timeseries.parquet")
    dev["time"] = pd.to_datetime(dev["time"]); st["time"] = pd.to_datetime(st["time"])
    rep = {"constraint": {
        "where": "optimization/unit_commitment.py: best_allocation (枚举内 abs(f1-f2) <= spread_tol*RATED -> continue)",
        "type": "对**指令流量**的硬约束 (不是软惩罚)",
        "internal_interpretation": "|λ4-λ5| <= 10pp = 赛题 ±10% 的内部解释 (非官方确认公式)"}}

    # 1 量化
    rep["flow_precision"] = {d: _flow_precision(dev[dev.device_id == d]["flow_m3_min"]) for d in ("4#", "5#")}

    # 2 站级总流量 vs 两机之和 (双机运行)
    piv = dev.pivot_table(index="time", columns="device_id", values="flow_m3_min", aggfunc="first")
    runf = dev.pivot_table(index="time", columns="device_id", values="running", aggfunc="max")
    m = st.set_index("time")["total_flow_m3_min"].to_frame("total").join(piv)
    r4 = runf[4].reindex(m.index).fillna(False).astype(bool) if 4 in runf else pd.Series(False, index=m.index)
    r5 = runf[5].reindex(m.index).fillna(False).astype(bool) if 5 in runf else pd.Series(False, index=m.index)
    both = (r4 & r5)
    mb = m[both].dropna(subset=["total", 4, 5])
    resid = (mb[4] + mb[5] - mb["total"]).to_numpy(float)
    resid = resid[np.isfinite(resid)]
    rep["station_vs_device_sum"] = {
        "n": int(resid.size), "mean_m3min": round(float(resid.mean()), 3), "std_m3min": round(float(resid.std()), 3),
        "p50": round(float(np.median(resid)), 3),
        "p05": round(float(np.percentile(resid, 5)), 3), "p95": round(float(np.percentile(resid, 95)), 3),
        "abs_p95": round(float(np.percentile(np.abs(resid), 95)), 3),
        "note": "含真实厂用气/泄漏/其它用户与仪表差异, 不能单独归因于流量计测量误差"}

    # 3 稳态相邻样本重复性 (|Δtotal| 小)
    t = m.dropna(subset=["total", 4, 5]).sort_index()
    d_total = t["total"].diff()
    steady = d_total.abs() < 2.0
    df4 = t[4].diff()[steady].dropna().to_numpy(float)
    df5 = t[5].diff()[steady].dropna().to_numpy(float)
    rep["steady_repeatability"] = {
        "n": int(df4.size), "std_df4_m3min": round(float(np.std(df4)), 3), "std_df5_m3min": round(float(np.std(df5)), 3),
        "note": "相邻样本差分, 含控制抖动与测量噪声, 为上界"}

    # 4 约束饱和程度 (账本, joint_nompc)
    sat = {}
    for w in ("dev", "valid", "test"):
        df = pd.read_csv(FIN / f"backtest_{w}_ledger.csv")
        f4 = df["joint_nompc_f4"].to_numpy(float); f5 = df["joint_nompc_f5"].to_numpy(float)
        both_ = (f4 > 0) & (f5 > 0)
        d = np.abs(f4[both_] / RATED - f5[both_] / RATED) * 100
        sat[w] = {"both_steps": int(both_.sum()), "max_pp": round(float(d.max()), 3),
                  "share_gt_9.5pp": round(float((d > 9.5).mean()), 4),
                  "share_gt_9.9pp": round(float((d > 9.9).mean()), 4),
                  "share_gt_9.0pp": round(float((d > 9.0).mean()), 4)}
    rep["constraint_saturation"] = sat

    # 5 误差->pp 换算
    def to_pp(sigma_m3min):
        return round(float(np.sqrt(2) * sigma_m3min / RATED * 100), 3)
    rep["to_pp"] = {
        "residual_std_pp": to_pp(rep["station_vs_device_sum"]["std_m3min"]),
        "residual_absp95_pp": to_pp(rep["station_vs_device_sum"]["abs_p95"]),
        "steady_df4_pp": to_pp(rep["steady_repeatability"]["std_df4_m3min"]),
        "formula": "sigma_D(pp) = sqrt(2)*sigma_flow(m3/min)/268.9*100"}

    rep["not_estimable"] = [
        "指令-执行跟踪误差: 历史数据无(指令流量, 实际流量)配对, 无法估计执行误差",
        "单一流量计无冗余/无标定记录: 无法分离测量误差与真实工况波动",
        "B2 功率模型误差映射到流量: 现有残差为功率(kW)而非流量, 不能直接给出 pp 余量",
        "回测按指令精确执行: 其 D 为理想(无执行误差)值, 不能作为现场 D 的上界",
    ]
    (FIN / "loadratio_margin_analysis.json").write_text(json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(rep, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
