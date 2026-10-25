"""母管压力动态的机器学习模型 (第二阶段)。

两个用途:
  1. 真实 SCADA 一步预测对照 (与 B2 物理/线性模型比较);
  2. 为 MPC 提供**仿真环境模型**: 学习 p_{k+1}=p_k+dt*((q_sup-q_dem-q_blow)/K - leak*(p-p_env))
     的一步映射 (含噪声), 供滚动时域优化使用。

特征: p_k, q_sup, q_dem, (q_sup-q_dem), (p_k-p_env); 目标: Δp。
模型: LightGBM 回归 (非线性) 与 线性最小二乘 (对照)。
全部 validation_mode=simulation; 不修改 B2/B6 契约。
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np

DEFAULT_CFG = dict(k_vol=23700.0, leak=0.001, p_env=0.55, dt_min=5.0,
                   p_lo=0.40, p_hi=0.75)


def _step(p, q_sup, q_dem, q_blow, c) -> float:
    dp = c["dt_min"] * ((q_sup - q_dem - q_blow) / c["k_vol"] - c["leak"] * (p - c["p_env"]))
    return float(np.clip(p + dp, c["p_lo"], c["p_hi"]))


def make_dataset(n: int, seed: int, c: dict, noise_std: float = 0.002):
    """在约束范围内随机采样 (p, q_sup, q_dem) 生成一步转移样本。"""
    rng = np.random.default_rng(seed)
    p = rng.uniform(0.50, 0.72, n)
    q_sup = rng.uniform(0.0, 540.0, n)
    q_dem = rng.uniform(80.0, 560.0, n)
    q_blow = np.where(p > 0.663, np.clip(1500 * (p - 0.663), 0, q_sup), 0.0)
    p_next = np.array([_step(p[i], q_sup[i], q_dem[i], q_blow[i], c) for i in range(n)])
    p_next = p_next + rng.normal(0, noise_std, n)
    X = np.column_stack([p, q_sup, q_dem, q_sup - q_dem, p - c["p_env"]])
    y = p_next - p
    return X, y, p_next


@dataclass
class PressureModel:
    kind: str
    model: object
    c: dict
    metrics: dict

    def step(self, p, q_sup, q_dem, q_blow=0.0):
        x = np.column_stack([np.atleast_1d(p) + 0 * np.atleast_1d(q_sup),
                             np.atleast_1d(q_sup), np.atleast_1d(q_dem),
                             np.atleast_1d(q_sup) - np.atleast_1d(q_dem),
                             np.atleast_1d(p) - self.c["p_env"]])
        dp = np.asarray(self.model.predict(x), dtype=float)
        return np.clip(np.atleast_1d(p) + dp, self.c["p_lo"], self.c["p_hi"])


def train(kind: str = "lightgbm", c: dict = None, cfg_path: str = "configs/stage2_pressure.yaml",
          model_path: str = "artifacts/models/stage2/pressure_model.json") -> PressureModel:
    c = c or DEFAULT_CFG
    Xtr, ytr, _ = make_dataset(6000, 11, c)
    Xva, yva, _ = make_dataset(2000, 22, c)
    Xte, yte, _ = make_dataset(2000, 33, c)
    if kind == "lightgbm":
        import lightgbm as lgb
        m = lgb.LGBMRegressor(n_estimators=300, learning_rate=0.05, num_leaves=31,
                              random_state=42, n_jobs=2, verbose=-1)
    else:
        from sklearn.linear_model import LinearRegression
        m = LinearRegression()
    m.fit(Xtr, ytr)
    metrics = {}
    for nm, (X, y) in {"valid": (Xva, yva), "test": (Xte, yte)}.items():
        pred = m.predict(X)
        err = pred - y
        ss = float(np.sum(err ** 2)); st = float(np.sum((y - y.mean()) ** 2)) or 1.0
        metrics[nm] = {"MAE": round(float(np.mean(np.abs(err))), 6),
                       "RMSE": round(float(np.sqrt(np.mean(err ** 2))), 6),
                       "R2": round(1 - ss / st, 5)}
    pm = PressureModel(kind, m, c, metrics)
    Path(model_path).parent.mkdir(parents=True, exist_ok=True)
    Path(model_path).write_text(json.dumps({"kind": kind, "metrics": metrics,
                                            "validation_mode": "simulation"}, indent=2),
                                encoding="utf-8")
    return pm
