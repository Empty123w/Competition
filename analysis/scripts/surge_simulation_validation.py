"""B4 喘振预警的独立高频(秒级)仿真验证。

纪律:
  - 复用已有 B4 代理 (air_station.safety.surge), **不修改**其阈值/边界;
  - 仿真真值线由**独立参数**给出(与 B4 配置不同), 记录可追溯, 不用 B4 阈值造真值自测;
  - 秒级数据为**合成仿真**, 非现场秒级证据, 也不由分钟数据插值冒充;
  - 开发场景(dev)与验证场景(valid)分开; 报警/事件/指标定义**预先固定**。

定义(预先固定):
  - 事件(true surge): 合成物理流量 < 独立真值喘振线 q_true(p) 连续 >= EVENT_PERSIST_S 秒;
    事件时刻 = 首个满足秒。
  - 报警(alarm): B4 risk_level ∈ {medium, high} (unknown 单列, 不计入报警)。
  - 提前时间 lead = 事件时刻 - 首次报警时刻; lead>=0 且报警早于/等于事件才计入覆盖。
  - 指标: 事件级 精确率/召回率/F1/FPR/FNR; 逐事件 lead; lead>=30s 覆盖率; unknown 计数。

输出: analysis/reports/b/surge_simulation_validation.json
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from air_station.optimization import contracts as C
from air_station.safety import surge as SURGE

OUT = Path("analysis/reports/b/surge_simulation_validation.json")

# ---- 预先固定定义 ----
EVENT_PERSIST_S = 3
LEAD_TARGET_S = 30.0
ALARM_LEVELS = {"medium", "high"}
# 独立真值喘振线 (合成真值, 与 B4 engineering 边界 90/350 不同, 可追溯)
TRUE_Q_REF = 80.0
TRUE_Q_SLOPE = 260.0
TRUE_P_REF = 0.66


def q_true(p):
    return TRUE_Q_REF + TRUE_Q_SLOPE * (p - TRUE_P_REF)


def _simulate(kind, seed, seconds=600, fs=1):
    """秒级合成: 返回 p[], flow[], cmd[], demand[], sensor_valid[] 。"""
    rng = np.random.default_rng(seed)
    n = seconds * fs
    t = np.arange(n) / fs
    cmd = np.full(n, 180.0)
    p = np.full(n, 0.62)
    svalid = np.ones(n, bool)
    if kind == "normal_steady":
        cmd[:] = 190.0
    elif kind == "normal_small":
        cmd = 185 + 8 * np.sin(2 * np.pi * t / 120)
    elif kind in ("gradual_approach", "dev_gradual"):
        cmd = 190 - (1.0 if kind == "gradual_approach" else 1.4) * t   # 逐步下探穿越真值线
    elif kind == "rapid_load_change":
        cmd = 190 - 90 * (t > 180)                # 快速降载
    elif kind == "machine_switch":
        cmd = 190 - 70 * ((t > 200) & (t < 260))  # 切换导致的流量凹谷
    elif kind == "noisy":
        cmd = 190 - 0.8 * t                       # 缓降 + 强噪声
    elif kind == "sensor_missing":
        cmd = 190 - 0.9 * t
        svalid[(t > 220) & (t < 300)] = False     # 传感缺失窗口
    # 一阶流量动态 + 噪声
    flow = np.zeros(n)
    f = 190.0
    for i in range(n):
        f += (cmd[i] - f) * 0.15
        noise = rng.normal(0, 2.5 if kind != "noisy" else 8.0)
        flow[i] = f + noise
    # 压力: 与供给略正相关 + 噪声, 保持在边界域内
    p = 0.60 + 0.0006 * (flow - 180) + rng.normal(0, 0.004, n)
    p = np.clip(p, 0.50, 0.70)
    demand = cmd + rng.normal(0, 3, n)
    return t, p, flow, cmd, demand, svalid


def _label_event(p, flow):
    below = flow < q_true(p)
    events = []
    i = 0
    n = len(below)
    while i < n:
        if below[i]:
            j = i
            while j < n and below[j]:
                j += 1
            if j - i >= EVENT_PERSIST_S:
                events.append(i)
            i = j
        else:
            i += 1
    return events, below


def _run_b4_seconds(p, flow, svalid):
    """逐秒运行冻结 B4; 返回每档 risk_level 序列。"""
    levels = []
    for i in range(len(flow)):
        if not svalid[i]:
            pp, ff = None, None
        else:
            pp, ff = float(p[i]), float(flow[i])
        dev = C.DeviceSnapshot("4#", True, ff, None, pp, None, None,
                               bool(svalid[i]), "surge_sim")
        snap = C.StationSnapshot(f"sec{i}", pp, [dev], "surge_sim")
        res = C.DispatchResult(status=C.STATUS_SUCCESS, as_of=f"sec{i}", policy_version="surge-sim")
        res.device_actions = [C.DeviceAction("4#", "hold", ff, None, True)]
        res.transition_plan = []
        try:
            a = SURGE.assess_surge_risk(snap, res)
            levels.append(SURGE.worst_level(a))
        except Exception:
            levels.append("unknown")
    return levels


def _ew_alarm(p, flow, svalid, band, b4_boundary):
    """独立早预警层(新算法, 不改 B4): flow < q_b4(p) + band 且传感有效。"""
    out = np.zeros(len(flow), bool)
    for i in range(len(flow)):
        if not svalid[i]:
            continue
        out[i] = flow[i] < b4_boundary(p[i]) + band
    return out


def _b4_boundary(p):
    """B4 engineering 边界 (与 configs/safety.yaml 一致; 只读, 不修改)。"""
    return 90.0 + 350.0 * (p - 0.66)


def _ew_metrics(events, alarm):
    leads = []
    first = np.where(alarm)[0]
    for e in events:
        pre = first[first <= e]
        leads.append(int(e - pre[0]) if len(pre) else None)
    covered = [l for l in leads if l is not None and l >= LEAD_TARGET_S]
    return {"leads_s": leads,
            "lead_ge_30s_coverage": (round(len(covered) / len(events), 4) if events else None),
            "n_detected": sum(1 for l in leads if l is not None)}


def _metrics(events, levels, below):
    n = len(levels)
    alarm = np.array([lv in ALARM_LEVELS for lv in levels])
    unknown = np.array([lv == "unknown" for lv in levels])
    # 时间级
    tp = int((alarm & below).sum()); fp = int((alarm & ~below).sum())
    fn = int((~alarm & below).sum()); tn = int((~alarm & ~below).sum())
    prec = tp / (tp + fp) if tp + fp else None
    rec = tp / (tp + fn) if tp + fn else None
    f1 = (2 * prec * rec / (prec + rec)) if (prec and rec) else None
    fpr = fp / (fp + tn) if fp + tn else None
    fnr = fn / (fn + tp) if fn + tp else None
    # 事件级 lead
    first_alarm = np.where(alarm)[0]
    leads = []
    for e in events:
        pre = first_alarm[first_alarm <= e]
        if len(pre):
            leads.append(int(e - pre[0]))
        else:
            leads.append(None)
    covered = [l for l in leads if l is not None and l >= LEAD_TARGET_S]
    return {"time_level": {"TP": tp, "FP": fp, "FN": fn, "TN": tn,
                           "precision": _r(prec), "recall": _r(rec), "f1": _r(f1),
                           "FPR": _r(fpr), "FNR": _r(fnr),
                           "unknown_seconds": int(unknown.sum())},
            "event_level": {"n_events": len(events),
                            "n_detected": sum(1 for l in leads if l is not None),
                            "leads_s": leads,
                            "lead_ge_30s_coverage": (round(len(covered) / len(events), 4) if events else None),
                            "min_lead_s": (min([l for l in leads if l is not None]) if any(l is not None for l in leads) else None)}}


def _r(x):
    return round(float(x), 4) if x is not None else None


def main():
    dev_kinds = ["normal_steady", "normal_small", "dev_gradual"]
    valid_kinds = ["gradual_approach", "rapid_load_change", "machine_switch", "noisy", "sensor_missing"]
    EW_BANDS = [20.0, 30.0, 40.0, 60.0]
    rep = {"protocol": {"evidence_scope": "合成秒级仿真 (非现场; 非分钟插值)",
                        "event_definition": f"flow < q_true(p) 连续>={EVENT_PERSIST_S}s",
                        "alarm_definition": "B4 risk_level in {medium,high}; unknown 单列",
                        "lead_definition": "event_time - first_alarm_time (s)",
                        "lead_target_s": LEAD_TARGET_S,
                        "true_boundary": {"q_ref": TRUE_Q_REF, "slope": TRUE_Q_SLOPE, "p_ref": TRUE_P_REF,
                                          "note": "独立真值线; 与 B4 配置边界参数不同, 防止自测达标"},
                        "uses_B5_final_window": False,
                        "note_B4_unchanged": "复用冻结 B4 代理, 未改其阈值/边界"},
            "dev_scenarios": {}, "valid_scenarios": {}}
    for group, kinds in (("dev_scenarios", dev_kinds), ("valid_scenarios", valid_kinds)):
        for i, kind in enumerate(kinds):
            t, p, flow, cmd, demand, svalid = _simulate(kind, seed=1000 + i + (0 if group.startswith("dev") else 50))
            events, below = _label_event(p, flow)
            levels = _run_b4_seconds(p, flow, svalid)
            rep[group][kind] = {"n_seconds": len(flow),
                                "true_events": len(events),
                                **_metrics(events, levels, below)}

    # ---- 独立早预警层: 在 dev 事件场景上选 band, 冻结后用于 valid ----
    dev_ev = []
    for kind in dev_kinds:
        t, p, flow, cmd, demand, svalid = _simulate(kind, seed=1000 + dev_kinds.index(kind))
        events, _ = _label_event(p, flow)
        dev_ev.append((kind, p, flow, svalid, events))
    chosen = None
    band_scan = {}
    for band in EW_BANDS:
        leads_all, fp, tot = [], 0, 0
        for kind, p, flow, svalid, events in dev_ev:
            al = _ew_alarm(p, flow, svalid, band, _b4_boundary)
            m = _ew_metrics(events, al)
            leads_all += [l for l in m["leads_s"] if l is not None]
            fp += int((al & ~_label_event(p, flow)[1]).sum()); tot += len(flow)
        band_scan[band] = {"dev_min_lead_s": min(leads_all) if leads_all else None,
                           "dev_fpr": round(fp / tot, 4) if tot else None,
                           "dev_n_events": sum(len(e[4]) for e in dev_ev)}
        if leads_all and min(leads_all) >= LEAD_TARGET_S and (fp / tot if tot else 1) <= 0.05:
            chosen = band
            break
    rep["early_warning_layer"] = {
        "definition": "新独立早预警层(不改 B4): alarm = flow < q_b4(p) + band",
        "band_scan_dev": band_scan, "chosen_band_m3min": chosen,
        "note": "band 在 dev 场景选定并冻结, 用于 valid; 属新算法, 独立于 B4, 标为仿真"}
    if chosen is not None:
        vsum = {"n_events": 0, "detected": 0, "leads": []}
        for kind in valid_kinds:
            idx = valid_kinds.index(kind)
            t, p, flow, cmd, demand, svalid = _simulate(kind, seed=1050 + idx)
            events, _ = _label_event(p, flow)
            al = _ew_alarm(p, flow, svalid, chosen, _b4_boundary)
            m = _ew_metrics(events, al)
            rep["early_warning_layer"].setdefault("valid_scenarios", {})[kind] = m
            vsum["n_events"] += len(events)
            vsum["detected"] += m["n_detected"]
            vsum["leads"] += [l for l in m["leads_s"] if l is not None]
        rep["early_warning_layer"]["valid_summary"] = {
            "n_events": vsum["n_events"], "n_detected": vsum["detected"],
            "lead_ge_30s_coverage": (round(sum(1 for l in vsum["leads"] if l >= LEAD_TARGET_S) / vsum["n_events"], 4)
                                     if vsum["n_events"] else None)}
    # 汇总 valid
    all_leads, all_events, p_sum, r_sum = [], 0, [], []
    for kind, m in rep["valid_scenarios"].items():
        all_events += m["event_level"]["n_events"]
        all_leads += [l for l in m["event_level"]["leads_s"] if l is not None]
        if m["time_level"]["precision"] is not None:
            p_sum.append(m["time_level"]["precision"])
        if m["time_level"]["recall"] is not None:
            r_sum.append(m["time_level"]["recall"])
    rep["valid_summary"] = {"n_events": all_events,
                            "n_detected": len(all_leads),
                            "mean_precision": _r(np.mean(p_sum)) if p_sum else None,
                            "mean_recall": _r(np.mean(r_sum)) if r_sum else None,
                            "lead_ge_30s_coverage": (round(sum(1 for l in all_leads if l >= LEAD_TARGET_S) / all_events, 4) if all_events else None)}
    OUT.write_text(json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(rep, ensure_ascii=False, indent=2)[:3500])


if __name__ == "__main__":
    main()
