"""独立约束验证器 (P0 任务三)。

对**整段未来时域序列**独立复核 (不复用 plan() 的枚举/剪枝逻辑):
  - 每步容量 + 备用 (extra) 可行, 设备可用;
  - 设备**最短运行 / 最短停机**: 由真实时间线 (含 as_of 前最后切换) 判定;
  - **滚动窗口**启停次数 <= max_switches_per_hour (逐次切换检查其前 60 min 内次数);
  - **切换过程供气连续**: 先启动替代容量再停机, 每个子步供气 >= 需求+备用;
  - 单机**功率上限**;
返回 violations 列表 (含 k 步与原因码)。

供 plan 一致性对照与测试使用; 不修改冻结 B1-B4。
"""
from __future__ import annotations

import pandas as pd

from air_station.optimization import optimizer as OPT

DEVICE_IDS = ("4#", "5#")


def _need(required: float, con) -> float:
    return float(required) + con.reserve.extra_m3_min(float(required))


def verify_sequence(seq, required_seq, caps, con, cur_on, cur_last_switch,
                    as_of, power_cap=0.0, power_model_path: str = "artifacts/models/power_model.json",
                    step_minutes: int = 15) -> list[dict]:
    V: list[dict] = []
    t0 = pd.Timestamp(as_of)
    prev = set(cur_on)
    last_switch = {d: pd.Timestamp(v) for d, v in (cur_last_switch or {}).items()}
    horizon_switch_times: list[pd.Timestamp] = []

    def add(code, k, detail=""):
        V.append({"code": code, "step": k, "detail": detail})

    for k, combo in enumerate(seq):
        combo = set(combo)
        tk = t0 + pd.Timedelta(minutes=step_minutes * k)
        need = _need(required_seq[k], con)

        for d in combo:
            if not caps[d].available:
                add("DEVICE_UNAVAILABLE", k, d)

        cap_combo = sum(caps[d].max_flow_m3min for d in combo)
        if not combo:
            if required_seq[k] > 1e-6:
                add("INFEASIBLE_CAPACITY_EMPTY", k, f"required={required_seq[k]:.1f}")
        elif cap_combo + 1e-6 < need:
            add("INFEASIBLE_CAPACITY", k, f"cap={cap_combo:.1f} need={need:.1f}")

        starts = [d for d in DEVICE_IDS if d in combo and d not in prev]
        stops = [d for d in DEVICE_IDS if d not in combo and d in prev]

        # 切换过程供气连续: 先启动替代容量, 再停机
        live = set(prev)
        for d in starts:
            live.add(d)
            if sum(caps[x].max_flow_m3min for x in live) + 1e-6 < need:
                add("TRANSITION_CAPACITY_GAP", k, f"start:{d}")
        for d in stops:
            live.discard(d)
            if sum(caps[x].max_flow_m3min for x in live) + 1e-6 < need:
                add("TRANSITION_CAPACITY_GAP", k, f"stop:{d}")

        # 最短运行/停机 + 记录切换时间
        for d in stops:
            ls = last_switch.get(d)
            if ls is not None and (tk - ls).total_seconds() / 60.0 < con.min_run_minutes - 1e-9:
                add("MIN_RUN_VIOLATION", k, f"{d} elapsed={(tk - ls).total_seconds() / 60.0:.1f}")
            last_switch[d] = tk
            horizon_switch_times.append(tk)
        for d in starts:
            ls = last_switch.get(d)
            if ls is not None and (tk - ls).total_seconds() / 60.0 < con.min_stop_minutes - 1e-9:
                add("MIN_STOP_VIOLATION", k, f"{d} elapsed={(tk - ls).total_seconds() / 60.0:.1f}")
            last_switch[d] = tk
            horizon_switch_times.append(tk)

        # 滚动窗口: 检查"最近 60 min 内切换次数" (最近窗口以 horizon 内切换为准)
        cnt = sum(1 for st in horizon_switch_times
                  if 0.0 <= (tk - st).total_seconds() / 60.0 <= 60.0)
        if cnt > con.max_switches_per_hour:
            add("SWITCH_BUDGET_EXCEEDED", k, f"switches_in_60min={cnt}")

        # 功率上限 (独立分配)
        if combo:
            alloc = OPT.allocate_flow(list(combo), required_seq[k], caps, power_model_path)
            if not alloc.get("ok"):
                add("ALLOCATION_FAILED", k, str(alloc.get("reason", "")))
            elif power_cap > 0:
                for d in combo:
                    if alloc["device_power"].get(d, 0.0) > power_cap + 1e-6:
                        add("POWER_OVER_CAP", k, f"{d}={alloc['device_power'][d]:.1f}>{power_cap}")
        prev = combo
    return V
