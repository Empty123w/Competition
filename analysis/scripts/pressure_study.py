"""压力专项: 历史分布/事件/与流量关系 + 数据驱动压力模型(时间顺序校验) + 仿真钳制成因。

输出: analysis/reports/b/pressure_study.json (+ 模型校验指标)
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from air_station.baseline import pipeline as PIPE

ROOT = Path(__file__).resolve().parents[2]
BAND = (0.55, 0.66)
TRAIN = ("2026-03-26", "2026-08-20")
TEST = ("2026-08-21", "2026-09-11")


def build_features():
    dev, st = PIPE.load_processed()
    dev = dev.copy(); st = st.copy()
    dev["time"] = pd.to_datetime(dev["time"]); st["time"] = pd.to_datetime(st["time"])
    run = (dev[dev["running"].astype(bool)].groupby("time").size().rename("n_running"))
    s = st.set_index("time").join(run).reset_index()
    s["n_running"] = s["n_running"].fillna(0)
    s["hp"] = s["header_pressure_mpa"].astype(float)
    s["tf"] = s["total_flow_m3_min"].astype(float)
    s["hp_lag1"] = s["hp"].shift(1)
    s["tf_lag1"] = s["tf"].shift(1)
    s["hour"] = s["time"].dt.hour
    return s


def main():
    s = build_features()
    rep = {"band_mpa": list(BAND), "band_source": "工程假设 (任务书 §2.3-5, 甲方未确认)"}
    # 采样频率
    for w, (a, b) in {"dev": TRAIN, "valid": TEST}.items():
        m = s[(s.time >= a) & (s.time <= b)].copy()
        dt = m["time"].diff().dt.total_seconds().value_counts()
        hp = m["hp"]; run = m[m.tf > 0]
        rep[w] = {
            "sampling_seconds_top": {int(k): int(v) for k, v in dt.head(3).items()},
            "n": int(len(m)),
            "hp_mean_std": [round(float(run.hp.mean()), 4), round(float(run.hp.std()), 4)],
            "hp_min_max": [round(float(run.hp.min()), 4), round(float(run.hp.max()), 4)],
            "compliance_pct": round(float(((run.hp >= BAND[0]) & (run.hp <= BAND[1])).mean() * 100), 3),
            "below_band_pct": round(float((run.hp < BAND[0]).mean() * 100), 3),
            "above_band_pct": round(float((run.hp > BAND[1]).mean() * 100), 3),
            "corr_hp_tf": round(float(run.hp.corr(run.tf)), 4),
            "corr_hp_nrunning": round(float(run.hp.corr(run.n_running)), 4),
        }
        # 低压/超压"事件"(连续段)
        def events(mask):
            ev = 0; cur = False
            for x in mask:
                if x and not cur:
                    ev += 1; cur = True
                elif not x:
                    cur = False
            return ev
        rep[w]["low_pressure_events"] = events((run.hp < BAND[0]).values)
        rep[w]["over_pressure_events"] = events((run.hp > BAND[1]).values)
    # 数据驱动一步模型: 时间顺序 train(dev)->test(valid)
    feat = ["hp_lag1", "tf", "tf_lag1", "n_running", "hour"]
    tr = s[(s.time >= TRAIN[0]) & (s.time <= TRAIN[1])].dropna(subset=feat + ["hp"])
    te = s[(s.time >= TEST[0]) & (s.time <= TEST[1])].dropna(subset=feat + ["hp"])
    from sklearn.linear_model import Ridge
    from sklearn.ensemble import GradientBoostingRegressor
    from sklearn.metrics import mean_absolute_error
    res = {}
    for name, model in (("ridge", Ridge(alpha=1.0)),
                        ("gbr", GradientBoostingRegressor(n_estimators=200, max_depth=3, random_state=0))):
        model.fit(tr[feat], tr["hp"])
        pred = model.predict(te[feat])
        mae = mean_absolute_error(te["hp"], pred)
        pers = mean_absolute_error(te["hp"], te["hp_lag1"])
        # 事件识别 (越界)
        for tag, fn in (("above", lambda x: x > BAND[1]), ("below", lambda x: x < BAND[0])):
            act = fn(te["hp"].values); pr = fn(pred)
            tp = int((act & pr).sum()); fp = int((~act & pr).sum()); fn_ = int((act & ~pr).sum())
            prec = tp / (tp + fp) if tp + fp else None
            rec = tp / (tp + fn_) if tp + fn_ else None
            res.setdefault(name, {})[f"{tag}_band"] = {"precision": round(prec, 4) if prec else None,
                                                       "recall": round(rec, 4) if rec else None,
                                                       "n_actual": int(act.sum())}
        res.setdefault(name, {}).update({"mae_mpa": round(float(mae), 5),
                                         "persistence_mae_mpa": round(float(pers), 5),
                                         "beats_persistence": bool(mae < pers),
                                         "n_train": int(len(tr)), "n_test": int(len(te))})
    rep["model_validation"] = {"split": {"train": TRAIN, "test": TEST}, "features": feat, "results": res,
                               "note": "独立用气需求(Q_demand)未测 -> 仅一步预测, 反事实不可辨识"}
    # 仿真钳制成因
    rep["sim_clamp_reason"] = {
        "supply_over_demand_m3min": 40.0, "K_VOL_m3_per_MPa": 23700.0, "LEAK_per_min": 0.001,
        "P0": 0.60, "clamp": [0.40, 0.75],
        "steady_state_p": round(0.55 + (40.0 / 23700.0) / 0.001, 3),
        "explanation": "闭环过供 40 m3/min / K_VOL 需由 LEAK*(p-P_ENV) 平衡 -> 稳态 p 远超上钳制 -> 钳到 0.75 产生超带伪影"}
    OUT = ROOT / "analysis/reports/b/pressure_study.json"
    OUT.write_text(json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(rep, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
