"""设备健康诊断服务 (阶段 A5)。

严格约束:
  - 直接读取 A3 的 health_features.parquet, 不重算特征;
  - 加载后第一步过滤 running == 1;
  - 阈值全部来自 configs/health_thresholds.yaml。

对外接口 JSON(字段名固定):
{
  "status": "success",
  "validation_mode": "unsupervised",
  "evaluation_time": "...",
  "devices": [
    {"device_id":"4#","health_score":92,"risk_level":"正常","anomaly_score":0.12,
     "top_anomaly_factor":"无","current_value":"N/A","normal_range":"N/A",
     "trend_direction":"平稳","suggestion":"维持当前运行工况"}, ...
  ]
}
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from air_station.models.health import anomaly, rules, scoring

_CATEGORY_OF = {
    "vib1_current": "vibration", "vib2_current": "vibration", "vib3_current": "vibration",
    "discharge_temp_c": "temperature", "oil_tank_c": "temperature",
    "coil_max_diff_c": "temperature",
    "unit_flow_power": "unit_flow_power",
    "oil_filter_dp": "pressure_drop", "air_filter_dp": "pressure_drop",
}


def needed_columns(thresholds: dict) -> set[str]:
    cols = {"time", "device_id", "running"}
    tw = thresholds.get("trend", {}).get("window", 15)
    for r in thresholds.get("expert_rules", []):
        cols.add(r["feature"])
        cols.add(f"trend{tw}_{r['feature']}")
    cols |= set(thresholds.get("anomaly", {}).get("dev_features", []))
    return cols


def _load_features(processed_dir: str, thresholds: dict) -> pd.DataFrame:
    """只读所需列并降为 float32, 加载后第一步过滤 running==1。"""
    path = Path(processed_dir) / "health_features.parquet"
    import pyarrow.parquet as pq
    avail = set(pq.read_schema(path).names)
    use = sorted(c for c in needed_columns(thresholds) if c in avail)
    df = pd.read_parquet(path, columns=use)
    for c in df.columns:
        if df[c].dtype == "float64":
            df[c] = df[c].astype("float32")
    df["time"] = pd.to_datetime(df["time"])
    return df[df["running"].astype(bool)].reset_index(drop=True)


def _trend_direction(row: pd.Series, feature: str, higher_is_worse: bool,
                     window: int, eps: float) -> str:
    col = f"trend{window}_{feature}"
    if col not in row.index or pd.isna(row[col]):
        return "平稳"
    slope = float(row[col])
    if slope > eps:
        return "恶化" if higher_is_worse else "好转"
    if slope < -eps:
        return "好转" if higher_is_worse else "恶化"
    return "平稳"


def _device_entry(number: int, row: pd.Series, thresholds: dict) -> dict:
    rules_cfg = thresholds.get("expert_rules", [])
    detail = rules.latest_rule_detail(row, rules_cfg)
    dev_features = thresholds.get("anomaly", {}).get("dev_features", [])
    dev_scale = thresholds.get("anomaly", {}).get("dev_scale", 3.0)
    top_dev, top_dev_val = anomaly.top_deviation(row, dev_features)
    anomaly_score = float(min(abs(top_dev_val) / dev_scale, 1.0)) if top_dev else 0.0

    worst = max(detail, key=lambda x: x["penalty"]) if detail else None
    rule_penalty = worst["penalty"] if worst else 0.0
    score = float(scoring.combine([rule_penalty], [anomaly_score], thresholds)[0])
    level = scoring.risk_level(score, thresholds)

    tw = thresholds.get("trend", {})
    sug = thresholds.get("suggestions", {})
    # 判定主因: 规则扣分优先, 否则 dev 偏离
    if worst and worst["penalty"] > 0:
        feature = worst["feature"]
        direction = _trend_direction(row, feature, worst["higher_is_worse"],
                                     tw.get("window", 15), tw.get("slope_epsilon", 0.02))
        factor = worst["label"] + ("连续上升" if direction == "恶化" else
                                   ("持续下降" if direction == "好转" else "偏离正常范围"))
        cur = f"{worst['value']:.2f} {worst['unit']}"
        rng = worst.get("normal_range")
        rng_s = f"{rng[0]} ~ {rng[1]} {worst['unit']}" if rng else "N/A"
        cat = _CATEGORY_OF.get(feature, "default")
        suggestion = sug.get(cat, sug.get("default", ""))
    elif top_dev and anomaly_score > 0.5:
        factor = top_dev.replace("dev_", "") + "相对同负荷正常值偏离"
        cur, rng_s, direction = "N/A", "N/A", "平稳"
        suggestion = sug.get("anomaly", sug.get("default", ""))
    else:
        factor, cur, rng_s, direction = "无", "N/A", "N/A", "平稳"
        suggestion = sug.get("default", "维持当前运行工况")

    return {
        "device_id": f"{number}#",
        "health_score": int(round(score)),
        "risk_level": level,
        "anomaly_score": round(anomaly_score, 2),
        "top_anomaly_factor": factor,
        "current_value": cur,
        "normal_range": rng_s,
        "trend_direction": direction,
        "suggestion": suggestion,
    }


def health_json(processed_dir: str = "data/processed",
                thresholds_path: str = rules.DEFAULT_THRESHOLDS) -> dict:
    thresholds = rules.load_thresholds(thresholds_path)
    df = _load_features(processed_dir, thresholds)
    devices = []
    eval_time = None
    for number, sub in df.sort_values(["device_id", "time"]).groupby("device_id", observed=True):
        row = sub.iloc[-1]
        eval_time = pd.Timestamp(row["time"]) if eval_time is None else max(eval_time, pd.Timestamp(row["time"]))
        devices.append(_device_entry(int(number), row, thresholds))
    return {
        "status": "success",
        "validation_mode": "unsupervised",
        "evaluation_time": eval_time.isoformat() if eval_time is not None else None,
        "devices": devices,
    }


if __name__ == "__main__":
    import json
    print(json.dumps(health_json(), ensure_ascii=False, indent=2))
