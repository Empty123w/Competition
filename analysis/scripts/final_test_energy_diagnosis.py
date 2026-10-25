"""test 节能不足 6% 的根因诊断 (真实历史账本 + B2 功率模型; 不改算法核心)。

输出: analysis/reports/b/final/test_energy_diagnosis.json
       analysis/reports/b/final/TEST_ENERGY_DIAGNOSIS.md (由 final_test_energy_report.py 生成)

内容:
  A. 统一口径的六策略 dev/valid/test/pooled 指标 (含样本差异核查)
  B. 三窗口工况分布 (真实 SCADA 代理需求)
  C. B1 基线效率与加载率分布
  D. 分负荷区间的能耗贡献 (B1/B3/joint/joint_nompc)
  E. 约束代价 (均衡/低负荷/MPC 压力项)
  F. B2 功率模型分窗口误差与覆盖
  G. 模型可行理想能效下界 (忽略时序切换的乐观估计)
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from air_station.optimization import performance as PERF  # noqa: E402

FIN = ROOT / "analysis/reports/b/final"
RATED, STEP, CAP2 = 268.9, 15, 537.8
# 统一对照口径: 六个策略共同可执行 (含 joint_nompc) 的 service 样本
STRATS = ("b1_rule", "b3_b4", "bal", "lrb_t10", "joint_nompc", "joint")
S6_OLD = ("b1_rule", "b3_b4", "bal", "lrb_t10", "mpc_hist", "joint")
WINDOWS = ("dev", "valid", "test")
MODEL = ROOT / "artifacts/models/power_model.json"


def _load(w):
    return pd.read_csv(FIN / f"backtest_{w}_ledger.csv")


def _scope(df, strats, service=True):
    common = pd.Series(True, index=df.index)
    for s in strats:
        common &= df[f"{s}_status"].eq("success") & df[f"{s}_exec"].astype(bool)
    if not service:
        return common
    svc = common.copy()
    for s in strats:
        svc &= (df[f"{s}_flow"].fillna(0) >= df["obs"] - 0.05)
    return svc


def _block(g):
    dt_h = STEP / 60.0
    b1E = float(g["b1_rule_power"].fillna(0).sum()) * dt_h
    b1V = float(g["b1_rule_flow"].fillna(0).sum()) * STEP
    b1sec = b1E / b1V if b1V else None
    b1low = _low(g, "b1_rule")
    Vsvc = float(np.minimum(g["obs"].fillna(0).to_numpy(float), CAP2).sum()) * STEP
    out = {"steps": int(len(g)), "b1_energy_kwh": round(b1E, 1), "b1_supply_m3": round(b1V, 1),
           "service_eq_volume_m3": round(Vsvc, 1),
           "b1_sec": round(b1sec, 6) if b1sec else None}
    for s in STRATS:
        E = float(g[f"{s}_power"].fillna(0).sum()) * dt_h
        V = float(g[f"{s}_flow"].fillna(0).sum()) * STEP
        sec = E / V if V else None
        out[s] = {"energy_kwh": round(E, 1), "supply_m3": round(V, 1), "sec": round(sec, 6) if sec else None,
                  "sec_reduction_pct": round((b1sec - sec) / b1sec * 100, 3) if b1sec and sec else None,
                  "saving_pct": round((b1E - E) / b1E * 100, 3) if b1E else None,
                  "low_min": round(_low(g, s), 1),
                  "low_reduction_pct": round((b1low - _low(g, s)) / b1low * 100, 3) if b1low else None,
                  "supply_diff_pct": round((V - b1V) / b1V * 100, 3) if b1V else None,
                  "gap_m3": round(float((g["obs"].fillna(0) - g[f"{s}_flow"].fillna(0)).clip(lower=0).sum()) * STEP, 1),
                  "energy_saving_equalV_pct": round((b1E - E) / b1E * 100, 3) if b1E else None}
    return out


def _low(g, s):
    a = (g[f"{s}_f4"] > 0) & (g[f"{s}_f4"] / RATED < 0.5)
    b = (g[f"{s}_f5"] > 0) & (g[f"{s}_f5"] / RATED < 0.5)
    return float((a.sum() + b.sum()) * STEP)


def _dev(g, s):
    f4 = g[f"{s}_f4"].to_numpy(float); f5 = g[f"{s}_f5"].to_numpy(float)
    both = (f4 > 0) & (f5 > 0)
    single = (f4 > 0) ^ (f5 > 0)
    d = np.abs(f4[both] / RATED - f5[both] / RATED) * 100 if both.any() else np.array([])
    return {"mean_pp": round(float(d.mean()), 3) if d.size else None,
            "p95_pp": round(float(np.percentile(d, 95)), 3) if d.size else None,
            "max_pp": round(float(d.max()), 3) if d.size else None,
            "both": int(both.sum()), "single": int(single.sum()), "none": int((~both & ~single).sum()),
            "both_coverage": round(float(both.mean()), 4)}


def A_unified():
    frames = {w: _load(w) for w in WINDOWS}
    pooled = pd.concat([frames[w] for w in WINDOWS], ignore_index=True)
    res = {"scope_note": "统一口径: 六策略共同可执行且 service(flow>=obs-0.05); 含 joint_nompc",
           "per_window": {}, "pooled": {}, "sample_check": {}}
    for w in WINDOWS:
        g = frames[w].loc[_scope(frames[w], STRATS)]
        res["per_window"][w] = {"_steps": int(len(g)), **_block(g),
                                "_deviation": {s: _dev(g, s) for s in STRATS}}
        # 样本差异核查
        g8 = frames[w].loc[_scope(frames[w], STRATS + ("mpc_hist",))]
        g6 = frames[w].loc[_scope(frames[w], S6_OLD)]
        res["sample_check"][w] = {"six_no_mpc_hist": int(len(g6)), "six_with_nompc": int(len(g)),
                                  "diff": int(len(g6) - len(g)), "with_b3_lrb": int(len(frames[w].loc[_scope(frames[w], STRATS + ("b3_lrb",))])),
                                  "cause": "10789 vs 10793 = 纳入 b3_lrb 后其 4 步不可行被剔除 (joint_nompc 不影响样本范围)"}
    gp = pooled.loc[_scope(pooled, STRATS)]
    res["pooled"] = {"_steps": int(len(gp)), **_block(gp), "_deviation": {s: _dev(gp, s) for s in STRATS}}
    gp6 = pooled.loc[_scope(pooled, S6_OLD)]
    gp8 = pooled.loc[_scope(pooled, STRATS + ("mpc_hist",))]
    res["sample_check"]["pooled"] = {"six_no_mpc_hist": int(len(gp6)), "six_with_nompc": int(len(gp)),
                                     "diff": int(len(gp6) - len(gp)), "with_b3_lrb": int(len(pooled.loc[_scope(pooled, STRATS + ("b3_lrb",))])),
                                     "cause": "pooled: 6策略=10793; 纳入 b3_lrb 后=10789 → 差异 4 步由 b3_lrb 不可行造成; joint_nompc 不改变样本范围"}
    return res, frames


def B_workload():
    st = pd.read_parquet(ROOT / "data/processed/station_timeseries.parquet")
    st["time"] = pd.to_datetime(st["time"])
    dev = pd.read_parquet(ROOT / "data/processed/device_timeseries.parquet")
    dev["time"] = pd.to_datetime(dev["time"])
    out = {}
    for w, (a, b) in {"dev": ("2026-03-12", "2026-07-31"), "valid": ("2026-08-01", "2026-08-20"),
                      "test": ("2026-08-21", "2026-09-12")}.items():
        g = st[(st.time >= a) & (st.time <= b)]
        f = g["total_flow_m3_min"].dropna()
        gd = dev[(dev.time >= a) & (dev.time <= b)]
        # 单/双机运行占比 (按整点 5min 重采样, 统计同一时刻运行台数)
        run = gd[gd.running.astype(bool)].groupby("time")["device_id"].nunique().resample("5min").max()
        both = float((run >= 2).mean()); single = float((run == 1).mean()); none = float((run.fillna(0) == 0).mean())
        bands = {"low_<50%cap": float((f < 0.5 * CAP2).mean()),
                 "mid_50-90%cap": float(((f >= 0.5 * CAP2) & (f < 0.9 * CAP2)).mean()),
                 "high_>=90%cap": float((f >= 0.9 * CAP2).mean()),
                 "over_cap_>537.8": float((f > CAP2).mean())}
        out[w] = {"n": int(len(f)), "mean": round(float(f.mean()), 1), "p10": round(float(f.quantile(.1)), 1),
                  "p50": round(float(f.median()), 1), "p90": round(float(f.quantile(.9)), 1),
                  "max": round(float(f.max()), 1), "mean_ratio_to_cap": round(float(f.mean() / CAP2), 3),
                  "bands": {k: round(v, 4) for k, v in bands.items()},
                  "single_share": round(single, 4), "dual_share": round(both, 4), "off_share": round(none, 4)}
    return out


def C_b1_baseline(frames):
    out = {}
    for w in WINDOWS:
        g = frames[w].loc[_scope(frames[w], STRATS)]
        f4 = g["b1_rule_f4"].to_numpy(float); f5 = g["b1_rule_f5"].to_numpy(float)
        lr = np.concatenate([f4[f4 > 0] / RATED, f5[f5 > 0] / RATED])
        dt_h = STEP / 60.0
        E = float(g["b1_rule_power"].fillna(0).sum()) * dt_h
        V = float(g["b1_rule_flow"].fillna(0).sum()) * STEP
        out[w] = {"b1_sec": round(E / V, 6), "b1_energy_kwh": round(E, 1), "b1_supply_m3": round(V, 1),
                  "b1_low_min": round(_low(g, "b1_rule"), 1),
                  "load_ratio_mean": round(float(lr.mean()), 4), "load_ratio_p50": round(float(np.median(lr)), 4),
                  "load_ratio_below_0.5_share": round(float((lr < 0.5).mean()), 4),
                  "load_ratio_below_0.7_share": round(float((lr < 0.7).mean()), 4)}
    return out


def D_band_contribution(frames):
    """按代理需求区间分桶, 统计各策略能耗与 SEC, 量化差异来源。"""
    edges = [0, 268.9, 400, 480, 537.8, 1e9]
    labels = ["<268.9", "268.9-400", "400-480", "480-537.8", ">537.8"]
    out = {}
    for w in WINDOWS:
        g = frames[w].loc[_scope(frames[w], STRATS)].copy()
        g["band"] = pd.cut(g["obs"], bins=edges, labels=labels, right=False)
        dt_h = STEP / 60.0
        rows = {}
        for lb in labels:
            sub = g[g["band"] == lb]
            if len(sub) == 0:
                rows[lb] = {"steps": 0}; continue
            row = {"steps": int(len(sub)), "obs_mean": round(float(sub["obs"].mean()), 1)}
            for s in ("b1_rule", "b3_b4", "joint_nompc", "joint"):
                E = float(sub[f"{s}_power"].fillna(0).sum()) * dt_h
                V = float(sub[f"{s}_flow"].fillna(0).sum()) * STEP
                row[s] = {"E": round(E, 1), "sec": round(E / V, 6) if V else None}
            row["b3_vs_b1_sec_pct"] = round((row["b1_rule"]["sec"] - row["b3_b4"]["sec"])
                                            / row["b1_rule"]["sec"] * 100, 3) if row["b1_rule"]["sec"] else 0
            row["joint_vs_b3_E_pct"] = round((row["b3_b4"]["E"] - row["joint"]["E"]) / row["b3_b4"]["E"] * 100, 3) if row["b3_b4"]["E"] else 0
            row["nompc_vs_b3_E_pct"] = round((row["b3_b4"]["E"] - row["joint_nompc"]["E"]) / row["b3_b4"]["E"] * 100, 3) if row["b3_b4"]["E"] else 0
            rows[lb] = row
        out[w] = rows
    return out


def E_constraints(frames):
    """约束代价 (基于已有账本, 不新增算法): 均衡约束 (b3 vs b3_lrb), MPC 压力项 (joint vs joint_nompc)。"""
    out = {}
    for w in WINDOWS:
        g = frames[w].loc[_scope(frames[w], STRATS)]
        dt_h = STEP / 60.0
        def sec(s):
            E = float(g[f"{s}_power"].fillna(0).sum()) * dt_h; V = float(g[f"{s}_flow"].fillna(0).sum()) * STEP
            return E / V if V else None
        b1 = sec("b1_rule")
        out[w] = {
            "joint_vs_nompc_sec_pp": round((sec("joint") - sec("joint_nompc")) / b1 * 100, 3)
            if sec("joint") and sec("joint_nompc") else None,
            "note": "均衡约束代价用 pooled 的 b3 vs b3_lrb 估计 (见 pooled), MPC 压力项用 joint vs joint_nompc",
        }
    gp = pd.concat([frames[w] for w in WINDOWS], ignore_index=True).loc[_scope(pd.concat([frames[w] for w in WINDOWS], ignore_index=True), ("b1_rule", "b3_b4", "bal", "lrb_t10", "b3_lrb", "joint_nompc", "mpc_hist", "joint"))]
    dt_h = STEP / 60.0
    def psec(s):
        E = float(gp[f"{s}_power"].fillna(0).sum()) * dt_h; V = float(gp[f"{s}_flow"].fillna(0).sum()) * STEP
        return E / V if V else None
    b1 = psec("b1_rule")
    out["pooled"] = {"b3_sec_red_pct": round((b1 - psec("b3_b4")) / b1 * 100, 3),
                     "b3_lrb_sec_red_pct": round((b1 - psec("b3_lrb")) / b1 * 100, 3),
                     "balance_constraint_cost_pp": round((psec("b3_lrb") - psec("b3_b4")) / b1 * 100, 3),
                     "joint_vs_nompc_pp": round((psec("joint") - psec("joint_nompc")) / b1 * 100, 3)}
    return out


def F_power_model():
    """B2 功率模型分窗口误差与覆盖 (实际 power_kw vs 估计)。"""
    dev = pd.read_parquet(ROOT / "data/processed/device_timeseries.parquet")
    dev["time"] = pd.to_datetime(dev["time"])
    out = {}
    for w, (a, b) in {"dev": ("2026-03-12", "2026-07-31"), "valid": ("2026-08-01", "2026-08-20"),
                      "test": ("2026-08-21", "2026-09-12")}.items():
        g = dev[(dev.time >= a) & (dev.time <= b)]
        g = g[g.running.astype(bool) & (g.flow_m3_min > 0) & np.isfinite(g.power_kw)]
        errs = []; flows = []
        for _, r in g.iterrows():
            e = PERF.estimate_power(r["device_id"], float(r["flow_m3_min"]), model_path=str(MODEL))
            if e["status"] == "success":
                errs.append(e["estimated_power_kw"] - float(r["power_kw"])); flows.append(float(r["flow_m3_min"]))
        errs = np.array(errs); flows = np.array(flows)
        out[w] = {"n": int(len(errs)), "MAE_kw": round(float(np.mean(np.abs(errs))), 2),
                  "bias_kw": round(float(np.mean(errs)), 2), "RMSE_kw": round(float(np.sqrt(np.mean(errs ** 2))), 2),
                  "flow_mean": round(float(flows.mean()), 1) if len(flows) else None,
                  "flow_p95": round(float(np.percentile(flows, 95)), 1) if len(flows) else None,
                  "extrap_share_gt268.9": round(float((flows > 268.9).mean()), 4) if len(flows) else None,
                  "measured_power_share_gt1600kw": round(float((g.power_kw > 1600).mean()), 4)}
    return out


def G_ideal_lower_bound(frames):
    """模型条件下的理想能效下界 (忽略时序切换/爬坡; 用代理需求 d)。
    ideal_energy: 最小估算功率满足 min(d,capacity);
    ideal_balanced: 双机时附加 |λ4-λ5|<=10pp 约束。
    """
    cache = {}
    def pw(d, q):
        k = (d, round(float(q), 1))
        if k not in cache:
            r = PERF.estimate_power(d, float(q), model_path=str(MODEL))
            cache[k] = r["estimated_power_kw"] if r["status"] == "success" else None
        return cache[k]
    devs = ("4#", "5#")
    def best_split(total, balanced):
        lo, hi = 120.0, 268.9
        if total <= 268.9 + 1e-9:
            best = None
            for d in devs:
                f = min(max(total, lo), hi); p = pw(d, f)
                if p is not None and (best is None or p < best): best = p
            return best, {"4#": 0.0, "5#": 0.0}
        if total > 2 * hi + 1e-9:
            return None, None
        best = None
        for f1 in np.arange(lo, hi + 1e-6, 1.0):
            f2 = total - f1
            if f2 < lo - 1e-6 or f2 > hi + 1e-6:
                continue
            if balanced and abs(f1 - f2) > 0.10 * RATED + 1e-6:
                continue
            p1, p2 = pw("4#", f1), pw("5#", f2)
            if p1 is None or p2 is None:
                continue
            if best is None or p1 + p2 < best:
                best = p1 + p2
        return best, {"4#": 1.0, "5#": 1.0}
    out = {}
    for w in WINDOWS:
        g = frames[w].loc[_scope(frames[w], STRATS)]
        d = g["obs"].to_numpy(float)
        dt_h = STEP / 60.0
        E = {"ideal_energy": 0.0, "ideal_balanced": 0.0}
        V = {"ideal_energy": 0.0, "ideal_balanced": 0.0}
        infeas = {"ideal_energy": 0, "ideal_balanced": 0}
        for x in d:
            supply = min(x, CAP2)
            for tag, bal in (("ideal_energy", False), ("ideal_balanced", True)):
                p, _ = best_split(x, bal)
                if p is None:
                    infeas[tag] += 1; p = best_split(CAP2, bal)[0] or 0.0
                E[tag] += p * dt_h; V[tag] += supply * STEP
        b1E = float(g["b1_rule_power"].fillna(0).sum()) * dt_h
        b1V = float(g["b1_rule_flow"].fillna(0).sum()) * STEP
        Vsvc = float(np.minimum(np.asarray(d), CAP2).sum()) * STEP
        out[w] = {}
        for tag in ("ideal_energy", "ideal_balanced"):
            sec = E[tag] / Vsvc if Vsvc else None
            out[w][tag] = {"E": round(E[tag], 1), "V_service_eq": round(Vsvc, 1),
                           "sec_service_eq": round(sec, 6) if sec else None,
                           "energy_saving_vs_b1_equalV_pct": round((b1E - E[tag]) / b1E * 100, 3) if b1E else None,
                           "infeasible_steps": infeas[tag]}
        out[w]["b1"] = {"E": round(b1E, 1), "V": round(b1V, 1), "sec": round(b1E / b1V, 6)}
        out[w]["note"] = "模型下界; 忽略时序切换/爬坡/最短开停机; 用代理需求 obs; 非现场可达上限"
    return out


def main():
    res, frames = A_unified()
    res["workload"] = B_workload()
    res["b1_baseline"] = C_b1_baseline(frames)
    res["band_contribution"] = D_band_contribution(frames)
    res["constraints"] = E_constraints(frames)
    res["power_model"] = F_power_model()
    res["ideal_lower_bound"] = G_ideal_lower_bound(frames)
    (FIN / "test_energy_diagnosis.json").write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"sample_check": res["sample_check"],
                      "pooled_sec": {s: res["pooled"][s]["sec_reduction_pct"] for s in STRATS},
                      "per_window_sec": {w: {s: res["per_window"][w][s]["sec_reduction_pct"] for s in STRATS}
                                         for w in WINDOWS},
                      "ideal": {w: {k: res["ideal_lower_bound"][w][k].get("energy_saving_vs_b1_equalV_pct")
                                    for k in ("ideal_energy", "ideal_balanced")} for w in WINDOWS}},
                     ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
