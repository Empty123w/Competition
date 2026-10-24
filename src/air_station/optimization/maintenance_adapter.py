"""B 侧维护软约束适配器 (阶段 A7, 新增)。

把 A 的 `maintenance_json()` 结果换算为每台机组的**软惩罚/预警标记**，
供调度在需要时选择性纳入；**不修改** `optimization` 既有算法与输出。

用法(由调用方决定是否接入):
    from air_station.optimization.maintenance_adapter import maintenance_soft_constraint
    soft = maintenance_soft_constraint()
    # soft["devices"]["4#"]["penalty"] 可作为额外惩罚项传入优化器的自定义目标
"""
from __future__ import annotations


def maintenance_soft_constraint(config_path: str = "configs/maintenance_policy.yaml",
                                processed_dir: str | None = None,
                                warn_penalty: float = 0.05) -> dict:
    """返回每台机组的维护软约束。

    - warning=True 的机组给一个小的软惩罚(默认 0.05)，供调度降低优先级；
    - 结果附带 validation_mode，调用方不得当作现场停机依据。
    """
    from air_station.models.rul.maintenance_sim import maintenance_json
    r = maintenance_json(config_path=config_path, processed_dir=processed_dir)
    devices = {}
    for d in r.get("devices", []):
        devices[d["device_id"]] = {
            "warning": bool(d.get("warning", False)),
            "risk_level": d.get("risk_level"),
            "trend_score": d.get("trend_score"),
            "penalty": warn_penalty if d.get("warning") else 0.0,
            "top_trend_factor": d.get("top_trend_factor"),
        }
    return {"status": "success", "source": "maintenance_json",
            "validation_mode": r.get("validation_mode"),
            "warn_penalty": warn_penalty, "devices": devices}
