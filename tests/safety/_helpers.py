"""B4 测试构造器。"""
from __future__ import annotations

from air_station.optimization.contracts import (
    DeviceAction,
    DispatchResult,
    StationSnapshot,
)

AS_OF = "2026-09-11T00:00:00"


def snapshot(pressure=0.60, running=(True, True), d4=0.58, d5=0.60,
             igv4=100.0, igv5=100.0, bov4=100.0, bov5=100.0, f4=250.0, f5=240.0):
    from air_station.optimization.contracts import DeviceSnapshot
    devs = []
    if running[0]:
        devs.append(DeviceSnapshot("4#", True, f4, 1300.0, d4, igv4, bov4))
    if running[1]:
        devs.append(DeviceSnapshot("5#", True, f5, 1280.0, d5, igv5, bov5))
    return StationSnapshot(AS_OF, pressure, devs, data_source="test")


def candidate(flows: dict, transition_plan=None) -> DispatchResult:
    actions = []
    for did, f in flows.items():
        actions.append(DeviceAction(did, "hold", float(f), float(f) / 268.9, True))
    return DispatchResult(status="success", as_of=AS_OF, policy_version="test",
                          device_actions=actions, transition_plan=transition_plan or [])
