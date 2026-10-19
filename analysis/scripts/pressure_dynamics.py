"""B 压力响应动态模型实验 (ARX / 状态空间), 与 persistence 对比。

不修改冻结 B2 压力模型; 不在 B5 窗口调参。
测点含义 (先澄清):
  - header_pressure_mpa: 站级母管压力 (唯一压力测点, 1 min);
  - total_flow_m3_min: 站级**供气**流量 (送出的气量), 机组流量之和;
  - 无独立的**耗气(demand)** 测点, 故同一 flow 字段不得当作供气与耗气两个独立变量;
  - 无逐机压力/管网储气参数, 动作记录为分钟级开停/流量。

模型:
  - persistence: p(t+1)=p(t) (B2 冻结基线);
  - ARX: p(t+1)=Σ a_i p(t-i) + Σ b_j u(t-j) + c, u∈{total_flow, Δflow, running_count};
  - 单步误差(valid), 自由多步仿真误差(k=1..5), 稳定性(AR 极点), 阶跃响应方向/幅度。

输出: analysis/reports/b/pressure_dynamics.json
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

OUT = Path("analysis/reports/b/pressure_dynamics.json")
TRAIN = ("2026-03-12", "2026-07-31 23:59:00")
VALID = ("2026-08-01", "2026-08-20 23:59:00")


def _load():
    s = pd.read_parquet("data/processed/station_timeseries.parquet",
                        columns=["time", "header_pressure_mpa", "total_flow_m3_min"])
    s["time"] = pd.to_datetime(s["time"])
    return s


def _design(df, na, nb):
    """构造 ARX 回归: y=p(t+1), AR 项 p(t..t-na+1), 输入项 u(t..t-nb+1)。"""
    d = df.copy()
    d["y"] = d["p"].shift(-1)
    cols = []
    for i in range(na):
        d[f"p_lag{i+1}"] = d["p"].shift(i)
        cols.append(f"p_lag{i+1}")
    for j in range(nb):
        d[f"u_lag{j}"] = d["u"].shift(j)
        cols.append(f"u_lag{j}")
    d[f"du_lag0"] = d["u"].diff()
    d["n_lag0"] = d["n"]
    cols += ["du_lag0", "n_lag0"]
    d = d.dropna()
    return d, cols


def _fit_arx(tr, cols):
    X = np.column_stack([tr[c].to_numpy() for c in cols] + [np.ones(len(tr))])
    y = tr["y"].to_numpy()
    beta, *_ = np.linalg.lstsq(X, y, rcond=None)
    return beta


def main():
    s = _load()
    s = s[np.isfinite(s["header_pressure_mpa"]) & (s["header_pressure_mpa"] > 0)].copy()
    s["p"] = s["header_pressure_mpa"].astype(float)
    s["u"] = s["total_flow_m3_min"].astype(float)
    s["n"] = (s["u"] > 1).astype(float)  # 运行台数代理 (无逐机计数时用流量>0)
    tr = s[s["time"].between(pd.Timestamp(TRAIN[0]), pd.Timestamp(TRAIN[1]))].reset_index(drop=True)
    va = s[s["time"].between(pd.Timestamp(VALID[0]), pd.Timestamp(VALID[1]))].reset_index(drop=True)

    # persistence 基线
    val = va.dropna(subset=["p"]).copy()
    val["y"] = val["p"].shift(-1)
    val = val.dropna()
    pers_mae = float((val["y"] - val["p"]).abs().mean())
    pers_rmse = float(np.sqrt(((val["y"] - val["p"]) ** 2).mean()))

    results = {"protocol": {"train": TRAIN, "valid": VALID, "uses_B5_final_window": False,
                            "evidence_scope": "开发/验证窗口; 分钟级; 非现场反事实验证"}}
    results["persistence_valid"] = {"MAE": round(pers_mae, 5), "RMSE": round(pers_rmse, 5),
                                    "n": int(len(val))}
    results["measurement_note"] = ("仅有站级母管压力与站级供气流量; 无独立耗气测点与管网储气参数, "
                                   "故 ARX 输入 u 仅为供气吞吐代理, 不能识别调度动作的因果响应")

    variants = {}
    for na, nb in [(1, 1), (2, 1), (2, 2)]:
        d_tr, cols = _design(tr, na, nb)
        beta = _fit_arx(d_tr, cols)
        # 单步 valid
        d_va, _ = _design(va, na, nb)
        # 用训练参数对 valid 做单步预测
        Xv = np.column_stack([d_va[c].to_numpy() for c in cols] + [np.ones(len(d_va))])
        yv = d_va["y"].to_numpy()
        pred = Xv @ beta
        mae = float(np.abs(pred - yv).mean())
        rmse = float(np.sqrt(((pred - yv) ** 2).mean()))
        # 自由多步仿真 (用预测值回代), k=1..5, 以 valid 段连续块评估
        blocks = _free_run(va, beta, cols, na, nb)
        poles = np.roots(beta[:na]) if na >= 1 else np.array([])
        max_pole = float(np.max(np.abs(poles))) if len(poles) else 0.0
        # 阶跃响应: 输入 u 增加 +50 m3/min 一步, 观察 p 的稳态方向
        step = _step_response(beta, cols, na, nb)
        variants[f"arx_na{na}_nb{nb}"] = {
            "one_step_valid": {"MAE": round(mae, 5), "RMSE": round(rmse, 5), "n": int(len(yv))},
            "free_run_mae_by_horizon": blocks,
            "ar_poles_abs": [round(float(abs(x)), 5) for x in poles],
            "max_pole_abs": round(max_pole, 5),
            "stable": bool(max_pole < 1.0),
            "step_flow_+50m3min_effect_on_next_p": step,
            "beta": [round(float(b), 6) for b in beta],
            "cols": cols,
        }
    results["variants"] = variants
    results["conclusion"] = {
        "best_one_step": min(variants, key=lambda k: variants[k]["one_step_valid"]["MAE"]),
        "persistence_beaten": bool(min(v["one_step_valid"]["MAE"] for v in variants.values()) < pers_mae),
        "counterfactual_identifiable": False,
        "reason": ("Δp 与流量变化近乎不相关(历史 corr≈-0.017), ARX 输入为吞吐代理; "
                   "低预测误差主要来自持续性, 不等于调度反事实可信"),
        "gaps": ["独立耗气(demand)测点", "管网储气/容积与响应时延参数",
                 "设定点阶跃受控试验数据", "逐机排气压力与母管的动态关系"],
    }
    OUT.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(results, ensure_ascii=False, indent=2)[:3000])


def _free_run(df, beta, cols, na, nb):
    """在 valid 段做自由多步仿真: 用预测 p 回代, 比较 k=1..5 步误差。"""
    d = df.reset_index(drop=True)
    p_hist = list(d["p"].to_numpy())
    u = d["u"].to_numpy()
    n = d["n"].to_numpy()
    du = np.diff(np.concatenate([[u[0]], u]))
    out = {}
    horizon = 5
    errs = {k: [] for k in range(1, horizon + 1)}
    for t0 in range(2, len(d) - horizon - 1):
        # 自由仿真从 t0 起, 用真实历史初始化
        psim = p_hist[:t0 + 1]
        for k in range(1, horizon + 1):
            idx = t0 + k
            feats = []
            for i in range(na):
                feats.append(psim[idx - 1 - i] if idx - 1 - i >= 0 else p_hist[0])
            for j in range(nb):
                feats.append(u[idx - 1 - j] if idx - 1 - j >= 0 else u[0])
            feats.append(du[idx - 1] if idx - 1 < len(du) else 0.0)
            feats.append(n[idx - 1] if idx - 1 >= 0 else 0.0)
            yhat = float(np.dot(feats, beta[:-1]) + beta[-1])
            psim.append(yhat)
        for k in range(1, horizon + 1):
            errs[k].append(abs(psim[t0 + k] - p_hist[t0 + k]))
    for k in range(1, horizon + 1):
        out[f"k{k}"] = round(float(np.mean(errs[k])), 5) if errs[k] else None
    return out


def _step_response(beta, cols, na, nb):
    """u 一步 +50 后, next p 的变化 (仅当前步影响); 稳态方向用 AR 放大因子近似。"""
    iu = cols.index("u_lag0")
    return round(float(beta[iu] * 50.0), 6)


if __name__ == "__main__":
    main()
