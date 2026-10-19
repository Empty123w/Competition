"""EW 预警验证 v3 — 修正事件匹配窗与误报判定窗不一致等问题。

保留原始 `surge_ew_validation_v2.py` 与结果(25.1 次/h)不变; 本脚本为新版本, 输出新文件。

确认并修正的问题 (P0):
  P2-A 窗口不一致: 事件匹配用 [s-WIN, e], 但误报判定用"不在 [s,e] 内" -> 事件前 120s 的
       **合法预警被无条件计为误报**。v3 统一: 误报=报警起始**不落在任何事件匹配窗**内。
  P2-B 起始次数: 原 `_metrics` 的 onset 计算漏掉**第 0 秒已激活**的情形; v3 统一计入。
  P2-C unknown 语义: 原 `valid` 参数未用, unknown 步被当作 TN/FN。v3 **将 unknown 步屏蔽**
       (不计入 TP/FP/FN/TN), 并单列。
  P2-D 分项报告: v3 分别给 事件级误报、时间级 FP、报警活跃时长。

输出: analysis/reports/b/surge_ew_validation_v3.json
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from air_station.optimization import contracts as CT
from air_station.safety import surge as SURGE

OUT = Path("analysis/reports/b/surge_ew_validation_v3.json")
EVENT_PERSIST_S = 3
LEAD_TARGET_S = 30.0
MATCH_WINDOW_S = 120
ALARM_LEVELS = {"medium", "high"}
BAND = 40.0
A, B, C = 82.0, 300.0, -400.0


def q_stab(p):
    x = p - 0.66
    return A + B * x + C * x * x


def q_b4(p):
    return 90.0 + 350.0 * (p - 0.66)


def _sim(kind, seed, seconds=900):
    rng = np.random.default_rng(seed)
    n = seconds; t = np.arange(n, dtype=float)
    cmd = np.full(n, 185.0); svalid = np.ones(n, bool)
    if kind == "normal_low":
        cmd = 160 + 5 * np.sin(2 * np.pi * t / 150)
    elif kind == "brief_disturb":
        cmd = 185 - 60 * ((t > 400) & (t < 401))
    elif kind == "gradual":
        cmd = 190 - 1.0 * t
    elif kind == "rapid":
        cmd = 190 - 95 * (t > 300)
    elif kind == "switch":
        cmd = 190 - 75 * ((t > 320) & (t < 400))
    elif kind == "noisy":
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


def _onsets(alarm):
    a = alarm.astype(int)
    onset = (a[1:] == 1) & (a[:-1] == 0)
    idx = list(np.where(onset)[0] + 1)
    if len(a) and a[0] == 1:
        idx = [0] + idx
    return idx


def _metrics(events, alarm, unknown_mask, n):
    below = np.zeros(n, bool)
    for s, e in events:
        below[s:e + 1] = True
    known = ~unknown_mask
    match = np.zeros(n, bool)
    for s, e in events:
        match[max(0, s - MATCH_WINDOW_S):e + 1] = True
    # 时间级 (排除 unknown)
    tp = int((alarm & below & known).sum()); fp = int((alarm & ~below & known).sum())
    fn = int((~alarm & below & known).sum()); tn = int((~alarm & ~below & known).sum())
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
            within = first[(first > s) & (first <= e)]
            leads.append(int(s - within[0]) if len(within) else None)
    n_events = len(events)
    det = sum(1 for l in leads if l is not None)
    adv = sum(1 for l in leads if l is not None and l >= LEAD_TARGET_S)
    # 误报 (事件级): 起始不落在任何匹配窗
    ons = _onsets(alarm)
    fa_event = [o for o in ons if not match[o]]
    hours = n / 3600.0
    return {"time_level": {"TP": tp, "FP": fp, "FN": fn, "TN": tn,
                           "precision": _r(prec), "recall": _r(rec),
                           "f1": _r(2 * prec * rec / (prec + rec)) if (prec and rec) else None},
            "event_level": {"n_events": n_events, "n_detected": det, "leads_s": leads,
                            "lead_ge_30s_coverage_all_events": (round(adv / n_events, 4) if n_events else None)},
            "false_alarms": {"event_level_count": len(fa_event),
                             "event_level_per_hour": round(len(fa_event) / hours, 3) if hours else None,
                             "total_onsets": len(ons),
                             "onsets_in_match_window": len(ons) - len(fa_event)},
            "alarm_active_seconds": int(alarm.sum()),
            "unknown_seconds": int(unknown_mask.sum()),
            "normal_seconds": int((~below & known).sum())}


def _r(x):
    return round(float(x), 4) if x is not None else None


def main():
    dev = ["normal_low", "brief_disturb", "gradual"]
    valid = ["normal_low", "brief_disturb", "gradual", "rapid", "switch", "noisy", "sensor_missing"]
    rep = {"protocol": {
        "truth_mechanism": {"form": "quadratic q_stab(p)=A+B*x+C*x^2, x=p-0.66",
                            "params": {"A": A, "B": B, "C": C},
                            "independence": "与 B4 linear 90+350*x 形式与参数均不同, 独立工程估计",
                            "limitation": "仿真替代, 非厂家真实喘振线"},
        "fixes": {"P2-A": "误报判定窗与事件匹配窗统一为 [s-WIN, e]",
                  "P2-B": "计入第 0 秒已激活的报警起始",
                  "P2-C": "unknown 步从时间级混淆矩阵屏蔽并单列"},
        "event_definition": f"flow<q_stab(p) 连续>={EVENT_PERSIST_S}s",
        "alarm_definition": "B4 risk in {medium,high}; EW flow<q_b4(p)+band; unknown 单列",
        "match_window_s": MATCH_WINDOW_S, "lead_target_s": LEAD_TARGET_S,
        "lead_denominator": "全部危险事件(漏报不排除)", "ew_band_m3min": BAND, "band_frozen": True,
        "evidence_scope": "合成秒级仿真; 非现场", "uses_B5_final_window": False}}

    for group, kinds in (("dev", dev), ("valid", valid)):
        scen = {}
        agg = {t: {"tp": 0, "fp": 0, "fn": 0, "tn": 0, "events": 0, "det": 0, "adv": 0,
                   "fa_event": 0, "alarm_s": 0, "unknown_s": 0, "n": 0} for t in ("b4", "ew")}
        for idx, kind in enumerate(kinds):
            p, flow, sv = _sim(kind, seed=(10 if group == "dev" else 60) + idx)
            ev, below = _events(p, flow)
            lv = _b4_levels(p, flow, sv)
            b4a = np.array([x in ALARM_LEVELS for x in lv])
            ewa = np.array([sv[i] and (flow[i] < q_b4(p[i]) + BAND) for i in range(len(flow))])
            unk = np.array([x == "unknown" for x in lv])
            mb = _metrics(ev, b4a, unk, len(flow))
            me = _metrics(ev, ewa, np.zeros(len(flow), bool), len(flow))
            scen[kind] = {"n_seconds": len(flow), "true_events": len(ev), "b4": mb, "ew": me}
            for tag, m, al in (("b4", mb, b4a), ("ew", me, ewa)):
                agg[tag]["tp"] += m["time_level"]["TP"]; agg[tag]["fp"] += m["time_level"]["FP"]
                agg[tag]["fn"] += m["time_level"]["FN"]; agg[tag]["tn"] += m["time_level"]["TN"]
                agg[tag]["events"] += len(ev); agg[tag]["det"] += m["event_level"]["n_detected"]
                agg[tag]["adv"] += sum(1 for l in m["event_level"]["leads_s"] if l is not None and l >= LEAD_TARGET_S)
                agg[tag]["fa_event"] += m["false_alarms"]["event_level_count"]
                agg[tag]["alarm_s"] += m["alarm_active_seconds"]
                agg[tag]["unknown_s"] += m["unknown_seconds"]
                agg[tag]["n"] += len(flow)
        tot_hours = sum(s["n_seconds"] for s in scen.values()) / 3600.0
        tot_s = sum(s["n_seconds"] for s in scen.values())
        summary = {}
        for tag in ("b4", "ew"):
            a = agg[tag]
            prec = a["tp"] / (a["tp"] + a["fp"]) if a["tp"] + a["fp"] else None
            rec = a["tp"] / (a["tp"] + a["fn"]) if a["tp"] + a["fn"] else None
            summary[tag] = {"events": a["events"], "detected": a["det"],
                            "recall_event_level": _r(a["det"] / a["events"]) if a["events"] else None,
                            "lead_ge_30s_coverage_all_events": _r(a["adv"] / a["events"]) if a["events"] else None,
                            "precision_time_level": _r(prec), "recall_time_level": _r(rec),
                            "event_level_false_alarms": a["fa_event"],
                            "event_level_false_alarms_per_hour": round(a["fa_event"] / tot_hours, 3) if tot_hours else None,
                            "time_level_FP_steps": a["fp"],
                            "alarm_active_ratio": _r(a["alarm_s"] / tot_s) if tot_s else None,
                            "unknown_seconds": a["unknown_s"]}
        rep[group] = {"scenarios": scen, "summary": summary, "total_hours": round(tot_hours, 3)}

    OUT.write_text(json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(rep["valid"]["summary"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
