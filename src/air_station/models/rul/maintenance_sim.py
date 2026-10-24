"""预测性维护策略仿真 (阶段 A7)。

在模拟退化闭环下, 配对比较:
  - 基线(反应式): 退化到失效才处理 -> 非计划停机;
  - 策略(预测性): 用**模型预测的** RUL ≤ 阈值 -> 提前计划停机。
自证三项指标: 非计划停机下降率、MTBF 提升率、预警提前时间。
输出明确标注 validation_mode=simulation。
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from air_station.models.rul import features as RF
from air_station.models.rul import simulator

NODE = "node"


def load_config(path: str = "configs/maintenance_policy.yaml") -> dict:
    import yaml
    return yaml.safe_load(Path(path).read_text(encoding="utf-8"))


def _gen_episodes(cfg: dict) -> pd.DataFrame:
    rul_cfg = simulator.load_config(cfg["paths"]["rul_config"])
    rul_cfg["seed"] = int(cfg["seed"])
    rul_cfg["episodes_per_device"] = int(cfg["episodes_per_device"])
    rul_cfg["initial_rul_hours"] = float(cfg["initial_rul_hours"])
    rul_cfg["maintenance_reset_rul_hours"] = float(cfg["fail_rul_hours"])
    return simulator.generate(rul_cfg)


def _predict_rul(df: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    import joblib
    meta = json.loads((Path(cfg["paths"]["model_dir"]) / "rul_meta.json").read_text(encoding="utf-8"))
    feats = list(meta["features"])
    model = joblib.load(Path(cfg["paths"]["model_dir"]) / "rul_median.pkl")["model"]
    f = RF.build_features(df, group_col="episode_id", window_steps=8)
    ok = f[feats].notna().all(axis=1)
    f = f[ok].copy()
    f["pred_rul"] = model.predict(f[feats].astype(float))
    return f


def simulate(cfg: dict) -> dict:
    df = _gen_episodes(cfg)
    f = _predict_rul(df, cfg)
    warn = float(cfg["warn_rul_hours"]); fail = float(cfg["fail_rul_hours"])
    lead_target = float(cfg["lead_target_hours"])

    rows = []
    for (dev, ep), sub in f.groupby(["device_id", "episode_id"], observed=True):
        sub = sub.sort_values("time")
        t0 = pd.Timestamp(sub["time"].iloc[0]); t1 = pd.Timestamp(sub["time"].iloc[-1])
        duration = (t1 - t0).total_seconds() / 3600.0
        # 失效时刻: RUL 首次 <= fail 的节点, 否则末尾
        fl = sub[sub["rul_hours"] <= fail]
        fail_time = pd.Timestamp(fl["time"].iloc[0]) if len(fl) else t1
        trig = sub[sub["pred_rul"] <= warn]
        if len(trig) and pd.Timestamp(trig["time"].iloc[0]) < fail_time:
            trigger_time = pd.Timestamp(trig["time"].iloc[0])
            detected = True
            lead = (fail_time - trigger_time).total_seconds() / 3600.0
        else:
            detected = False
            lead = np.nan
        rows.append({"device_id": int(dev), "episode_id": ep, "duration_h": duration,
                     "detected": detected, "lead_hours": lead,
                     "unplanned_base": 1, "unplanned_prop": 0 if detected else 1})
    ep = pd.DataFrame(rows)

    n_base = int(ep["unplanned_base"].sum())
    n_prop = int(ep["unplanned_prop"].sum())
    total_h = float(ep["duration_h"].sum())
    mtbf_base = total_h / n_base if n_base else None
    mtbf_prop = total_h / n_prop if n_prop else None
    leads = ep.loc[ep["detected"], "lead_hours"].dropna()

    out = {
        "validation_mode": "simulation",
        "origin": "simulated",
        "policy": {"baseline": cfg["baseline_policy"], "proposed": cfg["proposed_policy"],
                   "warn_rul_hours": warn, "fail_rul_hours": fail, "lead_target_hours": lead_target},
        "episodes": int(len(ep)),
        "unplanned_stops": {"baseline": n_base, "proposed": n_prop,
                            "reduction_pct": round((1 - n_prop / n_base) * 100, 2) if n_base else None},
        "mtbf_hours": {"baseline": round(mtbf_base, 2) if mtbf_base else None,
                       "proposed": round(mtbf_prop, 2) if mtbf_prop else None,
                       "improvement_pct": round((mtbf_prop / mtbf_base - 1) * 100, 2)
                       if (mtbf_base and mtbf_prop) else None},
        "warning_lead_hours": {"mean": round(float(leads.mean()), 2) if len(leads) else None,
                               "median": round(float(leads.median()), 2) if len(leads) else None,
                               "p10": round(float(leads.quantile(0.10)), 2) if len(leads) else None,
                               "frac_ge_target": round(float((leads >= lead_target).mean()), 4) if len(leads) else None},
        "detection_rate": round(float(ep["detected"].mean()), 4),
    }
    return {"metrics": out, "detail": ep}


def run(config_path: str = "configs/maintenance_policy.yaml") -> dict:
    cfg = load_config(config_path)
    res = simulate(cfg)
    m = res["metrics"]
    mp = Path(cfg["paths"]["metrics"]); mp.parent.mkdir(parents=True, exist_ok=True)
    mp.write_text(json.dumps(m, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_report(Path(cfg["paths"]["report_dir"]) / "maintenance_report.md", m)
    return res


def _write_report(path: Path, m: dict) -> None:
    u = m["unplanned_stops"]; t = m["mtbf_hours"]; l = m["warning_lead_hours"]
    lines = ["# 阶段 A7 预测性维护仿真报告（三指标自证）", "",
             "> **声明**：本报告为**模拟退化闭环**结果（`validation_mode=simulation`），"
             "触发使用**模型预测 RUL**（非真值），不代表现场效果。", "",
             "## 1. 结论", "",
             f"- 非计划停机下降率：**{u['reduction_pct']}%**（目标 ≥50%）",
             f"- MTBF 提升率：**{t['improvement_pct']}%**（目标 ≥30%）",
             f"- 预警提前（中位）：**{l['median']} h**（目标 ≥24h）；≥24h 占比 {l['frac_ge_target']}", "",
             "## 2. 明细", "",
             "| 项 | 基线(反应式) | 策略(预测性) |", "|---|---|---|",
             f"| 非计划停机次数 | {u['baseline']} | {u['proposed']} |",
             f"| MTBF(h) | {t['baseline']} | {t['proposed']} |",
             f"| 预警提前(h) 均值/中位/P10 | - | {l['mean']} / {l['median']} / {l['p10']} |",
             f"| 检出率 | - | {m['detection_rate']} |", "",
             "## 3. 方法", "",
             "- 配对闭环：同批模拟 episode 上比较两种策略；",
             "- 基线=退化到失效才处理（非计划停机）；策略=预测 RUL≤阈值即提前计划停机；",
             "- 触发使用**模型预测 RUL**，降低自证嫌疑；", "",
             "## 4. 局限", "",
             "- 标签由模拟生成，属自证，可信度低于真实故障记录；",
             "- 不含真实维修工单/停机台账；结果用于方法演示与赛题合规申报。", ""]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser(description="预测性维护仿真")
    ap.add_argument("--config", default="configs/maintenance_policy.yaml")
    args = ap.parse_args()
    res = run(args.config)
    m = res["metrics"]
    print(f"[OK] 非计划停机下降={m['unplanned_stops']['reduction_pct']}% "
          f"MTBF提升={m['mtbf_hours']['improvement_pct']}% "
          f"预警提前中位={m['warning_lead_hours']['median']}h "
          f"检出率={m['detection_rate']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
