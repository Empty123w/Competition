"""用气负荷预测模型训练 (阶段 A4)。

- 三个独立直接预测模型(15/30/60 分钟), 避免递归误差累积;
- 严格按时间顺序切分(训练/验证/测试), 标准化与特征选择仅在训练集上进行(本实现树模型无需标准化);
- 首选 LightGBM, 不可用时回退 XGBoost / HistGradientBoosting;
- 置信区间来自验证集残差分位数。
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

from air_station.models.forecasting import baselines


def load_config(path: str) -> dict:
    import yaml
    return yaml.safe_load(Path(path).read_text(encoding="utf-8"))


def build_model(cfg: dict):
    m = cfg.get("model", {})
    mtype = m.get("type", "lightgbm")
    rs = m.get("random_state", 42)
    params = m.get("params", {})
    if mtype == "lightgbm":
        try:
            from lightgbm import LGBMRegressor
            return LGBMRegressor(random_state=rs, n_jobs=-1, verbose=-1, **params), "LightGBM"
        except Exception:
            mtype = m.get("fallback", "hist_gbdt")
    if mtype == "xgboost":
        try:
            from xgboost import XGBRegressor
            return XGBRegressor(random_state=rs, n_jobs=-1, **params), "XGBoost"
        except Exception:
            mtype = m.get("fallback", "hist_gbdt")
    from sklearn.ensemble import HistGradientBoostingRegressor
    hgb = {"max_iter": params.get("n_estimators", 300),
           "learning_rate": params.get("learning_rate", 0.05),
           "max_leaf_nodes": params.get("num_leaves", 31)}
    return HistGradientBoostingRegressor(random_state=rs, **hgb), "HistGradientBoosting"


def _split(df: pd.DataFrame, cfg: dict) -> dict[str, pd.DataFrame]:
    s = cfg["split"]
    t = df["time"]
    return {
        "train": df[t <= pd.Timestamp(s["train"]["end"])].reset_index(drop=True),
        "valid": df[(t > pd.Timestamp(s["train"]["end"])) &
                    (t <= pd.Timestamp(s["valid"]["end"]))].reset_index(drop=True),
        "test": df[t > pd.Timestamp(s["valid"]["end"])].reset_index(drop=True),
    }


def train(processed_dir: str = "data/processed",
          config_path: str = "configs/forecasting.yaml",
          model_dir: str = "artifacts/models") -> dict:
    import joblib
    cfg = load_config(config_path)
    feats = pd.read_parquet(Path(processed_dir) / "forecast_features.parquet")
    from air_station.models.forecasting import features as F
    fcols = F.feature_columns(feats, cfg.get("features", {}).get("exclude"))
    parts = _split(feats, cfg)
    total = len(feats)
    ratios = {k: round(len(v) / total, 4) for k, v in parts.items()}

    model_dir = Path(model_dir)
    model_dir.mkdir(parents=True, exist_ok=True)
    e = cfg.get("evaluate", {})
    q_lo, q_hi = e.get("ci_lower_quantile", 0.05), e.get("ci_upper_quantile", 0.95)
    horizons = cfg["forecast"]["horizon_minutes"]

    meta = {"trained_at": datetime.now().isoformat(timespec="seconds"),
            "model_type": None, "feature_cols": fcols,
            "data_split_ratios": ratios, "models": {}}
    for h in horizons:
        tr = parts["train"].copy()
        va = parts["valid"].copy()
        tr["y"] = tr["target"].shift(-h)
        va["y"] = va["target"].shift(-h)
        tr = tr.dropna(subset=["y"])
        va = va.dropna(subset=["y"])
        model, mtype = build_model(cfg)
        model.fit(tr[fcols], tr["y"])
        meta["model_type"] = mtype
        pred_va = model.predict(va[fcols])
        lo, hi = baselines.signed_residual_quantiles(va["y"].to_numpy(), pred_va, q_lo, q_hi)
        payload = {"model": model, "model_type": mtype, "horizon": h, "feature_cols": fcols,
                   "ci_lower": lo, "ci_upper": hi,
                   "trained_at": meta["trained_at"], "n_train": int(len(tr))}
        joblib.dump(payload, model_dir / f"forecast_{h}m.pkl")
        # 树模型额外保存原生文本
        if hasattr(model, "booster_"):
            try:
                model.booster_.save_model(str(model_dir / f"forecast_{h}m.txt"))
            except Exception:
                pass
        meta["models"][str(h)] = {"path": str(model_dir / f"forecast_{h}m.pkl"),
                                  "ci_lower": lo, "ci_upper": hi, "n_train": int(len(tr))}
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
    print(f"[OK] 模型={meta['model_type']} 时域={list(meta['models'])} "
          f"划分={meta['data_split_ratios']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
