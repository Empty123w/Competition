"""模拟 EAM (企业资产管理): 维保计划查询与工单生成 (阶段 C)。"""
from __future__ import annotations

import json
from pathlib import Path

DEFAULT_ARCHIVE = "data/设备档案.json"


def maintenance_plan(device_id: str | None = None,
                     archive_path: str | Path = DEFAULT_ARCHIVE,
                     remaining_hours: dict | None = None) -> dict:
    """返回设备的保养项目与剩余时间(取自设备档案 + 运行数据)。"""
    path = Path(archive_path)
    if not path.exists():
        raise FileNotFoundError(f"[eam] 设备档案缺失: {path}")
    raw = json.loads(path.read_text(encoding="utf-8"))
    items = []
    for item in raw.get("设备档案", []):
        dev = f"{item['编号']}#"
        if device_id and dev != device_id:
            continue
        items.append({
            "device_id": dev,
            "model": item.get("型号", ""),
            "plans": item.get("保养计划", []),
            "remaining_hours": (remaining_hours or {}).get(dev),
        })
    return {"status": "success", "source": "simulated_eam", "devices": items}


def build_work_order_reason(device_id: str, health_entry: dict | None,
                            note: str | None = None) -> str:
    if note:
        return note
    if health_entry:
        return (f"设备 {device_id} 健康等级 {health_entry.get('risk_level', '关注')}: "
                f"{health_entry.get('top_anomaly_factor', '异常')} "
                f"({health_entry.get('suggestion', '建议点检')})")
    return f"设备 {device_id} 预防性维护"
