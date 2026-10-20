"""B4 喘振代理边界 (任务书 V1.1 §8.1)。

两级边界:
  1. 历史运行包络 (historical_envelope): 按机组/压力分箱的低分位流量,
     仅表示"历史少见的运行点", **不是**真实喘振线;
  2. 机理/工程假设 (engineering_assumed): 随压力单调上升的示意边界,
     缺厂家资料时的保守替代。

边界产物存 `artifacts/models/surge_boundary.json`, 带版本与来源。
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from air_station.optimization.contracts import normalize_device_id

DEFAULT_CONFIG = "configs/safety.yaml"
DEFAULT_BOUNDARY = "artifacts/models/surge_boundary.json"


def load_config(path: str | Path = DEFAULT_CONFIG) -> dict:
    import yaml
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"[surge] 安全配置缺失: {p}")
    return yaml.safe_load(p.read_text(encoding="utf-8"))


def engineering_boundary_flow(pressure_mpa: float, cfg: dict) -> tuple[float, str]:
    """工程假设边界流量: q = q_ref + slope*(p - p_ref), 随压力单调上升。"""
    eng = cfg["boundary"]["engineering"]
    q = float(eng["reference_flow_m3min"]) + float(eng["slope_m3min_per_mpa"]) * (
        float(pressure_mpa) - float(eng["reference_pressure_mpa"]))
    return max(q, 0.0), eng.get("source", "engineering")


def _pressure_in_domain(pressure_mpa: float, cfg: dict) -> bool:
    lo, hi = cfg["boundary"]["engineering"].get("pressure_domain_mpa", [0.45, 0.75])
    return (pressure_mpa is not None) and (lo <= float(pressure_mpa) <= hi)


def build_boundaries(processed_dir: str | Path = "data/processed",
                     config_path: str | Path = DEFAULT_CONFIG,
                     out_path: str | Path = DEFAULT_BOUNDARY) -> dict:
    """从历史数据构建 per-device 压力分箱低分位包络, 保存工件。"""
    cfg = load_config(config_path)
    hist = cfg["boundary"]["historical"]
    base = Path(processed_dir)
    dev_path = base / "device_timeseries.parquet"
    if not dev_path.exists():
        raise FileNotFoundError(f"[surge] 设备数据缺失: {dev_path}")
    df = pd.read_parquet(dev_path, columns=[
        "time", "device_id", "running", "flow_m3_min", "discharge_pressure_mpa", "quality_valid"])
    df["device_id"] = df["device_id"].map(normalize_device_id)
    df = df[df["running"].astype(bool) & (df["flow_m3_min"] > 0)
            & df["quality_valid"].astype(bool) & (df["discharge_pressure_mpa"] > 0)]
    bins = hist.get("bins_mpa", [0.0, 0.55, 0.58, 0.60, 0.62, 0.65, 1.0])
    q = float(hist.get("low_percentile", 0.02))
    min_n = int(hist.get("min_bin_samples", 500))
    devices = {}
    for did, sub in df.groupby("device_id"):
        sub = sub.assign(bin=pd.cut(sub["discharge_pressure_mpa"], bins))
        knots = []
        for b, g in sub.groupby("bin", observed=True):
            if len(g) < min_n:
                continue
            p_center = float(g["discharge_pressure_mpa"].mean())
            knots.append({"pressure_mpa": round(p_center, 4),
                          "flow_m3min": round(float(g["flow_m3_min"].quantile(q)), 2),
                          "n": int(len(g))})
        knots.sort(key=lambda k: k["pressure_mpa"])
        devices[did] = {"knots": knots, "low_percentile": q, "min_bin_samples": min_n}
    artifact = {
        "boundary_version": cfg["meta"]["boundary_version"],
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "validation_mode": "simulation",
        "source": hist.get("source", "inferred"),
        "note": "历史少见运行点包络, 非真实喘振线",
        "devices": devices,
    }
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    Path(out_path).write_text(json.dumps(artifact, ensure_ascii=False, indent=2), encoding="utf-8")
    return artifact


def load_boundary_artifact(path: str | Path = DEFAULT_BOUNDARY) -> dict:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"[surge] 边界工件缺失: {p} (先运行 build_boundaries)")
    return json.loads(p.read_text(encoding="utf-8"))


def historical_envelope_flow(device_id: str, pressure_mpa: float, artifact: dict):
    """历史包络流量 (线性插值); 无可用结点返回 None。"""
    dev = (artifact or {}).get("devices", {}).get(normalize_device_id(device_id))
    if not dev or not dev.get("knots"):
        return None
    pts = sorted(dev["knots"], key=lambda k: k["pressure_mpa"])
    xs = [k["pressure_mpa"] for k in pts]
    ys = [k["flow_m3min"] for k in pts]
    if pressure_mpa <= xs[0]:
        return float(ys[0])
    if pressure_mpa >= xs[-1]:
        return float(ys[-1])
    return float(np.interp(float(pressure_mpa), xs, ys))


def boundary_flow(device_id: str, pressure_mpa: float, cfg: dict,
                  artifact: dict, source: str | None = None):
    """返回 (边界流量, 来源名) 或 (None, None)。"""
    src = source or cfg["boundary"].get("source", "engineering_assumed")
    if src == "historical_envelope":
        q = historical_envelope_flow(device_id, pressure_mpa, artifact)
        return (q, "historical_envelope") if q is not None else (None, None)
    q, s = engineering_boundary_flow(pressure_mpa, cfg)
    return q, s


if __name__ == "__main__":
    art = build_boundaries()
    print(json.dumps({d: [k["pressure_mpa"] for k in v["knots"]] for d, v in art["devices"].items()},
                     ensure_ascii=False))
