"""开发段 vs 验证段能效差异定量分解 (只读现有账本 + 复用预测包)。

回答:
  1) 需求分布 / 预测误差 / 上界宽度差异
  2) 单机/双机比例、加载率
  3) B1 与 bal 各自 SEC
  4) 负荷分箱的节能收益/损失贡献
  5) 可行域差异
  6) bal SEC 差距归因
输出: analysis/reports/b/dev_valid_diag.json
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from air_station.baseline import pipeline as PIPE
from air_station.optimization.forecast_adapter import load_forecast_bundle

ROOT = Path(__file__).resolve().parents[2]
HB = ROOT / "artifacts" / "handoff_b"
STEP, RATED = 15, 268.9
DT_H = STEP / 60.0


def forecast_frame(window: str) -> pd.DataFrame:
    scor = pd.read_parquet(HB / "forecast_scoring_actuals_calibrated.parquet")
    fn = ("forecast_dev_dispatch_calibrated.parquet" if window == "dev"
          else "forecast_validation_dispatch_calibrated.parquet")
    split = "dev" if window == "dev" else "valid"
    b = load_forecast_bundle(HB / fn, scoring=scor, scoring_split=split)
    grid = [t for t in sorted(b.by_issue) if t.minute % STEP == 0
            and all(h in b.by_issue[t] for h in (15, 30, 45, 60))]
    d = pd.DataFrame({"t": grid})
    d["obs"] = [b.actual_at_target(t, 15) for t in grid]
    for h in (15, 30, 45, 60):
        d[f"pf{h}"] = [b.forecast_result(t, h).predicted_flow for t in grid]
        d[f"ub{h}"] = [b.forecast_result(t, h).upper_bound for t in grid]
    return d


def load_ledger(window: str) -> pd.DataFrame:
    f = ("analysis/reports/b/multikpi_dev_0_13000_ledger.csv" if window == "dev"
         else "analysis/reports/b/multikpi_valid_0_2000_ledger.csv")
    return pd.read_csv(ROOT / f)


def sec_of(df, s):
    E = float(df[f"{s}_power"].fillna(0).sum()) * DT_H
    V = float(df[f"{s}_flow"].fillna(0).sum()) * STEP
    return (E, V, (E / V if V else None))


def running_stats(df, s):
    f4 = df[f"{s}_f4"].fillna(0.0); f5 = df[f"{s}_f5"].fillna(0.0)
    on4 = f4 > 0; on5 = f5 > 0
    n1 = (on4 ^ on5); n2 = (on4 & on5); n0 = (~on4 & ~on5)
    n = len(df)
    return {
        "pct_none": round(float(n0.mean()) * 100, 2),
        "pct_single": round(float(n1.mean()) * 100, 2),
        "pct_double": round(float(n2.mean()) * 100, 2),
        "pct_5alone": round(float((on5 & ~on4).mean()) * 100, 2),
        "pct_4alone": round(float((on4 & ~on5).mean()) * 100, 2),
        "mean_load4": round(float((f4[on4] / RATED).mean()), 4) if on4.any() else None,
        "mean_load5": round(float((f5[on5] / RATED).mean()), 4) if on5.any() else None,
        "frac_running_load_lt05": round(float(((f4[on4] / RATED < 0.5).sum() + (f5[on5] / RATED < 0.5).sum())
                                             / max(1, on4.sum() + on5.sum())) * 100, 2),
    }


def bin_contrib(df, s, edges):
    """按 obs 负荷分箱: 逐步 Δpower, Δflow, 步数, 低负荷步。"""
    d = df.copy()
    d["bin"] = pd.cut(d["obs"], edges, right=False)
    out = {}
    for b, g in d.groupby("bin", observed=True):
        e1 = float(g["b1_rule_power"].fillna(0).sum()) * DT_H
        e2 = float(g[f"{s}_power"].fillna(0).sum()) * DT_H
        v1 = float(g["b1_rule_flow"].fillna(0).sum()) * STEP
        v2 = float(g[f"{s}_flow"].fillna(0).sum()) * STEP
        low1 = float((((g["b1_rule_f4"] > 0) & (g["b1_rule_f4"] / RATED < 0.5)).sum()
                      + ((g["b1_rule_f5"] > 0) & (g["b1_rule_f5"] / RATED < 0.5)).sum()) * STEP)
        low2 = float((((g[f"{s}_f4"] > 0) & (g[f"{s}_f4"] / RATED < 0.5)).sum()
                      + ((g[f"{s}_f5"] > 0) & (g[f"{s}_f5"] / RATED < 0.5)).sum()) * STEP)
        out[str(b)] = {"steps": int(len(g)), "dE_kwh": round(e1 - e2, 1), "dV_m3": round(v1 - v2, 1),
                       "dLow_min": round(low1 - low2, 1),
                       "b1_sec": round(e1 / v1, 5) if v1 else None,
                       "bal_sec": round(e2 / v2, 5) if v2 else None}
    return out


def main():
    edges = [0, 256.1, 268.9, 350, 450, 537.8, 10000]
    rep = {"note": "只读账本; common=全策略可执行子集; full=全窗(口径不可比时以common为准)"}
    frames = {}
    for w in ("dev", "valid"):
        ff = forecast_frame(w)
        ld = load_ledger(w)
        # 对齐时间
        ff["t"] = pd.to_datetime(ff["t"]); ld["t"] = pd.to_datetime(ld["t"])
        m = ld.merge(ff[["t", "pf15", "ub15", "pf30", "ub30", "pf45", "ub45", "pf60", "ub60"]],
                     on="t", how="left")
        frames[w] = m
        n = len(m)
        r = {"n": n,
             "obs_mean": round(float(m.obs.mean()), 1), "obs_median": round(float(m.obs.median()), 1),
             "obs_p95": round(float(m.obs.quantile(.95)), 1), "obs_max": round(float(m.obs.max()), 1),
             "obs_zero_frac": round(float((m.obs <= 0.05).mean()) * 100, 2),
             "obs_gt_cap_frac": round(float((m.obs > 2 * RATED).mean()) * 100, 2),
             "obs_gt_1x_frac": round(float((m.obs > RATED).mean()) * 100, 2),
             "pf15_mae": round(float((m.pf15 - m.obs).abs().mean()), 2),
             "pf15_bias": round(float((m.pf15 - m.obs).mean()), 2),
             "ub15_width_mean": round(float((m.ub15 - m.pf15).mean()), 2),
             "ub15_width_p95": round(float((m.ub15 - m.pf15).quantile(.95)), 2),
             "ub60_width_mean": round(float((m.ub60 - m.pf60).mean()), 2),
             "pf15_mae_after_floor_note": "分母floor=1.0 见 a6 验收"}
        # 共同可执行子集 (b1,b3,bal)
        strat = ["b1_rule", "b3_b4", "bal"]
        cm = m.copy()
        for s in strat:
            cm = cm[cm[f"{s}_status"].eq("success") & cm[f"{s}_exec"]]
        r["common_steps"] = int(len(cm))
        for s in strat:
            E, V, sec = sec_of(cm, s)
            r[f"{s}_common"] = {"energy_kwh": round(E, 1), "supply_m3": round(V, 1),
                                "sec": round(sec, 6) if sec else None,
                                "run": running_stats(cm, s)}
        e1, v1, s1 = sec_of(cm, "b1_rule"); e2, v2, s2 = sec_of(cm, "bal")
        r["bal_common_sec_reduction_pct"] = round((s1 - s2) / s1 * 100, 4)
        r["bal_common_saving_pct"] = round((e1 - e2) / e1 * 100, 4)
        r["bal_common_dV_m3"] = round(v2 - v1, 1)
        # b3
        e3, v3, s3 = sec_of(cm, "b3_b4")
        r["b3_common_sec_reduction_pct"] = round((s1 - s3) / s1 * 100, 4)
        # 负荷分箱 (common)
        r["bins_bal"] = bin_contrib(cm, "bal", edges)
        r["bins_b3"] = bin_contrib(cm, "b3_b4", edges)
        # full 口径
        for s in strat:
            E, V, sec = sec_of(m, s)
            r[f"{s}_full"] = {"energy_kwh": round(E, 1), "supply_m3": round(V, 1),
                              "sec": round(sec, 6) if sec else None,
                              "steps_nonzero": int((m[f"{s}_flow"].fillna(0) > 0).sum())}
        rep[w] = r

    # 差值汇总
    d, v = rep["dev"], rep["valid"]
    rep["delta"] = {
        "obs_mean": round(d["obs_mean"] - v["obs_mean"], 1),
        "pf15_mae": round(d["pf15_mae"] - v["pf15_mae"], 2),
        "ub15_width_mean": round(d["ub15_width_mean"] - v["ub15_width_mean"], 2),
        "b1_common_sec": round(d["b1_rule_common"]["sec"] - v["b1_rule_common"]["sec"], 6),
        "bal_common_sec": round(d["bal_common"]["sec"] - v["bal_common"]["sec"], 6),
        "bal_common_sec_reduction_pp": round(d["bal_common_sec_reduction_pct"] - v["bal_common_sec_reduction_pct"], 4),
    }
    OUT = ROOT / "analysis/reports/b/dev_valid_diag.json"
    OUT.write_text(json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(rep, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
