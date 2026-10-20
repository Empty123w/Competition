"""新提前预警层 (EW) 的完整独立验证 v2 — 保留冻结 B4, EW 单独保存。

真值机制 (独立于 B4, 可追溯):
  - 降低阶压缩机失稳代理: 失稳流量界限为**独立的二次函数** q_stab(p)=A+B·x+C·x² (x=p-0.66),
    参数 A=82,B=300,C=-400 由**独立工程估计**给出 (与 B4 linear 90/350 的**函数形式与参数均不同**);
  - 事件 = flow < q_stab(p) 连续 >= EVENT_PERSIST_S 秒;
  - 局限: 仍为**仿真替代**, 非厂家真实喘振线; 不伪造厂家边界。

指标定义 (预先固定):
  - 报警: B4 risk∈{medium,high}; EW: flow < q_b4(p)+band; unknown 单列, 不计报警;
  - 事件匹配: 若在 [t_event_start - WINDOW_S, t_event_end] 内出现报警 -> 检出;
    lead = t_event_start - 首次(不晚于事件)报警时刻, 无则 None;
  - lead>=30s 覆盖率分母 = **全部危险事件**(漏报不排除);
  - 误报小时率 = (不在任何事件窗内的报警起始次数) / 总小时数;
  - 精确率/召回 = 时间级(报警步 vs 事件步)。

输出: analysis/reports/b/surge_ew_validation_v2.json
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from air_station.optimization import contracts as CT
from air_station.safety import surge as SURGE

OUT = Path("analysis/reports/b/surge_ew_validation_v2.json")
EVENT_PERSIST_S = 3
LEAD_TARGET_S = 30.0
MATCH_WINDOW_S = 120   # 事件匹配前窗 (含报警可略早于事件)
ALARM_LEVELS = {"medium", "high"}
BAND = 40.0            # EW 已选版本 (固定)
# 独立真值失稳界限 (二次; 与 B4 linear 不同)
A, B, C = 82.0, 300.0, -400.0


def q_stab(p):
    x = p - 0.66
    return A + B * x + C * x * x


def q_b4(p):
    return 90.0 + 350.0 * (p - 0.66)


def _sim(kind, seed, seconds=900):
    rng = np.random.default_rng(seed)
    n = seconds
    t = np.arange(n, dtype=float)
    cmd = np.full(n, 185.0)
    svalid = np.ones(n, bool)
    if kind == "normal_low":          # 正常低流量(不穿越)
        cmd = 160 + 5 * np.sin(2 * np.pi * t / 150)
    elif kind == "brief_disturb":     # 短暂扰动(短于持续阈值)
        cmd = 185 - 60 * ((t > 400) & (t < 401))
    elif kind == "gradual":           # 渐进危险
        cmd = 190 - 1.0 * t
    elif kind == "rapid":             # 快速突变
        cmd = 190 - 95 * (t > 300)
    elif kind == "switch":            # 切换凹谷
        cmd = 190 - 75 * ((t > 320) & (t < 400))
    elif kind == "noisy":             # 缓降 + 强噪声
        cmd = 190 - 0.8 * t
    elif kind == "sensor_missing":
        cmd = 190 - 0.9 * t
        svalid[(t > 500) & (t < 600)] = False
    flow = np.zeros(n); f = 185.0
    sigma = 8.0 if kind == "noisy" else 2.5
    for i in range(n):
        f += (cmd[i] - f) * 0.15
        flow[i] = f + rng.normal(0, sigma)
    p = np.clip(0.60 + 0.0006 * (flow - 180) + rng.normal(0, 0.004, n), 0.50, 0.70)
    return p, flow, svalid


def _events(p, flow):
    below = flow < q_stab(p)
    ev, i, n = [], 0, len(below)
    while i < n:
        if below[i]:
            j = i
            while j < n and below[j]:
                j += 1
            if j - i >= EVENT_PERSIST_S:
                ev.append((i, j - 1))
            i = j
        else:
            i += 1
    return ev, below


def _b4_levels(p, flow, svalid):
    lv = []
    for i in range(len(flow)):
        pp = None if not svalid[i] else float(p[i])
        ff = None if not svalid[i] else float(flow[i])
        dev = CT.DeviceSnapshot("4#", True, ff, None, pp, None, None, bool(svalid[i]), "surge_sim")
        snap = CT.StationSnapshot(f"s{i}", pp, [dev], "surge_sim")
        res = CT.DispatchResult(status=CT.STATUS_SUCCESS, as_of=f"s{i}", policy_version="surge-sim")
        res.device_actions = [CT.DeviceAction("4#", "hold", ff, None, True)]
        res.transition_plan = []
        try:
            lv.append(SURGE.worst_level(SURGE.assess_surge_risk(snap, res)))
        except Exception:
            lv.append("unknown")
    return lv


def _ew_alarm(p, flow, svalid, band):
    return np.array([svalid[i] and (flow[i] < q_b4(p[i]) + band) for i in range(len(flow))])


def _alarm_onsets(alarm):
    a = alarm.astype(int)
    return int(((a[1:] == 1) & (a[:-1] == 0)).sum()) + (1 if a[0] == 1 else 0)


def _metrics(events, alarm, valid, n):
    below = np.zeros(n, bool)
    for s, e in events:
        below[s:e + 1] = True
    tp = int((alarm & below).sum()); fp = int((alarm & ~below).sum())
    fn = int((~alarm & below).sum()); tn = int((~alarm & ~below).sum())
    prec = tp / (tp + fp) if tp + fp else None
    rec = tp / (tp + fn) if tp + fn else None
    # 事件级
    first = np.where(alarm)[0]
    leads = []
    for s, e in events:
        pre = first[(first <= s) & (first >= s - MATCH_WINDOW_S)]
        if len(pre):
            leads.append(int(s - pre[0]))
        else:
            # 事件窗内报警也算检出但无提前
            within = first[(first > s) & (first <= e)]
            leads.append(int(s - within[0]) if len(within) else None)  # 负值=滞后
    n_events = len(events)
    det = sum(1 for l in leads if l is not None)
    adv = sum(1 for l in leads if l is not None and l >= LEAD_TARGET_S)
    # 误报小时率: 报警起始, 且其时刻不在任何事件窗
    onsets = np.where(alarm.astype(int)[1:] > alarm.astype(int)[:-1])[0] + 1
    fa = sum(1 for o in onsets if not below[o])
    hours = n / 3600.0
    return {"time_level": {"TP": tp, "FP": fp, "FN": fn, "TN": tn,
                           "precision": _r(prec), "recall": _r(rec),
                           "f1": _r(2 * prec * rec / (prec + rec)) if (prec and rec) else None},
            "event_level": {"n_events": n_events, "n_detected": det,
                            "leads_s": leads,
                            "lead_ge_30s_coverage_all_events": (round(adv / n_events, 4) if n_events else None)},
            "false_alarm_onsets": fa, "false_alarms_per_hour": round(fa / hours, 3) if hours else None,
            "alarm_active_seconds": int(alarm.sum()),
            "normal_seconds": int((~below).sum())}


def _r(x):
    return round(float(x), 4) if x is not None else None


def main():
    dev = ["normal_low", "brief_disturb", "gradual"]
    valid = ["normal_low", "brief_disturb", "gradual", "rapid", "switch", "noisy", "sensor_missing"]
    rep = {"protocol": {
        "truth_mechanism": {
            "form": "quadratic stability limit q_stab(p)=A+B*x+C*x^2, x=p-0.66",
            "params": {"A": A, "B": B, "C": C},
            "independence": "与 B4 的 linear 90+350*x 在函数形式与参数上均不同, 且由独立工程估计给定",
            "limitation": "仍为仿真替代, 非厂家真实喘振线; 不伪造厂家边界"},
        "event_definition": f"flow<q_stab(p) 连续>={EVENT_PERSIST_S}s",
        "alarm_definition": "B4 risk in {medium,high}; EW flow<q_b4(p)+band; unknown 单列(不计报警)",
        "match_window_s": MATCH_WINDOW_S, "lead_target_s": LEAD_TARGET_S,
        "lead_denominator": "全部危险事件(漏报不排除)",
        "ew_band_m3min": BAND, "band_frozen": True,
        "evidence_scope": "合成秒级仿真; 非现场; 非分钟插值",
        "uses_B5_final_window": False}}

    for group, kinds in (("dev", dev), ("valid", valid)):
        agg = {"b4": {"tp": 0, "fp": 0, "fn": 0, "tn": 0, "events": 0, "det_b4": 0, "adv_b4": 0,
                      "fa_b4": 0, "unknown_s": 0, "normal_s": 0, "alarm_s_b4": 0},
               "ew": {"tp": 0, "fp": 0, "fn": 0, "tn": 0, "events": 0, "det_ew": 0, "adv_ew": 0,
                      "fa_ew": 0, "normal_s": 0, "alarm_s_ew": 0}}
        scen = {}
        for idx, kind in enumerate(kinds):
            p, flow, sv = _sim(kind, seed=(10 if group == "dev" else 60) + idx)
            ev, below = _events(p, flow)
            lv = _b4_levels(p, flow, sv)
            b4alarm = np.array([x in ALARM_LEVELS for x in lv])
            ewalarm = _ew_alarm(p, flow, sv, BAND)
            mb = _metrics(ev, b4alarm, sv, len(flow))
            me = _metrics(ev, ewalarm, sv, len(flow))
            scen[kind] = {"n_seconds": len(flow), "true_events": len(ev),
                          "unknown_seconds": int(sum(1 for x in lv if x == "unknown")),
                          "b4": mb, "ew": me}
            for tag, m, al, det, adv in (("b4", mb, b4alarm, mb["event_level"]["n_detected"],
                                          sum(1 for l in mb["event_level"]["leads_s"] if l is not None and l >= LEAD_TARGET_S)),
                                         ("ew", me, ewalarm, me["event_level"]["n_detected"],
                                          sum(1 for l in me["event_level"]["leads_s"] if l is not None and l >= LEAD_TARGET_S))):
                agg[tag]["tp"] += m["time_level"]["TP"]; agg[tag]["fp"] += m["time_level"]["FP"]
                agg[tag]["fn"] += m["time_level"]["FN"]; agg[tag]["tn"] += m["time_level"]["TN"]
                agg[tag]["events"] += len(ev); agg[tag][f"det_{tag}"] += det; agg[tag][f"adv_{tag}"] += adv
                agg[tag]["fa_" + tag] += m["false_alarm_onsets"]; agg[tag]["alarm_s_" + tag] += m["alarm_active_seconds"]
                if tag == "b4":
                    agg["b4"]["unknown_s"] += scen[kind]["unknown_seconds"]
                agg[tag]["normal_s"] += m["normal_seconds"]
        tot_hours = sum(s["n_seconds"] for s in scen.values()) / 3600.0
        summary = {}
        for tag in ("b4", "ew"):
            a = agg[tag]
            prec = a["tp"] / (a["tp"] + a["fp"]) if a["tp"] + a["fp"] else None
            rec = a["tp"] / (a["tp"] + a["fn"]) if a["tp"] + a["fn"] else None
            summary[tag] = {
                "events": a["events"], "detected": a[f"det_{tag}"],
                "recall_event_level": _r(a[f"det_{tag}"] / a["events"]) if a["events"] else None,
                "lead_ge_30s_coverage_all_events": _r(a[f"adv_{tag}"] / a["events"]) if a["events"] else None,
                "precision_time_level": _r(prec), "recall_time_level": _r(rec),
                "false_alarms_per_hour": round(a[f"fa_{tag}"] / tot_hours, 3) if tot_hours else None,
                "alarm_active_ratio": _r(a[f"alarm_s_{tag}"] / max(1, sum(s["n_seconds"] for s in scen.values())))}
            if tag == "b4":
                summary[tag]["unknown_seconds"] = a["unknown_s"]
        rep[group] = {"scenarios": scen, "summary": summary, "total_hours": round(tot_hours, 3)}

    OUT.write_text(json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(rep["valid"]["summary"], ensure_ascii=False, indent=2))
    print(json.dumps(rep["dev"]["summary"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
