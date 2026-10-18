"""用气负荷预测服务 (阶段 A4, A5 冻结接口)。

唯一对外入口: forecast_json()

{
  "status": "success",
  "model_type": "LightGBM",
  "prediction_base_time": "2026-09-12T00:00:00",
  "predictions": [
    {"horizon_minutes": 15, "target_time": "...", "predicted_flow": ...,
     "lower_bound": ..., "upper_bound": ...}, ...
  ],
  "elapsed_seconds": 1.54
}
"""
from __future__ import annotations

import time
from datetime import timedelta
from pathlib import Path

import numpy as np
import pandas as pd

_CACHE: dict = {}


def _check_features(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(f"[forecast] 特征文件缺失: {path} (先运行 data.pipeline --stage features)")
    df = pd.read_parquet(path)
    if df.empty:
        raise ValueError(f"[forecast] 特征文件为空: {path}")
    return df


def _load_model(model_dir: Path, horizon: int) -> dict:
    """加载模型, 缺文件/损坏/依赖缺失均抛出可定位异常。"""
    import joblib
    path = model_dir / f"forecast_{horizon}m.pkl"
    if not path.exists():
        raise FileNotFoundError(
            f"[forecast] 缺少时域 {horizon} 分钟的模型文件: {path} (先运行 models.forecasting.train)")
    try:
        return joblib.load(path)
    except Exception as e:  # noqa
        raise RuntimeError(
            f"[forecast] 模型文件无法加载或依赖缺失: {path}; 原因: {e}") from e


def forecast_json(processed_dir: str = "data/processed",
                  config_path: str = "configs/forecasting.yaml",
                  model_dir: str = "artifacts/models",
                  horizons: list[int] | None = None) -> dict:
    import yaml
    cfg_path = Path(config_path)
    if not cfg_path.exists():
        raise FileNotFoundError(f"[forecast] 配置文件缺失: {cfg_path}")
    cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    supported = list(cfg["forecast"]["horizon_minutes"])
    horizons = list(horizons) if horizons else list(supported)

    unsupported = [h for h in horizons if h not in supported]
    if unsupported:
        missing = [str(Path(model_dir) / f"forecast_{h}m.pkl") for h in unsupported]
        raise ValueError(
            f"[forecast] 不支持的预测时域 {unsupported}; 已训练时域={supported}; 缺失模型路径={missing}")

    feats = _check_features(Path(processed_dir) / "forecast_features.parquet")
    last = feats.iloc[[-1]]
    base_time = pd.Timestamp(last["time"].iloc[0])

    t0 = time.perf_counter()
    preds = []
    model_type = "LightGBM"
    for h in horizons:
        packed = _load_model(Path(model_dir), h)
        model, fcols = packed["model"], packed["feature_cols"]
        model_type = packed.get("model_type", model_type)
        try:
            p = float(model.predict(last[fcols])[0])
        except Exception as e:  # noqa
            raise RuntimeError(f"[forecast] 时域 {h} 推理失败: {e}") from e
        if not np.isfinite(p) or not np.isfinite(packed["ci_lower"]) or not np.isfinite(packed["ci_upper"]):
            raise RuntimeError(f"[forecast] 时域 {h} 预测值非有限数(NaN/Inf), 拒绝返回")
        preds.append({
            "horizon_minutes": int(h),
            "target_time": (base_time + timedelta(minutes=int(h))).isoformat(),
            "predicted_flow": round(p, 2),
            "lower_bound": round(p + packed["ci_lower"], 2),
            "upper_bound": round(p + packed["ci_upper"], 2),
        })
    elapsed = time.perf_counter() - t0
    return {"status": "success", "model_type": model_type,
            "prediction_base_time": base_time.isoformat(),
            "predictions": preds, "elapsed_seconds": round(elapsed, 3)}


if __name__ == "__main__":
    import json
    print(json.dumps(forecast_json(), ensure_ascii=False, indent=2, allow_nan=False))
