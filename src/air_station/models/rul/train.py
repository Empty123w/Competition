"""RUL 模型训练: 三个 GradientBoostingRegressor(分位数 0.5/0.1/0.9)。

按 episode_id 划分 train/val/test, 保证同一退化序列不跨集合(防泄漏)。
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from air_station.models.rul import features as F
from air_station.models.rul import simulator


def load_config(path: str = "configs/rul.yaml") -> dict:
    return simulator.load_config(path)


def split_episodes(episodes: list[str], cfg: dict) -> dict[str, list[str]]:
    rng = np.random.RandomState(int(cfg["seed"]))
    eps = list(episodes)
    rng.shuffle(eps)
    n = len(eps)
    n_tr = int(round(n * cfg["split"]["train"]))
    n_va = int(round(n * cfg["split"]["val"]))
    return {"train": eps[:n_tr], "val": eps[n_tr:n_tr + n_va], "test": eps[n_tr + n_va:]}


def build_model(cfg: dict, alpha: float):
    from sklearn.ensemble import GradientBoostingRegressor
    p = dict(cfg["model"]["params"])
    return GradientBoostingRegressor(loss="quantile", alpha=alpha,
                                     random_state=cfg["model"].get("random_state", 42), **p)


def train(config_path: str = "configs/rul.yaml",
          seq_path: str = "data/simulated/rul_sequences.parquet",
          model_dir: str = "artifacts/models") -> dict:
    import joblib
    cfg = load_config(config_path)
    if not Path(seq_path).exists():
        df = simulator.generate_and_save(cfg, seq_path)
    else:
        df = pd.read_parquet(seq_path)
    df["time"] = pd.to_datetime(df["time"])
    df = F.build_features(df, group_col="episode_id",
                          window_steps=cfg["baseline"]["window_steps"])
    splits = split_episodes(sorted(df["episode_id"].unique()), cfg)
    tr = df[df["episode_id"].isin(splits["train"])].dropna(subset=F.FEATURES + ["rul_hours"])
    Xtr, ytr = F.feature_matrix(tr), tr["rul_hours"].to_numpy()

    model_dir = Path(model_dir)
    model_dir.mkdir(parents=True, exist_ok=True)
    meta = {"model_type": cfg["model"]["type"], "features": F.FEATURES,
            "n_train": int(len(tr)), "episodes": {k: len(v) for k, v in splits.items()}}
    for name, alpha in (("median", 0.5), ("lower", 0.1), ("upper", 0.9)):
        m = build_model(cfg, alpha)
        m.fit(Xtr, ytr)
        joblib.dump({"model": m, "features": F.FEATURES, "alpha": alpha,
                     "model_type": cfg["model"]["type"]}, model_dir / f"rul_{name}.pkl")
    # 供评估复用的划分
    (model_dir / "rul_split.json").write_text(
        json.dumps(splits, ensure_ascii=False, indent=2), encoding="utf-8")
    (model_dir / "rul_meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return meta


def main() -> int:
    ap = argparse.ArgumentParser(description="训练 RUL 模型")
    ap.add_argument("--config", default="configs/rul.yaml")
    ap.add_argument("--sequences", default="data/simulated/rul_sequences.parquet")
    ap.add_argument("--model-dir", default="artifacts/models")
    args = ap.parse_args()
    meta = train(args.config, args.sequences, args.model_dir)
    print(f"[OK] RUL 模型训练完成 类型={meta['model_type']} 训练样本={meta['n_train']} "
          f"episodes(train/val/test)={[meta['episodes'][k] for k in ('train','val','test')]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
