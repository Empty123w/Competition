"""RUL 服务接口 (阶段 A5-RUL)。

唯一入口: rul_json()

{
  "status": "success",
  "validation_mode": "simulation",
  "evaluation_time": "2026-...Z",
  "devices": [
    {"device_id":"4#","estimated_rul_hours":..,"lower_bound_hours":..,
     "upper_bound_hours":..,"method":"GradientBoostingRegressor"}, ...
  ]
}
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from air_station.models.rul import features as F
from air_station.models.rul import simulator

MODEL_TYPE = "GradientBoostingRegressor"


def _to_utc_z(ts) -> str:
    from zoneinfo import ZoneInfo
    t = pd.Timestamp(ts)
    if t.tzinfo is None:
        t = t.tz_localize("Asia/Shanghai")
    return t.tz_convert("UTC").strftime("%Y-%m-%dT%H:%M:%SZ")


def _load(model_dir: Path, name: str):
    import joblib
    path = model_dir / f"rul_{name}.pkl"
    if not path.exists():
        raise FileNotFoundError(f"[rul] 缺少模型文件: {path} (先运行 models.rul.train)")
    try:
        return joblib.load(path)
    except Exception as e:  # noqa
        raise RuntimeError(f"[rul] 模型文件无法加载: {path}; 原因: {e}") from e


def rul_json(config_path: str = "configs/rul.yaml",
             sequences_path: str = "data/simulated/rul_sequences.parquet",
             model_dir: str = "artifacts/models") -> dict:
    cfg = simulator.load_config(config_path)
    sp = Path(sequences_path)
    if not sp.exists():
        raise FileNotFoundError(f"[rul] 缺少模拟序列文件: {sp} (先运行 models.rul.simulator)")
    df = pd.read_parquet(sp)
    df["time"] = pd.to_datetime(df["time"])
    df = F.build_features(df, group_col="episode_id",
                          window_steps=cfg["baseline"]["window_steps"])

    models = {n: _load(Path(model_dir), n) for n in ("median", "lower", "upper")}
    fcols = F.FEATURES
    devices = []
    eval_time = None
    for dev, sub in df.sort_values(["device_id", "time"]).groupby("device_id", observed=True):
        row = sub.iloc[[-1]]
        ts = pd.Timestamp(row["time"].iloc[0])
        eval_time = ts if eval_time is None else max(eval_time, ts)
        if row[fcols].isna().any(axis=1).iloc[0]:
            raise ValueError(f"[rul] 设备 {int(dev)}# 最新特征含缺失, 无法估计")
        med = float(models["median"]["model"].predict(row[fcols])[0])
        lo = float(models["lower"]["model"].predict(row[fcols])[0])
        hi = float(models["upper"]["model"].predict(row[fcols])[0])
        lo, hi = min(lo, med, hi), max(lo, med, hi)
        if not (np.isfinite(lo) and np.isfinite(med) and np.isfinite(hi)):
            raise RuntimeError(f"[rul] 设备 {int(dev)}# 预测值非有限数")
        lo, med, hi = max(lo, 0.0), max(med, 0.0), max(hi, 0.0)
        devices.append({
            "device_id": f"{int(dev)}#",
            "estimated_rul_hours": round(med, 1),
            "lower_bound_hours": round(lo, 1),
            "upper_bound_hours": round(hi, 1),
            "method": MODEL_TYPE,
        })
    devices.sort(key=lambda d: d["device_id"])
    return {
        "status": "success",
        "validation_mode": "simulation",
        "evaluation_time": _to_utc_z(eval_time) if eval_time is not None else None,
        "devices": devices,
    }


if __name__ == "__main__":
    import json
    print(json.dumps(rul_json(), ensure_ascii=False, indent=2, allow_nan=False))
