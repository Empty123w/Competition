"""健康评分 / RUL 历史 as-of 接入与保守回退 (新增模块; 不改冻结 B1-B4)。

规则 (任务书要求):
  - 健康每小时、RUL 每 6 小时更新; 逐机按**调度时刻 t 向后 as-of 关联**, 并校验有效期;
  - RUL 仅当 **quality_valid 且 未过期 且 非 out_of_distribution 且 满足来源要求** 时, 才允许
    参与**可选软惩罚**;
  - RUL 无效 -> 回退**有效健康等级**; 健康亦无效 -> **unknown 保守处理**;
  - RUL **不得**单独触发停机、降低供气备用或放松安全约束 (本模块仅产出惩罚输入与标记)。

时间语义: 历史表 `time` 为记录时刻; as-of 取 `time <= t` 的最新记录 (无未来泄漏)。
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

import pandas as pd

# 健康风险中文标签 -> 内部风险等级
HEALTH_RISK_MAP = {"正常": "normal", "关注": "attention", "预警": "warning", "严重": "critical"}


def normalize_device_id(device_id) -> str:
    s = str(device_id)
    return s if s.endswith("#") else f"{int(float(s))}#"


def load_history(path: str | Path) -> pd.DataFrame:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"[health_rul_adapter] 历史表缺失: {p}")
    df = pd.read_parquet(p)
    df["time"] = pd.to_datetime(df["time"])
    df["_dev"] = df["device_id"].map(normalize_device_id)
    return df.sort_values("time").reset_index(drop=True)


def _latest(df: pd.DataFrame, device_id: str, t) -> Optional[pd.Series]:
    dev = normalize_device_id(device_id)
    sub = df[(df["_dev"] == dev) & (df["time"] <= pd.Timestamp(t))]
    return sub.iloc[-1] if len(sub) else None


def asof_health(hdf: pd.DataFrame, device_id: str, t) -> dict:
    """返回 {status, risk_level, health_score, reason, source_time}。status∈{ok,invalid,expired,unknown}。"""
    row = _latest(hdf, device_id, t)
    if row is None:
        return {"status": "unknown", "risk_level": "unknown", "health_score": None,
                "reason": "no_record", "source_time": None}
    ts = pd.Timestamp(t)
    stale_min = (ts - row["time"]).total_seconds() / 60.0
    max_stale = float(row.get("max_staleness_minutes", 60) or 60)
    if not bool(row["quality_valid"]):
        return {"status": "invalid", "risk_level": "unknown", "health_score": None,
                "reason": str(row.get("quality_reason", "invalid")), "source_time": str(row["time"])}
    if pd.notna(row.get("valid_until")) and ts > pd.Timestamp(row["valid_until"]):
        return {"status": "expired", "risk_level": "unknown", "health_score": None,
                "reason": "expired", "source_time": str(row["time"])}
    if stale_min > max_stale + 1e-9:
        return {"status": "expired", "risk_level": "unknown", "health_score": None,
                "reason": f"stale={stale_min:.1f}>{max_stale}", "source_time": str(row["time"])}
    return {"status": "ok", "risk_level": HEALTH_RISK_MAP.get(str(row.get("risk_level")), "unknown"),
            "health_score": float(row["health_score"]), "reason": str(row.get("quality_reason", "ok")),
            "source_time": str(row["time"])}


def asof_rul(rdf: pd.DataFrame, device_id: str, t, *, require_real_training: bool = False) -> dict:
    """RUL as-of; status∈{ok,ood,invalid,expired,unknown}。"""
    row = _latest(rdf, device_id, t)
    if row is None:
        return {"status": "unknown", "rul_hours": None, "reason": "no_record", "source_time": None,
                "evidence": "unknown"}
    ts = pd.Timestamp(t)
    stale_h = (ts - row["time"]).total_seconds() / 3600.0
    max_stale = float(row.get("max_staleness_hours", 6) or 6)
    if bool(row.get("out_of_distribution")):
        return {"status": "ood", "rul_hours": None, "reason": "out_of_distribution",
                "source_time": str(row["time"]), "evidence": "simulation"}
    if not bool(row["quality_valid"]):
        return {"status": "invalid", "rul_hours": None, "reason": str(row.get("quality_reason", "invalid")),
                "source_time": str(row["time"]), "evidence": "simulation"}
    if not bool(row.get("fresh_prediction", True)):
        return {"status": "invalid", "rul_hours": None, "reason": "not_fresh",
                "source_time": str(row["time"]), "evidence": "simulation"}
    if pd.notna(row.get("valid_until")) and ts > pd.Timestamp(row["valid_until"]):
        return {"status": "expired", "rul_hours": None, "reason": "expired",
                "source_time": str(row["time"]), "evidence": "simulation"}
    if stale_h > max_stale + 1e-9:
        return {"status": "expired", "rul_hours": None, "reason": f"stale={stale_h:.2f}>{max_stale}",
                "source_time": str(row["time"]), "evidence": "simulation"}
    origin = str(row.get("model_training_origin", "unknown"))
    if require_real_training and origin != "real":
        return {"status": "invalid", "rul_hours": None, "reason": f"training_origin={origin}",
                "source_time": str(row["time"]), "evidence": "simulation"}
    return {"status": "ok", "rul_hours": float(row["estimated_rul_hours"]),
            "reason": "ok", "source_time": str(row["time"]), "evidence": "simulation"}


def dispatch_health_rul(hdf: pd.DataFrame, rdf: pd.DataFrame, device_id: str, t,
                        *, rul_enabled: bool = True, require_real_training: bool = False) -> dict:
    """调度用保守回退: 优先有效 RUL(软惩罚); 无效回退有效健康; 健康无效 -> unknown 保守。

    返回 {risk_level, rul_hours(optional), rul_status, health_status, fallback, evidence, notes}
    说明: 输出**仅供可选软惩罚与标记**; 不用于停机/备用/安全放松。
    """
    h = asof_health(hdf, device_id, t)
    r = asof_rul(rdf, device_id, t, require_real_training=require_real_training) if rul_enabled \
        else {"status": "disabled", "rul_hours": None, "evidence": "disabled"}
    out = {"risk_level": h["risk_level"], "health_status": h["status"],
           "rul_status": r["status"], "rul_hours": (r["rul_hours"] if r["status"] == "ok" else None),
           "evidence": "simulation", "fallback": "none", "notes": []}
    if r["status"] == "ok":
        out["fallback"] = "none (RUL 可用, 仅软惩罚)"
    else:
        if h["status"] == "ok":
            out["fallback"] = "health"
            out["notes"].append(f"RUL {r['status']} -> 回退有效健康等级 {h['risk_level']}")
        else:
            out["fallback"] = "unknown"
            out["risk_level"] = "unknown"
            out["notes"].append(f"健康 {h['status']} 且 RUL {r['status']} -> unknown 保守处理")
    return out
