"""第三阶段: 因果(无未来信息)负荷预测器。

背景: 联合 MPC 原先使用 `demand[k..k+H]` (仿真未来真值+噪声) 作前瞻 -> **未来信息泄漏**。
本模块仅用**当前及历史可观测需求**构造多步前瞻预测, 不读取任何未来真值。

说明 (为何不复用 A 组预测器): A 组 `forecasting/service.py::forecast_json` 读取真实特征库
`data/processed/forecast_features.parquet` 的**最后一行**, 且时域固定为 15/30/45/60 分钟的单次推理;
它无法在合成闭环的任意时刻逐点驱动。故按约定回退为"基于当前及历史可见信息的因果预测"。

方法:
  - persistence : 未来 = 当前观测 (对阶跃响应最快, 对缓变略滞后)
  - ewma         : 未来 = 指数平滑水位
  - seasonal     : 未来 = 上一周期同相位 (周期=288 步=24h@5min)
  - ar           : AR(p) 在线最小二乘 + 递归多步

全部 causal: 只使用 t<=k 的观测。
"""
from __future__ import annotations

from collections import deque
from typing import Optional

import numpy as np


class CausalForecaster:
    def __init__(self, method: str = "persistence", period: int = 288,
                 ewma_alpha: float = 0.3, ar_order: int = 3, ar_refit: int = 48):
        self.method = method
        self.period = int(period)
        self.alpha = float(ewma_alpha)
        self.p = int(ar_order)
        self.ar_refit = int(ar_refit)
        self.hist: list[float] = []
        self._level: Optional[float] = None
        self._ar_coef: Optional[np.ndarray] = None
        self._since_fit = 10 ** 9

    # ---------------- 观测更新 ----------------
    def observe(self, value: float) -> None:
        v = float(value)
        self.hist.append(v)
        self._level = v if self._level is None else (1 - self.alpha) * self._level + self.alpha * v
        self._since_fit += 1

    def _fit_ar(self) -> None:
        n = len(self.hist)
        if n < self.p + 16:
            return
        x = np.asarray(self.hist, dtype=float)
        X = np.stack([x[self.p - i - 1: n - i - 1] for i in range(self.p)], axis=1)
        y = x[self.p:]
        A = np.hstack([X, np.ones((len(X), 1))])
        coef, *_ = np.linalg.lstsq(A, y, rcond=None)
        self._ar_coef = coef
        self._since_fit = 0

    # ---------------- 预测 ----------------
    def predict_future(self, h: int) -> list[float]:
        """预测 t+1 .. t+h (不含当前)。仅用 t<=k 的观测。"""
        if h <= 0:
            return []
        last = self.hist[-1] if self.hist else 0.0
        if self.method == "ewma":
            lv = self._level if self._level is not None else last
            return [float(lv)] * h
        if self.method == "seasonal":
            out = []
            n = len(self.hist)
            for i in range(1, h + 1):
                idx = n - self.period + i - 1
                out.append(float(self.hist[idx]) if 0 <= idx < n else float(last))
            return out
        if self.method == "ar":
            if self._since_fit >= self.ar_refit or self._ar_coef is None:
                self._fit_ar()
            if self._ar_coef is None:
                return [float(last)] * h
            coef = self._ar_coef
            buf = list(self.hist[-(self.p):])
            out = []
            for _ in range(h):
                x = np.asarray(buf[-self.p:], dtype=float)
                val = float(x @ coef[:self.p] + coef[self.p])
                out.append(val)
                buf.append(val)
            return out
        # persistence (默认)
        return [float(last)] * h

    def predict_with_nowcast(self, h: int, obs_now: float) -> list[float]:
        """[当前观测, t+1, ..., t+h-1] 共 h 个元素。"""
        return [float(obs_now)] + self.predict_future(h - 1)
