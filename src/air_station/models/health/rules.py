"""专家规则健康扣分 (阶段 A5)。

阈值全部来自 configs/health_thresholds.yaml, 代码不硬编码。
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

DEFAULT_THRESHOLDS = "configs/health_thresholds.yaml"


def load_thresholds(path: str | Path = DEFAULT_THRESHOLDS) -> dict:
    import yaml
    return yaml.safe_load(Path(path).read_text(encoding="utf-8"))


def penalty_array(values, warn: float, alarm: float, higher_is_worse: bool = True) -> np.ndarray:
    v = np.asarray(values, dtype=float)
    if higher_is_worse:
        span = (alarm - warn) if alarm != warn else 1.0
        p = (v - warn) / span
    else:
        span = (warn - alarm) if warn != alarm else 1.0
        p = (warn - v) / span
    return np.clip(p, 0.0, 1.0)


def rule_penalties(frame: pd.DataFrame, rules: list[dict]) -> pd.DataFrame:
    """返回每行的各规则扣分宽表, 列名为规则 label。"""
    out = pd.DataFrame(index=frame.index)
    for r in rules:
        col = r["feature"]
        if col not in frame.columns:
            continue
        out[r["label"]] = penalty_array(frame[col].to_numpy(),
                                        r["warn"], r["alarm"],
                                        r.get("higher_is_worse", True))
    return out


def latest_rule_detail(row: pd.Series, rules: list[dict]) -> list[dict]:
    """单行各规则详情(含当前值/正常范围/扣分)。"""
    detail = []
    for r in rules:
        col = r["feature"]
        if col not in row.index or pd.isna(row[col]):
            continue
        val = float(row[col])
        pen = float(penalty_array([val], r["warn"], r["alarm"],
                                  r.get("higher_is_worse", True))[0])
        detail.append({"feature": col, "label": r["label"], "unit": r["unit"],
                       "value": val, "penalty": pen,
                       "normal_range": r.get("normal_range"),
                       "higher_is_worse": r.get("higher_is_worse", True)})
    return detail
