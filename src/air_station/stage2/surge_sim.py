"""独立高频喘振工程仿真 (第二阶段)。

目的: 在**无真实喘振标签/秒级数据**的条件下, 构建可复现的高频工程仿真,
      为喘振预警器 (准确率/精确率/召回/F1/误报/漏报/提前时间) 提供独立评估集。

关键独立性:
  - **真值机制**由仿真物理驱动: q_stab(p)=A+B*x+C*x^2 (二次式, 与 B4 线性代理 90+350x 形式/参数均不同),
    事件 = flow < q_stab(p) 连续 >= 3 s; 下降由随机深度 "负荷扰动" 触发;
  - **被评估预警器**只能使用带噪可测量 (flow/pressure/IGV/BOV/电流), **不得使用 q_stab**;
  - dev/valid/test 用不同种子基, 段间独立, 互不重叠。

标注: validation_mode=simulation, 非厂家真实喘振线, 不用于真实设备控制。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

DEFAULT_CONFIG = "configs/stage2_surge.yaml"


def load_config(path: str | Path = DEFAULT_CONFIG) -> dict:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"[surge_sim] 配置缺失: {p}")
    return yaml.safe_load(p.read_text(encoding="utf-8"))


def q_stab(p, truth: dict) -> np.ndarray:
    """真值稳定流量边界 (二次; 独立于 B4 线性代理)。"""
    x = np.asarray(p, dtype=float) - float(truth["p_ref_mpa"])
    return float(truth["A_m3min"]) + float(truth["B_m3min_per_mpa"]) * x + \
        float(truth["C_m3min_per_mpa2"]) * x * x


@dataclass
class SimSeries:
    scenario: str
    seed: int
    t: np.ndarray
    pressure: np.ndarray
    flow: np.ndarray
    igv: np.ndarray
    bov: np.ndarray
    current: np.ndarray
    q_stab: np.ndarray
    events: list = field(default_factory=list)

    def frame(self, noisy: bool = True) -> pd.DataFrame:
        """返回可测量通道 (预警器可见; 不含 q_stab)。"""
        return pd.DataFrame({
            "t_s": self.t,
            "pressure_mpa": self.pressure_noisy if noisy else self.pressure,
            "flow_m3min": self.flow_noisy if noisy else self.flow,
            "igv_percent": self.igv_noisy if noisy else self.igv,
            "bov_closed_percent": self.bov_noisy if noisy else self.bov,
            "motor_current_a": self.current_noisy if noisy else self.current,
        })


def detect_events(flow: np.ndarray, boundary: np.ndarray, hz: float,
                  min_len_s: float = 3.0, gap_s: float = 20.0) -> list[dict]:
    """事件 = flow < boundary 连续 >= min_len_s; 相邻间隔 < gap_s 合并。"""
    below = np.asarray(flow) < np.asarray(boundary)
    min_len = int(round(min_len_s * hz))
    gap = int(round(gap_s * hz))
    events = []
    i, n = 0, len(below)
    while i < n:
        if not below[i]:
            i += 1
            continue
        j = i
        while j + 1 < n and below[j + 1]:
            j += 1
        if (j - i + 1) >= min_len:
            events.append({"onset_s": round(i / hz, 3), "end_s": round(j / hz, 3),
                           "min_flow": float(np.min(flow[i:j + 1]))})
        i = j + 1
    # 合并近邻
    merged = []
    for e in events:
        if merged and (e["onset_s"] - merged[-1]["end_s"]) * hz < gap:
            merged[-1]["end_s"] = e["end_s"]
            merged[-1]["min_flow"] = min(merged[-1]["min_flow"], e["min_flow"])
        else:
            merged.append(dict(e))
    return merged


def _disturbance(n: int, hz: float, onsets: list[float], depth: list[float],
                 ramp_s: float, recover_s: float, pre_drift_s: float = 0.0,
                 pre_drift_depth: list[float] | None = None) -> np.ndarray:
    """构造叠加式负向扰动包络 (m3/min): 事件前缓降 + 突发下探 + 恢复。"""
    d = np.zeros(n)
    pre_drift_depth = pre_drift_depth or [0.0] * len(onsets)
    for onset, dep, dd in zip(onsets, depth, pre_drift_depth):
        i_onset = int(round(onset * hz))
        ir = max(1, int(round(ramp_s * hz)))
        ih = max(1, int(round(1.0 * hz)))
        ic = max(1, int(round(recover_s * hz)))
        sharp = np.concatenate([np.linspace(0, -dep, ir), -dep * np.ones(ih),
                                np.linspace(-dep, 0, ic)])
        if pre_drift_s > 0 and dd > 0:
            ip = max(1, int(round(pre_drift_s * hz)))
            decl = max(1, int(round(min(pre_drift_s * 0.25, 12.0) * hz)))
            hold = max(1, ip - decl)
            drift = np.concatenate([np.linspace(0.0, -dd, decl), -dd * np.ones(hold)])
            seg = np.concatenate([drift, sharp])
            i0 = i_onset - len(drift)
        else:
            seg = sharp
            i0 = i_onset
        i0 = max(0, i0)
        end = min(n, i0 + len(seg))
        d[i0:end] += seg[:end - i0]
    return d


def simulate_scenario(scenario: str, cfg: dict, seed: int) -> SimSeries:
    sc = cfg["scenarios"][scenario]
    hz = float(cfg["sampling"]["hz"])
    n = int(cfg["sampling"]["nominal_steps"])
    truth = cfg["truth"]
    s = cfg["sensors"]
    rng = np.random.default_rng(seed)
    noise_mult = float(sc.get("noise_mult", 1.0))

    k = np.arange(n)
    pressure = 0.62 + 0.02 * np.sin(2 * np.pi * k / (n / 1.5)) - 0.01 * (k / n)
    pressure = np.clip(pressure, 0.55, 0.70)
    bound = q_stab(pressure, truth)

    mf = float(sc["target_margin_frac"])
    flow_base = bound * (1.0 + mf) + rng.normal(0, 1.2, n)

    events_planned = int(sc.get("n_events", 0))
    onsets, depths, drift_depths = [], [], []
    if events_planned > 0:
        span_lo, span_hi = 0.20 * n, 0.88 * n
        base = np.linspace(span_lo, span_hi, events_planned)
        for b in base:
            o = float(np.clip(b + rng.normal(0, 0.02 * n), 1.0, n - 3.0 * hz * 30))
            onsets.append(o)
            i0 = int(round(o))
            gap_now = float(flow_base[i0] - bound[i0])
            dd = float(truth["disturbance"]["pre_drift_frac"]) * max(gap_now, 4.0)
            # 突发扰动使最低流量下探到边界以下 below m3/min, 保证清晰越界
            below = rng.uniform(0.10, 0.30) * max(float(bound[i0]), 20.0)
            drift_depths.append(max(2.0, dd))
            depths.append(max(3.0, (gap_now - dd) + below))
    ramp = float(truth["disturbance"]["onset_ramp_s"])
    rec = float(truth["disturbance"]["recover_s"])
    pre_drift = float(truth["disturbance"].get("pre_drift_s", 0.0))
    if sc["kind"] == "rapid":
        ramp, rec, pre_drift = 2.0, 6.0, 0.0
    dist = _disturbance(n, hz, onsets, depths, ramp, rec, pre_drift, drift_depths)
    jit = rng.normal(0, float(truth["disturbance"]["jitter_std_m3min"]), n)
    flow = np.clip(flow_base + dist + jit, 0.0, 300.0)

    rated = 268.9
    lam = np.clip(flow / rated, 0.02, 1.2)
    igv = np.clip(28.0 + 70.0 * lam, 0.0, 100.0)
    open_frac = np.clip((bound - flow) / np.maximum(bound, 1.0), 0.0, 1.0)
    bov_closed = np.clip(100.0 - 55.0 * open_frac, 0.0, 100.0)
    current = 40.0 + 2.6 * flow

    pressure_noisy = pressure + rng.normal(0, s["pressure_noise_std_mpa"] * noise_mult, n)
    flow_noisy = flow + rng.normal(0, s["flow_noise_std_m3min"] * noise_mult, n)
    igv_noisy = igv + rng.normal(0, s["igv_noise_std_percent"] * noise_mult, n)
    bov_noisy = bov_closed + rng.normal(0, s["bov_noise_std_percent"] * noise_mult, n)
    current_noisy = current + rng.normal(0, s["current_noise_std_a"] * noise_mult, n)

    events = detect_events(flow, bound, hz, 3.0, float(truth["min_event_gap_s"]))
    obj = SimSeries(scenario, seed, k / hz, pressure, flow, igv, bov_closed, current,
                    bound, events)
    obj.pressure_noisy = pressure_noisy
    obj.flow_noisy = flow_noisy
    obj.igv_noisy = igv_noisy
    obj.bov_noisy = bov_noisy
    obj.current_noisy = current_noisy
    return obj


def build_split(cfg: dict, segment: str) -> list[SimSeries]:
    """按 split 配置生成一段独立数据 (dev/valid/test)。"""
    sp = cfg["split"][segment]
    base = int(sp["seed_base"])
    rep = int(sp["rep"])
    out = []
    for i, scen in enumerate(cfg["scenarios"].keys()):
        for r in range(rep):
            out.append(simulate_scenario(scen, cfg, base + i * 100 + r))
    return out
