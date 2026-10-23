"""喘振集成桥: 当前工况快照 → B 的 assess_surge_risk。

B 的喘振评估需要 `(snapshot, candidate)`; C 以当前工况构造一个"保持"候选,
调用 B 的 `assess_surge_risk`, 原样返回 B 的 SurgeAssessment + C 侧元信息。
代理边界与 blocking 语义完全由 B 决定, C 不重算。
"""
from __future__ import annotations

import time

from air_station.optimization import b_api
from air_station.optimization.contracts import DeviceAction, DispatchResult
from air_station.integration.dispatch_bridge import build_snapshot, DEFAULT_PROCESSED

DEFAULT_SAFETY_CONFIG = "configs/safety.yaml"
DEFAULT_BOUNDARY = "artifacts/models/surge_boundary.json"
_WORST_ORDER = {"low": 0, "medium": 1, "high": 2, "unknown": 3}
_WORST_LABEL = {0: "low", 1: "medium", 2: "high", 3: "unknown"}


def _hold_candidate(snapshot) -> DispatchResult:
    actions = [
        DeviceAction(device_id=d.device_id, action="hold",
                     target_flow_m3_min=d.flow_m3_min, target_load_ratio=None,
                     running_after=bool(d.running))
        for d in snapshot.devices
    ]
    return DispatchResult(status="success", as_of=snapshot.as_of,
                          policy_version="c-surge-hold-current", device_actions=actions)


def surge_assessment(as_of=None, processed_dir: str = DEFAULT_PROCESSED,
                     config_path: str = DEFAULT_SAFETY_CONFIG,
                     boundary_path: str = DEFAULT_BOUNDARY) -> dict:
    t0 = time.perf_counter()
    snapshot = build_snapshot(as_of=as_of, processed_dir=processed_dir)
    candidate = _hold_candidate(snapshot)
    assessment = b_api.assess_surge_risk(snapshot, candidate,
                                         config_path=config_path,
                                         boundary_path=boundary_path)
    out = assessment.to_dict()
    worst = 0
    for d in out.get("devices", []):
        r = str(d.get("risk_level", "unknown")).lower()
        worst = max(worst, _WORST_ORDER.get(r, 3))
    out["station_risk_level"] = _WORST_LABEL[worst]
    out["c_meta"] = {
        "needs_human_review": worst >= _WORST_ORDER["medium"]
        or bool(out.get("safety_blocked")),
        "execution_allowed": False,
        "elapsed_seconds": round(time.perf_counter() - t0, 3),
    }
    return out
