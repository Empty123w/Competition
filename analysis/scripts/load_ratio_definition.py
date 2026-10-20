"""B 加载率指标: 工程定义 + 计算 + 验证 (CAL-04 工程提案, 待 C 确认)。

不修改冻结 B1-B4; 不在 B5 窗口调参; 使用开发期(2026-03-12~07-31)设备性能数据
确定目标加载率(设备性能来源, 非优化器输出), 在验证期(08-01~08-20)评估偏离度。

定义 (B 当前工程定义, 待确认):
  - 逐机加载率 λ_i(t) = 实际排气量 flow_i / 额定排气量 268.9  (= 数据字段 load_ratio)。
  - 目标加载率 λ*: 由开发期设备性能数据(比功率 SEC 随 λ 变化)确定; 离心机 SEC 单调下降,
    拟合 argmin 落在额定点, 故 λ*=1.0(额定加载); 另给"效率平台下界"(SEC 在最优 5% 内的最低 λ)。
  - 偏离度 d_i(t) = (λ* - λ_i)/λ* x 100%  (相对百分比, 非百分点); 超标 = |d| > 10%。
  - 适用工况: 运行且质量有效的机组; 排除停机/无效/流量越界样本。

P0 修正 (SEC 单位):
  原 `y = power_kw / flow_m3_min`, 量纲为 **kW·min/m³**, 却标注为 kWh/m³ (差 60 倍)。
  修正为 **SEC[kWh/m³] = power_kw / (flow_m3_min * 60)**; 目标 λ* 与平台下界不受影响
  (单调缩放不改变 argmin)。原始结果保留于已提交 JSON; 本版输出到 `_v2` 文件。

输出:
  analysis/reports/b/load_ratio_definition_v2.json
  analysis/reports/b/load_ratio_ledger_v2.csv
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import numpy as np
import pandas as pd

from air_station.baseline import pipeline as PIPE
from air_station.baseline import rules as B1
from air_station.optimization import contracts as C
from air_station.optimization import performance as PERF
from air_station.optimization.service import load_optimization_config, optimize_dispatch

OUT = Path("analysis/reports/b/load_ratio_definition_v2.json")
LED = Path("analysis/reports/b/load_ratio_ledger_v2.csv")

RATED = 268.9
MIN_FLOW = 120.0
DEV = ("2026-03-12", "2026-07-31 23:59:00")
VAL = ("2026-08-01", "2026-08-20 23:59:00")
TOLERANCE_PCT = 10.0


def _clean(df):
    m = ((df["running"] == True) & (df["quality_valid"] == True)
         & df["flow_m3_min"].between(MIN_FLOW, RATED) & (df["power_kw"] > 50))
    return df[m].copy()


def _fit_targets(dev):
    """由开发期设备性能数据确定 λ* 与效率平台下界; 来源=设备性能, 非优化器输出。"""
    out = {}
    for did, g in dev.groupby("device_id"):
        x = (g["flow_m3_min"] / RATED).to_numpy()
        # SEC[kWh/m3] = power_kw / (flow_m3_min * 60);  (原式缺 *60, 量纲为 kW·min/m3)
        y = (g["power_kw"] / (g["flow_m3_min"] * 60.0)).to_numpy()
        c = np.polyfit(x, y, 2)
        xs = np.linspace(0.75, 1.0, 300)
        ys = np.polyval(c, xs)
        xstar = float(xs[int(np.argmin(ys))])
        ybest = float(ys.min())
        plateau = xs[ys <= ybest * 1.05]
        out[did] = {
            "lambda_target": round(1.0 if xstar >= 0.999 else xstar, 4),
            "sec_min_kwh_per_m3": round(ybest, 6),
            "sec_min_unit": "kWh/m3",
            "plateau_lo": round(float(plateau.min()), 4),
            "quad_coeff": [round(float(v), 6) for v in c],
            "n_dev_samples": int(len(g)),
            "source": "development_performance_data (设备性能, 非优化器输出)",
        }
    return out


def _dev_stats(lams: dict, lam_target, minutes: float, label: str):
    """逐机偏离度统计。lam_target 可为常数或逐机 dict。"""
    def _t(did):
        return float(lam_target[did]) if isinstance(lam_target, dict) else float(lam_target)
    rows, exceed_min, total_min = [], 0.0, 0.0
    for did, series in lams.items():
        arr = np.asarray(series, float)
        arr = arr[np.isfinite(arr)]
        if not len(arr):
            continue
        t = _t(did)
        d = (arr - t) / t * 100.0
        ex = np.abs(d) > TOLERANCE_PCT
        total_min += len(arr) * minutes
        exceed_min += float(ex.sum()) * minutes
        rows.append({"device_id": did, "n": int(len(arr)), "lambda_target": round(t, 4),
                     "lambda_mean": round(float(arr.mean()), 4),
                     "dev_mean_pct": round(float(d.mean()), 4),
                     "dev_absmax_pct": round(float(np.abs(d).max()), 4),
                     "underload_ratio": round(float((d < -TOLERANCE_PCT).mean()), 4),
                     "overload_ratio": round(float((d > TOLERANCE_PCT).mean()), 4),
                     "exceed_ratio": round(float(ex.mean()), 4),
                     "exceed_minutes": round(float(ex.sum()) * minutes, 1)})
    return {"label": label, "per_device": rows,
            "exceed_ratio_overall": round(exceed_min / total_min, 4) if total_min else None,
            "exceed_minutes_overall": round(exceed_min, 1)}


def eval_observed(dev, val):
    v = _clean(val)
    lams = {did: (g["flow_m3_min"] / RATED).to_numpy() for did, g in v.groupby("device_id")}
    return lams, 1.0


def eval_b3(device_df, station_df, con):
    times = sorted(station_df["time"])
    vtimes = [t for t in times if VAL[0] <= str(t)[:10] <= VAL[1]][::15]
    flow_at = dict(zip(station_df["time"].astype(str), station_df["total_flow_m3_min"]))
    lams = {"4#": [], "5#": []}
    for t in vtimes:
        obs = float(flow_at.get(str(t), np.nan))
        if not np.isfinite(obs) or obs <= 0:
            continue
        snap = PIPE.snapshot_from_frames(device_df, station_df, t)
        fc = C.ForecastResult(str(t), 15, obs, obs, obs, "nowcast")
        r = optimize_dispatch(snap, fc, [], con)
        if r.status != "success" or r.safety_blocked:
            continue
        for a in r.device_actions:
            if a.running_after:
                lams[a.device_id].append((a.target_flow_m3_min or 0.0) / RATED)
    return lams, 1.0


def main():
    t0 = time.perf_counter()
    base_cfg = B1.load_config(B1.DEFAULT_CONFIG)
    con = C.DispatchConstraints.from_config(base_cfg)
    dev_raw, station_df = PIPE.load_processed()
    _ = load_optimization_config()
    dev_clean = _clean(dev_raw[dev_raw["time"].between(pd.Timestamp(DEV[0]), pd.Timestamp(DEV[1]))])
    val_clean = _clean(dev_raw[dev_raw["time"].between(pd.Timestamp(VAL[0]), pd.Timestamp(VAL[1]))])

    targets = _fit_targets(dev_clean)
    lam_target = 1.0  # 口径 A: 额定加载
    plateau_targets = {did: t["plateau_lo"] for did, t in targets.items()}  # 口径 B

    obs_lams, _ = eval_observed(dev_clean, val_clean)
    b3_lams, _ = eval_b3(dev_raw, station_df, con)

    rep = {
        "definition": {
            "load_ratio": "flow_m3_min / rated_capacity_m3min (rated=268.9)",
            "lambda_target": lam_target,
            "target_source": "开发期设备性能数据 (SEC-λ 拟合 argmin); 非优化器输出",
            "deviation_formula": "(lambda - lambda_target)/lambda_target * 100%  (相对百分比, 有符号)",
            "relative_vs_percentage_point": "相对百分比",
            "target_variants": {"A_rated": "λ*=1.0", "B_plateau_lo": "逐机效率平台下界(SEC 在最优5%内的最低λ)"},
            "note_target_choice": "CAL-04 未确认; 目标口径未定, A/B 两种都报; 不得据验证结果改目标",
            "tolerance_pct": TOLERANCE_PCT,
            "applicable": "运行且质量有效、flow 在 [120,268.9]; 排除停机/无效/越界/瞬态启停样本",
            "cal04_status": "pending (B 工程提案, 待 C/工艺确认)",
        },
        "targets_by_device": targets,
        # 口径 A: 目标=额定加载 λ*=1.0
        "A_target_rated": {
            "observed_valid": _dev_stats(obs_lams, 1.0, 1.0, "observed_valid_1min"),
            "b3_plan_valid": _dev_stats(b3_lams, 1.0, 15.0, "b3_plan_valid_15min")},
        # 口径 B: 目标=逐机效率平台下界 (设备性能来源)
        "B_target_plateau_lo": {
            "observed_valid": _dev_stats(obs_lams, plateau_targets, 1.0, "observed_valid_1min"),
            "b3_plan_valid": _dev_stats(b3_lams, plateau_targets, 15.0, "b3_plan_valid_15min")},
        "runtime_s": round(time.perf_counter() - t0, 1),
    }
    OUT.write_text(json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")
    # 账本: 逐机加载率偏离 (验证期观测)
    rows = []
    vv = val_clean.copy()
    vv["lambda"] = vv["flow_m3_min"] / RATED
    vv["dev_pct"] = (lam_target - vv["lambda"]) / lam_target * 100
    vv["exceed"] = vv["dev_pct"].abs() > TOLERANCE_PCT
    vv[["time", "device_id", "flow_m3_min", "lambda", "dev_pct", "exceed"]].to_csv(LED, index=False)
    print(json.dumps(rep, ensure_ascii=False, indent=2)[:3000])


if __name__ == "__main__":
    main()
