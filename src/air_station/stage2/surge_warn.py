"""喘振预警模型与评估 (第二阶段)。

独立评估: 预警器只看带噪可测量通道 (flow/pressure/IGV/BOV/电流) 与 B4 代理边界;
真值来自 surge_sim 的独立二次边界 + 随机扰动事件。

模型族:
  - lightgbm : 手工特征 + GBDT 二分类 (低开销, 首选筛查)
  - gru/lstm : torch 序列模型 (可用时)
  - ensemble : 概率平均
  - b4_proxy : 现有 B4 代理规则 (对照基线)

指标: 时间级 Accuracy/Precision/Recall/F1/FPR/FNR; 事件级 recall/误报率/提前时间(>=30s)。
全部 validation_mode=simulation。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

RATED = 268.9


# ------------------------------------------------------------------ #
# 特征
# ------------------------------------------------------------------ #
def b4_boundary_flow(pressure, ref_p=0.66, ref_q=90.0, slope=350.0):
    return ref_q + slope * (np.asarray(pressure, dtype=float) - ref_p)


def window_features(df: pd.DataFrame, hz: float, window_s: int = 45) -> pd.DataFrame:
    """从可测量通道构造逐秒特征 (含趋势/滚动统计/代理裕度)。"""
    W = max(3, int(round(window_s * hz)))
    f = df["flow_m3min"].to_numpy(dtype=float)
    p = df["pressure_mpa"].to_numpy(dtype=float)
    igv = df["igv_percent"].to_numpy(dtype=float)
    bov = df["bov_closed_percent"].to_numpy(dtype=float)
    cur = df["motor_current_a"].to_numpy(dtype=float)
    n = len(f)
    qb4 = b4_boundary_flow(p)
    margin = (f - qb4) / np.maximum(qb4, 1.0)

    def lag(x, s):
        L = int(round(s * hz))
        out = np.full(n, np.nan)
        if L < n:
            out[L:] = x[:-L] if L > 0 else x
        out = np.where(np.isnan(out), x, out)
        return out

    def roll(x, fn):
        s = pd.Series(x)
        return fn(s.rolling(W, min_periods=1)).to_numpy()

    # 趋势斜率特征 (每采样点): 对回看窗做一次线性拟合的斜率
    def slope(x, s_window):
        L = max(3, int(round(s_window * hz)))
        x = np.asarray(x, dtype=float)
        n = len(x)
        out = np.zeros(n)
        for i in range(n):
            lo = max(0, i - L + 1)
            seg = x[lo:i + 1]
            if len(seg) < 3:
                continue
            k = np.arange(len(seg), dtype=float)
            kc = k - k.mean()
            out[i] = float((kc * (seg - seg.mean())).sum() / max((kc ** 2).sum(), 1e-9))
        return out

    feat = {
        "flow": f, "pressure": p, "igv": igv, "bov": bov, "current": cur,
        "margin": margin,
        "d_flow_5": f - lag(f, 5), "d_flow_15": f - lag(f, 15), "d_flow_30": f - lag(f, 30),
        "d_press_15": p - lag(p, 15),
        "flow_roll_min": roll(f, lambda r: r.min()), "flow_roll_mean": roll(f, lambda r: r.mean()),
        "flow_roll_std": roll(f, lambda r: r.std(ddof=0)),
        "margin_roll_min": roll(margin, lambda r: r.min()),
        "igv_roll_min": roll(igv, lambda r: r.min()),
        "bov_roll_min": roll(bov, lambda r: r.min()),
        "flow_lag5": lag(f, 5), "flow_lag15": lag(f, 15),
        "flow_slope_40": slope(f, 40), "flow_slope_20": slope(f, 20),
        "margin_slope_40": slope(margin, 40),
        "flow_range_40": roll(f, lambda r: r.max()) - roll(f, lambda r: r.min()),
        "margin_roll_mean": roll(margin, lambda r: r.mean()),
        "margin_drop_30": margin - lag(margin, 30),
        "margin_roll_min_60": pd.Series(margin).rolling(max(3, int(60 * hz)), min_periods=1).min().to_numpy(),
        "flow_pct_60": (f - lag(f, 60)) / np.maximum(np.abs(lag(f, 60)), 1.0),
        "low_margin_frac_45": pd.Series((margin < 0.08).astype(float)).rolling(
            W, min_periods=1).mean().to_numpy(),
        "flow_slope_15": slope(f, 15),
    }
    return pd.DataFrame(feat)


FEATURE_COLS = ["flow", "pressure", "igv", "bov", "current", "margin",
                "d_flow_5", "d_flow_15", "d_flow_30", "d_press_15",
                "flow_roll_min", "flow_roll_mean", "flow_roll_std",
                "margin_roll_min", "igv_roll_min", "bov_roll_min",
                "flow_lag5", "flow_lag15",
                "flow_slope_40", "flow_slope_20", "margin_slope_40",
                "flow_range_40", "margin_roll_mean",
                "margin_drop_30", "margin_roll_min_60", "flow_pct_60",
                "low_margin_frac_45", "flow_slope_15"]


def hazard_label(t_s: np.ndarray, events: list, horizon_s: float) -> np.ndarray:
    """y(t)=1 若 (t, t+horizon_s] 内出现事件起点。"""
    y = np.zeros(len(t_s), dtype=int)
    for e in events:
        o = e["onset_s"]
        lo, hi = o - horizon_s, o
        y[(t_s > lo + 1e-9) & (t_s <= hi + 1e-9)] = 1
    return y


# ------------------------------------------------------------------ #
# 数据集装配
# ------------------------------------------------------------------ #
@dataclass
class SurgeDataset:
    X: np.ndarray
    y: np.ndarray
    series: list
    events: list


def assemble(series_list: list, hz: float, window_s: int, horizon_s: float) -> SurgeDataset:
    Xs, ys, meta = [], [], []
    all_events = []
    for si, s in enumerate(series_list):
        df = s.frame(noisy=True)
        feats = window_features(df, hz, window_s)
        y = hazard_label(s.t, s.events, horizon_s)
        Xs.append(feats[FEATURE_COLS].to_numpy(dtype=float))
        ys.append(y)
        all_events.append((si, s))
    X = np.vstack(Xs)
    y = np.concatenate(ys)
    X = np.nan_to_num(X, nan=0.0, posinf=0.0, neginf=0.0)
    return SurgeDataset(X, y, series_list, all_events)


# ------------------------------------------------------------------ #
# 时间级 / 事件级指标
# ------------------------------------------------------------------ #
def time_level_metrics(y_true, y_pred) -> dict:
    y_true = np.asarray(y_true); y_pred = np.asarray(y_pred).astype(bool)
    tp = int(np.sum((y_true == 1) & y_pred))
    fp = int(np.sum((y_true == 0) & y_pred))
    fn = int(np.sum((y_true == 1) & (~y_pred)))
    tn = int(np.sum((y_true == 0) & (~y_pred)))
    prec = tp / (tp + fp) if (tp + fp) else None
    rec = tp / (tp + fn) if (tp + fn) else None
    f1 = (2 * prec * rec / (prec + rec)) if (prec and rec) else None
    acc = (tp + tn) / max(1, (tp + fp + fn + tn))
    fpr = fp / (fp + tn) if (fp + tn) else None
    fnr = fn / (fn + tp) if (fn + tp) else None
    return {"TP": tp, "FP": fp, "FN": fn, "TN": tn,
            "accuracy": round(acc, 4), "precision": _r(prec), "recall": _r(rec), "f1": _r(f1),
            "fpr": _r(fpr), "fnr": _r(fnr)}


def _r(x):
    return None if x is None else round(float(x), 4)


def alarm_runs(pred: np.ndarray, hz: float, min_len_s: float = 0.0) -> list[tuple[float, float]]:
    pred = np.asarray(pred).astype(bool)
    runs = []
    i, n = 0, len(pred)
    while i < n:
        if not pred[i]:
            i += 1
            continue
        j = i
        while j + 1 < n and pred[j + 1]:
            j += 1
        if (j - i + 1) >= int(round(min_len_s * hz)):
            runs.append((i / hz, j / hz))
        i = j + 1
    return runs


def event_level_metrics(events: list, pred: np.ndarray, hz: float,
                        match_window_s: float = 120.0, lead_target_s: float = 30.0,
                        total_hours: float = 0.0, min_alarm_s: float = 0.0) -> dict:
    runs = alarm_runs(pred, hz, min_alarm_s)
    det, leads = 0, []
    for e in events:
        o = e["onset_s"]
        lo = o - match_window_s
        best = None
        for (a, b) in runs:
            if b >= lo and a <= o:            # 报警段与匹配窗相交且不晚于事件起点
                if best is None or a < best:
                    best = a
        if best is not None:
            det += 1
            leads.append(round(o - best, 3))
    # 误报: 报警段起点不在任何事件匹配窗内
    fa = 0
    for (a, _b) in runs:
        in_any = any((e["onset_s"] - match_window_s) <= a <= e["onset_s"] for e in events)
        if not in_any:
            fa += 1
    n_ev = len(events)
    lead_ge30 = sum(1 for x in leads if x >= lead_target_s)
    matched = len(runs) - fa
    return {
        "events": n_ev, "detected": det,
        "recall_event_level": _r(det / n_ev) if n_ev else None,
        "lead_s": leads,
        "lead_median_s": _r(np.median(leads)) if leads else None,
        "lead_min_s": _r(np.min(leads)) if leads else None,
        "lead_ge30_coverage": _r(lead_ge30 / n_ev) if n_ev else None,
        "false_alarms": fa,
        "false_alarms_per_hour": _r(fa / total_hours) if total_hours > 0 else None,
        "n_alarm_runs": len(runs),
        "true_warnings": matched,
        "precision_event_level": _r(matched / len(runs)) if runs else None,
        "fnr_event_level": _r((n_ev - lead_ge30) / n_ev) if n_ev else None,
    }
