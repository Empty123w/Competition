"""数据校验与质量报告。"""
from __future__ import annotations

import numpy as np
import pandas as pd

TIME_COL = "time"


def alignment_report(tables: dict[str, pd.DataFrame]) -> dict:
    """检查各表时间戳是否逐行对齐、采样频率是否一致。"""
    names = [n for n, df in tables.items() if TIME_COL in df.columns]
    if not names:
        return {"aligned": False, "reason": "无时间列"}
    base = tables[names[0]][TIME_COL]
    rows: dict[str, dict] = {}
    all_aligned = True
    for n in names:
        t = tables[n][TIME_COL]
        aligned = len(t) == len(base) and bool((t.reset_index(drop=True) == base.reset_index(drop=True)).all())
        all_aligned &= aligned
        steps = t.diff().dt.total_seconds().dropna()
        rows[n] = {
            "rows": int(len(t)),
            "aligned": aligned,
            "duplicated_ts": int(t.duplicated().sum()),
            "freq_mode_s": float(steps.mode().iloc[0]) if not steps.empty else None,
        }
    return {
        "aligned": all_aligned,
        "start": str(base.min()),
        "end": str(base.max()),
        "tables": rows,
    }


def missing_report(df: pd.DataFrame) -> dict:
    miss = df.isna().sum()
    miss = miss[miss > 0]
    return {str(c): {"missing": int(v), "ratio": round(float(v) / len(df), 5)} for c, v in miss.items()}


def constant_columns(df: pd.DataFrame) -> list[str]:
    out = []
    for c in df.columns:
        if c == TIME_COL:
            continue
        if df[c].nunique(dropna=True) <= 1:
            out.append(str(c))
    return out


def outlier_report(df: pd.DataFrame, iqr_k: float = 1.5) -> dict:
    """IQR 异常占比(注意: 开停机双峰会抬高占比)。"""
    out: dict[str, dict] = {}
    for c in df.select_dtypes(include=[np.number]).columns:
        x = df[c].dropna()
        if len(x) < 8:
            continue
        q1, q3 = x.quantile(0.25), x.quantile(0.75)
        iqr = q3 - q1
        if iqr == 0:
            continue
        lo, hi = q1 - iqr_k * iqr, q3 + iqr_k * iqr
        ratio = float(((x < lo) | (x > hi)).mean())
        if ratio > 0:
            out[str(c)] = {"outlier_ratio": round(ratio, 5), "lower": float(lo), "upper": float(hi)}
    return out


def quality_report(tables: dict[str, pd.DataFrame]) -> dict:
    """汇总质量报告。"""
    report: dict = {"alignment": alignment_report(tables), "tables": {}}
    for n, df in tables.items():
        report["tables"][n] = {
            "shape": [int(df.shape[0]), int(df.shape[1])],
            "duplicated_rows": int(df.duplicated().sum()),
            "missing": missing_report(df),
            "constant_columns": constant_columns(df),
        }
    return report


def assert_aligned(tables: dict[str, pd.DataFrame]) -> None:
    rep = alignment_report(tables)
    if not rep.get("aligned"):
        raise ValueError(f"时序表未对齐: {rep}")
