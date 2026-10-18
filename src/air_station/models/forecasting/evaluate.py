"""用气负荷预测评估。"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from air_station.data import transformers as T
from air_station.models.forecasting import baselines, features as F


def mape(y_true: np.ndarray, y_pred: np.ndarray, eps: float = 1.0) -> float:
    y_true = np.asarray(y_true, dtype=float)
    y_pred = np.asarray(y_pred, dtype=float)
    return float(np.mean(np.abs(y_true - y_pred) / np.maximum(np.abs(y_true), eps)) * 100)


def _metrics(y_true, y_pred) -> dict:
    y_true = np.asarray(y_true, dtype=float)
    y_pred = np.asarray(y_pred, dtype=float)
    err = y_true - y_pred
    return {"mae": float(np.mean(np.abs(err))),
            "rmse": float(np.sqrt(np.mean(err ** 2))),
            "mape": mape(y_true, y_pred)}


def evaluate(processed_dir: str = "data/processed",
             config_path: str = "configs/forecasting.yaml",
             model_dir: str = "artifacts/models",
             report_dir: str = "artifacts/metrics") -> dict:
    import joblib

    config = yaml.safe_load(Path(config_path).read_text(encoding="utf-8"))
    station = T.load_processed(processed_dir, "station")
    df = F.build_features(station, config)
    fcols = F.feature_columns(df)
    split = config.get("split", {})
    parts = T.split_by_time(df, split["train_end"], split["valid_end"])
    test = parts["test"].reset_index(drop=True)

    results: dict = {"model": {}, "baselines": {}, "n_test": int(len(test))}

    # 模型逐时域评估
    for horizon in config.get("horizon_minutes", [15]):
        packed = joblib.load(Path(model_dir) / f"forecast_{horizon}.joblib")
        model = packed["model"]
        data = test.copy()
        data["y"] = data["target"].shift(-horizon)
        data = data.dropna(subset=fcols + ["y"])
        if len(data) == 0:
            continue
        pred = model.predict(data[fcols].astype("float32"))
        results["model"][str(horizon)] = _metrics(data["y"].values, pred)

    # 基线评估(以 15 分钟时域为例, 逐点对齐)
    h0 = config.get("horizon_minutes", [15])[0]
    data = test.copy()
    data["y"] = data["target"].shift(-h0)
    data["persistence"] = data["target"]
    data["seasonal_naive"] = data["target"].shift(1440 - h0)
    data["moving_average"] = data["target"].rolling(60, min_periods=1).mean()
    data = data.dropna(subset=["y", "persistence", "seasonal_naive", "moving_average"])
    y_true = data["y"].values
    for name in ("persistence", "seasonal_naive", "moving_average"):
        results["baselines"][name] = _metrics(y_true, data[name].values)

    out = Path(report_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "forecast_metrics.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    return results


def main() -> int:
    ap = argparse.ArgumentParser(description="评估用气负荷预测")
    ap.add_argument("--processed-dir", default="data/processed")
    ap.add_argument("--config", default="configs/forecasting.yaml")
    ap.add_argument("--model-dir", default="artifacts/models")
    ap.add_argument("--report-dir", default="artifacts/metrics")
    args = ap.parse_args()
    res = evaluate(args.processed_dir, args.config, args.model_dir, args.report_dir)
    for h, m in res["model"].items():
        print(f"  horizon={h}min MAPE={m['mape']:.2f}% MAE={m['mae']:.2f}")
    print(f"[OK] 评估完成 n_test={res['n_test']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
