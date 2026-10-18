"""用气负荷预测模型训练。"""
from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

import pandas as pd

from air_station.data import transformers as T
from air_station.models.forecasting import features as F

TIME_COL = "time"


def build_model(model_cfg: dict):
    mtype = model_cfg.get("type", "hist_gbdt")
    params = model_cfg.get("params", {})
    rs = model_cfg.get("random_state", 42)
    if mtype == "hist_gbdt":
        from sklearn.ensemble import HistGradientBoostingRegressor
        return HistGradientBoostingRegressor(random_state=rs, **params)
    if mtype == "ridge":
        from sklearn.linear_model import Ridge
        return Ridge(alpha=params.get("alpha", 1.0))
    raise ValueError(f"未知模型类型: {mtype}")


def make_supervised(df: pd.DataFrame, horizon: int, feature_cols: list[str]) -> tuple[pd.DataFrame, pd.Series]:
    """构造 (X, y): y 为向前 horizon 步的目标。"""
    data = df.copy()
    data["y"] = data["target"].shift(-horizon)
    data = data.dropna(subset=feature_cols + ["y"])
    X = data[feature_cols].astype("float32")
    y = data["y"].astype("float32")
    return X, y


def train(processed_dir: str = "data/processed",
          config_path: str = "configs/forecasting.yaml",
          model_dir: str = "artifacts/models") -> dict:
    import yaml
    config = yaml.safe_load(Path(config_path).read_text(encoding="utf-8"))
    target = config.get("target", "总流量")

    station = T.load_processed(processed_dir, "station")
    df = F.build_features(station, config)
    fcols = F.feature_columns(df)

    split = config.get("split", {})
    parts = T.split_by_time(df, split["train_end"], split["valid_end"])
    train_df = parts["train"]

    model_dir = Path(model_dir)
    model_dir.mkdir(parents=True, exist_ok=True)
    meta = {"trained_at": datetime.now().isoformat(timespec="seconds"),
            "target": target, "model_type": config["model"]["type"],
            "feature_cols": fcols, "models": {}}

    import joblib
    for horizon in config.get("horizon_minutes", [15]):
        X, y = make_supervised(train_df, horizon, fcols)
        model = build_model(config["model"])
        model.fit(X, y)
        path = model_dir / f"forecast_{horizon}.joblib"
        joblib.dump({"model": model, "feature_cols": fcols, "horizon": horizon}, path)
        meta["models"][str(horizon)] = {"path": str(path), "n_train": int(len(X))}

    (model_dir / "forecast_meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return meta


def main() -> int:
    ap = argparse.ArgumentParser(description="训练用气负荷预测模型")
    ap.add_argument("--processed-dir", default="data/processed")
    ap.add_argument("--config", default="configs/forecasting.yaml")
    ap.add_argument("--model-dir", default="artifacts/models")
    args = ap.parse_args()
    meta = train(args.processed_dir, args.config, args.model_dir)
    hs = list(meta["models"].keys())
    print(f"[OK] 训练完成, 模型类型={meta['model_type']} 时域={hs}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
